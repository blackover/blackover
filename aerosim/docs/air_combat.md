# Air combat

A second fighter, flying the same flight model, flown by an adversary pilot
that reasons in the terms air combat is actually taught in. This document is
what the thing is and -- more usefully -- what it is not.

## What a dogfight is here

Two `Simulation` objects on one clock. The player's and the bandit's. Both
run the full 6-DoF model through the same atmosphere over the same terrain,
both are limited by the same stall and the same placard, and both are
commanded through the same `PilotInput` -- stick, rudder, throttle,
afterburner. The bandit has no control path into the flight model that the
player does not also have, and that is the only arrangement under which
winning means anything. An opponent on rails can do things the aeroplane
cannot, and beating it teaches you nothing about flying.

The bandit's simulation shares the player's `Terrain` and `Weather` objects
by reference rather than by construction. Two aircraft over independently
generated hills, or in independently generated gusts, are not in the same
fight.

## The geometry

Basic fighter manoeuvres are not reasoned about in positions and velocities.
They are reasoned about in a handful of derived quantities, and `combat/
geometry.py` computes them as pure functions of two states:

| Quantity | What it is | Why a pilot cares |
|---|---|---|
| **Range** | distance apart | decides which manoeuvres exist at all |
| **Closure** | range rate, negative closing | decides whether an attack ends in a shot or an overshoot |
| **Angle off tail** | angle at the defender between its tail and the line to you | *the* scoreboard: zero is dead astern |
| **Aspect** | angle at the defender between its **nose** and the line back to you | zero means it is looking at you |
| **Antenna train angle** | how far off your own nose it is | zero means you are pointing at it |
| **Line-of-sight rate** | how fast the line between you rotates | a high rate up close is an overshoot before it happens |
| **Specific energy** | `h + V²/2g`, as a height | the currency: who can afford what |

Angle off tail and antenna train angle are *different numbers*, and the
difference between them is the whole of pursuit-curve BFM. Aspect is the
supplement of angle off tail, measured from the nose rather than the tail --
worth stating because getting that backwards gives a `defensive` test that
no geometry can satisfy, so the adversary never notices it is being attacked
and never breaks. That bug was in this code and a unit test found it.

## The manoeuvres

Each is here because it answers a specific geometry.

**Pure pursuit** -- nose on the bandit. Closes fast. Flown briefly, to
shoot, because held it drives you into an overshoot.

**Lead pursuit** -- nose *ahead* of the bandit, into its turn. Gains angles
and closes hardest. The attacking curve. Implemented as an aim point at the
bandit's position advanced along its own velocity by the time of flight.

**Lag pursuit** -- nose *behind* it, outside its turn. Gives up angles to
hold range and energy; the single most useful thing a novice does not do.
The same aim-point construction with a negative lead time, which is
genuinely how the three curves relate to each other.

**High yo-yo** -- closing too fast from behind: out of the plane of the turn
and up, trade speed for position, let the bandit pull ahead, come back down.
Converts an overshoot into an attack.

**Low yo-yo** -- behind but slow: nose below the bandit's plane, trade height
for speed, cut the corner. The reverse trade.

**Break turn** -- defensive, maximum-performance turn into the attacker.
Spends every joule to spoil a tracking solution. Right when you are about to
be shot and wrong at every other time, because afterwards you have nothing.

**Scissors** -- both aircraft overshooting each other repeatedly, each trying
to end up behind. A slow fight, won by whoever can fly slower. Gated on
speed as well as range, because the same range-and-closure picture also
describes a 500-knot line-abreast merge, where flying a scissors throws away
everything for nothing.

**Extension** -- away in a straight line to build energy, then back.
Deliberately giving up position to buy the energy to take it back. Flown as
a heading change onto the reciprocal bearing, not as a lift vector pointed
away: the lift-vector version swings through its whole range when the bandit
passes near straight-up or straight-down, and the aileron slams stop to stop.

## How the pilot flies them

Every manoeuvre comes down to two numbers: where to point the lift vector
(a bank angle) and how hard to pull (a load factor). That is the whole of
BFM control, and it is why the same two numbers describe all nine.

Four things sit between those two numbers and the stick, and each exists
because its absence produced a specific, measured failure:

**The pull is converted to an angle of attack, not to stick.** The g a given
stick deflection produces scales with dynamic pressure, so a fixed gain that
pulls 5 g at corner speed saturates the stick at 500 knots. Going through
alpha removes the speed dependence and gives the alpha limit for free, as a
clamp on the command rather than an override fighting a saturated loop.

**The roll loop has an integrator.** A hard turn needs sustained aileron to
hold against the rolling moments it generates, and a proportional law can
only produce that by sitting on a standing error -- about fifteen degrees,
measured. Fifteen degrees matters more than it sounds: 6 g at the 80° of
bank the pilot asked for holds the nose level, and 6 g at the 67° it
actually gets leaves over 2 g curving the flight path upward. The aircraft
zooms, decelerates, and arrives slow and high. Closing the bank error closed
the whole energy problem.

**The flight path is limited at both ends.** A load factor and a bank angle
commanded independently do not describe a turn; they describe whatever the
leftover lift does to the flight path. So the climb is limited by a ceiling
that scales with energy -- at the recovery floor the aircraft may not climb
at all, and by nearly twice it may go wherever it likes -- and the dive is
eased off from 22° down, by rolling the lift vector back toward the horizon
rather than by pulling, because past 90° of bank every extra g makes a dive
*steeper*.

**Energy is defended.** Below a floor set at 1.3 times the 1-g stall speed,
the manoeuvre is abandoned: the throttle goes forward, the pull eases toward
1 g, and in a deep deficit the aircraft unloads to a low angle of attack and
goes downhill, which is the only thing that buys speed back. The floor is
deliberately *not* tied to corner speed -- corner at a 9 g placard is three
times the stall, near 480 kt at medium altitude, and a fighter that refuses
to manoeuvre below its 9 g corner refuses to manoeuvre at all.

Near the ground the turn flattens out, because a turning fight descends by
construction and lift that is turning the aircraft is lift not holding it
up. Below that, a pull-out commands everything the wing has.

## Four bugs, and what each looked like from outside

Every one of these presented as bad *tactics*. That is the thing worth
taking away: when a control law is wrong, the aircraft still flies, the
pilot still picks manoeuvres, and the whole thing looks like an AI that
needs tuning. It does not. Tuning an AI on top of a broken inner loop is
how you spend a day making it worse.

**The bank filter accumulated without wrapping.** The first-order lag that
stands in for reaction time added a wrapped *error* to an unwrapped
*accumulator*, so the accumulator drifted — past 300 degrees, measured.
The next line clamped it to ±85°, and from then on the clamp held the
aircraft in a hard left turn for the rest of the engagement whatever the
pilot asked for.
*Symptom:* every fight "separated". Two aircraft that merged, turned once
and then flew away from each other in lazy circles to 12 km.

**`geometry.defensive` tested the aspect angle backwards.** Aspect here is
measured off the target's *nose*, so an aircraft pointing at you has an
aspect near zero. The test asked for aspect greater than 120°, which no
geometry satisfies.
*Symptom:* the adversary never broke. It would sit in front of an attacker
flying lead pursuit, calmly continuing its own attack on something else.
A unit test built from a hand-placed pair of aircraft found this one before
any amount of watching it fly would have.

**Lag pursuit triggered on line-of-sight rate alone.** Lag is the cure for
an impending overshoot, and an overshoot requires closure. A high
line-of-sight rate at constant range just means the bandit is turning hard,
and the answer to that is more turn, not less.
*Symptom:* an attacker 900 m astern would steadily give up the position,
ending 6 km away with its nose pointing in the opposite direction — while
reporting, correctly, that it was flying lag pursuit.

**The flight-path hold limited its rate instead of its authority.** It
clamped the commanded path rate to ±0.2 rad/s and then multiplied by
`V/g`, which at fighter speeds is about 25 seconds. The clamp was therefore
a limit of five g on a loop whose job was to trim.
*Symptom:* load factors swinging between −4 and +7 during an extension,
and a "level turn" that climbed 14,000 ft.

Two more were design errors rather than defects, and both had the same
shape — a quantity that is not constant being treated as one:

**Stick is not load factor.** The g a stick deflection produces scales with
dynamic pressure, so the gain that pulls 5 g at corner speed saturates at
500 kt. The pitch loop now solves for the angle of attack that makes the
commanded g and drives *that*, which removes the speed dependence and makes
the stall limit a clamp on the command rather than an override fighting a
saturated loop.

**Proportional roll control cannot hold a turn.** A hard turn needs
sustained aileron against the rolling moments it generates, and a
proportional law can only produce that by sitting on a standing error —
about 15°. Six g at the 80° of bank the pilot asked for holds the nose
level; six g at the 67° it actually got leaves over 2 g curving the flight
path upward.
*Symptom:* an aircraft that zoomed out of every hard turn, decelerated, and
arrived slow and high. This read as an energy-management problem and was
cured by an integrator.

## Skill

Four levels. Skill is **not** a performance cheat: every level flies the same
airframe within the same limits. What changes is the pilot --
how often it decides, how fast it reacts, how precisely it holds a pursuit
curve, and how often it picks the energy-correct manoeuvre rather than simply
pointing at the other aeroplane. That last one is the instinct the whole
discipline exists to overcome, and it is what makes a novice lose an energy
fight without understanding why.

## Scoring

There is **no weapon**. What is scored is *tracking time*: how long each
aircraft holds the other inside a cone off its nose, within a range band,
with the line-of-sight rate low enough to hold the pipper on. That is how
air-combat training is actually assessed -- the exercise is to achieve and
hold a firing position and the debrief measures how long you held it -- and
it needs no weapon model to mean something.

The envelope is 9° off the nose, 90 m to 850 m, and 0.30 rad/s: generous in
angle and tight in range, because that is the shape of a gun solution. Two
seconds unbroken counts as decisive. `known_limitations.md` says plainly
what that volume is and is not.

## What this is not

- **Not a gun.** Nothing is fired, nothing flies out, nothing is hit. The
  tracking envelope is a geometric scoring volume. There is no ballistics,
  no dispersion, no time of flight except in the lead pipper's own
  arithmetic, and no damage model of any kind.
- **Not a missile.** No guidance, no seeker, no countermeasures, no radar
  model. Range and aspect are computed geometrically and perfectly: the
  bandit always knows exactly where you are, and so do you.
- **Not an expert pilot.** The adversary flies real manoeuvres chosen on
  real criteria, and it will out-fly a careless player. Flown against a copy
  of itself it does get gun solutions — 8.2° of pointing error inside a 9°
  cone on a head-on pass between two veterans, with tracking time on the
  board — but they last a half second against the two that count as
  decisive. From an offensive perch it tends to point rather than close, and
  a neutral merge between two veterans reliably ends in the separation
  floor. It is a credible opponent, not a demonstration of how BFM should be
  flown. `known_limitations.md` §15 has the measurements.
- **Not multiplayer.** One player, one adversary, one process.
- **Not a formation or a furball.** Exactly two aircraft. Nothing in the
  geometry or the tactics generalises to three without being rewritten.
