# Known Limitations

Every simulator simplifies. The difference between a useful tool and a
misleading one is whether the simplifications are written down with their
consequences quantified. This document is the list.

**Read this before quoting any number this simulator produces.**

Each entry states the simplification, why it was accepted, what it costs, and
what would be required to remove it.

---

## 1. Earth model

**Simplification.** Flat, non-rotating Earth. Position is a local NED tangent
plane.

**Consequence.** Coriolis and transport-rate accelerations are omitted. At
250 m/s and 45° latitude the omitted Coriolis acceleration is approximately
2.6 × 10⁻² m/s², roughly three orders of magnitude below the aerodynamic
accelerations of interest. Over a 20-minute flight the accumulated position
error is on the order of a kilometre.

**Acceptable for.** Handling qualities, autopilot response, systems behaviour,
any study over minutes rather than hours. Everything this game does.

**Not acceptable for.** Long-range navigation accuracy, inertial navigation
error modelling, great-circle route validation.

---

## 2. Aerodynamics

### 2.1 Linear lateral-directional derivatives

**Simplification.** Side force, rolling moment and yawing moment are linear in
sideslip. There is no lateral stall, no post-stall departure, no spin model.

**Consequence.** Behaviour beyond `beta_max` (20° for both supplied aircraft)
or beyond the stall is not representative. The model produces smooth,
plausible, *incorrect* numbers rather than failing visibly.

**Mitigation in place.** `AeroModel.compute` raises `out_of_envelope` beyond
1.5 × stall angle of attack or beyond `beta_max`, the orchestrator posts an
ENVELOPE caution to the event log, and the panel lights an ENVELOPE
annunciator. You are told when the model has left the region it can defend.

**This matters most for the fighter.** A fighter spends much of its useful
envelope at exactly the high-alpha, high-beta conditions this build-up
represents least well. The AeroFalcon-X aerodynamics section is rated
*low* confidence in its own manifest for that reason.

**To remove.** Non-linear β tables and a departure/spin model, both of which
require data this project does not have.

### 2.2 Rigid airframe

No aeroelastic deformation, no structural modes. Structural mode coupling with
the flight control system cannot be studied. For a narrow-body transport the
first symmetric bending mode is well above the flight-control bandwidth, so
rigid-body handling qualities are largely unaffected.

**Not acceptable for.** Flutter analysis, structural mode filter design, ride
quality assessment.

### 2.3 Quasi-steady aerodynamics

The only unsteady effect represented is the pitch-rate derivative Cm_q. There
is no downwash lag term (Cm_α̇) and no dynamic-stall hysteresis, so short-period
damping is underestimated somewhat and rapid manoeuvring through the stall will
not reproduce a real hysteresis loop.

### 2.4 Compressibility

**Simplification.** Compressibility appears only as a Prandtl-Glauert lift
correction and a drag-rise increment from a Mach table. There is no transonic
pitch-up ("Mach tuck"), no shock-induced separation, no aileron reversal.

**Consequence.** Behaviour above each package's declared `mach_validated_max`
(0.86 airliner, 0.85 fighter) is not representative. The Prandtl-Glauert factor
is clamped at M 0.92 to prevent a singularity at M 1.0.

**Not acceptable for.** High-speed upset studies, Mach-tuck recovery,
anything transonic or supersonic. **This is the limitation that constrains
what the AeroFalcon-X can legitimately be used to study.** Its VMO is
deliberately set below the airframe's structural capability, because an
operating limit the aerodynamic model cannot support is a limit in name only.

### 2.5 Synthesised stall curve

When a package supplies no explicit `cl_table`, one is synthesised from `cl0`,
`cl_alpha` and `alpha_stall`: linear to the stall angle, a rounded peak, then
decay towards a flat-plate value. Both supplied aircraft use the synthesised
curve.

**Consequence.** Post-stall lift is representative in shape but not validated
in magnitude. The curve is continuous by construction — no step larger than
0.08 CL between quarter-degree points, asserted by
`test_lift_curve_has_no_discontinuity` — so it cannot inject impulsive forces
into the integrator. It is deliberately *not* symmetric about zero alpha:
mirroring the upright peak onto the inverted side deletes the camber term.

**Preferred alternative.** Supply an explicit `cl_table` in
`aerodynamics.yaml`. The synthesised curve is a fallback, not a target.

### 2.6 Ground effect

Empirical induced-drag factor 33h^1.5/(1 + 33h^1.5) with an 8 % lift increment,
both functions of height in wingspans only. No dependence on aspect ratio,
sweep or attitude. The model vanishes exactly one span above the ground, which
is conventional but abrupt compared with reality.

---

## 3. Propulsion

**Simplification.** Thrust is an algebraic map of N1, density ratio and Mach
with first-order spool dynamics. This is not a thermodynamic cycle deck.

**Consequence.** No compressor stall, no surge, no thermal transient, no
bleed-air effect, and no difference between accelerating and decelerating fuel
schedules. **EGT is an indicative linear function of N1 and is not a
temperature prediction.**

**Acceptable for.** Thrust response timing, engine-out handling, fuel-burn
bookkeeping, autothrottle behaviour.

**Not acceptable for.** Engine performance analysis, exceedance investigation,
anything where EGT or N2 matters.

### 3.1 Thrust augmentation

The afterburner on the AeroFalcon-X is a generic thrust and fuel-flow
multiplier (×1.63 thrust, ×2.40 fuel). It represents no specific technology and
is deliberately non-representative of any real system.

### 3.2 Failed engines

A failed engine produces exactly zero thrust and burns no fuel while its spool
decays for the gauge. A real windmilling engine produces *drag*, which is not
modelled, so engine-out performance here is marginally optimistic.

---

## 4. Landing gear

**Simplification.** Each strut is an independent non-linear spring-damper with
tanh-regularised Coulomb tyre friction and asymmetric compression/extension
damping. There is no oleo gas curve, no tyre relaxation length, no anti-skid,
no strut-to-strut hydraulic coupling.

**Consequence.** Touchdown loads are indicative, not structural. Braking is a
single friction coefficient and will not reproduce anti-skid cycling.

**Numerical note.** Stiff struts with an explicit integrator are the classic
source of ground bounce. Stiffness is therefore specified as a static
deflection under the aircraft's own weight and damping as a fraction of
critical, placing the ground mode near 3 Hz — comfortably inside the 100 Hz
kernel step. Friction is regularised with tanh over a 0.3 m/s velocity scale so
a parked aircraft does not chatter between positive and negative friction.

**Consequence of that regularisation.** Below roughly 0.3 m/s the tyre force
fades smoothly to zero rather than holding statically, so a parked aircraft
creeps at about half a knot instead of remaining exactly stationary. This is
visible on the airspeed readout at the start of a runway scenario.

---

## 5. Atmosphere and wind

### 5.1 Temperature offset

An ISA temperature offset changes temperature and density but leaves the
pressure column unchanged, so pressure-altitude relationships on a hot or cold
day are not strictly correct. The density effect — the part that governs
aircraft performance — is correct.

### 5.2 Turbulence

**Simplification.** The Dryden gust model is realised as three *first-order*
shaping filters rather than the full second-order transfer functions of
MIL-F-8785C.

**Rationale.** The objective is a plausible, bounded, reproducible disturbance
for handling and autopilot work, not spectral fidelity.

**Consequence.** The power spectral density rolls off at 20 dB/decade rather
than 30 dB/decade, so high-frequency gust energy is overstated. Intensity
scaling with altitude is linear, not the MIL-F-8785C probability-of-exceedance
model.

**Not acceptable for.** Gust load certification analysis, ride quality
assessment against a standard.

### 5.3 Wind field

Wind is horizontally uniform, varying with altitude only. There are no
microbursts, no shear fronts, no terrain-induced flow. Layer interpolation is
linear, direction interpolates through the shorter arc, and the outermost
layers are held constant beyond the table ends.

---

## 6. Mass properties

Fuel tanks are point masses at fixed body stations. Tank inertia about its own
axes is neglected; only the parallel-axis transfer term is retained, which for
a transport is two to three orders of magnitude larger.

**There is no fuel slosh model**, so lateral dynamics during rapid manoeuvring
with partly filled tanks are not represented. Fuel does not migrate within a
tank under acceleration, and tank centroids do not shift as quantity falls.

---

## 7. Integration scheme

**Simplification.** Fixed-step RK4 over the 13 continuous states. Control
surface positions, thrust and mass properties are held constant across the four
RK4 stages (zero-order hold), because they contain rate limiters and discrete
logic that are not differentiable.

Aerodynamic and ground-reaction forces *are* re-evaluated at each stage — they
are smooth functions of the state, and the gear spring is the stiffest element
in the model, so freezing it would cost more accuracy than it saves. The gust
vector is frozen for the whole step so determinism does not depend on how many
times the derivative function happens to be called.

**Consequence.** This is a co-simulation split: continuous states integrate at
RK4 accuracy while discrete states update once per kernel step, giving them
first-order accuracy. At 100 Hz the resulting error is small relative to the
modelling uncertainty in the aerodynamic data, but it is real.

**Validated step size.** 0.01 s. The setup screen allows 0.02 s and warns.

---

## 8. Systems

Electrical, hydraulic and fuel are modelled at the level of sources, buses and
pressures with explicit dependency propagation: fuel feeds engines, running
engines drive generators, generators drive hydraulic pumps, pressure sets
actuator rate, and actuator rate changes how the aircraft handles. No
special-case logic connects those steps.

They are **not** circuit-level or component-level models. There is no load
shedding beyond a single pressure figure, and no sensor error model at all —
the autopilot and the panel read truth rather than a measured value. Sensor
noise, bias, drift and latency are *not* represented in this build.

---

## 9. Autoflight and autoland

**What it is.** A phase state machine that moves the *targets* of the ordinary
autopilot — altitude, heading, vertical speed, airspeed. It is not a second
set of control laws, and it has no authority the pilot does not also have.

**Simplification.** Guidance geometry is a single runway: a threshold point, a
heading and an elevation. There is no navigation database, no published
procedure, no missed-approach track, and no ILS — the "localizer" and
"glidepath" are computed directly from that geometry, with perfect knowledge
of the aircraft's position. A real autoland flies a received signal, with beam
noise and bends, and its accuracy degrades with distance from the transmitter.

**Consequence.** Tracking is better than a real system's, and none of the
failure modes that make real autoland systems interesting are represented:
no beam interference, no signal loss, no receiver failure, no decision height
logic beyond a single lined-up-or-go-around check, and no redundancy or
disagreement between channels. **This is not a Category III autoland model and
must not be read as one.**

**Not modelled.** Crosswind decrab or sideslip on touchdown — the aircraft
tracks the centreline by heading, so in a crosswind it lands slightly crabbed.
There is no autobrake logic; the rollout applies full braking.

**Take-off.** Rotation is flown open-loop by ramping the stick, because the
autopilot has no ground mode. There is no V1, no rejected take-off, no
balanced field calculation, and no engine-failure-after-V1 case.

**Tuning.** The flare profile, configuration schedule and capture criteria are
per-aircraft data in `autopilot.yaml`, not code. They were tuned by flying
them, not derived, so they are reasonable rather than optimal.

---

## 10. Telemetry and replay

**Simplification.** Telemetry is sampled at a fixed rate (50 Hz by default)
from a kernel running at 100 Hz, so a recording is a *decimated* view of the
run rather than every step of it.

**Consequence.** A replay reproduces the sampled states exactly, but anything
that happened between samples is not in the file. A control transient shorter
than 20 ms — an actuator hitting a rate limit for a single step, say — can be
invisible in a recording of a run where it demonstrably occurred. Raise the
sample rate to the kernel rate if that matters.

**Also.** Ground speed, track and flight path angle are recomputed on load
rather than stored, because they are exactly recoverable from the thirteen
states and a second stored copy is a number that can disagree with the first.
Lift and drag coefficients are not recorded at all.

**What the hash does and does not tell you.** The SHA-256 in the manifest
covers the telemetry file only. It detects a changed or truncated recording.
It says nothing about whether the model that produced it was correct — that is
what the package checksum, the confidence ratings and the test suite are for.

---

## 11. Terrain

**Simplification.** Ground elevation is a procedural heightfield: a hash of the
integer lattice summed over four or five octaves of value noise, with an
optional ridge fold. It is not a survey, a DEM, or a model of any real place.
The airport sits on a flat plateau blended into the surrounding relief over
9 km, because approach guidance, gear reaction and the autoland flare all
assume level ground near the runway.

**Consequence.** Elevations are plausible rather than correct. Slopes are
smooth at the octave scale and have no cliffs, no watercourses and no man-made
cuttings. There is no vegetation, no surface classification and no variation in
rolling friction with surface type — the whole field has the runway's friction
coefficients, including the parts of it that are, by their colour, a mountain.

**What it *is* authoritative for.** The renderer and the flight model read the
same `Terrain` object, so the hill you can see is the hill you hit. There is
deliberately no second copy of the surface. Terrain impact is reported
distinctly from a runway crash, with the elevation that was struck.

**Also.** The field is a pure function of `(seed, profile, field_elevation)`,
so it is not stored in telemetry: a replay rebuilds the identical landscape
from the manifest. Changing the seed changes the landscape a recorded flight
appears to have flown over, which is why the seed is part of the manifest.

**Not acceptable for.** Terrain-following or terrain-avoidance system
development, obstacle clearance analysis, or anything where a real elevation
matters. There is no TAWS and no obstacle database.

---

## 12. Visual effects

**Simplification.** Exhaust plumes, contrails and wingtip vortices are drawn
from state variables through hand-tuned thresholds. They are gated on the
physical conditions that produce them — spool speed and the afterburner flag
for the plume, static air temperature for contrails, load factor and altitude
for vortices — so they carry information rather than being decoration. None of
them is a calculation of the thing they depict.

**Consequence.** The contrail threshold is a fixed −40 °C with an 8 K
shoulder, not the Appleman criterion, so it ignores pressure and humidity
entirely and will show a persistent trail in air that in reality is far too dry
for one. Plume length scales with spool speed and reheat; it is not a nozzle
calculation and should not be read as one. The vortex condition uses altitude
as a proxy for humidity, which is a stand-in for a quantity this simulator does
not have.

**Invariant.** Effects read the simulation and write nothing back. An effect
that could alter the aircraft's path would be a second, undocumented force
model. This is tested.

---

## 13. Weather

**Simplification.** Cloud is a single deck with a base, tops and a coverage
fraction; six eighths or more makes it solid enough to fly into and anything
less is flown past. Precipitation is one of six presets, each a visibility
multiplier and a density for the visual field. Surface condition is one of six
states, applied as multipliers on the gear model's own dry-runway
coefficients.

**Consequence.** There is no drop-size distribution, no liquid water content,
no cloud type, no convective structure, no embedded turbulence tied to the
cloud, no drainage or contaminant depth, and no hydroplaning. The precipitation
field is *screen space*: it is the near field a windscreen sees, not a
world-space volume, so it does not occlude anything and does not vary with
where you look.

**Icing.** Accretion is gated on visible moisture and a recovery temperature
between 0 and −20 °C, peaking near −6 °C, with a rate proportional to airspeed
and a fixed time constant. That is the right *shape*: the liquid-water window
is real, the ram rise that keeps a fast aircraft out of it is real and is
computed from the stagnation relation rather than special-cased, and the
consequences — 30 % of CLmax, 35 % of the stall angle, added parasite drag and
added mass forward of the CG — are the right ones in the right proportions.
It is not a microphysics model: there is no accretion geometry, no distinction
between rime and glaze, no runback, no tailplane stall and no propeller or
intake icing. Anti-ice sheds at a fixed rate whatever the conditions and costs
nothing, where a real system is a bleed-air or electrical load.

**Runway state.** Braking-action multipliers, not measured friction
coefficients. They reproduce published contaminated-runway distance factors to
about ten per cent, which is enough to make the decision to land somewhere
else a real one and not enough to compute a landing distance from.

**Not acceptable for.** Icing certification work of any kind, contaminated
runway performance calculation, or anything where a real meteorological
quantity matters.

---

## 14. Not modelled at all

For the avoidance of doubt, none of the following exist:

- Buildings, vegetation, water, roads, or any scenery beyond the heightfield
- Thunderstorms, windshear fronts, microbursts or wake turbulence
- Any navigation aid, radio, or air traffic environment
- Thrust reversers (the rollout uses wheel brakes and speedbrake only)
- Rotorcraft, propeller aircraft, or any non-fixed-wing configuration
- Cabin pressurisation, environmental control, or oxygen systems
- Certification-grade correlation with any real aircraft type
- **Any weapon, sensor, targeting or mission system.** The AeroFalcon-X is an
  airframe. Its external stores exist only as mass and parasite drag, because
  that is the part which changes how the aircraft flies.

---

## 15. Design analysis

**Simplification.** ``--design`` grades a package by interrogating the same
models the simulator flies, and linearises the force build-up numerically
about one trim point to obtain the five classical modes.

**Consequence.** The modes describe *that* trim point, at *that* loading, at
*that* altitude, with the gear and flaps where they were. A handling-qualities
assessment sweeps the envelope and every CG; this samples one condition. The
target bands are drawn from published Level 1 practice, and a band is a
generalisation about a class of aircraft rather than a requirement on yours.

**And it grades the aircraft you described, not the one you could build.**
There is no structural model, so nothing checks that a wing of that area and
that aspect ratio can carry that load at that mass. A package that scores well
on every figure and weighs half what its geometry implies is a good design of
an aircraft that does not exist.

---

## 16. Verification status

Modelling limitations and *verification* limitations are different things.
Every layer in this build has passing tests — see `README.md` for the count —
but a passing test suite bounds the ways the code has been checked, not the
ways it could be wrong. A documented limitation in this file is not a
substitute for a test, and a test is not a substitute for validation against
flight data, which this project has none of.
