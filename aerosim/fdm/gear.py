"""Landing gear ground reaction.

Each strut is an independent non-linear spring-damper with tanh-regularised
Coulomb tyre friction. There is no oleo gas curve, no tyre relaxation length,
no anti-skid, and no strut-to-strut hydraulic coupling, so touchdown loads are
indicative rather than structural.

Numerical note
--------------
Stiff struts with an explicit integrator are the classic source of ground
bounce. Stiffness is therefore specified as a static deflection under the
aircraft's own weight and damping as a fraction of critical, which places the
ground mode near 3 Hz -- comfortably inside a 100 Hz kernel step. Friction is
regularised with tanh over a 0.3 m/s velocity scale so a parked aircraft does
not chatter between positive and negative friction. The cost of that
regularisation is that below roughly 0.3 m/s the tyre force fades smoothly to
zero rather than holding statically, so an aircraft parked on a slope creeps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.frames import cross3, dcm_body_to_ned, dcm_ned_to_body
from ..core.units import to_si

FRICTION_VELOCITY_SCALE = 0.3  # m/s


@dataclass
class Strut:
    name: str
    position: np.ndarray  # body axes, from the reference point
    stiffness: float  # N/m
    damping: float  # N s/m
    max_travel: float  # m
    steerable: bool = False
    braked: bool = False
    compression: float = 0.0
    on_ground: bool = False
    load: float = 0.0


@dataclass
class GearOutput:
    force_body: np.ndarray = field(default_factory=lambda: np.zeros(3))
    moment_body: np.ndarray = field(default_factory=lambda: np.zeros(3))
    any_contact: bool = False
    max_load: float = 0.0
    weight_on_wheels: bool = False


class GearModel:
    """Ground reaction from a set of independent struts."""

    def __init__(self, model, terrain=None, terrain_elevation: float = 0.0) -> None:
        # Ground height comes from the terrain when one is supplied, so the
        # wheels stand on the surface that is drawn rather than on a plane that
        # merely happens to be near it. Without one it is a single flat plane,
        # which is what every scenario before terrain existed assumed.
        self.terrain = terrain
        self.terrain_elevation = terrain_elevation

        self.rolling_friction = model.get("landing_gear", "rolling_friction", 0.02)
        self.brake_friction = model.get("landing_gear", "brake_friction", 0.40)
        self.side_friction = model.get("landing_gear", "side_friction", 0.65)
        self.max_steer_angle = model.get("landing_gear", "max_steer_angle", math.radians(60.0))

        mtom = model.get("mass_properties", "max_takeoff_mass")
        static_deflection = model.get("landing_gear", "static_deflection", 0.20)
        damping_ratio = model.get("landing_gear", "damping_ratio", 0.55)

        self.struts: list[Strut] = []
        specs = model.raw("landing_gear", "gear", []) or []
        for spec in specs:
            position = spec.get("position", {})
            share = float(spec.get("load_share", 1.0 / max(1, len(specs))))

            # Stiffness from the static deflection this strut's share of the
            # aircraft weight should produce, rather than a bare N/m nobody can
            # sanity-check by eye.
            supported = mtom * share
            stiffness = supported * 9.80665 / max(static_deflection, 1.0e-3)
            damping = 2.0 * damping_ratio * math.sqrt(stiffness * supported)

            self.struts.append(
                Strut(
                    name=str(spec.get("name", f"gear{len(self.struts)}")),
                    position=np.array(
                        [
                            to_si(position.get("x", 0.0)),
                            to_si(position.get("y", 0.0)),
                            to_si(position.get("z", 2.0)),
                        ]
                    ),
                    stiffness=stiffness,
                    damping=damping,
                    max_travel=to_si(spec.get("max_travel", 0.45)),
                    steerable=bool(spec.get("steerable", False)),
                    braked=bool(spec.get("braked", False)),
                )
            )

    def ground_height(self, north: float, east: float) -> float:
        """Terrain elevation under a point, or the flat plane if there is none."""
        if self.terrain is None:
            return self.terrain_elevation
        return self.terrain.height_at(north, east)

    @property
    def lowest_point(self) -> float:
        """Body-axis z of the lowest wheel, used to sit the aircraft on ground."""
        if not self.struts:
            return 0.0
        return max(float(strut.position[2]) for strut in self.struts)

    def compute(
        self,
        *,
        position_ned: np.ndarray,
        velocity_body: np.ndarray,
        quaternion: np.ndarray,
        rates: np.ndarray,
        gear_down: bool,
        brake: float,
        steering: float,
    ) -> GearOutput:
        """Ground reaction force and moment in body axes."""
        output = GearOutput()
        if not gear_down:
            return output

        body_to_ned = dcm_body_to_ned(quaternion)
        ned_to_body = dcm_ned_to_body(quaternion)
        altitude = -float(position_ned[2])

        for strut in self.struts:
            # Where this wheel is, in NED.
            offset_ned = body_to_ned @ strut.position
            # Each wheel is tested against the ground under *that wheel*. On a
            # slope the mains touch before the nose, which is the behaviour
            # that makes a landing on rising ground feel different.
            ground = self.ground_height(
                float(position_ned[0]) + float(offset_ned[0]),
                float(position_ned[1]) + float(offset_ned[1]),
            )
            wheel_height = altitude - float(offset_ned[2]) - ground

            if wheel_height >= 0.0:
                strut.compression = 0.0
                strut.on_ground = False
                strut.load = 0.0
                continue

            compression = min(-wheel_height, strut.max_travel)
            strut.compression = compression
            strut.on_ground = True

            # Velocity of this wheel, body axes, including the rotation term.
            wheel_velocity_body = velocity_body + cross3(rates, strut.position)
            wheel_velocity_ned = body_to_ned @ wheel_velocity_body
            sink_rate = float(wheel_velocity_ned[2])  # positive downwards

            # Asymmetric damping: an oleo resists compression harder than it
            # resists extension, which is what stops the aircraft bouncing back
            # up with the energy it just absorbed on touchdown.
            damper = strut.damping * sink_rate
            if sink_rate < 0.0:
                damper *= 0.4
            normal = max(0.0, strut.stiffness * compression + damper)
            strut.load = normal
            output.max_load = max(output.max_load, normal)

            # Ground-plane velocity in NED, then the rolling direction.
            ground_velocity = np.array(
                [float(wheel_velocity_ned[0]), float(wheel_velocity_ned[1]), 0.0]
            )

            # Heading of this wheel: aircraft heading plus steering if steerable.
            heading_vector_body = np.array([1.0, 0.0, 0.0])
            if strut.steerable:
                steer = max(-1.0, min(1.0, steering)) * self.max_steer_angle
                heading_vector_body = np.array([math.cos(steer), math.sin(steer), 0.0])
            roll_dir_ned = body_to_ned @ heading_vector_body
            roll_dir_ned[2] = 0.0
            norm = float(np.linalg.norm(roll_dir_ned))
            if norm > 1.0e-6:
                roll_dir_ned /= norm
            else:
                roll_dir_ned = np.array([1.0, 0.0, 0.0])

            side_dir_ned = np.array([-roll_dir_ned[1], roll_dir_ned[0], 0.0])

            v_roll = float(ground_velocity @ roll_dir_ned)
            v_side = float(ground_velocity @ side_dir_ned)

            mu_roll = self.rolling_friction
            if strut.braked:
                mu_roll += self.brake_friction * max(0.0, min(1.0, brake))

            # tanh regularisation: smooth through zero so a stationary aircraft
            # does not oscillate between +mu and -mu at the step rate.
            f_roll = -normal * mu_roll * math.tanh(v_roll / FRICTION_VELOCITY_SCALE)
            f_side = -normal * self.side_friction * math.tanh(
                v_side / FRICTION_VELOCITY_SCALE
            )

            force_ned = (
                np.array([0.0, 0.0, -normal])
                + f_roll * roll_dir_ned
                + f_side * side_dir_ned
            )
            force_body = ned_to_body @ force_ned

            output.force_body = output.force_body + force_body
            output.moment_body = output.moment_body + cross3(strut.position, force_body)
            output.any_contact = True

        output.weight_on_wheels = output.any_contact
        return output

    def reset(self) -> None:
        for strut in self.struts:
            strut.compression = 0.0
            strut.on_ground = False
            strut.load = 0.0
