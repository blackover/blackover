"""Subsystem lifecycle and execution order.

The order below is fixed and documented. Actuators are limited before the
dynamics see them, and systems degrade before the actuators are limited, so a
hydraulic failure reaches the aircraft's handling by the only route it has:
pressure sets actuator rate, rate sets how fast a surface moves, and the
handling change falls out. No special-case logic connects those steps.

    1. failure triggers
    2. pilot or autopilot produce commands
    3. aircraft systems: fuel -> electrical -> hydraulic
    4. actuators apply rate, position and failure limits
    5. flight dynamics integrate one step (RK4), updating mass en route
    6. health checks: numerical, envelope, terrain
    7. event log

Steps 1-4 and 6-7 update once per kernel step. Only step 5 runs at RK4
accuracy; the discrete subsystems are held constant across the four stages
because they contain rate limiters and discrete logic that are not
differentiable. This is a standard co-simulation split.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..control.actuators import ActuatorFailure, ControlSurfaces
from ..control.autoflight import AutoFlight
from ..control.autoflight import Runway as GuidanceRunway
from ..control.autopilot import Autopilot, LateralMode, ThrustMode, VerticalMode
from ..env.atmosphere import Atmosphere
from ..env.terrain import Terrain
from ..env.weather import ICE_MASS_FRACTION, Weather
from ..env.wind import TURBULENCE_PRESETS, WindField
from ..fdm.fdm import Controls, FlightDynamics
from ..fdm.trim import apply_trim, trim_level_flight
from ..game.config import FailureMode, SimConditions, StartMode
from .clock import Clock
from .state import State
from .units import deg, ft, to_ft, to_kt


@dataclass
class Event:
    time: float
    severity: str  # INFO, CAUTION, WARNING
    message: str


@dataclass
class PilotInput:
    """What the pilot is asking for, before any limiting.

    Held separately from the surface positions so the panel can show the
    difference: a jammed or rate-limited surface is visible as a divergence
    between what was asked for and what happened.
    """

    pitch: float = 0.0  # -1 nose down .. +1 nose up
    roll: float = 0.0  # -1 left .. +1 right
    yaw: float = 0.0  # -1 nose left .. +1 nose right
    throttle: float = 0.0
    flap: float = 0.0
    speedbrake: float = 0.0
    brake: float = 0.0
    gear_down: bool = True
    afterburner: bool = False
    anti_ice: bool = False


class Simulation:
    """One aircraft, one environment, one deterministic clock."""

    def __init__(self, model, conditions: SimConditions) -> None:
        self.model = model
        self.conditions = conditions
        self.clock = Clock(dt=conditions.dt)

        self.atmosphere = Atmosphere(conditions.temperature_offset, conditions.qnh)
        self.wind = self._build_wind(conditions)
        self.terrain = Terrain(
            seed=conditions.seed,
            profile=conditions.terrain,
            field_elevation=conditions.field_elevation,
        )
        # Shared with the renderer, exactly like the terrain: the deck drawn
        # on screen is the deck the aircraft ices up inside, and the runway
        # the gear brakes on is the one the weather says it is.
        self.weather = Weather(
            precipitation=conditions.precipitation,
            cloud_cover=conditions.cloud_cover,
            cloud_base=conditions.cloud_base,
            cloud_thickness=conditions.cloud_thickness,
            runway_state=conditions.runway_state,
            visibility=conditions.visibility,
            field_elevation=conditions.field_elevation,
        )

        self.fdm = FlightDynamics(
            model,
            atmosphere=self.atmosphere,
            wind=self.wind,
            field_elevation=conditions.field_elevation,
            terrain=self.terrain,
        )
        self.fdm.gear.surface = self.weather.runway
        self.surfaces = ControlSurfaces(model)
        self.autopilot = Autopilot(model)
        self.autoflight = AutoFlight(
            model,
            GuidanceRunway(
                heading=conditions.heading,
                elevation=conditions.field_elevation,
            ),
        )

        self.pilot = PilotInput()
        self.throttle = 0.0
        self.events: list[Event] = []
        self.crashed = False
        self.crash_reason = ""
        self.paused = False

        self.hydraulic_pressure = 1.0
        self.electrical_ok = True
        self.fuel_leak_rate = 0.0
        self._failure_armed = conditions.failure != FailureMode.NONE
        self._failure_fired = False
        self.pitch_trim = 0.0
        self._autopilot_elevator = 0.0
        self._recent: dict[str, float] = {}

        self.initialise()

    # -- construction ------------------------------------------------------

    @staticmethod
    def _build_wind(conditions: SimConditions) -> WindField:
        intensity = TURBULENCE_PRESETS.get(conditions.turbulence, 0.0)
        if conditions.wind_shear and conditions.wind_speed > 0.4:
            return WindField.sheared(
                surface_speed=conditions.wind_speed,
                surface_direction=conditions.wind_direction,
                aloft_speed=conditions.wind_speed * 2.4,
                aloft_direction=conditions.wind_direction + deg(35.0),
                aloft_altitude=ft(18000.0),
                turbulence_intensity=intensity,
                seed=conditions.seed,
            )
        return WindField.uniform(
            conditions.wind_speed,
            conditions.wind_direction,
            turbulence_intensity=intensity,
            seed=conditions.seed,
        )

    def initialise(self) -> None:
        """Build the starting state from the pre-flight conditions."""
        conditions = self.conditions
        mass_model = self.fdm.mass_model

        self.fdm.initialise(
            fuel=conditions.fuel_fraction * mass_model.fuel_capacity,
            payload=conditions.payload_fraction * mass_model.max_payload,
        )

        # External stores are mass and parasite drag; the drag part is a
        # configuration, so it is applied once here rather than commanded.
        stores_cd = self.model.get("aerodynamics", "drag.cd_stores", 0.0)
        self.fdm.aero.extra_cd0 = stores_cd * conditions.payload_fraction

        self.surfaces.reset()
        self.autopilot.disengage()
        self.clock.reset()
        self.events.clear()
        self.crashed = False
        self.crash_reason = ""
        self.pitch_trim = 0.0

        if conditions.start_mode == StartMode.RUNWAY:
            self._initialise_runway()
        elif conditions.start_mode == StartMode.APPROACH:
            self._initialise_airborne(gamma=deg(-3.0), on_approach=True)
        else:
            self._initialise_airborne(gamma=0.0, on_approach=False)

        self.log("INFO", f"{self.model.display_name} ready, seed {conditions.seed}")
        for warning in conditions.validate(self.model):
            self.log("CAUTION", warning)

    def _initialise_runway(self) -> None:
        conditions = self.conditions
        state = State.from_conditions(
            altitude=conditions.field_elevation,
            vtas=0.0,
            heading=conditions.heading,
        )
        self.fdm.place_on_ground(state)
        state.on_ground = True

        self.pilot = PilotInput(gear_down=True, flap=0.25 if not conditions.is_military else 0.5)
        self.throttle = 0.0
        self.surfaces.flap.position = self.pilot.flap
        self.surfaces.gear_position = 1.0

        controls = Controls(gear_down=True, flap=self.pilot.flap)
        self.fdm.reset(state, controls)
        self.log("INFO", "lined up on the runway, brakes off, cleared for take-off")

    def _initialise_airborne(self, *, gamma: float, on_approach: bool) -> None:
        conditions = self.conditions
        altitude = max(conditions.altitude, conditions.field_elevation + ft(500.0))
        if on_approach:
            # Six miles on a three degree path puts the threshold ahead and
            # below; the aircraft is configured to land, not to cruise.
            altitude = conditions.field_elevation + ft(1900.0)

        flap = 1.0 if on_approach else 0.0
        gear_down = on_approach

        if on_approach:
            # An approach is flown at a speed the aircraft's weight and
            # configuration dictate, not at the cruise speed selected on the
            # setup screen. Using the latter puts a narrow-body over the
            # threshold at 280 kt with full flap -- well past VFE, and no trim
            # solution exists that is not a dive.
            air = self.atmosphere.sample(altitude)
            mass = self.fdm.mass_model.compute().mass
            vref = 1.3 * self.fdm.aero.stall_speed(mass, air.density, flap=1.0)
            vtas = vref
            self.log("INFO", f"approach speed computed as Vref {to_kt(vref):.0f} kt")
        else:
            vtas = self.atmosphere.tas_from_cas(conditions.airspeed, altitude)

        trim = trim_level_flight(
            self.fdm,
            altitude=altitude,
            vtas=vtas,
            gamma=gamma,
            flap=flap,
            gear_down=gear_down,
        )

        north = -1852.0 * 6.0 * math.cos(conditions.heading) if on_approach else 0.0
        east = -1852.0 * 6.0 * math.sin(conditions.heading) if on_approach else 0.0

        controls = Controls(gear_down=gear_down, flap=flap)
        apply_trim(
            self.fdm,
            trim,
            vtas=vtas,
            altitude=altitude,
            heading=conditions.heading,
            north=north,
            east=east,
            controls=controls,
        )

        self.pilot = PilotInput(gear_down=gear_down, flap=flap)
        self.throttle = trim.throttle
        self.surfaces.flap.position = flap
        self.surfaces.gear_position = 1.0 if gear_down else 0.0
        self.surfaces.elevator.position = trim.elevator
        self.pitch_trim = trim.elevator

        if not trim.converged:
            self.log("CAUTION", "trim did not converge; the start is not in equilibrium")
        self.log(
            "INFO",
            f"airborne start, trimmed at {to_kt(self.fdm.state.derived.vcas):.0f} kt CAS, "
            f"{to_ft(altitude):,.0f} ft",
        )

    # -- logging -----------------------------------------------------------

    def log(
        self,
        severity: str,
        message: str,
        throttle_seconds: float = 4.0,
        key: str | None = None,
    ) -> None:
        """Record an event, suppressing repeats of the same kind.

        A stall lasting ten seconds is one event, not a thousand identical
        lines that push everything else off the screen. ``key`` groups messages
        whose text varies but whose meaning does not -- an envelope caution
        quoting a Mach number is never textually identical twice running, so
        keying on the message alone throttles nothing at all.
        """
        now = self.clock.time
        bucket = key or message
        last = self._recent.get(bucket)
        if last is not None and now - last < throttle_seconds:
            return
        self._recent[bucket] = now
        self.events.append(Event(now, severity, message))
        if len(self.events) > 400:
            del self.events[:200]

    # -- the step ----------------------------------------------------------

    def step(self) -> None:
        """Advance exactly one fixed step through the documented order."""
        if self.crashed or self.paused:
            return

        dt = self.clock.dt
        derived = self.fdm.state.derived

        # 1 -- failure triggers
        self._evaluate_failures()

        # 1b -- autoflight moves the autopilot's targets, before the autopilot
        # reads them. It commands through the same cascade the pilot does; it
        # is not a second set of control laws.
        self.autoflight.update(self, dt)
        self._drain_autoflight()

        # 2 -- pilot or autopilot produce commands
        elevator_cmd, aileron_cmd, rudder_cmd, throttle_cmd = self._commands(dt, derived)

        # 2b -- weather acting on the airframe. Before the systems update so
        # the added mass and drag are in place for this step's integration.
        self._update_icing(dt, derived)

        # 3 -- aircraft systems: fuel -> electrical -> hydraulic
        self._update_systems(dt)
        self.surfaces.set_hydraulic_pressure(self.hydraulic_pressure)

        # 4 -- actuators apply rate, position and failure limits
        self.surfaces.update(
            dt,
            elevator=elevator_cmd,
            aileron=aileron_cmd,
            rudder=rudder_cmd,
            flap=self.pilot.flap,
            speedbrake=self.pilot.speedbrake,
            gear_down=self.pilot.gear_down,
        )

        # 5 -- flight dynamics integrate one step
        controls = Controls(
            elevator=self.surfaces.elevator.position,
            aileron=self.surfaces.aileron.position,
            rudder=self.surfaces.rudder.position,
            throttle=throttle_cmd,
            flap=self.surfaces.flap.position,
            speedbrake=self.surfaces.speedbrake.position,
            brake=self.pilot.brake,
            # The nosewheel follows the rudder pedals, not the stick. Steering
            # with aileron is more discoverable on a keyboard and wrong, and a
            # simulator that quietly rewires a control to be easier is no
            # longer showing you the aircraft.
            steering=self.pilot.yaw if self.fdm.state.on_ground else 0.0,
            gear_down=self.surfaces.gear_effective_down,
            afterburner=self.pilot.afterburner,
        )
        diagnostics = self.fdm.step(dt, controls)

        # 6 -- health checks
        self._health_checks(diagnostics)

        # 7 -- clock and event log
        self.clock.advance()

    def _commands(self, dt: float, derived):
        """Turn pilot and autopilot demands into surface commands."""
        pilot = self.pilot

        # Pilot pull is nose up, and nose up is NEGATIVE elevator: a positive
        # deflection is trailing edge down, which lifts the tail and pitches
        # the nose down. Same for the rudder, whose positive sense is trailing
        # edge left. The signs live here, once, rather than in the data.
        #
        # pitch_trim is the surface position that holds the trimmed attitude
        # hands off. Without it the stick returning to centre also returns the
        # elevator to zero, which is not neutral -- it is a pitch command.
        elevator = self.pitch_trim - pilot.pitch
        aileron = pilot.roll
        rudder = -pilot.yaw

        if self.conditions.flight_assist and not self.fdm.state.on_ground:
            # Rate damping only. It resists the aircraft's own oscillations and
            # leaves the pilot's command alone -- it is a damper, not a
            # controller, and it will happily let you stall.
            #
            # Each sign is the one that OPPOSES the measured rate. Getting a
            # damper's sign backwards does not produce a weak damper: it
            # produces positive feedback, and the aircraft departs in about a
            # second and a half.
            rates = self.fdm.state.rates
            speed_scale = min(1.0, derived.vtas / 90.0)
            elevator += 0.55 * float(rates[1]) * speed_scale  # nose-down rate -> nose-up surface
            aileron -= 0.28 * float(rates[0]) * speed_scale  # roll right -> left aileron
            rudder += 0.30 * float(rates[2]) * speed_scale  # yaw right -> left rudder

        throttle = self.throttle

        autopilot = self.autopilot.update(dt, derived, self.throttle)
        if autopilot.engaged:
            if self.autopilot.vertical is not VerticalMode.OFF and abs(pilot.pitch) < 0.02:
                elevator = autopilot.elevator
                self._autopilot_elevator = elevator
            elif abs(pilot.pitch) >= 0.02:
                self._disengage("pitch axis, pilot input")

            if self.autopilot.lateral is not LateralMode.OFF and abs(pilot.roll) < 0.02:
                aileron = autopilot.aileron
            elif abs(pilot.roll) >= 0.02:
                self._disengage("roll axis, pilot input")

            if self.autopilot.thrust is ThrustMode.SPEED:
                throttle = autopilot.throttle
                self.throttle = throttle

            if abs(pilot.yaw) < 0.02:
                rudder += autopilot.rudder

        return (
            max(-1.0, min(1.0, elevator)),
            max(-1.0, min(1.0, aileron)),
            max(-1.0, min(1.0, rudder)),
            max(0.0, min(1.0, throttle)),
        )

    def _disengage(self, reason: str) -> None:
        if self.autopilot.engaged:
            self.handover_trim()
            self.autopilot.disengage()
            self.log("CAUTION", f"autopilot disengaged: {reason}")

    def handover_trim(self) -> None:
        """Bumpless transfer: hand the pilot the surface the autopilot held.

        The autopilot's elevator is mostly integrator state. Dropping it and
        reverting to the stored trim steps the surface by however much that
        integrator had accumulated, which the pilot feels as a jolt at the
        exact moment they take over -- and which is worst during a manoeuvre,
        because that is when the integrator is furthest from the trim value.
        """
        if self.autopilot.vertical is not VerticalMode.OFF:
            self.pitch_trim = max(-0.9, min(0.9, self._autopilot_elevator))

    # -- systems -----------------------------------------------------------

    def _update_icing(self, dt: float, derived) -> None:
        """Accrete or shed airframe ice, and tell the pilot which is happening.

        The contamination fraction is the state; the aero model turns it into
        lost lift, a lower stall angle and added drag, and the mass model
        turns it into weight forward of the CG. Anti-ice sheds rather than
        prevents, because a boot or a hot leading edge removes ice that has
        already formed.
        """
        weather = self.weather
        severity = weather.icing_severity(
            altitude=derived.altitude,
            temperature=derived.temperature,
            mach=derived.mach,
        )
        rate = weather.ice_rate(severity, derived.vtas, self.pilot.anti_ice)

        previous = self.fdm.aero.ice
        ice = max(0.0, min(1.0, previous + rate * dt))
        self.fdm.aero.ice = ice
        self.fdm.mass_model.set_ice(
            ICE_MASS_FRACTION * self.fdm.mass_model.empty_mass * ice
        )

        # Announce on the way past each quarter, rising only. Announcing on
        # the way down as well turns a slow shed into a stream of messages
        # about a problem that is going away.
        for level, name in ((0.25, "light"), (0.50, "moderate"), (0.80, "severe")):
            if previous < level <= ice:
                self.log(
                    "CAUTION",
                    f"ICE {name}: stall speed up "
                    f"{(1.0 / math.sqrt(1.0 - 0.30 * ice) - 1.0) * 100:.0f} %"
                    + ("" if self.pilot.anti_ice else " -- anti-ice is off"),
                )
        if previous >= 0.05 > ice:
            self.log("INFO", "airframe clear of ice")

    def _update_systems(self, dt: float) -> None:
        """Fuel feeds electrical generation, which feeds hydraulic pressure."""
        if self.fuel_leak_rate > 0.0:
            self.fdm.mass_model.burn(self.fuel_leak_rate * dt)

        # Generators come online at idle, not at some fraction above it. The
        # threshold sits below IDLE_N1 so a healthy engine at idle powers the
        # aircraft, which is the whole point of an idle setting.
        running = [e for e in self.fdm.propulsion.engines if e.running and e.n1 > 0.15]
        self.electrical_ok = bool(running)

        if not self.electrical_ok:
            # Engine-driven pumps stop with the engines; pressure decays to
            # what a windmilling or standby source can hold.
            target = 0.25
        elif self.conditions.failure == FailureMode.HYDRAULIC and self._failure_fired:
            target = 0.30
        else:
            target = 1.0

        self.hydraulic_pressure += (target - self.hydraulic_pressure) * (
            1.0 - math.exp(-dt / 2.5)
        )

    def _evaluate_failures(self) -> None:
        conditions = self.conditions
        if not self._failure_armed or self.clock.time < conditions.failure_time:
            return

        self._failure_armed = False
        self._failure_fired = True
        failure = conditions.failure

        if failure == FailureMode.ENGINE_OUT:
            self.fdm.propulsion.fail_engine(0)
            count = len(self.fdm.propulsion.engines)
            self.log(
                "WARNING",
                "ENGINE 1 FAILURE"
                + (" -- single engine, you are now a glider" if count == 1 else ""),
            )
        elif failure == FailureMode.HYDRAULIC:
            self.log("WARNING", "HYDRAULIC PRESSURE LOSS -- control rates degraded")
        elif failure == FailureMode.ELEVATOR_JAM:
            self.surfaces.elevator.failure = ActuatorFailure.JAMMED
            self.log(
                "WARNING",
                f"ELEVATOR JAMMED at {self.surfaces.elevator.position:+.2f} -- "
                "pitch with thrust",
            )
        elif failure == FailureMode.FUEL_LEAK:
            self.fuel_leak_rate = self.fdm.mass_model.fuel_capacity / 900.0
            self.log("WARNING", "FUEL LEAK -- quantity falling")

    # -- health ------------------------------------------------------------

    def _health_checks(self, diagnostics) -> None:
        for message in diagnostics.events:
            severity = "WARNING" if message.startswith(("STALL", "OVER", "FUEL")) else "INFO"
            # Key on the message's first word. "OVER-G 2.9 g above 2.5 g limit"
            # is never textually identical twice running, so keying on the
            # whole message throttles nothing and the log fills with four
            # readings of the same exceedance.
            self.log(severity, message, key=message.split(" ", 1)[0])

        if diagnostics.out_of_envelope:
            self.log(
                "CAUTION",
                f"ENVELOPE: {diagnostics.envelope_reason} -- the model is outside "
                "its validated range here",
                throttle_seconds=10.0,
                key="envelope",
            )

        if diagnostics.crashed and not self.crashed:
            self.crashed = True
            self.crash_reason = diagnostics.crash_reason
            self.log("WARNING", f"SIMULATION ENDED: {diagnostics.crash_reason}")

    # -- convenience -------------------------------------------------------

    @property
    def time(self) -> float:
        return self.clock.time

    def _drain_autoflight(self) -> None:
        """Move the autoflight's announcements into the event log.

        Each message is its own throttle key. Grouping them -- by the word
        before the colon, say -- means every phase transition shares one key,
        and "flaps up" one second after "climbing to 8000 ft" is discarded as
        a repeat of a message it has nothing to do with.
        """
        for message in self.autoflight.drain_events():
            self.log("INFO", message)

    def engage_autoflight(self) -> None:
        """Hand the whole flight to the autoflight system."""
        self.autoflight.engage(self)
        self._drain_autoflight()

    def engage_autoland(self) -> None:
        """Route to the runway and land."""
        if not self.autoflight.engaged:
            self.autoflight.engage(self)
        self.autoflight.arm_landing(self)
        self._drain_autoflight()

    def disengage_autoflight(self) -> None:
        was_engaged = self.autoflight.engaged
        self.autoflight.disengage(self)
        self._drain_autoflight()
        if was_engaged:
            self.log("CAUTION", "autoflight disengaged, you have the aircraft")

    def engage_altitude_hold(self) -> None:
        self.autopilot.hold_altitude(self.fdm.state.derived.altitude)
        self.log("INFO", f"ALT hold at {to_ft(self.fdm.state.derived.altitude):,.0f} ft")

    def engage_heading_hold(self) -> None:
        self.autopilot.hold_heading(self.fdm.state.derived.yaw)
        self.log("INFO", f"HDG hold on {math.degrees(self.fdm.state.derived.yaw) % 360:03.0f}")

    def engage_speed_hold(self) -> None:
        self.autopilot.hold_speed(self.fdm.state.derived.vcas)
        self.log("INFO", f"SPD hold at {to_kt(self.fdm.state.derived.vcas):.0f} kt")
