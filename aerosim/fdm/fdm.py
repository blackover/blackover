"""Force assembly and integration -- the flight dynamics model proper.

One ``step`` does this, in this order:

1. sample the atmosphere and advance the turbulence filters (once per step)
2. spool the engines and burn fuel (once per step)
3. recompute mass properties from the new tank quantities
4. integrate the continuous states with RK4, re-evaluating the aerodynamic and
   ground-reaction forces at each stage
5. publish derived quantities

Thrust, control surface positions and mass are frozen across the four RK4
stages; aerodynamics and gear reaction are not, because they are smooth
functions of the state and the gear spring is the stiffest thing in the model.
The gust vector is frozen for the whole step so that determinism does not
depend on how many times the derivative happens to be called.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core import frames
from ..core.frames import cross3
from ..core.state import IX_QUAT, IX_RATE, IX_VEL, State
from ..core.units import G0
from ..env.atmosphere import Atmosphere
from ..env.wind import WindField
from .aero import AeroModel
from .gear import GearModel
from .mass import MassModel
from .propulsion import PropulsionModel
from .rigid_body import gravity_body, rk4_step, state_derivative


@dataclass
class Controls:
    """Effector positions after actuator limiting -- what the surfaces are doing.

    Deflections are normalised to [-1, 1] and scaled to physical angles by the
    aerodynamic derivatives, which are quoted per unit of normalised
    deflection.
    """

    elevator: float = 0.0
    aileron: float = 0.0
    rudder: float = 0.0
    throttle: float = 0.0
    flap: float = 0.0
    speedbrake: float = 0.0
    brake: float = 0.0
    steering: float = 0.0
    gear_down: bool = True
    afterburner: bool = False


@dataclass
class Diagnostics:
    """What the model did this step, for the panel and the event log."""

    lift: float = 0.0
    drag: float = 0.0
    thrust: float = 0.0
    weight: float = 0.0
    cl: float = 0.0
    cd: float = 0.0
    cm: float = 0.0
    lift_to_drag: float = 0.0
    stalled: bool = False
    out_of_envelope: bool = False
    envelope_reason: str = ""
    on_ground: bool = False
    gear_load: float = 0.0
    fuel_flow: float = 0.0
    stall_speed: float = 0.0
    overspeed: bool = False
    crashed: bool = False
    crash_reason: str = ""
    touchdown_rate: float = 0.0
    events: list[str] = field(default_factory=list)


class FlightDynamics:
    """The flight model for one aircraft in one atmosphere."""

    def __init__(
        self,
        model,
        *,
        atmosphere: Atmosphere | None = None,
        wind: WindField | None = None,
        field_elevation: float = 0.0,
        terrain=None,
    ) -> None:
        self.model = model
        self.atmosphere = atmosphere or Atmosphere()
        self.wind = wind or WindField()
        self.field_elevation = field_elevation
        # One heightfield, shared with the renderer. Terrain you can see but
        # cannot hit is worse than none, because the picture then contradicts
        # the simulation it is supposed to be showing.
        self.terrain = terrain

        self.aero = AeroModel(model)
        self.propulsion = PropulsionModel(model)
        self.mass_model = MassModel(model)
        self.gear = GearModel(
            model, terrain=terrain, terrain_elevation=field_elevation
        )

        self.vmo = model.get("limitations", "vmo")
        self.mmo = model.get("limitations", "mmo")
        self.load_limit_positive = model.get("limitations", "load_factor_positive", 2.5)
        self.load_limit_negative = model.get("limitations", "load_factor_negative", -1.0)
        self.load_limit_positive_flaps = model.get(
            "limitations", "load_factor_positive_flaps", self.load_limit_positive
        )
        self.max_touchdown_rate = model.get("limitations", "max_touchdown_rate", 3.7)

        self.state = State()
        self.controls = Controls()
        self.diagnostics = Diagnostics()

        self._mass_properties = self.mass_model.compute()
        self._gust_body = np.zeros(3)
        self._wind_ned = np.zeros(3)
        self._frozen_thrust = np.zeros(3)
        self._frozen_thrust_moment = np.zeros(3)
        self._was_airborne = False

    # -- setup -------------------------------------------------------------

    def initialise(self, *, fuel: float, payload: float) -> None:
        self.mass_model.set_fuel(fuel)
        self.mass_model.set_payload(payload)
        self._mass_properties = self.mass_model.compute()

    @property
    def mass_properties(self):
        return self._mass_properties

    def ground_clearance(self) -> float:
        """Height of the reference point above the wheels, in metres."""
        return self.gear.lowest_point

    def ground_height(self, north: float = 0.0, east: float = 0.0) -> float:
        """Terrain elevation under a position."""
        if self.terrain is None:
            return self.field_elevation
        return self.terrain.height_at(north, east)

    def place_on_ground(self, state: State) -> None:
        """Sit the aircraft on its wheels, struts already at static compression.

        Placing it with the wheels exactly touching leaves every strut at zero
        load, so the aircraft settles onto its oleos over the first second of
        every runway start and reports a touchdown it never made. Starting at
        the deflection its own weight produces skips both.
        """
        static = self.model.get("landing_gear", "static_deflection", 0.2)
        ground = self.ground_height(float(state.x[0]), float(state.x[1]))
        state.x[2] = -(ground + self.gear.lowest_point - static)

    # -- one step ----------------------------------------------------------

    def step(self, dt: float, controls: Controls) -> Diagnostics:
        """Advance the flight model exactly one fixed step."""
        self.controls = controls
        diagnostics = Diagnostics()
        state = self.state

        altitude = state.altitude
        air = self.atmosphere.sample(altitude)

        # -- environment, once per step -----------------------------------
        vtas_previous = max(self.state.derived.vtas, 1.0)
        self.wind.update_turbulence(dt, vtas_previous, altitude)
        self._wind_ned = self.wind.wind_ned(altitude)

        mach_previous = self.state.derived.mach

        # -- propulsion, once per step ------------------------------------
        fuel_available = not self.mass_model.fuel_exhausted
        propulsion = self.propulsion.update(
            dt,
            throttle=controls.throttle,
            afterburner=controls.afterburner,
            density_ratio=air.density_ratio,
            mach=mach_previous,
            fuel_available=fuel_available,
        )
        self._frozen_thrust = propulsion.force_body
        self._frozen_thrust_moment = propulsion.moment_body

        self.mass_model.burn(propulsion.total_fuel_flow * dt)
        if not fuel_available and propulsion.total_thrust > 0.0:
            diagnostics.events.append("FUEL EXHAUSTED")

        # -- mass properties from the new tank quantities ------------------
        self._mass_properties = self.mass_model.compute()
        mass_properties = self._mass_properties

        # Aerodynamic and gear moments are taken about the CG, so the offset
        # between the geometric reference point and the CG has to be applied.
        cg = mass_properties.cg

        aero_snapshot: dict = {}

        def derivative(x: np.ndarray) -> np.ndarray:
            force, moment, snapshot = self._forces(
                x, controls, air, mass_properties, cg
            )
            # Keep the most recent stage: RK4's fourth evaluation sits at
            # x + dt*k3, which is within a stage of the final state, so the
            # published diagnostics describe the attitude actually on screen.
            aero_snapshot.clear()
            aero_snapshot.update(snapshot)
            return state_derivative(
                x,
                force,
                moment,
                mass_properties.mass,
                mass_properties.inertia,
                mass_properties.inertia_inverse,
            )

        state.x = rk4_step(state.x, dt, derivative)
        state.normalise()

        if not state.is_finite():
            diagnostics.crashed = True
            diagnostics.crash_reason = "numerical divergence"
            diagnostics.events.append("NUMERICAL DIVERGENCE")
            self.diagnostics = diagnostics
            return diagnostics

        # -- publish derived quantities ------------------------------------
        self._publish(state, air, mass_properties, aero_snapshot, diagnostics)
        diagnostics.fuel_flow = propulsion.total_fuel_flow
        diagnostics.thrust = propulsion.total_thrust
        diagnostics.weight = mass_properties.mass * G0
        self.diagnostics = diagnostics
        return diagnostics

    # -- force assembly ----------------------------------------------------

    def _forces(self, x, controls, air, mass_properties, cg):
        """Total external force and moment about the CG, in body axes."""
        quaternion = x[IX_QUAT]
        velocity_body = x[IX_VEL]
        rates = x[IX_RATE]
        altitude = -float(x[2])
        height_agl = max(
            0.0, altitude - self.ground_height(float(x[0]), float(x[1]))
        )

        # Air-relative velocity: subtract the wind, in body axes.
        wind_body = frames.dcm_ned_to_body(quaternion) @ self._wind_ned
        air_velocity = velocity_body - wind_body

        vtas, alpha, beta = frames.wind_angles(
            float(air_velocity[0]), float(air_velocity[1]), float(air_velocity[2])
        )
        mach = vtas / air.sound_speed if air.sound_speed > 0.0 else 0.0

        aero = self.aero.compute(
            vtas=vtas,
            alpha=alpha,
            beta=beta,
            rates=rates,
            density=air.density,
            mach=mach,
            elevator=controls.elevator,
            aileron=controls.aileron,
            rudder=controls.rudder,
            flap=controls.flap,
            speedbrake=controls.speedbrake,
            gear_down=controls.gear_down,
            height_agl=height_agl,
        )

        # Aerodynamic moments are quoted about the aerodynamic reference point;
        # transfer them to the CG. Omitting this is the defect that makes the
        # published Cm disagree with the physics precisely when it is examined.
        aero_ref = np.array([self.aero.aero_ref_x, 0.0, 0.0])
        aero_moment = aero.moment_body + cross3(aero_ref - cg, aero.force_body)

        thrust_moment = self._frozen_thrust_moment + cross3(
            -cg, self._frozen_thrust
        )

        gear = self.gear.compute(
            position_ned=x[0:3],
            velocity_body=velocity_body,
            quaternion=quaternion,
            rates=rates,
            gear_down=controls.gear_down,
            brake=controls.brake,
            steering=controls.steering,
        )
        gear_moment = gear.moment_body + cross3(-cg, gear.force_body)

        weight = gravity_body(quaternion, mass_properties.mass)

        force = aero.force_body + self._frozen_thrust + gear.force_body + weight
        moment = aero_moment + thrust_moment + gear_moment

        snapshot = {
            "aero": aero,
            "gear": gear,
            "vtas": vtas,
            "alpha": alpha,
            "beta": beta,
            "mach": mach,
            "specific_force": (aero.force_body + self._frozen_thrust + gear.force_body),
        }
        return force, moment, snapshot

    # -- derived quantities ------------------------------------------------

    def _publish(self, state, air, mass_properties, snapshot, diagnostics):
        """Recompute the derived view from the freshly integrated state.

        Called every step *including the first*, so the panel never shows a
        zero airspeed on frame one while the aircraft is demonstrably moving.
        """
        derived = state.derived
        quaternion = state.quaternion
        altitude = state.altitude

        air_now = self.atmosphere.sample(altitude)
        wind_body = frames.dcm_ned_to_body(quaternion) @ self._wind_ned
        air_velocity = state.velocity_body - wind_body
        vtas, alpha, beta = frames.wind_angles(
            float(air_velocity[0]), float(air_velocity[1]), float(air_velocity[2])
        )

        derived.vtas = vtas
        derived.alpha = alpha
        derived.beta = beta
        derived.mach = vtas / air_now.sound_speed if air_now.sound_speed > 0.0 else 0.0
        derived.qbar = 0.5 * air_now.density * vtas * vtas
        derived.veas = self.atmosphere.eas_from_tas(vtas, altitude)
        derived.vcas = self.atmosphere.cas_from_tas(vtas, altitude)
        derived.altitude = altitude
        ground = self.ground_height(
            float(state.x[0]), float(state.x[1])
        )
        derived.altitude_agl = max(0.0, altitude - ground)
        derived.density = air_now.density
        derived.temperature = air_now.temperature
        derived.pressure = air_now.pressure
        derived.sound_speed = air_now.sound_speed

        derived.roll, derived.pitch, derived.yaw = state.euler()
        derived.p, derived.q, derived.r = (float(v) for v in state.rates)

        velocity_ned = state.velocity_ned
        derived.ground_speed = float(math.hypot(velocity_ned[0], velocity_ned[1]))
        derived.vertical_speed = -float(velocity_ned[2])
        derived.gamma = frames.flight_path_angle(velocity_ned)
        derived.track = frames.track_angle(velocity_ned)

        aero = snapshot.get("aero")
        gear = snapshot.get("gear")
        specific = snapshot.get("specific_force")

        if specific is not None and mass_properties.mass > 0.0:
            derived.accel_body = specific / mass_properties.mass
            derived.load_factor = float(-specific[2]) / (mass_properties.mass * G0)

        if aero is not None:
            diagnostics.lift = aero.lift
            diagnostics.drag = aero.drag
            diagnostics.cl = aero.cl
            diagnostics.cd = aero.cd
            diagnostics.cm = aero.cm
            diagnostics.stalled = aero.stalled
            diagnostics.out_of_envelope = aero.out_of_envelope
            diagnostics.envelope_reason = aero.envelope_reason
            diagnostics.lift_to_drag = aero.lift / aero.drag if aero.drag > 1.0e-6 else 0.0

        diagnostics.stall_speed = self.aero.stall_speed(
            mass_properties.mass, air_now.density, self.controls.flap
        )

        # -- ground contact and crash logic --------------------------------
        touching = bool(gear.any_contact) if gear is not None else False
        state.on_ground = touching
        diagnostics.on_ground = touching
        diagnostics.gear_load = gear.max_load if gear is not None else 0.0

        if touching and self._was_airborne:
            diagnostics.touchdown_rate = -derived.vertical_speed
            if diagnostics.touchdown_rate > self.max_touchdown_rate:
                diagnostics.crashed = True
                diagnostics.crash_reason = (
                    f"touchdown at {diagnostics.touchdown_rate:.1f} m/s exceeds "
                    f"{self.max_touchdown_rate:.1f} m/s gear limit"
                )
            else:
                diagnostics.events.append(
                    f"TOUCHDOWN {diagnostics.touchdown_rate:.1f} m/s"
                )
        self._was_airborne = not touching

        # A wing down at touchdown is a crash even though every strut reports
        # contact, so clearance is taken from the geometry rather than from a
        # bare bank-angle threshold: the limit then falls out of the aircraft's
        # own span and gear height instead of being restated per type.
        half_span = 0.5 * self.aero.wing_span
        wingtip_clearance = derived.altitude_agl - half_span * abs(
            math.sin(derived.roll)
        )
        if wingtip_clearance < 0.0 and derived.ground_speed > 3.0:
            diagnostics.crashed = True
            diagnostics.crash_reason = (
                f"wing strike at {math.degrees(abs(derived.roll)):.0f} deg bank"
            )

        # Reference point at or below the ground: gear-up contact, a nose-first
        # impact the struts never got a chance to absorb, or a hillside.
        if derived.altitude_agl <= 0.0:
            diagnostics.crashed = True
            ground = self.ground_height(float(state.x[0]), float(state.x[1]))
            if ground > self.field_elevation + 25.0:
                # Well above field elevation: this is rising ground, not the
                # airport, and calling it a landing accident would misdescribe
                # what happened.
                diagnostics.crash_reason = (
                    f"terrain impact at {ground:.0f} m elevation"
                )
            elif not self.controls.gear_down:
                diagnostics.crash_reason = "ground contact with gear retracted"
            else:
                diagnostics.crash_reason = "terrain impact"

        # -- envelope monitoring -------------------------------------------
        if derived.vcas > self.vmo:
            diagnostics.overspeed = True
            diagnostics.events.append(
                f"OVERSPEED {derived.vcas / 0.5144:.0f} kt CAS above VMO"
            )
        if derived.mach > self.mmo:
            diagnostics.overspeed = True
            diagnostics.events.append(f"MACH {derived.mach:.2f} above MMO")

        # Manoeuvring load limits apply in flight only. On the ground the
        # accelerometer is reading strut loads, and a normal touchdown spikes
        # to several g against a 2 g flaps placard -- which is not an
        # exceedance, it is a landing. Gear loads have their own limit, checked
        # above as max_touchdown_rate.
        if not touching:
            # Flaps lower the structural limit, so the placard that applies is
            # the one for the configuration the aircraft is actually in.
            limit = self.load_limit_positive
            if self.controls.flap > 0.01:
                limit = min(limit, self.load_limit_positive_flaps)
            if derived.load_factor > limit:
                diagnostics.events.append(
                    f"OVER-G {derived.load_factor:.1f} g above {limit:.1f} g limit"
                )
            elif derived.load_factor < self.load_limit_negative:
                diagnostics.events.append(f"NEGATIVE-G {derived.load_factor:.1f} g")

        if aero is not None and aero.stalled:
            diagnostics.events.append("STALL")

    # -- reset -------------------------------------------------------------

    def reset(self, state: State, controls: Controls | None = None) -> None:
        self.state = state
        self.controls = controls or Controls()
        self.gear.reset()
        self.propulsion.reset()
        self._gust_body = np.zeros(3)
        self._wind_ned = np.zeros(3)
        self._frozen_thrust = np.zeros(3)
        self._frozen_thrust_moment = np.zeros(3)
        self._mass_properties = self.mass_model.compute()

        # Publish a first derived view so nothing reads zeros before step one:
        # the reference implementation left airspeed unpublished until after
        # the first integration step, which showed up as a needle that flicked
        # to zero on frame one of every flight.
        air = self.atmosphere.sample(state.altitude)
        _, _, snapshot = self._forces(
            state.x, self.controls, air, self._mass_properties, self._mass_properties.cg
        )
        self._publish(state, air, self._mass_properties, snapshot, Diagnostics())

        # Establish contact state from the real strut geometry, so a runway
        # start does not report a touchdown on its first step.
        self._was_airborne = not state.on_ground
