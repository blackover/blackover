"""Two aircraft, one clock, and a way of saying who is winning.

The engagement owns a second ``Simulation`` for the bandit and steps it in
lockstep with the player's. Both run the full flight model; neither gets a
concession. The bandit's controls come from ``Adversary``, the player's from
the keyboard, and from the flight model's point of view there is no
difference between them.

Scoring
-------
There is no weapon here. What is scored is **tracking time**: how long each
aircraft holds the other inside a cone off its nose, within a range band,
with the closure under control. That is how air-combat training is actually
assessed -- the exercise is to achieve and hold a firing position, and the
debrief measures how long you held it -- and it needs no weapon model to be
meaningful. ``docs/known_limitations.md`` says what the envelope is and is
not.

The envelope is deliberately generous in angle and tight in range, because
that is the shape of a gun solution: you must be close, and you must be
behind, and both at once for long enough to matter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.orchestrator import Simulation
from ..core.units import deg, ft, kt
from ..game.config import SimConditions, StartMode
from .adversary import Adversary, AdversaryState
from .geometry import Geometry, observe

# The tracking envelope. A gun solution needs the target close, ahead, and
# not crossing the nose too fast to hold.
TRACK_CONE = deg(9.0)  # half-angle off own nose
TRACK_MIN_RANGE = 90.0  # m; closer than this is a collision risk, not a shot
TRACK_MAX_RANGE = 850.0  # m; beyond this a gun is a hope
TRACK_MAX_LOS_RATE = 0.30  # rad/s; faster and the pipper will not stay on

#: Continuous seconds inside the envelope that count as a decisive position.
DECISIVE_TRACK = 2.0

#: The perch: how far astern and how far above, metres. A training perch,
#: which is a gun-range position with height in hand, not a radar contact.
PERCH_RANGE = 900.0
PERCH_HEIGHT = 250.0

#: How far apart a neutral merge starts, metres -- about a nautical mile and
#: a quarter, which is a merge to fly rather than an immediate near miss.
#: At twelve hundred metres two fighters turning into each other arrive
#: inside the separation floor in under seven seconds and the fight is over
#: before it starts.
MERGE_SEPARATION = 2300.0

#: Closest two aircraft may pass before the fight is knocked off, metres.
#: Training rules put this at a few hundred feet; so does this.
MINIMUM_SEPARATION = 150.0


class Setup:
    """Starting geometries, named the way a training syllabus names them."""

    NEUTRAL = "neutral"
    OFFENSIVE = "offensive"
    DEFENSIVE = "defensive"
    HEAD_ON = "head_on"


SETUP_LABELS = {
    Setup.NEUTRAL: "Neutral merge  (line abreast, equal energy)",
    Setup.OFFENSIVE: "Offensive perch  (you start behind)",
    Setup.DEFENSIVE: "Defensive perch  (the bandit starts behind)",
    Setup.HEAD_ON: "Head-on pass  (closing nose to nose)",
}


@dataclass
class Score:
    """Tracking time, for one side."""

    #: Total seconds inside the envelope.
    total: float = 0.0
    #: Seconds in the current unbroken stretch.
    current: float = 0.0
    #: The longest unbroken stretch so far.
    best: float = 0.0
    #: How many separate times a position was achieved and held.
    solutions: int = 0
    #: True while inside the envelope right now.
    tracking: bool = False

    def update(self, inside: bool, dt: float) -> None:
        if inside:
            if not self.tracking:
                self.solutions += 1
            self.tracking = True
            self.current += dt
            self.total += dt
            self.best = max(self.best, self.current)
        else:
            self.tracking = False
            self.current = 0.0

    @property
    def decisive(self) -> bool:
        return self.best >= DECISIVE_TRACK


@dataclass
class EngagementState:
    """Everything the HUD and the debrief need."""

    #: The geometry from the player's point of view.
    geometry: Geometry | None = None
    #: And from the bandit's.
    bandit_geometry: Geometry | None = None
    player: Score = field(default_factory=Score)
    bandit: Score = field(default_factory=Score)
    adversary: AdversaryState | None = None
    #: Set when the fight is over, with the reason.
    finished: str = ""
    elapsed: float = 0.0


def in_envelope(geometry: Geometry) -> bool:
    """Is the other aircraft inside the tracking envelope right now?"""
    return (
        geometry.antenna_train <= TRACK_CONE
        and TRACK_MIN_RANGE <= geometry.range <= TRACK_MAX_RANGE
        and geometry.line_of_sight_rate <= TRACK_MAX_LOS_RATE
    )


class Engagement:
    """One fight: the player's simulation, the bandit's, and the score.

    The player's ``Simulation`` is passed in rather than owned, because the
    game already built it and there must be exactly one of it. The bandit's
    is built here from the same conditions, so both aircraft fly the same
    weather over the same terrain -- sharing those is what stops the two
    halves of the fight being two different flights.
    """

    def __init__(
        self,
        player: Simulation,
        conditions: SimConditions,
        *,
        bandit_model=None,
        setup: str = Setup.NEUTRAL,
        skill: str = "regular",
    ) -> None:
        self.setup = setup
        self.conditions = conditions
        self.state = EngagementState()

        bandit_conditions = self._bandit_conditions(conditions)
        self.bandit = Simulation(bandit_model or player.model, bandit_conditions)
        # One landscape and one sky for both aircraft, not two rolls of the
        # same dice: a bandit flying over different hills is not in the same
        # fight.
        self.bandit.terrain = player.terrain
        self.bandit.weather = player.weather
        self.bandit.fdm.terrain = player.terrain
        self.bandit.fdm.gear.terrain = player.terrain

        self.adversary = Adversary(
            self.bandit.model, skill=skill, seed=conditions.seed + 31
        )
        self._place(player, setup)

    # -- construction ------------------------------------------------------

    @staticmethod
    def _bandit_conditions(conditions: SimConditions) -> SimConditions:
        """The bandit's own pre-flight conditions: same world, own seed.

        A separate seed for the turbulence, because two aircraft a kilometre
        apart are not in the same gust, and the same everything else.
        """
        import dataclasses

        return dataclasses.replace(
            conditions,
            start_mode=StartMode.AIRBORNE,
            seed=conditions.seed + 101,
            failure="none",
            warnings=[],
        )

    def _place(self, player: Simulation, setup: str) -> None:
        """Put the two aircraft in the named starting geometry."""
        altitude = max(player.fdm.state.altitude, ft(5000))
        speed = max(player.fdm.state.derived.vtas, kt(300))
        heading = player.fdm.state.derived.yaw

        if setup == Setup.OFFENSIVE:
            # The classic offensive perch: the player behind the bandit and
            # ABOVE it. Above matters as much as behind -- a perch is a
            # position with energy in hand, and height is how that energy is
            # carried. Putting the attacker underneath instead leaves it
            # pointing beautifully at something it can never catch, because a
            # tail chase between identical aircraft at identical speed does
            # not close: measured, it tracked the bandit to within a tenth of
            # a degree and sat eleven hundred metres behind it for two and a
            # half minutes.
            #
            # And the range is a training perch's range, not a radar
            # contact's: nine hundred metres, which is the edge of the gun
            # envelope. The exercise is to convert a position, so the
            # position has to be one worth converting.
            offset, bandit_heading = PERCH_RANGE, heading
            bandit_altitude = altitude - PERCH_HEIGHT
            along, across = 1.0, 0.0
        elif setup == Setup.DEFENSIVE:
            # The same geometry the other way round: the bandit has the
            # perch, with the height to press it.
            offset, bandit_heading = -PERCH_RANGE, heading
            bandit_altitude = altitude + PERCH_HEIGHT
            along, across = 1.0, 0.0
        elif setup == Setup.HEAD_ON:
            offset, bandit_heading, bandit_altitude = 6000.0, heading + math.pi, altitude
            along, across = 1.0, 0.0
        else:  # NEUTRAL: line abreast, a kilometre apart, same energy
            offset, bandit_heading, bandit_altitude = 0.0, heading, altitude
            along, across = 0.0, MERGE_SEPARATION

        forward = np.array([math.cos(heading), math.sin(heading), 0.0])
        right = np.array([-math.sin(heading), math.cos(heading), 0.0])
        origin = player.fdm.state.position.copy()
        position = origin + forward * (offset * along) + right * across
        position[2] = -bandit_altitude

        self.bandit.place_airborne(
            altitude=bandit_altitude,
            vtas=speed,
            heading=bandit_heading,
            north=float(position[0]),
            east=float(position[1]),
        )
        self.bandit.throttle = 0.85
        self.bandit.log(
            "INFO",
            f"bandit airborne, {SETUP_LABELS[setup].split('(')[0].strip().lower()}",
        )

    # -- stepping ----------------------------------------------------------

    def step(self, player: Simulation, dt: float) -> EngagementState:
        """Advance the bandit one step and update the score."""
        state = self.state
        if state.finished:
            return state

        player_state = player.fdm.state
        state.adversary = self.adversary.update(self.bandit, player_state, dt)
        self.bandit.step()

        bandit_state = self.bandit.fdm.state
        state.geometry = observe(player_state, bandit_state)
        state.bandit_geometry = observe(bandit_state, player_state)
        state.elapsed += dt

        state.player.update(in_envelope(state.geometry), dt)
        state.bandit.update(in_envelope(state.bandit_geometry), dt)

        self._check_finished(player)
        return state

    def _check_finished(self, player: Simulation) -> None:
        state = self.state
        if self.bandit.crashed:
            state.finished = "bandit hit the ground"
        elif player.crashed:
            state.finished = "you hit the ground"
        elif state.geometry is not None and state.geometry.range < MINIMUM_SEPARATION:
            # Knock it off. Real air-combat training has a hard floor on how
            # close two aircraft may pass, and when it is broken the fight
            # stops -- not as a penalty but because at these speeds the next
            # thing that happens is decided by luck. Two fighters closing
            # inside a hundred and twenty metres at four hundred knots is a
            # near miss, and a simulator that quietly scores it as a good
            # attack is teaching the wrong lesson.
            state.finished = (
                f"knock it off -- passed within {state.geometry.range:.0f} m"
            )
        elif state.geometry is not None and state.geometry.range > 25000.0:
            state.finished = "the fight separated"

    # -- debrief -----------------------------------------------------------

    def debrief(self) -> list[str]:
        """What happened, in the terms the fight was flown in."""
        state = self.state
        lines = [
            f"Engagement: {SETUP_LABELS[self.setup]}",
            f"Duration:   {state.elapsed:.0f} s",
            "",
            f"{'':<12}{'tracking':>10}{'longest':>10}{'solutions':>11}",
            f"{'you':<12}{state.player.total:9.1f}s{state.player.best:9.1f}s"
            f"{state.player.solutions:11d}",
            f"{'bandit':<12}{state.bandit.total:9.1f}s{state.bandit.best:9.1f}s"
            f"{state.bandit.solutions:11d}",
            "",
        ]
        if state.finished:
            lines.append(f"Ended: {state.finished}")

        player, bandit = state.player, state.bandit
        if player.decisive and not bandit.decisive:
            lines.append("Result: you held a firing position and never gave one up.")
        elif bandit.decisive and not player.decisive:
            lines.append("Result: the bandit held a position on you. Review the break.")
        elif player.best > bandit.best:
            lines.append("Result: you had the better of it, but it stayed a fight.")
        elif bandit.best > player.best:
            lines.append("Result: the bandit had the better of it.")
        else:
            lines.append("Result: neutral. Nobody got behind anybody.")
        return lines
