"""An adversary pilot that flies basic fighter manoeuvres.

The bandit is not on a path. It is a second aircraft running the *same*
flight model, through the same aerodynamics, the same engine, the same stall
and the same structural limits, flown by the control laws below. That is the
only arrangement under which a dogfight means anything: an opponent on rails
can do things the aeroplane cannot, and beating it teaches you nothing.

What the pilot knows is the geometry in ``geometry.py`` -- range, closure,
angle off tail, antenna train angle, energy -- and what it decides is which
manoeuvre to fly. The manoeuvres are the standard ones, and each is here
because it answers a specific geometry:

**Pursuit curves.** Three ways to point an aeroplane at another one, and the
difference between them is the whole of gun-tracking BFM:

* *Pure pursuit* -- nose on the bandit. Closes range fast. Flown briefly, to
  shoot, because held it drives you into an overshoot.
* *Lead pursuit* -- nose ahead of the bandit, into the turn. Gains angles and
  closes hardest. The attacking curve.
* *Lag pursuit* -- nose behind the bandit, outside its turn. Gives up angles
  to hold range and energy. What you fly when you are closing too fast, and
  the single most useful thing a novice does not do.

**High yo-yo.** Closing too fast from behind: pull up out of the plane of the
turn, trade speed for height, let the bandit pull ahead, then pull back down
into position. Converts an overshoot into an attack.

**Low yo-yo.** Behind but slow and falling out of the turn: drop the nose
below the bandit's plane, trade height for speed, cut the corner and come up
behind. The reverse trade.

**Break turn.** Defensive, maximum-performance turn into the attacker. Spends
every joule the aircraft has to spoil the attacker's tracking solution. The
right answer when you are about to be shot and the wrong one at any other
time, because afterwards you have nothing left.

**Scissors.** Both aircraft overshooting each other repeatedly, each trying to
end up behind. A slow-speed fight the slower-flying aeroplane wins.

**Extension.** Run away in a straight line to build energy, then turn back.
Deliberately giving up position to buy the energy to take it back.

The pilot picks among these the way the textbooks say to: by range, by angle,
and above all by who has the energy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..core.units import G0, clamp, kt
from .geometry import Geometry, available_load_factor, corner_speed, observe


class Manoeuvre(Enum):
    """What the adversary is doing, and why."""

    LEAD_PURSUIT = "LEAD"
    PURE_PURSUIT = "PURE"
    LAG_PURSUIT = "LAG"
    HIGH_YOYO = "HIGH YO-YO"
    LOW_YOYO = "LOW YO-YO"
    BREAK = "BREAK"
    MERGE = "MERGE"
    SCISSORS = "SCISSORS"
    EXTEND = "EXTEND"
    RESET = "RESET"


@dataclass
class Skill:
    """How good this adversary is.

    Not a cheat factor -- it never gives the bandit more g or more thrust
    than the aeroplane has. It changes how *well* the pilot flies: how fast
    it reacts, how precisely it holds a pursuit curve, and how often it
    picks the right manoeuvre rather than the obvious one.
    """

    name: str = "regular"
    #: Seconds between decisions. A slow pilot commits late.
    decision_interval: float = 0.8
    #: Lag between seeing a change and responding, seconds.
    reaction_time: float = 0.6
    #: Fraction of available g it is willing to pull.
    aggression: float = 0.85
    #: Random error added to the commanded bank, radians.
    sloppiness: float = 0.10
    #: Probability per decision of choosing the energy-correct manoeuvre
    #: rather than simply pointing at the target.
    discipline: float = 0.75


SKILLS: dict[str, Skill] = {
    "novice": Skill("novice", 1.6, 1.2, 0.60, 0.30, 0.25),
    "regular": Skill("regular", 0.8, 0.6, 0.85, 0.10, 0.75),
    "veteran": Skill("veteran", 0.45, 0.30, 0.95, 0.04, 0.92),
    "ace": Skill("ace", 0.25, 0.15, 1.00, 0.015, 0.99),
}


# Range bands, metres. Gun employment happens inside a few hundred metres,
# and the manoeuvres that matter change with which band you are in.
GUN_RANGE = 750.0
CLOSE = 1500.0
MEDIUM = 4000.0

#: Inside this range, and slow, a neutral fight is a scissors. Much tighter
#: than ``CLOSE``, because a scissors is flown in hundreds of feet.
SCISSORS_RANGE = 600.0
#: Seconds to closest approach inside which a closing geometry is a
#: collision risk rather than an attack.
#: Deliberately generous. Two seconds of three and a half g builds only
#: about eighty metres of lateral offset, and two aircraft each managing
#: that still pass inside a hundred and sixty metres. Five seconds builds
#: four hundred, which is a pass rather than a near miss -- and a real merge
#: is called at a mile or more for exactly this reason.
COLLISION_SECONDS = 9.0
#: Closure, as a fraction of own airspeed, above which a convergence counts
#: as high-aspect and therefore as a collision risk rather than an attack.
COLLISION_ASPECT = 0.8
#: How far to one side of the other aircraft to aim at a merge, metres.
#: Comfortably outside the separation floor, because the aim point is what
#: is flown at and the pass is never quite as tidy as the geometry.
MERGE_MISS = 500.0
MERGE_BANK_MAX = math.radians(60.0)
#: A merge is flown at a moderate g, not a maximum one: the pass is about
#: geometry, and the energy is wanted for the fight on the far side of it.
#: It still has to be enough to reach the aim point -- at three and a half
#: g, five hundred metres of offset inside four seconds needs six and a
#: half g of pure lateral, so the aircraft flies a lead curve at an aim
#: point it cannot reach and arrives close anyway. Hence the generous
#: COLLISION_SECONDS above: the fix is time, not g.
MERGE_LOAD_FACTOR = 4.5
#: And slow, as a fraction of corner speed. A scissors is won by whichever
#: aircraft can fly slower without falling out of the sky, so it is flown
#: well down in the lift-limited regime -- not merely a little below corner,
#: which at a nine g placard is still five hundred knots.
SCISSORS_SPEED_FRACTION = 0.75

#: Seconds of lead, at most. Aiming further ahead than this of a hard
#: turning aircraft points at sky it will never reach.
LEAD_TIME_MAX = 2.5
#: Seconds of lag. Aiming this far behind the target is what holds range.
#: Modest, because lag pursuit gives up angles to hold range -- it is a
#: correction to a pursuit curve, not a decision to leave.
LAG_TIME = 0.8

#: Fraction of stall alpha the adversary will command. Not a cheat -- the
#: aircraft can exceed it, the pilot just does not ask it to, which is what
#: separates a fighter pilot from a departure.
ALPHA_LIMIT = 0.88

#: The angle of attack to unload to when rebuilding energy, radians. Near
#: the bottom of the drag curve: enough lift to fly, little induced drag.
ACCELERATION_ALPHA = math.radians(3.0)

#: Flight path angle to extend along, radians. Very slightly downhill: an
#: extension is for building speed, and gravity is free thrust.
EXTEND_GAMMA = math.radians(-2.0)
#: Flight path angle to recover energy along. Properly downhill, because at
#: this point speed is the only thing that matters.
RECOVERY_GAMMA = math.radians(-12.0)
#: Gain of the flight-path hold, 1/s. How fast the commanded path angle is
#: chased; higher oscillates, lower lets the nose wander.
GAMMA_GAIN = 0.55
#: Most the flight-path hold may add to or take off the load factor, g.
GAMMA_AUTHORITY = 1.5

#: Ground avoidance. The pull-out starts when the height remaining is less
#: than this many seconds of the present descent, bounded at both ends, and
#: the pull-out then commands everything the wing has.
GROUND_LOOKAHEAD = 11.0
GROUND_FLOOR_MIN = 400.0
GROUND_FLOOR_MAX = 2400.0

#: The steepest climb the pilot will accept, radians, and the speed -- as a
#: multiple of the recovery floor -- at which it is willing to use all of it.
ZOOM_GAMMA_MAX = math.radians(55.0)
ZOOM_FREE_RATIO = 1.9
#: The steepest dive the pilot will accept, radians. Steep enough for any
#: BFM and recoverable from inside the height a fight has.
DIVE_GAMMA_MAX = math.radians(60.0)
#: Where the dive limiter starts easing in, radians.
DIVE_GAMMA_START = math.radians(22.0)
#: Most bank allowed while recovering from too steep a dive. Under ninety
#: degrees, so the lift vector has an upward component to pull with.
DIVE_RECOVERY_BANK = math.radians(60.0)
#: Most bank an extension will use, radians -- enough to swing the nose
#: round, not enough to be a turning fight.
EXTEND_BANK_MAX = math.radians(70.0)

#: The speed a fighter pilot wants to be at, as a fraction of corner speed.
#: Measured rather than assumed: flying this aircraft round level turns at
#: a range of bank angles, sustained turn rate peaks at about 0.87 of the
#: 9 g corner speed -- 494 kt and 5.8 g at fifteen thousand feet, turning
#: at 12.7 degrees a second. A thrust-rich fighter's best sustained turn
#: really does sit close to corner.
FIGHT_SPEED_FRACTION = 0.87

#: Pointing error inside which the pilot switches from turning to tracking,
#: radians, and the gain of the tracking law in reciprocal seconds.
TRACK_HANDOVER = math.radians(30.0)
TRACK_POINT_GAIN = 1.3
#: And the range inside which precision tracking replaces cornering, metres.
#: A little outside the scoring envelope, so the attacker is still cutting
#: the corner as it arrives rather than stabilising short of it.
TRACK_HANDOVER_RANGE = 1100.0

#: Height above ground below which the pilot stops trading altitude for
#: anything, and the height it treats as the bottom of the fight. A turning
#: fight descends -- lift that is turning the aircraft is not holding it up
#: -- so without a floor the fight ends in the ground every time.
FIGHT_FLOOR = 1200.0
HARD_FLOOR = 450.0

#: Inner pitch loop: stick per radian of angle-of-attack error, and stick
#: per radian/second of pitch rate. Tuned on a commanded 4 g turn at medium
#: altitude, where this pair tracks the command to within 1 percent;
#: raising the gain to 6 overshoots it by nearly 40 percent, which in a
#: dogfight is the difference between a hard turn and a departure.
ALPHA_GAIN = 4.0
PITCH_RATE_DAMPING = 1.8

#: Inner roll loop: aileron per radian of bank error, per radian-second of
#: accumulated error, and per radian/second of roll rate. The integrator is
#: bounded so a bank the aircraft simply cannot reach does not wind it up.
ROLL_GAIN = 2.5
ROLL_INTEGRAL_GAIN = 1.4
ROLL_RATE_DAMPING = 0.55
BANK_INTEGRAL_LIMIT = 0.55

#: Speed below which energy recovery takes priority over the manoeuvre, as a
#: multiple of 1-g stall speed.
#:
#: Deliberately NOT tied to corner speed. Corner at a 9 g placard is about
#: three times the 1-g stall -- near 480 kt TAS for this aircraft at medium
#: altitude -- and a fighter that refuses to manoeuvre below its 9 g corner
#: refuses to manoeuvre at all. Real fighters fight hard well below it: at
#: twice the stall speed the wing still delivers four g. What actually ends
#: a fight is approaching the stall, where there is no g left to buy, so
#: that is what this floor tracks.
RECOVERY_STALL_MARGIN = 1.30
#: Once recovering, keep recovering until the speed is back above this
#: multiple of the stall. Without the gap the pilot alternates between
#: fighting and running every time the needle crosses one number.
RECOVERY_RELEASE_MARGIN = 1.75


@dataclass
class AdversaryState:
    """What the adversary is doing, for the HUD and the telemetry."""

    manoeuvre: Manoeuvre = Manoeuvre.RESET
    #: Seconds it has been flying this one.
    elapsed: float = 0.0
    #: Why it picked it, in one short phrase.
    reason: str = ""
    geometry: Geometry | None = None
    #: Commanded bank and load factor, for debugging the law.
    commanded_bank: float = 0.0
    commanded_g: float = 1.0


class Adversary:
    """Flies one aircraft against another using basic fighter manoeuvres.

    Drives the same ``PilotInput`` a human does -- stick, rudder, throttle --
    so there is no control path into the flight model that a player does not
    also have. The bandit cannot do anything you cannot.
    """

    def __init__(self, model, skill: str = "regular", seed: int = 1) -> None:
        self.model = model
        self.skill = SKILLS.get(skill, SKILLS["regular"])
        self._rng = np.random.default_rng(seed + 5171)

        self.load_limit = model.get("limitations", "load_factor_positive", 7.0)
        self.vmo = model.get("limitations", "vmo", kt(600.0))

        self.state = AdversaryState()
        self._next_decision = 0.0
        self._bank_filter = 0.0
        self._bank_integral = 0.0
        self._manoeuvre_started = 0.0
        self._scissors_side = 1.0

        # Turn performance at the current moment, recomputed once per step by
        # ``_performance``. Both the tactics and the energy gate read these,
        # and computing them once is what keeps the two from disagreeing
        # about what the aeroplane can do.
        self._corner = kt(330.0)
        self._available = 1.0
        self._stall_speed = kt(140.0)
        self._recovery_floor = kt(250.0)
        self._fight_speed = kt(450.0)
        #: 0 normally; rises toward 1 as the aircraft runs out of energy, and
        #: tells the pitch loop to unload instead of holding a load factor.
        self._recovery = 0.0
        #: True while committed to rebuilding energy; widens the floor.
        self._rebuilding = False

    # -- decision ---------------------------------------------------------

    def _choose(self, geometry: Geometry, own, target, air) -> tuple[Manoeuvre, str]:
        """Pick a manoeuvre from the geometry. This is the tactics."""
        skill = self.skill
        aot = geometry.angle_off_tail
        ata = geometry.antenna_train
        energy = geometry.energy_advantage

        # An undisciplined pilot just points at the other aeroplane. It is
        # the instinct the whole discipline exists to overcome, and it is
        # what makes a novice lose an energy fight without understanding why.
        if self._rng.random() > skill.discipline:
            return Manoeuvre.PURE_PURSUIT, "chasing the nose"

        # -- about to hit the other aeroplane ------------------------------
        # Before any tactic. At these speeds a closing geometry inside a few
        # hundred metres is seconds from a collision, and no position is
        # worth one. A pilot flies out of the other aircraft's plane of
        # motion; here that is a break away from the line of sight.
        # Judged in TIME, not distance. A fixed range threshold gives
        # whatever warning the closure happens to allow, and at merge speeds
        # three hundred metres is under a second -- less than the aircraft
        # needs to roll, let alone turn. Seconds-to-go is the quantity a
        # pilot actually uses, and it scales itself.
        # High-aspect convergence only, which is what the closure says: in a
        # stern chase the closure is a fraction of your own speed, and in a
        # nose-to-nose pass it is about twice it. Testing seconds-to-go alone
        # would call a gun attack a collision -- closing on a bandit at a
        # hundred and fifty metres a second from five hundred metres astern
        # is three seconds to go and exactly where you want to be.
        if geometry.closure < -COLLISION_ASPECT * own.derived.vtas:
            seconds_to_go = geometry.range / -geometry.closure
            if seconds_to_go < COLLISION_SECONDS:
                # Deconflict, do not break. A break turn aims the lift
                # vector AT the other aircraft, which on a closing pass is
                # the one thing that must not happen: two pilots both
                # breaking into each other converge, and that is exactly
                # what the logs showed -- every merge ended inside the
                # separation floor within seven seconds.
                #
                # Real training solves this by briefing a pass side, so this
                # does too: both aircraft offset to the same hand and pass
                # left-to-left. Deconflicting a merge is not avoidance, it
                # is how a merge is flown, and what comes out the far side
                # of it is a two-circle fight.
                return Manoeuvre.MERGE, "merging, passing left to left"

        # -- defensive: someone is behind me and about to shoot ------------
        if geometry.defensive:
            if geometry.range < GUN_RANGE:
                # Nothing matters except spoiling the solution.
                return Manoeuvre.BREAK, "guns range, breaking"
            if geometry.range > CLOSE and (
                energy < -250.0 or own.derived.vtas < self._recovery_floor
            ):
                # Behind and slow, but not yet in danger: run, rebuild, and
                # come back with something to fight with. A break turn with
                # no energy is a one-second manoeuvre followed by a spin.
                return Manoeuvre.EXTEND, "defensive and slow, extending"
            return Manoeuvre.BREAK, "defensive, turning into it"

        # -- out of energy, nobody shooting yet ----------------------------
        # Before any offensive or neutral reasoning: an aircraft below its
        # recovery floor cannot fly any of the manoeuvres below, and
        # pretending otherwise is how a fighter ends up slow, low and out of
        # the fight. Trade the position for the energy to retake it.
        if own.derived.vtas < self._recovery_floor and geometry.range > GUN_RANGE:
            return Manoeuvre.EXTEND, "no energy, unloading to rebuild"

        # -- offensive: I am behind, now do not waste it -------------------
        if aot < math.radians(70.0) and geometry.range < MEDIUM:
            closing_hard = geometry.closure < -60.0
            very_close = geometry.range < CLOSE

            if very_close and closing_hard:
                # The classic overshoot about to happen. Up and over.
                return Manoeuvre.HIGH_YOYO, "closing too fast"
            if very_close and geometry.line_of_sight_rate > 0.20 and geometry.closure < -15.0:
                # Cannot keep the nose on it and still closing; go outside
                # the turn instead of pulling harder and flying out in front.
                #
                # The closure term is essential. Lag pursuit is the cure for
                # an impending overshoot, and an overshoot needs closure to
                # happen -- a high line-of-sight rate at constant range just
                # means the bandit is turning hard, and the answer to that is
                # more turn, not less. Triggering on the rate alone made the
                # attacker aim behind a bandit it was not gaining on at all,
                # which walked it from nine hundred metres astern out to six
                # kilometres with its nose pointing the other way.
                return Manoeuvre.LAG_PURSUIT, "closing fast, going lag"
            if geometry.range < GUN_RANGE and ata < math.radians(20.0):
                return Manoeuvre.PURE_PURSUIT, "in the envelope"
            if energy < -150.0 and geometry.height_advantage < 0.0:
                # Behind, but slow and low: cut the corner with a dive.
                return Manoeuvre.LOW_YOYO, "behind but low on energy"
            return Manoeuvre.LEAD_PURSUIT, "closing for a solution"

        # -- neutral: nobody owns anything yet -----------------------------
        # A scissors is a SLOW fight at knife-fighting range: two aircraft
        # repeatedly overshooting each other at low speed. Range and closure
        # alone do not identify it -- two fighters passing line abreast at
        # 500 kt also have a small closure, and flying a scissors there just
        # throws away every joule of energy for nothing. The speed test is
        # what makes this manoeuvre mean what it is called.
        if (
            geometry.range < SCISSORS_RANGE
            and abs(geometry.closure) < 40.0
            and own.derived.vtas < SCISSORS_SPEED_FRACTION * self._corner
        ):
            return Manoeuvre.SCISSORS, "slow-speed knife fight"
        if energy < -300.0:
            return Manoeuvre.EXTEND, "down on energy, separating"
        if geometry.range > MEDIUM:
            return Manoeuvre.LEAD_PURSUIT, "closing the range"
        return Manoeuvre.LEAD_PURSUIT, "turning for position"

    # -- flying -----------------------------------------------------------

    def update(self, sim, target_state, dt: float) -> AdversaryState:
        """Fly one step. ``sim`` is the bandit's own Simulation."""
        own = sim.fdm.state
        derived = own.derived
        geometry = observe(own, target_state)
        self._performance(sim, derived)

        now = sim.time
        if now >= self._next_decision:
            self._next_decision = now + self.skill.decision_interval
            manoeuvre, reason = self._choose(geometry, own, target_state, None)
            if manoeuvre is not self.state.manoeuvre:
                self._manoeuvre_started = now
                if manoeuvre is Manoeuvre.SCISSORS:
                    self._scissors_side = -self._scissors_side
            self.state.manoeuvre = manoeuvre
            self.state.reason = reason

        self.state.elapsed = now - self._manoeuvre_started
        self.state.geometry = geometry

        bank, pull, throttle = self._commands(geometry, own, target_state, derived, sim)
        self._apply(sim, bank, pull, throttle, derived, dt)
        return self.state

    def _performance(self, sim, derived) -> None:
        """What the aeroplane can do right now, at this weight and altitude.

        Corner speed, the g available, and the stall all move with density
        and fuel state, so a bandit that reasons about them from fixed
        numbers is reasoning about a different aeroplane than the one it is
        flying. Computed once per step, before the tactics read them.
        """
        aero = sim.fdm.aero
        mass = sim.fdm.mass_properties.mass
        density = max(derived.density, 1.0e-4)

        self._corner = corner_speed(aero, mass, density, self.load_limit)
        self._available = available_load_factor(
            aero, mass, density, max(derived.vtas, 1.0), self.load_limit
        )
        self._stall_speed = aero.stall_speed(mass, density)
        # The speed the pilot wants to fight at: a fraction of corner, which
        # is where measured sustained turn rate peaks for a thrust-rich
        # fighter, and never above the placard.
        self._fight_speed = min(
            FIGHT_SPEED_FRACTION * self._corner, 0.92 * self.vmo
        )
        # Hysteresis: the floor sits higher while recovering, so the pilot
        # commits to rebuilding energy instead of oscillating across one
        # threshold.
        self._recovery_floor = self._stall_speed * (
            RECOVERY_RELEASE_MARGIN if self._rebuilding else RECOVERY_STALL_MARGIN
        )
        self._rebuilding = derived.vtas < self._recovery_floor

    def _commands(self, geometry: Geometry, own, target, derived, sim):
        """Turn the chosen manoeuvre into a bank angle, a g, and a throttle.

        Every manoeuvre here is expressed the way a pilot flies one: put the
        lift vector somewhere and pull. The lift vector direction is the bank
        angle about the line of sight, and how hard you pull is the g. That
        is the whole of BFM control, and it is why the same three numbers
        describe all nine manoeuvres.
        """
        manoeuvre = self.state.manoeuvre
        aggression = self.skill.aggression

        available = self._available
        corner = self._corner

        # Where the target is, relative to my own wings-level frame: the
        # angle to roll to put the lift vector on it. The three pursuit
        # curves differ only in *where along the target's flight path* the
        # aim point is, so they are all one function with one parameter.
        full = aggression * available
        hard = aggression * min(available, 0.85 * self.load_limit)

        if manoeuvre is Manoeuvre.BREAK:
            # Everything the aeroplane has, into the attacker, and unload
            # nothing. Speed decays to corner and stays there, because below
            # corner the turn rate falls away.
            bank = self._aim_bank(own, target, geometry, 0.0)
            pull = full
            throttle = 1.0

        elif manoeuvre is Manoeuvre.EXTEND:
            # Away from the threat, nose level or slightly down, everything
            # forward. Wings level, because any bank at all is turn and turn
            # is drag, and the whole point of extending is to buy energy.
            #
            # The pull is a FLIGHT PATH command, not a load factor. Holding
            # 1 g does not level an aeroplane off -- 1 g is what it pulls in
            # any steady climb, so a nose-high fighter commanded to 1 g
            # keeps climbing, decelerating all the way to a stall. Asking
            # for a slightly descending path instead is what extending
            # actually is, and the load factor needed to fly it falls out of
            # the kinematics.
            # Flown as a heading change onto the reciprocal bearing, not as
            # a lift vector pointed away. Adding half a turn to the aim bank
            # looks equivalent and is not: the aim bank swings through its
            # whole range when the target passes near straight-up or
            # straight-down, so the reciprocal of it thrashes between plus
            # and minus three hundred degrees and the aileron slams stop to
            # stop. An extension is a heading, and a heading hold is stable.
            bank = self._bank_away(own, geometry)
            pull = self._gamma_hold(derived, EXTEND_GAMMA)
            throttle = 1.0

        elif manoeuvre is Manoeuvre.HIGH_YOYO:
            # Out of the plane of the turn and up: trade speed for position
            # and let the closure wash off. Rolling the lift vector partway
            # back toward the vertical is what puts the nose above the
            # bandit's plane, which is the whole manoeuvre.
            bank = self._aim_bank(own, target, geometry, 0.0) * 0.5
            pull = 0.80 * hard
            throttle = 0.70

        elif manoeuvre is Manoeuvre.LOW_YOYO:
            # Nose below the bandit's plane to buy speed, then back up. Bank
            # past the target so the lift vector pulls the nose down and
            # inside its turn, cutting the corner.
            bank = self._aim_bank(own, target, geometry, 0.0) * 1.30
            pull = 0.55 * hard
            throttle = 1.0

        elif manoeuvre is Manoeuvre.LAG_PURSUIT:
            # Aim BEHIND the target's present position: a negative lead time
            # puts the nose outside its turn, which gives up angles to hold
            # range and energy. The one manoeuvre a novice never flies.
            bank = self._aim_bank(own, target, geometry, -LAG_TIME)
            pull = 0.65 * hard
            throttle = 0.80

        elif manoeuvre is Manoeuvre.PURE_PURSUIT:
            # Nose on the target itself: zero lead time.
            bank = self._aim_bank(own, target, geometry, 0.0)
            pull = self._pull_for_tracking(geometry, derived, hard)
            throttle = 0.90 if geometry.closure > -20.0 else 0.70

        elif manoeuvre is Manoeuvre.MERGE:
            # Nose offset to one side of the line of sight, in the
            # horizontal, so the two aircraft pass rather than meet. The
            # side is fixed rather than chosen, because two aircraft each
            # picking the "better" side is how a near miss happens.
            bank = self._bank_to_miss(own, target, geometry, MERGE_MISS)
            pull = min(hard, MERGE_LOAD_FACTOR)
            throttle = 1.0

        elif manoeuvre is Manoeuvre.SCISSORS:
            # Cross the other aircraft's flight path, slowly, alternating
            # sides. Whoever can fly slower without falling out of the sky
            # ends up behind -- so the throttle comes back, but only to the
            # point where the energy gate below starts defending the stall.
            bank = self._scissors_side * math.radians(70.0)
            pull = 0.7 * hard
            throttle = 0.45

        else:  # LEAD_PURSUIT and RESET
            # Aim AHEAD of the target along its flight path. The lead time
            # is the time of flight to it, which is what makes this
            # proportional navigation rather than a guess, and it is what an
            # attacking pilot does by eye when they "pull for the lead".
            bank = self._aim_bank(own, target, geometry, self._lead_time(geometry))
            pull = self._pull_for_tracking(geometry, derived, hard)
            throttle = 1.0 if geometry.range > CLOSE else 0.85

        # Sloppiness: a real pilot does not hold a bank angle exactly, and a
        # bandit that does feels like a machine.
        bank += float(self._rng.normal(0.0, self.skill.sloppiness))

        bank, pull = self._vertical_limit(pull, bank, derived)
        bank, pull, throttle = self._energy_gate(bank, pull, throttle, derived, corner)

        self.state.commanded_bank = bank
        self.state.commanded_g = pull
        return bank, pull, throttle

    # -- energy ------------------------------------------------------------

    def _energy_gate(self, bank, pull, throttle, derived, corner):
        """Refuse to fly a manoeuvre that would put the aircraft on the ground.

        This is the difference between a pilot and a control law. Every
        manoeuvre above asks for a bank and a g as though energy were free;
        it is not. Sustained hard turning below corner speed converts a
        fighter into a brick, and the brick loses -- first the turn rate,
        then the turn, then the aeroplane.

        So a real pilot watches the airspeed through a manoeuvre and gives up
        on it before the wing does. Below ``corner`` the throttle goes
        forward; below the recovery floor the pull is eased toward 1 g and
        the bank toward level, which trades the turn for the energy to turn
        again. The aircraft is never *prevented* from reaching high alpha --
        it simply is not commanded to hold it until it falls out of the sky.
        """
        floor = self._recovery_floor
        self._recovery = 0.0
        if derived.vtas < corner:
            throttle = 1.0

        # And an upper limit. A fighter does not run away from a turning
        # fight at Mach 1.5: past the speed where it turns best, more speed
        # is a bigger radius and nothing else, and past VMO it is damage.
        if derived.vtas > self._fight_speed * 1.12:
            throttle = min(throttle, 0.70)
        if derived.vtas > 0.96 * self.vmo:
            throttle = min(throttle, 0.25)

        if derived.vtas >= floor:
            return bank, pull, max(throttle, 0.25)

        # How far into the hole: 0 at the floor, 1 at the point where
        # nothing matters but flying again.
        span = max(floor - self._stall_speed * 1.02, 1.0)
        deficit = clamp((floor - derived.vtas) / span, 0.0, 1.0)

        # Ease the pull toward 1 g: the turn is already not working.
        pull = 1.0 + (pull - 1.0) * (1.0 - deficit)
        if deficit > 0.55:
            # Past here the aeroplane is not flying a manoeuvre any more, it
            # is recovering. ``_recovery`` tells the inner loop to unload to
            # a low angle of attack rather than hold a load factor, because
            # at these speeds *any* commanded g needs alpha near the limit
            # and holding it is how a slow fighter arrives at the ground.
            # Unloading lets the nose fall, and a nose-low unloaded fighter
            # at full burner accelerates faster than anything else it can do.
            self._recovery = (deficit - 0.55) / 0.45
            # Downhill, wings level, burner: the only way back. Blended in
            # rather than switched, so the manoeuvre is abandoned smoothly.
            recover = self._gamma_hold(derived, RECOVERY_GAMMA)
            pull += (recover - pull) * self._recovery
            # Roll out too: lift vector away from vertical is lift not
            # holding the aeroplane up.
            bank *= 1.0 - 0.85 * self._recovery
            self.state.reason = "out of energy, unloading"
        return bank, pull, 1.0

    def _vertical_limit(self, pull: float, bank: float, derived):
        """Refuse to let the nose climb away from the aircraft's energy.

        A load factor and a bank angle commanded independently do not
        describe a turn: they describe whatever the leftover lift does to
        the flight path. Six g at sixty degrees of bank is not a hard level
        turn, it is a climbing loop -- the vertical component is ``n cos
        phi``, and anything above ``cos gamma`` curves the path upward until
        the aircraft runs out of speed. Left alone, a fighter commanded that
        way zooms from fifteen thousand feet to twenty-eight thousand and
        arrives at eighty knots, which is what the flight model will
        faithfully reproduce because that is what the manoeuvre asked for.

        Going vertical is legitimate -- half of BFM happens in the vertical
        -- but only with the speed to come back down. So the ceiling on the
        flight path angle is a function of energy: at the recovery floor the
        aircraft may not climb at all, and by nearly twice the floor it may
        go wherever it likes. Between the two the limit is what a pilot
        means by "I don't have the speed for that".
        """
        floor = max(self._recovery_floor, 1.0)
        margin = clamp((derived.vtas / floor - 1.0) / (ZOOM_FREE_RATIO - 1.0), 0.0, 1.0)
        gamma_ceiling = ZOOM_GAMMA_MAX * margin

        # The load factor whose vertical component exactly holds the present
        # flight path, for whichever direction needs to be held.
        vertical = max(abs(math.cos(bank)), 0.10)
        level = math.cos(derived.gamma) / vertical

        if derived.gamma > gamma_ceiling:
            # Capping here stops the climb steepening without forcing the
            # nose back down, which would throw away the height already
            # bought.
            return bank, min(pull, level)

        # And the floor. A turning fight descends by construction: lift that
        # is turning the aircraft is lift not holding it up, and eighty
        # degrees of bank leaves almost none of it vertical. Without a floor
        # the fight is a spiral that ends in the ground, which is what the
        # engagement logs showed it doing -- in a hundred and fifty seconds
        # a hard fight comfortably spends fifteen thousand feet.
        #
        # A real pilot fights the last few thousand feet differently: the
        # ground becomes an opponent and the turn flattens out. Below
        # ``FIGHT_FLOOR`` the limit squeezes the descent out of the turn, so
        # the aircraft turns level or climbing instead of dying quietly.
        # A dive limit, for the same reason as the zoom limit and with the
        # same mechanism. Pointing the lift vector below the horizon and
        # pulling is a real manoeuvre, and left unbounded it took the
        # aircraft through seventy degrees nose down inside nine seconds --
        # from which the recovery costs more height than a fight usually has.
        # Sixty degrees is steep enough for any BFM and recoverable from.
        if derived.gamma < -DIVE_GAMMA_START:
            # Roll the lift vector back toward the horizon before pulling.
            # Raising the g alone does nothing here and can make it worse:
            # past ninety degrees of bank the lift vector points below the
            # horizon, so every extra g steepens the dive. That is how the
            # nose reached eighty degrees down with a sixty degree limit
            # nominally in force.
            #
            # Applied progressively rather than as a cliff at the limit,
            # because a limiter that only intervenes once the dive is already
            # too steep has, by then, spent the g that made it steep. Easing
            # the bank in from a third of the way down is what a pilot does
            # instead, and it keeps the manoeuvre available at shallow dive
            # angles where it is perfectly safe.
            steepness = clamp(
                (-derived.gamma - DIVE_GAMMA_START)
                / (DIVE_GAMMA_MAX - DIVE_GAMMA_START),
                0.0,
                1.0,
            )
            allowed = abs(bank) + (DIVE_RECOVERY_BANK - abs(bank)) * steepness
            bank = math.copysign(min(abs(bank), max(allowed, 0.0)), bank)
            upright = max(abs(math.cos(bank)), 0.10)
            level = math.cos(derived.gamma) / upright
            return bank, min(pull + (level - pull) * steepness, self._available)

        if derived.altitude_agl < FIGHT_FLOOR and derived.gamma < 0.0:
            urgency = clamp(
                (FIGHT_FLOOR - derived.altitude_agl) / (FIGHT_FLOOR - HARD_FLOOR),
                0.0,
                1.0,
            )
            # More g cannot fix this on its own: holding altitude at eighty
            # degrees of bank would take eleven g, which the wing does not
            # have at any speed this aircraft reaches. What a pilot does
            # instead is roll out -- the bank that lets the available g fly
            # level is acos(cos gamma / n), and flattening to it trades turn
            # rate for the only thing that matters this low.
            ceiling = math.acos(clamp(math.cos(derived.gamma) / max(pull, 1.0), 0.0, 1.0))
            limit = abs(bank) + (ceiling - abs(bank)) * urgency
            bank = math.copysign(min(abs(bank), max(limit, 0.0)), bank)
            # Recompute the level load factor for the bank that will actually
            # be flown. Using the pre-flattened bank asks for the g that
            # would hold altitude at eighty-five degrees -- eleven of it --
            # and the floor then commands max g through manoeuvres that are
            # trying to unload.
            flattened = max(abs(math.cos(bank)), 0.10)
            level = math.cos(derived.gamma) / flattened
            pull = max(pull, 1.0 + (min(level, self._available) - 1.0) * urgency)
        return bank, pull

    @staticmethod
    def _gamma_hold(derived, gamma_target: float) -> float:
        """The load factor that flies the aircraft onto a flight path angle.

        From the kinematics of a pull-up, ``n = cos(gamma) + V*gamma_dot/g``:
        the load factor needed to hold a path is the component that balances
        weight, plus whatever curves the path at the rate asked for. Driving
        ``gamma_dot`` proportionally to the error turns that identity into a
        flight-path hold, and it is why this returns less than 1 g when the
        nose is high -- which is exactly the command a zooming, decelerating
        fighter needs and the one a load-factor controller can never give.

        The limit is on the load factor the hold may add, not on the path
        rate it asks for. ``V/g`` is about twenty-five seconds at fighter
        speeds, so a path-rate limit of a fifth of a radian per second is a
        limit of five g -- the authority grows with speed and the loop ends
        up commanding more than the manoeuvre it is supposed to be trimming.
        Bounding the g keeps it a correction at every speed.
        """
        increment = GAMMA_GAIN * (gamma_target - derived.gamma)
        increment *= max(derived.vtas, 30.0) / G0
        wanted = math.cos(derived.gamma) + clamp(
            increment, -GAMMA_AUTHORITY, GAMMA_AUTHORITY
        )
        # Never negative. Unloading to zero g is the real manoeuvre -- it is
        # what "unload and accelerate" means and it gets the nose down fast.
        # Pushing to negative g does get it down faster, and no pilot does
        # it: it is unpleasant, it unports the fuel pickups, and there is
        # nothing in a dogfight it buys that a roll would not buy better.
        return max(wanted, 0.0)

    def _lead_time(self, geometry: Geometry) -> float:
        """Time of flight to the target, which is how far ahead to aim.

        Closing fast means a short lead; sitting at the same speed behind
        something means a long one. Capped, because aiming three seconds
        ahead of a hard-turning aircraft points at empty sky.
        """
        closing = max(-geometry.closure, 60.0)
        return clamp(geometry.range / closing, 0.0, LEAD_TIME_MAX)

    # -- aiming ------------------------------------------------------------

    def _aim_bank(self, own, target, geometry: Geometry, lead_time: float) -> float:
        """Bank angle that puts the lift vector on the aim point, radians.

        The aim point is the target's position ``lead_time`` seconds from now,
        on its present velocity. Positive lead time is lead pursuit, zero is
        pure, negative is lag -- the three pursuit curves are one geometric
        construction with one signed parameter, and that is genuinely how
        they relate to each other.

        The lift vector points along body -z. Rolling by the returned angle
        about the velocity vector aims it at the point, which is the single
        geometric idea the whole of BFM control rests on.
        """
        from ..core.frames import dcm_ned_to_body

        if lead_time == 0.0:
            direction = geometry.line_of_sight
        else:
            aim = target.position + target.velocity_ned * lead_time
            offset = aim - own.position
            norm = float(np.linalg.norm(offset))
            direction = geometry.line_of_sight if norm < 1.0 else offset / norm

        local = dcm_ned_to_body(own.quaternion) @ direction
        # In body axes, y is right and z is down; the lift vector is -z. The
        # angle from -z to the target's projection on the y-z plane is the
        # roll needed.
        return math.atan2(float(local[1]), -float(local[2]))

    @staticmethod
    def _bank_away(own, geometry: Geometry) -> float:
        """Bank that turns the nose onto the reciprocal of the bearing to the target.

        A horizontal heading problem, deliberately: an extension is flown
        wings-level once it is pointing the right way, and the only thing
        bank is for is getting it there.
        """
        line = geometry.line_of_sight
        bearing = math.atan2(-float(line[1]), -float(line[0]))
        error = math.atan2(
            math.sin(bearing - own.derived.yaw), math.cos(bearing - own.derived.yaw)
        )
        return clamp(2.0 * error, -EXTEND_BANK_MAX, EXTEND_BANK_MAX)

    @staticmethod
    def _bank_to_miss(own, target, geometry: Geometry, miss: float) -> float:
        """Bank that flies the nose at a point beside the target, not at it.

        Aimed at a POINT displaced from the other aircraft, rather than at a
        fixed angle off the line of sight. The difference decides whether
        the two aircraft actually pass: holding a constant bearing offset
        from a line of sight that is itself rotating is a pursuit curve with
        a lead angle, and a pursuit curve converges. Two aircraft both
        flying one met inside a hundred and fifty metres every time, with
        twenty-two degrees of offset dutifully held all the way in.

        Aiming at a displaced point instead makes the required bearing
        offset grow as the range closes, which is what holding a miss
        distance actually takes. It is also what a pilot means by passing
        down somebody's left-hand side: they pick a piece of sky off his
        wing and fly at that.

        The side falls out of the geometry consistently. Each aircraft
        displaces to the same hand of its own line of sight, and because the
        two lines of sight are opposite, the two displacements are opposite
        in the world -- so they diverge into a clean pass rather than both
        choosing the same corridor.
        """
        from ..core.frames import cross3

        # Offset perpendicular to the RELATIVE velocity, not to the line of
        # sight. Miss distance is measured across the closing vector -- that
        # is what "miss" means -- and across the line of sight is a
        # different direction entirely whenever the two aircraft are not
        # closing head-on. On a line-abreast merge the two are nearly at
        # right angles, and offsetting across the line of sight sent both
        # aircraft onto paths that swapped their positions and crossed in
        # the middle, which is not a pass.
        #
        # It also makes the handedness work out for free: the two aircraft
        # see relative velocities that are exact negatives, so the same
        # formula sends them opposite ways in the world and both offsets add
        # to the separation.
        closing = target.velocity_ned - own.velocity_ned
        lateral = cross3(closing, np.array([0.0, 0.0, 1.0]))
        norm = float(np.linalg.norm(lateral))
        if norm < 1.0e-6:
            lateral = cross3(geometry.line_of_sight, np.array([0.0, 0.0, 1.0]))
            norm = float(np.linalg.norm(lateral))
            if norm < 1.0e-6:
                return 0.0
        aim = target.position + (lateral / norm) * miss

        offset = aim - own.position
        bearing = math.atan2(float(offset[1]), float(offset[0]))
        error = math.atan2(
            math.sin(bearing - own.derived.yaw), math.cos(bearing - own.derived.yaw)
        )
        return clamp(2.2 * error, -MERGE_BANK_MAX, MERGE_BANK_MAX)

    def _pull_for_tracking(self, geometry: Geometry, derived, hard: float) -> float:
        """How hard to pull when trying to point at something.

        Hard when the nose is far off, gently when it is nearly on -- pulling
        maximum g with the pipper almost on the target overshoots it, and
        the oscillation that follows is the most common way an attack is
        wasted.
        """
        # Beyond the fight, pointing is a navigation problem with a minute to
        # solve it, not a turning contest. Hauling the aircraft round at six
        # g from five miles out spends exactly the energy that will decide
        # the fight on arrival, to gain angles that will have changed by the
        # time it gets there.
        if geometry.range > MEDIUM:
            hard = 1.0 + 0.40 * (hard - 1.0)

        error = geometry.antenna_train
        if error > TRACK_HANDOVER or geometry.range > TRACK_HANDOVER_RANGE:
            # Far off the nose, or too far away: this is a turn, and the
            # answer is all of it.
            #
            # The range test matters as much as the angle. Settling for the g
            # that merely holds the nose on a bandit a kilometre and a half
            # ahead produces an attacker that points beautifully and never
            # arrives -- measured, it tracked to a degree and a half and sat
            # at eleven hundred metres for the whole engagement, because a
            # tail chase at matched speed does not close. Closing is done by
            # cutting the corner, which costs g, and outside gun range that
            # is worth paying.
            return hard

        # Close to the nose, it is no longer a turn but a tracking task, and
        # the two need different laws. Pulling hard with the pipper nearly on
        # overshoots and starts an oscillation that never settles -- which is
        # why a stepped-down pull never quite points: it always commands
        # about two g whether it needs a tenth of one or four.
        #
        # So solve for the g that is actually wanted. The nose must rotate at
        # the rate that closes the pointing error, plus whatever rate the
        # line of sight itself is turning at, because a crossing target
        # requires a turn rate just to stay pointed at. The load factor for a
        # turn rate follows from the kinematics: n = sqrt(1 + (omega V / g)^2).
        # This is proportional navigation, and it is what makes the
        # difference between swinging past the target and settling on it.
        rate = TRACK_POINT_GAIN * error + geometry.line_of_sight_rate
        turn = rate * max(derived.vtas, 30.0) / G0
        needed = math.sqrt(1.0 + turn * turn)
        return clamp(needed, 1.0, hard)

    def _apply(self, sim, bank_command, pull, throttle, derived, dt) -> None:
        """Close the inner loops: bank and g onto stick, rudder and throttle."""
        pilot = sim.pilot
        skill = self.skill

        # First-order lag standing in for reaction time and for the fact that
        # a pilot's hand is not a step function.
        blend = 1.0 - math.exp(-dt / max(skill.reaction_time, 1.0e-3))
        error = math.atan2(
            math.sin(bank_command - self._bank_filter),
            math.cos(bank_command - self._bank_filter),
        )
        # Wrap the filter back into +/-pi. The error above is wrapped, but the
        # accumulator was not, and an unwrapped angle that is only ever added
        # to is not an angle -- it is an integral. Over a turning fight it
        # wound past three hundred degrees, and since the next line used to
        # clamp it to +/-85 degrees, the clamp then held the aircraft in a
        # hard left turn for the rest of the engagement whatever the pilot
        # asked for. Every separated fight in the logs was this one line.
        self._bank_filter = math.atan2(
            math.sin(self._bank_filter + error * blend),
            math.cos(self._bank_filter + error * blend),
        )

        # And no bank limit. A gentle flight director clamps to eighty-five
        # degrees because it is trying not to alarm anyone; BFM routinely
        # rolls past ninety and pulls, because that is how the lift vector is
        # pointed below the horizon. Clamping it does not make the aircraft
        # safer, it makes it roll to the wrong attitude: a command to put the
        # lift vector straight down arrives as eighty-five degrees of bank,
        # which points it sideways.
        target_bank = self._bank_filter

        # Roll: proportional to bank error, damped by roll rate, with an
        # integrator.
        #
        # The integrator is not a refinement, it is the difference between
        # this working and not. A hard turn needs a sustained aileron
        # deflection to hold against the rolling moments it generates, and a
        # proportional law can only produce that by sitting on a standing
        # error -- about fifteen degrees of bank, measured. Fifteen degrees
        # does not sound like much until the vertical component is written
        # out: six g at the eighty degrees of bank the pilot asked for holds
        # the nose level, and six g at the sixty-seven degrees it actually
        # gets leaves well over two g curving the flight path upward. The
        # aircraft zooms, decelerates, and arrives slow and high wondering
        # why. Closing the bank error closes the whole energy problem.
        bank_error = math.atan2(
            math.sin(target_bank - derived.roll), math.cos(target_bank - derived.roll)
        )
        self._bank_integral = clamp(
            self._bank_integral + bank_error * dt, -BANK_INTEGRAL_LIMIT,
            BANK_INTEGRAL_LIMIT,
        )
        pilot.roll = clamp(
            ROLL_GAIN * bank_error
            + ROLL_INTEGRAL_GAIN * self._bank_integral
            - ROLL_RATE_DAMPING * derived.p,
            -1.0,
            1.0,
        )

        # Pitch: command the ANGLE OF ATTACK that makes the commanded load
        # factor, not the load factor itself.
        #
        # Commanding g directly through a fixed stick gain cannot work,
        # because the g a given stick deflection produces scales with dynamic
        # pressure: the gain that pulls 5 g at corner speed pulls the wings
        # off at 500 kt, and the proportional controller that results simply
        # saturates the stick and holds it there. Going through alpha removes
        # the speed dependence -- the alpha for a given g is a function of
        # weight and dynamic pressure, which are both known -- and it gives
        # the alpha limit for free, as a clamp on the command rather than as
        # an override fighting a saturated loop.
        aero = sim.fdm.aero
        mass = sim.fdm.mass_properties.mass
        qbar = max(derived.qbar, 1.0)
        cl_needed = pull * mass * G0 / (qbar * aero.wing_area)
        alpha_command = (cl_needed - aero.cl0) / max(aero.cl_alpha, 1.0e-6)
        if self._recovery > 0.0:
            # Energy recovery: blend toward the angle of attack that
            # accelerates best rather than the one that holds the g.
            alpha_command += (ACCELERATION_ALPHA - alpha_command) * self._recovery
        alpha_command = clamp(
            alpha_command, -0.5 * aero.alpha_stall, ALPHA_LIMIT * aero.alpha_stall
        )
        alpha_error = alpha_command - derived.alpha
        pilot.pitch = clamp(
            ALPHA_GAIN * alpha_error - PITCH_RATE_DAMPING * derived.q, -0.6, 1.0
        )

        # Keep the ball centred: a slipping fighter is a slow fighter.
        pilot.yaw = clamp(-2.5 * derived.beta, -0.5, 0.5)

        sim.throttle = clamp(throttle, 0.0, 1.0)
        # Burner when the throttle is at the stop, and whenever the aircraft
        # is slower than it wants to be -- which is the only time a fighter
        # pilot thinks about it at all.
        pilot.afterburner = sim.fdm.propulsion.has_afterburner and (
            throttle > 0.92 or derived.vtas < self._recovery_floor
        )

        # Ground avoidance overrides everything. A bandit that flies itself
        # into a hill while winning the fight is a bug, not a tactic.
        #
        # The floor is the height the present rate of descent would use up
        # in the time a pull-out takes, not a fixed number: six hundred
        # metres is ample in a shallow glide and far too late at four
        # hundred metres a second straight down.
        floor = clamp(
            -derived.vertical_speed * GROUND_LOOKAHEAD, GROUND_FLOOR_MIN,
            GROUND_FLOOR_MAX,
        )
        if derived.altitude_agl < floor and derived.vertical_speed < -5.0:
            # Roll upright first. Pulling while inverted or past ninety
            # degrees of bank aims the lift vector at the ground, which is
            # the manoeuvre that kills people who notice the ground late.
            pilot.roll = clamp(-2.2 * derived.roll - 0.5 * derived.p, -1.0, 1.0)

            # Then pull everything the wing has. Not a flight-path hold:
            # that is a trim loop, bounded to a g and a half by design, and
            # a g and a half will not arrest a sixty degree dive at four
            # hundred knots inside two thousand metres. A pull-out is the
            # one manoeuvre where the correct command is the limit, and the
            # alpha clamp below still keeps it on the right side of a stall.
            # No allowance for being banked, and no gentle ramp in. Scaling
            # the pull by how upright the aircraft is sounds prudent and is
            # the opposite: at eighty degrees of bank it asks for about a
            # quarter of a g until the roll finishes, which is the part of
            # the manoeuvre there is no time for. A fighter rolls at a couple
            # of hundred degrees a second, so commanding the pull and the
            # roll together costs a fraction of a second of wasted lift and
            # buys the whole pull-out.
            urgency = clamp(1.0 - derived.altitude_agl / max(floor, 1.0), 0.75, 1.0)
            pull = 1.0 + (self._available - 1.0) * urgency
            cl = pull * mass * G0 / (qbar * aero.wing_area)
            alpha_pullout = clamp(
                (cl - aero.cl0) / max(aero.cl_alpha, 1.0e-6),
                0.0,
                ALPHA_LIMIT * aero.alpha_stall,
            )
            pilot.pitch = clamp(
                ALPHA_GAIN * (alpha_pullout - derived.alpha)
                - PITCH_RATE_DAMPING * derived.q,
                0.0,
                1.0,
            )
            self.state.commanded_g = pull
            sim.throttle = 1.0
            pilot.afterburner = sim.fdm.propulsion.has_afterburner
            self.state.reason = "ground proximity"
