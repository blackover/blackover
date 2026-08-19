# AeroSim Lab

A flight simulator you fly like a game, built on a flight model you can argue
with. Set every condition before departure, choose a **passenger jet** or a
**war plane**, and fly it in a chase view with a full instrument panel.

Aircraft behaviour comes from version-controlled YAML data packages rather than
from code. The simulation clock is deterministic and fixed-step. The renderer
reads the state and feeds nothing back — the kernel is the sole authority on
where the aircraft is.

---

## Running it

```bash
python play.py
```

That is the whole thing. If pygame, NumPy or PyYAML are missing it offers to
install them first, so a fresh checkout needs no setup step. On Windows you can
double-click `run-flightsim.bat`; on Linux or macOS, `./run-flightsim.sh`.

```bash
python play.py --record             # fly, recording telemetry from the start
python play.py --replay runs/<dir>  # watch a recorded run back
```

Requires Python 3.10+. Developed against Python 3.11, pygame 2.6.1,
NumPy 2.4.6. `python -m aerosim` works too if you would rather install the
dependencies yourself from `requirements-aerosim.txt`.

**In a hurry?** Start the flight, press `4`, then `5`. The aircraft takes off,
climbs, routes itself to the runway and lands, and you can watch.

```bash
python -m pytest tests/test_aerosim_*.py -q     # the test suite
```

---

## The pre-flight screen

Nothing starts until you say so. Every condition is set first, and the briefing
panel on the right recomputes live from the aircraft's own data package — so
the stall speed shown beside a 90 % fuel load is the stall speed that load
really produces, not a placard figure.

| Group | What you set |
|---|---|
| **Aircraft** | AeroLiner-200 (passenger) or AeroFalcon-X (war) |
| **Initial condition** | runway / airborne / approach, altitude, airspeed, heading, field elevation |
| **Terrain** | flat, gentle, rolling, hilly or mountainous |
| **Loading** | fuel and payload as a fraction of capacity |
| **Weather** | wind speed and direction, shear, turbulence, ISA offset, QNH, time of day, visibility |
| **Cloud** | cover, base and tops — an overcast is flown *into*, not past |
| **Precipitation** | drizzle, rain, heavy rain, snow, heavy snow |
| **Runway** | dry, damp, wet, standing water, compacted snow, ice |
| **Failures** | engine, hydraulic, elevator jam or fuel leak, and when it fires |
| **Simulation** | flight assist, step size, random seed |

Arrow keys move and change, `SHIFT`+arrow takes coarse steps, `ENTER` starts
the flight.

Pre-flight checks are **warnings, not blocks**. Ask for a start above VMO and
it will tell you, then fly it anyway — refusing would hide the very behaviour
someone setting up an unusual condition is trying to see.

Same settings plus the same seed produce the same flight, gust for gust.

---

## Flying

Chase view above, full instrument panel below.

| Key | |
|---|---|
| Arrow keys | pitch and roll — **DOWN pulls the nose up**, as a stick does |
| `Q` `E` | rudder left and right |
| `T` `Y` | pitch trim nose up and nose down |
| `A` `Z` | throttle up and down |
| `TAB` | afterburner (war plane only, above 85 % throttle) |
| `G` | landing gear |
| `F` `V` | flaps extend and retract |
| `B` | wheel brakes |
| `I` | anti-ice on and off — sheds airframe ice |
| `SPACE` | speedbrake |
| `1` `2` `3` | autopilot: altitude hold, heading hold, speed hold |
| `4` | **AUTO FLY** — takes off, climbs and cruises by itself |
| `5` | **AUTOLAND** — routes to the runway, lands and stops |
| `0` | autopilot and autoflight off |
| `F5` | start / stop telemetry recording |
| `[` `]` | chase camera closer and further |
| `C` | **flight traces** — speed, altitude, g, alpha, V/S, N1 over time |
| `,` `.` | shorter and longer trace window |
| `P` `H` `R` `ESC` | pause, help, restart, back to setup |

**Flight assist** (on by default) is a rate damper, not a controller. It
resists the aircraft's own oscillations and leaves your command alone. It will
happily let you stall.

The renderer is pure software — no GPU, no shaders — and holds a 60 Hz frame
budget: roughly 12–20 ms to draw and 2–3 ms for the 100 Hz flight model. The
physics rate is independent of the frame rate, so a slower machine renders
fewer frames of the *same* flight rather than a different one.

The panel shows attitude with a flight path marker, airspeed and altitude
tapes with stall and VMO bands, vertical speed, engine N1 and EGT, fuel, mass,
CG, and both commanded *and* actual control positions — so a jammed or
rate-limited surface reads as a divergence between the two rather than only as
odd handling.

### Weather that does something

Wind and turbulence were always there. What is new is the weather that acts
on the airframe rather than on the air:

- **Cloud is a place, not a texture.** Set the cover to 6/8 or more and it
  becomes a solid deck with a base and tops. Fly into it and the visibility
  collapses to forty metres, the sky and the ground disappear, and the panel
  is all you have. Below it the sky goes grey; above it, blue again.
- **Icing is gated on the *recovery* temperature.** Ice needs visible
  moisture and a leading edge between 0 and −20 °C — and the leading edge is
  not at the outside air temperature, it is at the stagnation temperature.
  A transport at 200 kt in a −8 °C cloud ices; the fighter through the same
  cloud at Mach 0.85 does not, because kinetic heating has put its leading
  edge 22 °C above the air around it. That falls out of the physics rather
  than being a special case, and it is why the ram rise appears in the
  report at all. Ice costs 30 % of CLmax, 35 % of the stall angle, and adds
  drag and weight forward of the CG. `I` sheds it.
- **A contaminated runway is a different runway.** Braking, cornering and
  rolling resistance all scale with the surface. Measured, from 130 kt:

  | Surface | Stopping distance | vs dry |
  |---|---|---|
  | dry | 692 m | 1.00× |
  | wet | 1 025 m | 1.48× |
  | standing water | 1 306 m | 1.89× |
  | compacted snow | 1 446 m | 2.09× |
  | ice | 2 867 m | 4.15× |

  Those ratios are the simulator's own output, and they land where published
  contaminated-runway factors do.

### The view

- **Terrain you can hit.** The renderer and the flight model read the same
  procedural heightfield, so the ridge on the horizon is the ridge the gear
  will find. Hills you can see but cannot hit are worse than no hills at all,
  because the picture then contradicts the simulation it exists to show.
  Three levels of detail — fine cells near the aircraft, each ring beyond it
  four times coarser — reach 25 km from the runway and 90 km from the cruise,
  capped by the visibility so nothing is drawn out where the haze has already
  turned the ground into sky.
- **Sky anchored to the horizon.** The gradient is positioned on the projected
  horizon rather than on the screen, so the fully hazed far ground and the sky
  meet in the same colour instead of along a hard line.
- **Effects that mean something.** The fighter's plume is its spool speed and
  its reheat state; a contrail says the air outside is below about −40 °C; the
  streamers off the wingtips say the wing is loaded past about 3 g in moist
  air. All three are qualitative — see `docs/known_limitations.md` §12.
- **Strip charts.** `C` opens six time histories over the last 20–240 seconds,
  with the placards drawn as bands and the *terrain profile under the altitude
  trace*, so the gap on the chart is the clearance the flight actually had.
  Two of them stay on the panel while you fly. They sample simulation time,
  not wall time, so pausing pauses them and a replay draws what the run did.

An instrument tells you what a quantity is now; a trace tells you what it has
been doing. A speed five knots above the stall is fine if it is steady and an
emergency if it has been falling for ten seconds, and the two look identical
on a tape.

---

## Autoflight — the aircraft flies itself

Press `4` and it takes off, climbs and cruises. Press `5` and it routes to the
runway, flies the approach, flares, touches down and stops. Move the stick and
it hands the aircraft straight back.

```
TAKEOFF -> CLIMB -> CRUISE -> TO_FAF -> APPROACH -> FLARE -> ROLLOUT -> DONE
                                            └── GO_AROUND ──┘
```

Every phase works by moving the **targets** of the same cascade you get with
keys 1/2/3. There is one set of control laws in this simulator, tuned and
tested once. The alternative — a controller per phase — is what produced a
7 m/s touchdown during development: a separate flare law meant a control
handover at the exact moment the aircraft could least afford one.

Guidance is derived from one runway: a threshold, a heading and an elevation.
Everything else falls out — along-track distance, cross-track error, and the
height the glidepath wants at this range.

Flown from a standing start to a full stop, untouched:

```
AeroLiner-200   T/O -> CLB 32s -> CRZ 246s -> NAV -> APP 861s -> FLARE 1053s -> stopped 1100s
                touchdown 0.93 m/s (183 fpm), 0.0 m off the centreline
AeroFalcon-X    T/O -> CLB 22s -> CRZ 126s -> NAV -> APP 666s -> FLARE 923s -> stopped 956s
                touchdown 0.88 m/s (173 fpm), 0.0 m off the centreline
```

It will also recover a bad situation. Dropped over the threshold at 10 000 ft
pointing 90° across the runway, it flies away, lines up and lands on the
centreline. Displaced 300 m sideways at 60 ft, it goes around rather than
banking onto the runway from the side — the bank limit closes down to 4° near
the ground, because the alternative is a wingtip in the tarmac.

The flare profile, configuration schedule and capture criteria are per-aircraft
data in `autopilot.yaml`, not code. What this is *not* is in
[`docs/known_limitations.md`](docs/known_limitations.md) §9: there is no ILS,
no navigation database, no decrab, and it is emphatically not a Cat III model.

## Telemetry and replay

`F5` in flight starts recording. Each run writes a timestamped directory:

```
runs/2026-08-04-070312/
├── telemetry.csv    one row per sample, header names carrying their units
└── manifest.yaml    what produced it, and a SHA-256 over the CSV
```

The manifest is the point. Telemetry on its own is a pile of numbers that
cannot be attributed to anything; telemetry beside a manifest naming the model
version, its package checksum, the seed and every pre-flight condition can be
reproduced, and the hash says whether it is still the file that was written.

```bash
python -m aerosim --replay runs/2026-08-04-070312
```

The replay animates the run with the same chase view and the same panel, and
prints a warning if the telemetry no longer matches its hash. **It has no
kernel.** It reads telemetry and nothing else — there is deliberately no code
path from a replay into the flight model, and a test asserts that neither the
session nor its `fdm` has a `step` method. That is what makes a replay usable
as evidence: what you are watching is what was recorded, not a re-simulation
that might have diverged from it.

Columns are per-engine rather than averaged. A single mean N1 would show two
healthy engines at reduced power during an engine-out instead of one failed and
one at maximum, which is the entire point of that scenario.

Sample rate is independent of step size, so a run flown at dt = 0.005 and one
at dt = 0.01 produce comparable files.

| Key in replay | |
|---|---|
| `SPACE` | pause |
| arrows | scrub one second |
| `+` `-` | playback speed, 0.125x to 8x |
| `HOME` `END` | jump to start or end |

## The two aircraft

**AeroLiner-200** — generic twin-turbofan narrow-body, 79 t MTOM. Heavy,
stable, slow in roll, autopilot-flown. Fictional, assembled from open
literature and first-principles estimates.

**AeroFalcon-X** — generic single-engine multirole fighter, 19.2 t MTOM.
Afterburner, 9 g envelope, rolls about eight times faster, and near enough
neutrally stable in pitch to feel immediate.

Neither is any real aircraft type. No manufacturer data, nothing proprietary,
nothing export-controlled.

The fighter package models the **airframe only** — mass, aerodynamics,
propulsion, structure and handling. It carries no weapon, sensor, targeting or
mission system. External stores appear solely as the mass and parasite drag of
a loaded pylon, because that is the part which changes how the aircraft flies.

### Cross-validated figures

Computed by the code from three independently authored data files:

```
                      AeroLiner-200        AeroFalcon-X
aspect ratio                  9.44                3.20
CL_max                       1.672               1.358
peak L/D                     16.61               10.23
static margin            22.8 % MAC          11.6 % MAC
Vs predicted / quoted   138.4 / 138.0 kt   138.4 / 138.0 kt
                         0.3 % agreement    0.3 % agreement
```

The stall-speed agreement means something because `stall_speed_clean` lives in
`limitations.yaml`, CL_max is derived from `aerodynamics.yaml`, and wing area
comes from `geometry.yaml`. Three separate files agreeing is evidence; a model
agreeing with itself would not be.

---

## Design principles

**Data, not code.** An aircraft is a directory of YAML files. Adding one
requires no source change. The loader validates against a schema, computes a
SHA-256 over the contents, and refuses to run if anything mandatory is missing
or carries an unrecognised unit. Validation reports *every* problem at once, so
a data author fixes one round of errors rather than one error per run.

**SI internally, aviation units at the boundary.** Feet, knots, degrees and
pounds exist only where data is read, where the panel is drawn, and in test
expectations. One registry defines every conversion; nothing restates a factor
locally.

**Determinism by construction.** Simulation time is `step_index × dt`, never an
accumulated sum, so 120 000 steps land on exactly 1200.0 s. Every stochastic
source draws from its own seeded generator. The render rate follows whatever
the machine manages; the flight model always steps at exactly `dt`.

**Explicit out-of-range behaviour.** Every lookup table declares `clamp`,
`linear` or `error`. There is deliberately no default that extrapolates
silently — a stall curve extended to 60° produces plausible-looking numbers
that mean nothing.

**The model says when it is out of its depth.** Each package declares the Mach
and sideslip beyond which its aerodynamics stop being representative. Past
those, an ENVELOPE caution lights and the event log says why. Push the fighter
past M 0.85 and it will tell you that what you are watching is no longer
defensible.

**Failures propagate physically.** Fuel feeds engines, running engines drive
generators, generators drive hydraulic pressure, pressure sets actuator rate,
and actuator rate changes how the aircraft handles. No special-case logic
connects those steps.

---

## Architecture

```
aerosim/
├── core/
│   ├── units.py            SI registry, boundary conversion, angle wrapping
│   ├── frames.py           quaternion NED<->body, wind angles, DCM
│   ├── state.py            13-element state vector, derived quantities
│   ├── clock.py            fixed-step clock, render-rate decoupling
│   ├── model_package.py    package loading, schema validation, checksum
│   └── orchestrator.py     subsystem lifecycle and execution order
├── analysis/
│   ├── design.py           design report card, graded against target bands
│   └── modes.py            linearised short period, phugoid, dutch roll, ...
├── env/
│   ├── atmosphere.py       ISA 1976 to 47 km, offsets, CAS/EAS conversions
│   ├── terrain.py          procedural heightfield -- drawn AND flown
│   ├── weather.py          cloud, precipitation, icing, surface condition
│   └── wind.py             layered wind, seeded turbulence
├── fdm/
│   ├── tables.py           1-D and 2-D interpolation with range policy
│   ├── aero.py             stability-derivative build-up, stall, ground effect
│   ├── propulsion.py       turbofan, spool dynamics, afterburner, fuel flow
│   ├── mass.py             mass, CG and inertia as functions of loading
│   ├── gear.py             ground reaction, braking, tyre friction
│   ├── rigid_body.py       6DoF equations of motion, RK4
│   ├── trim.py             steady-flight trim solver
│   └── fdm.py              force assembly and integration
├── control/
│   ├── actuators.py        rate/position limits, jam, runaway, reduced authority
│   ├── autopilot.py        mode state machine, cascaded control laws
│   └── autoflight.py       phase machine: take-off, climb, autoland, go-around
├── telemetry/
│   ├── recorder.py         buffered CSV, run manifest, integrity hash
│   └── replay.py           reads telemetry and feeds nothing back
├── game/
│   ├── config.py           the pre-flight conditions object
│   ├── setup_screen.py     condition entry and live briefing
│   ├── mesh.py             aircraft geometry, built from the data package
│   ├── sky.py              sun, gradient, haze, cloud field
│   ├── renderer.py         chase camera, clipping, terrain LOD, runway
│   ├── effects.py          exhaust plume, contrails, wingtip vortices
│   ├── instruments.py      PFD, tapes, engine and systems panel
│   ├── charts.py           strip charts: the flight as a time history
│   └── app.py              the game loop
└── data/aircraft/
    ├── aeroliner_200/      passenger
    └── aerofalcon_x/       war
```

Roughly 9 900 lines of source, 2 800 lines of tests, 770 lines of aircraft data.

### Execution order

Fixed, documented, and exercised by the test suite:

```
1. failure triggers
2. pilot or autopilot produce commands
3. aircraft systems: fuel -> electrical -> hydraulic
4. actuators apply rate, position and failure limits
5. flight dynamics integrate one step (RK4), mass updated en route
6. health checks: numerical, envelope, terrain
7. event log
```

Only step 5 runs at RK4 accuracy. Control surface positions, thrust and mass
are held constant across the four stages because they contain rate limiters
and discrete logic that are not differentiable. Aerodynamic and gear forces
*are* re-evaluated per stage — they are smooth functions of the state, and the
gear spring is the stiffest thing in the model. The gust vector is frozen for
the whole step so determinism does not depend on how many times the derivative
function happens to be called.

---

## Aircraft data packages

Eight mandatory files plus two optional:

| File | Contents |
|---|---|
| `manifest.yaml` | identity, category, provenance, per-section confidence |
| `geometry.yaml` | reference area, span, chord, aerodynamic reference point |
| `mass_properties.yaml` | empty mass, inertia tensor, CG limits, tanks |
| `aerodynamics.yaml` | stability and control derivatives, stall, flaps |
| `propulsion.yaml` | engine count, positions, thrust map, augmentation |
| `flight_controls.yaml` | surface travel, rate limits, time constants |
| `landing_gear.yaml` | strut positions, stiffness, friction |
| `limitations.yaml` | VMO, MMO, load factors, stall speed |
| `autopilot.yaml` | *optional* — control-law gains and limits |
| `systems.yaml` | *optional* — tanks, buses, hydraulics |

Values carry their units as strings: `"340 kt"`, `"25 deg"`, `"-1.30 1/rad"`.
A bare number is assumed already SI. Unknown units are rejected at load time.

`manifest.yaml` records, per section, the **source**, a **confidence rating**
and the **assumptions**. A derivative estimated by handbook methods and one
measured in flight test are not the same artefact and are not presented as
though they were.

### Coordinate convention

All positions are true body axes: origin at the aerodynamic reference point,
**x forward, y right, z down**, metres. This is deliberately *not* the
aft-increasing fuselage-station convention used on loading sheets. Mixing the
two reverses every moment arm in the aircraft, and the reversal produces a
model that still flies.

---

## Designing your own aircraft

Every aircraft is a folder of YAML in `data/aircraft/`. Drop a third one in and
it appears on the pre-flight screen, gets a mesh built from its own geometry,
and flies through the same force model as the other two.

To find out whether it is any *good*:

```bash
python -m aerosim --design aeroliner_200
python -m aerosim --design my_aircraft --design-altitude 8000
```

The report needs no display. It reads the package, grades about two dozen
figures against target bands, linearises the **real force model** about trim
to get the five classical modes, and then says in plain language what to
change and what the change will cost:

```
  Natural modes (linearised about trim)
  -------------------------------------
     ok  short period damping           +0.404   target +0.300 - +2.000   period 4.4 s
    LOW  phugoid damping                +0.039   target +0.040 - +1.000   period 87.1 s
    LOW  dutch roll damping             +0.046   target +0.080 - +1.000   period 5.3 s
     ok  roll time constant               0.78 s target < 1.40
     ok  spiral doubling time              999 s target > 20              convergent

  Notes
  -----
   * Dutch-roll damping is 0.05. It will wallow in turbulence. Raise cn_r
     (fin area or tail arm) or reduce cl_beta (less dihedral or less sweep)
     -- too much dihedral effect relative to weathercock stability is the
     usual cause.
```

The modes come from finite-differencing the same force build-up the simulator
flies, not from a separate stability matrix — so the flap increments, the
ground effect and the Prandtl-Glauert factor are in the eigenvalues too, and
what is analysed is what is flown. A test asserts the linearisation against an
independent perturbation of the nonlinear model.

A verdict of `LOW` or `HIGH` is not a failure: it means the design is unusual
in that respect and the reason should be deliberate. The fighter fails the
transport's wing-loading band, and it fails it in the direction that makes it
a fighter.

The full guide — what every number trades against, which coefficient moves
which mode, and the order to decide things in — is
[`docs/aircraft_design.md`](docs/aircraft_design.md).

---

## Verification

```bash
python -m pytest tests/test_aerosim_foundation.py -q   # units, frames, clock, atmosphere
python -m pytest tests/test_aerosim_dynamics.py -q     # tables, aero, mass, engines, trim
python -m pytest tests/test_aerosim_game.py -q         # orchestrator, autopilot, renderer
python -m pytest tests/test_aerosim_telemetry.py -q    # recording, integrity, replay
python -m pytest tests/test_aerosim_autoflight.py -q   # auto take-off, autoland
python -m pytest tests/test_aerosim_analysis.py -q     # linearised modes, design report
```

Tests fall into three deliberately distinct categories:

**External validation** — against independently published reference data. The
atmosphere is checked against U.S. Standard Atmosphere 1976 table values to
0.1 % on pressure and density and 0.05 K on temperature. Those numbers are the
standard's, not this code's output.

**Analytical comparison** — against the closed form the code claims to
implement. The drag polar against CD = CD₀ + CL²/(π·AR·e); the parallel-axis
term against Steiner; free fall against s = ½gt².

**Physical admissibility** — properties that must hold for any real aircraft.
dCm/dα < 0 or it diverges in pitch. Cn_β > 0 or it has no weathercock
stability. The inertia tensor symmetric positive definite with principal
moments satisfying A + B ≥ C, or it describes no rigid body that exists. A
transposed sign here produces an aircraft that flies and is wrong, which is far
harder to diagnose later than a failed assertion now.

### Defects the suite and the build found

Fifteen, all in code that appeared to work:

1. **Trim solved a different model from the one being flown.** The trim solver
   assembled thrust forces itself and omitted the engine-position moment the
   force assembly includes. Residual 1 × 10⁻¹⁴ — and every airborne start
   still began with a ±770 ft phugoid. Fixed by making the solver call the
   propulsion model. Now ±11 ft.
2. **A damper with a backwards sign.** The flight-assist pitch and yaw dampers
   subtracted where they should have added, so they reinforced the rate they
   were meant to oppose. Not a weak damper: positive feedback, and the fighter
   departed in about a second and a half.
3. **An autopilot D term that was never connected.** The code passed a literal
   `0.0` as the derivative while the comment beside it described feeding in the
   measured body rate. P+I only, and the loops oscillated instead of settling.
4. **Trim elevator discarded on reset.** `apply_trim` spooled the engines and
   the following `reset()` put them straight back to idle, so every airborne
   start sank for four seconds while the spools caught up with a lever already
   in the right place.
5. **A failed engine that kept pushing.** Its decaying spool still fed the
   thrust map, leaving about a newton of thrust for the rest of the flight —
   small enough that it would never have been noticed.
6. **Approach speed taken from the cruise setting.** Put a narrow-body over the
   threshold at 280 kt with full flap, far past VFE, where no trim solution
   exists that is not a dive.
7. **A normal landing reported as a structural exceedance.** The manoeuvring
   g limit was applied on the ground, where the accelerometer is reading strut
   loads. A 2.3 m/s touchdown spikes to 4 g against a 2 g flaps placard, so
   every landing logged OVER-G. The flare itself peaks at 1.12 g; gear loads
   are a different case, already covered by the touchdown-rate check.
8. **An autopilot handover that jolted the controls.** Disengaging discarded
   the integrator state and reverted the elevator to the stored trim, stepping
   the surface at the exact moment the pilot took over. Worst mid-manoeuvre,
   where the integrator is furthest from trim — taking over during a flare
   turned a 2.4 m/s touchdown into 7 m/s.
9. **An altitude-hold cascade that flew into the ground.** Three faults
   compounding: the vertical-speed gain demanded 29° of pitch per 10 m/s of
   error, so it pinned the pitch limit; the feed-forward used instantaneous
   alpha, and since alpha = pitch − gamma it chased its own output and
   ratcheted upward; and engaging with a large error stepped the inner target,
   saturating the pitch loop into a bunt. Fixed with a gamma + filtered-alpha
   feed-forward and a rate-limited attitude command. Altitude hold now settles
   within a few feet instead of descending 13 000 ft.
10. **A localizer captured from 2.3 km off the centreline.** Capture tested
    absolute distance to the extended centreline rather than the angle to it,
    so far out on the intercept the geometry looked "close" and the autoland
    armed while still well displaced. It then rolled hard to correct at low
    altitude, and put a wingtip in the ground. Fixed with an angular capture
    criterion, a bank limit that closes to 4° near the ground, and a go-around
    at minimums rather than a save attempt.
11. **An auto take-off that stalled every departure.** Rotation was an
    open-loop stick ramp, which took the aircraft to 20° alpha with a STALL and
    an ENVELOPE caution on the way out. Replaced with attitude-hold rotation —
    and that alone still overshot to 22°, because the law was allowed to pull
    but never to push. Letting it command forward stick once airborne gives a
    13.9° peak at 10.5° alpha.
12. **Terrain rings that left a gap in the ground.** Each level of detail snaps
    its grid to its own cell size, so no two levels are aligned. The hollow cut
    in the coarse ring for the fine one to fill was counted in the coarse
    ring's cells, and therefore landed up to a whole coarse cell off the fine
    ring's real footprint — a band of bare base plane straight across the
    middle distance, in the part of the picture the eye is guaranteed to be
    looking at. Fixed by measuring the hollow in metres against what the finer
    ring is geometrically guaranteed to cover.
13. **A detail level that collapsed to its own floor.** The terrain cell size
    is snapped to a round number so the grid does not shimmer as the aircraft
    climbs, and the snap read `10 ** round(log10(size))`. Rounding the
    *exponent* sends anything above 3.16 × 10^k up a decade, after which
    `size / magnitude` rounds to zero and the whole expression collapses to
    the clamp. The near cell was therefore pinned at 120 m for every altitude
    between about 900 m and 7 km, and the outer ring reached 25 km instead of
    90 — at exactly the altitudes where the horizon is furthest away. It drew
    a plausible picture the whole time, which is why it took a printed table
    of cell size against altitude to find it.
14. **An aircraft that rotated through its own nose gear.** A strut past full
    travel is on its stop, and a stop is not a spring that has stopped
    pushing harder — the normal force saturated at full compression. Braking
    hard enough to bottom the nose gear therefore let the fighter keep
    rotating: 79° nose down on a flat, dry runway, ending in a "terrain
    impact" that was its own nose hitting the ground it was already standing
    on. Found by a contaminated-runway test that happened to brake harder
    than anything had before.
15. **A setup screen that had outgrown its window.** One un-scrolled column,
    laid out when there were fewer settings, drawing the last group straight
    through the key legend at the bottom of the screen. Every setting added
    since made it worse and none of them made it visible, because the row
    being edited was usually near the top.

---

## Scope and non-goals

Fixed-wing, flat-Earth, rigid-body, for handling qualities, autopilot
behaviour, systems interaction and failure response.

It does **not** model: aeroelasticity, rotorcraft, propeller aircraft, detailed
engine thermodynamics, transonic or supersonic aerodynamics, sensor error,
navigation aids, scenery beyond a procedural heightfield and one runway,
certification-grade correlation with any real type, or any weapon or mission
system.

Documented simplifications and their quantified consequences are in
[`docs/known_limitations.md`](docs/known_limitations.md), which is required
reading before quoting any number this simulator produces.
