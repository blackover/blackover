# Designing an Aircraft for AeroSim Lab

Every aircraft in this simulator is a folder of YAML. Nothing about the two
that ship with it is privileged: drop a third folder into
`aerosim/data/aircraft/` and it appears on the pre-flight screen, gets a mesh
built from its own geometry, and flies through exactly the same force model.

This is the guide to making that third folder a *good* aeroplane — and the
tool that tells you whether you have.

```bash
python -m aerosim --design aeroliner_200
python -m aerosim --design my_aircraft --design-altitude 8000
```

The report grades every figure below against a target band and then explains,
in plain language, what to change and what it will cost.

---

## The one rule

**Every number is a trade.** There is no setting you can raise for free. The
report exists because a design is a set of compromises and the only way to
know whether yours are the right ones is to see all of them at once.

If a figure comes out `LOW` or `HIGH`, that is not a failure. It is the tool
saying *this is unusual, and it should be on purpose*. The AeroFalcon-X fails
the transport's wing-loading band, and it fails it in the direction that makes
it a fighter.

---

## 1. Start with the mission, then the wing

Pick the mission first and two numbers follow from it almost immediately.

**Wing loading** `W / S` — the single most consequential number in the whole
package. It sets the stall speed, and through it the approach speed, the
runway length, the turn radius and how the aircraft rides in turbulence.

| W/S (N/m²) | Character |
|---|---|
| 1 500 – 2 500 | Trainer, bush aircraft. Lands anywhere, thrown about by gusts. |
| 2 500 – 5 500 | Fighter. Turns hard; short field only with a lot of flap. |
| 3 500 – 7 000 | Transport. Efficient in the cruise, needs pavement. |
| > 7 000 | Very high speed. Approach speed becomes the design driver. |

Halving wing area does not halve drag: at cruise a wing is mostly *parasite*
drag, so a smaller wing cruises better and lands worse. That is the trade in
one sentence.

**Aspect ratio** `b² / S` — sets induced drag, which is where range comes
from and where roll rate goes to die.

| AR | Character |
|---|---|
| 2 – 4 | Fighter. Rolls fast, high induced drag, high stall angle. |
| 6 – 8 | Regional, business. |
| 8 – 11 | Transport. Efficient, and slow in roll. |
| > 14 | Sailplane. Beautiful L/D, structurally awkward. |

The connection is not decorative: peak `L/D` goes roughly as `√(AR / CD0)`,
and the report computes the actual figure from your own lift curve and drag
polar rather than from that approximation.

---

## 2. Thrust: enough, and not much more

**Thrust-to-weight** decides the take-off roll, the climb angle and — on a
fighter — whether a turn can be sustained or only entered.

| T/W (static, sea level) | Character |
|---|---|
| 0.25 – 0.30 | Twin transport. Adequate; engine-out is a real event. |
| 0.30 – 0.40 | Transport with margin. Climbs well, burns more. |
| 0.75 – 1.10 | Fighter, dry-to-reheat. |
| > 1.10 | Accelerates vertically. |

Adding thrust is the most tempting fix for a bad design and almost always the
wrong one. If the climb angle is poor, look first at whether the *drag* is
high (`cd0`) or the wing is too small — thrust costs fuel for the whole
flight, and the report's Breguet range figure is where you see the bill.

In this simulator the useful fields are:

```yaml
engine_count: 2
max_thrust_per_engine: "145 kn"      # kilonewtons, not knots
specific_fuel_consumption: 1.55e-5   # kg per newton-second
mach_thrust_table:                   # how thrust varies with speed
  range: clamp
  breakpoints: [0.00, 0.30, 0.60, 0.85]
  values:      [1.000, 0.870, 0.800, 0.790]
```

A high-bypass fan **loses** thrust with speed; a military engine with good ram
recovery **gains** it past M 1. Getting that table's shape right matters more
than the peak number.

---

## 3. Stability: the part that decides how it feels

Three coefficients between them decide whether an aircraft is pleasant.

### `cm_alpha` — pitch stiffness

**Must be negative.** The package loader refuses a positive value, because an
aircraft with positive `cm_alpha` diverges in pitch and no amount of
autopilot hides it.

The report converts it into a **static margin** (`−cm_alpha / cl_alpha`, as a
percentage of mean chord):

- **15 – 25 %** — a transport. Stable, docile, and paying trim drag for it.
- **5 – 12 %** — sporting. Manoeuvres willingly.
- **negative** — relaxed stability. Needs continuous augmentation; in this
  simulator that means the flight assist, and the report says so.

### `cn_beta` — weathercock stability

**Must be positive**, or the aircraft has no directional stability at all.
0.08 – 0.16 is the usual band. It comes from fin area and tail arm.

### `cl_beta` — dihedral effect

**Negative**, typically −0.05 to −0.15. This is the one people get wrong.
Too much dihedral effect relative to `cn_beta` gives a badly damped **dutch
roll** — a wallowing yaw-and-roll oscillation that makes an aircraft
unpleasant in exactly the conditions where you want it steady.

> The ratio `|cl_beta| / cn_beta` is the number to watch. Above about 1.2 the
> dutch roll starts to complain. The AeroLiner-200 sits at 1.08 and its dutch
> roll damping still comes out at 0.05, below the 0.08 a specification would
> ask for — which is a real characteristic of that package, found by this
> tool, and left in because a slightly wallowy narrow-body is a *true* thing
> for a narrow-body to be.

---

## 4. The five modes

`--design` linearises the real force model about trim and reports the five
classical modes. This is the most useful part of the report, because it says
what the aircraft will *feel* like before you fly it.

| Mode | What it is | Level 1 target |
|---|---|---|
| **Short period** | Fast pitch bobble after a stick input, 1–5 s | damping 0.30 – 2.0 |
| **Phugoid** | Slow speed/altitude exchange, 30–120 s | damping > 0.04 |
| **Dutch roll** | Yaw-roll wallow, 2–8 s | damping > 0.08 |
| **Roll subsidence** | How fast a roll rate settles | time constant < 1.4 s |
| **Spiral** | Slow bank divergence | doubles in > 20 s |

What moves them:

- **Short period damping** ← `cm_q` (tail area × tail arm²) and `cm_alpha`.
  Low damping feels twitchy and makes precise pitch tracking tiring.
- **Phugoid damping** ← drag, mostly. A slippery aircraft has a lightly damped
  phugoid, which is why gliders wallow in speed. It is slow enough that
  nobody minds much.
- **Dutch roll damping** ← `cn_r` (fin) up, `cl_beta` (dihedral, sweep) down.
- **Roll time constant** ← `cl_p` (roll damping, rises with aspect ratio) and
  `cl_da` (aileron power).
- **Spiral** ← the balance of `cl_beta` against `cn_beta` again, the other way
  round from the dutch roll. **You cannot have both** perfectly: fixing a
  divergent spiral by adding dihedral degrades the dutch roll, and vice
  versa. Real aircraft accept a slowly divergent spiral and damp the dutch
  roll, because the pilot can correct a slow bank and cannot damp a fast
  wallow.

---

## 5. Landing is the hard part

The approach speed is `1.3 × Vs` at landing flap, and it drives the runway
length, the tyre and brake energy, and how forgiving the aircraft is.

```
Vs = sqrt( 2 W / (rho S CLmax) )
```

so every lever is one of: less weight, more area, more `CLmax`. Flaps are the
cheapest of the three:

```yaml
flaps:
  delta_cl_max: 0.90     # how much CLmax the full flap setting adds
  delta_cd_max: 0.085    # and what it costs in drag
  delta_alpha_stall: "-3.0 deg"
```

`delta_cl_max` of 0.6–1.0 is a plain flap; 1.0–1.6 is a slotted or Fowler
system, and it should cost proportionally more drag and more mass. An
approach speed above about 160 kt means a long, dry runway every single time
— and this simulator now models a contaminated one, so check the report's
approach figure against the runway you intend to use it from.

---

## 6. Mass and inertia: the checks that catch typos

```yaml
inertia:
  ixx: "2.1e6 kg m2"
  iyy: "3.5e6 kg m2"
  izz: "5.2e6 kg m2"
  ixz: "1.1e5 kg m2"
```

The loader enforces the **triangle inequality** — `Ixx + Iyy ≥ Izz` and its
permutations — because a tensor that violates it describes no rigid body that
can exist. For a conventional aircraft the ordering is almost always
`Ixx < Iyy < Izz`, with `Izz ≈ Ixx + Iyy`.

A useful sanity check: `Ixx ≈ m (0.25 b)²` and `Iyy ≈ m (0.28 L)²` get within
about 30 % for most conventional layouts.

The **CG range** matters as much as the CG:

```yaml
cg_limits: { forward: "-0.35 m", aft: "0.45 m" }
```

Aft of the aft limit the static margin goes negative. Forward of the forward
limit the elevator runs out of authority in the flare. The panel shows CG live
and turns it amber outside the limits.

---

## 7. Declare the envelope honestly

```yaml
mach_validated_max: 0.85
lateral: { beta_max: "20 deg" }
```

These are not limits — they are statements about where your *data* stops
being defensible. Past them the simulator lights an ENVELOPE caution and the
event log says why. That is the feature: an aircraft that quietly keeps
producing plausible numbers outside its validated range is more dangerous
than one that admits it does not know.

---

## 8. A worked order of operations

1. **Mission** → cruise speed, altitude, payload, field length.
2. **Wing loading** from the approach speed you can accept.
3. **Wing area and span** from that and an aspect ratio suited to the class.
4. **Mass breakdown**: empty / payload / fuel. Keep empty fraction 0.40–0.65.
5. **Thrust** from the climb angle and field length you need, not from taste.
6. **Drag polar**: `cd0` from wetted area and cleanliness, `oswald` 0.70–0.85.
7. **Tail volumes** → `cm_alpha`, `cm_q`, `cn_beta`, `cn_r`.
8. **Run `--design`.** Fix what is off, and check what your fix broke.
9. **Fly it.** Take-off, a turn, a stall, an approach, an autoland. The
   report cannot tell you whether it is *fun*.

---

## What the report cannot tell you

It reads the package, so it inherits everything in
[`known_limitations.md`](known_limitations.md): linear lateral derivatives, a
synthesised stall curve, a rigid airframe, an algebraic engine. The modes are
linearised about one trim point at one loading — a real handling-qualities
assessment sweeps the whole envelope and every CG.

And it grades the aircraft you *described*, not the one you could build.
Nothing here checks that a wing of that area and that aspect ratio can carry
that load at that mass, because there is no structural model. A package that
scores well on every figure and weighs half what its geometry implies is a
good design of an aircraft that does not exist.
