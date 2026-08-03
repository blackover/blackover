"""Mass, centre of gravity and inertia as functions of loading.

Fuel tanks are point masses at fixed body stations. Tank inertia about its own
axes is neglected and only the parallel-axis transfer term is retained; for a
transport the neglected term is two to three orders of magnitude smaller.

There is no fuel slosh model and fuel does not migrate within a tank under
acceleration, so lateral dynamics during rapid manoeuvring with partly filled
tanks are not represented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.units import to_si


@dataclass
class Tank:
    """One fuel tank: a point mass at a fixed body station."""

    name: str
    capacity: float  # kg
    quantity: float  # kg
    position: np.ndarray = field(default_factory=lambda: np.zeros(3))

    @property
    def fraction(self) -> float:
        return self.quantity / self.capacity if self.capacity > 0.0 else 0.0


@dataclass
class MassProperties:
    """Current mass state, in body axes about the reference point."""

    mass: float
    cg: np.ndarray
    inertia: np.ndarray
    inertia_inverse: np.ndarray
    fuel_mass: float = 0.0
    payload_mass: float = 0.0
    empty_mass: float = 0.0


class MassModel:
    """Assembles mass properties from empty aircraft, payload and fuel."""

    def __init__(self, model) -> None:
        self.empty_mass = model.get("mass_properties", "empty_mass")
        self.max_takeoff_mass = model.get("mass_properties", "max_takeoff_mass")
        self.max_payload = model.get("mass_properties", "max_payload", 0.0)

        inertia = model.raw("mass_properties", "inertia", {})
        self.ixx = to_si(inertia["ixx"])
        self.iyy = to_si(inertia["iyy"])
        self.izz = to_si(inertia["izz"])
        self.ixz = to_si(inertia.get("ixz", 0.0))

        cg = model.raw("mass_properties", "empty_cg", {})
        self.empty_cg = np.array(
            [to_si(cg.get("x", 0.0)), to_si(cg.get("y", 0.0)), to_si(cg.get("z", 0.0))]
        )

        payload_cg = model.raw("mass_properties", "payload_cg", {})
        self.payload_cg = np.array(
            [
                to_si(payload_cg.get("x", self.empty_cg[0])),
                to_si(payload_cg.get("y", 0.0)),
                to_si(payload_cg.get("z", 0.0)),
            ]
        )

        self.cg_forward_limit = model.get("mass_properties", "cg_forward_limit", -1.0)
        self.cg_aft_limit = model.get("mass_properties", "cg_aft_limit", 1.0)

        self.tanks: list[Tank] = []
        for spec in model.raw("mass_properties", "tanks", []) or []:
            position = spec.get("position", {})
            self.tanks.append(
                Tank(
                    name=str(spec.get("name", f"tank{len(self.tanks)}")),
                    capacity=to_si(spec.get("capacity", 0.0)),
                    quantity=0.0,
                    position=np.array(
                        [
                            to_si(position.get("x", 0.0)),
                            to_si(position.get("y", 0.0)),
                            to_si(position.get("z", 0.0)),
                        ]
                    ),
                )
            )

        if not self.tanks:
            capacity = model.get("mass_properties", "fuel_capacity", 0.0)
            self.tanks = [Tank("main", capacity, 0.0, self.empty_cg.copy())]

        self.payload_mass = 0.0

    # ---------------------------------------------------------------------

    @property
    def fuel_capacity(self) -> float:
        return sum(tank.capacity for tank in self.tanks)

    @property
    def fuel_mass(self) -> float:
        return sum(tank.quantity for tank in self.tanks)

    def set_fuel(self, total: float) -> None:
        """Distribute a total fuel load across tanks proportionally."""
        capacity = self.fuel_capacity
        total = max(0.0, min(total, capacity))
        if capacity <= 0.0:
            return
        share = total / capacity
        for tank in self.tanks:
            tank.quantity = tank.capacity * share

    def set_payload(self, mass: float) -> None:
        self.payload_mass = max(0.0, mass)

    def burn(self, mass: float) -> float:
        """Draw ``mass`` kg from the tanks, returning what was actually drawn.

        Tanks are drained together rather than in a sequence, because a
        scheduled feed order is a systems behaviour and this is the mass model.
        """
        available = self.fuel_mass
        drawn = min(max(0.0, mass), available)
        if available <= 0.0:
            return 0.0
        share = drawn / available
        for tank in self.tanks:
            tank.quantity -= tank.quantity * share
        return drawn

    @property
    def fuel_exhausted(self) -> bool:
        return self.fuel_mass <= 1.0e-6

    # ---------------------------------------------------------------------

    def compute(self) -> MassProperties:
        """Current mass, CG and inertia tensor about the CG."""
        mass = self.empty_mass + self.payload_mass + self.fuel_mass

        moment = self.empty_mass * self.empty_cg + self.payload_mass * self.payload_cg
        for tank in self.tanks:
            moment = moment + tank.quantity * tank.position
        cg = moment / mass if mass > 0.0 else self.empty_cg.copy()

        # Base tensor is quoted about the empty CG; transfer it to the current
        # CG and add each point mass's own transfer term (Steiner).
        inertia = np.array(
            [
                [self.ixx, 0.0, -self.ixz],
                [0.0, self.iyy, 0.0],
                [-self.ixz, 0.0, self.izz],
            ]
        )

        inertia = inertia + _parallel_axis(self.empty_mass, self.empty_cg - cg)
        inertia = inertia + _parallel_axis(self.payload_mass, self.payload_cg - cg)
        for tank in self.tanks:
            inertia = inertia + _parallel_axis(tank.quantity, tank.position - cg)

        return MassProperties(
            mass=mass,
            cg=cg,
            inertia=inertia,
            inertia_inverse=np.linalg.inv(inertia),
            fuel_mass=self.fuel_mass,
            payload_mass=self.payload_mass,
            empty_mass=self.empty_mass,
        )

    def cg_within_limits(self, cg_x: float) -> bool:
        """True when the CG lies inside the declared envelope.

        Bounded by min/max rather than by forward/aft directly, so the check
        holds whichever sign convention the data author had in mind -- in body
        axes the forward limit is the numerically *larger* x, which is the
        opposite of a loading sheet and reliably catches people out.
        """
        low = min(self.cg_forward_limit, self.cg_aft_limit)
        high = max(self.cg_forward_limit, self.cg_aft_limit)
        return low <= cg_x <= high


def _parallel_axis(mass: float, offset: np.ndarray) -> np.ndarray:
    """Steiner transfer term for a point mass at ``offset`` from the axis."""
    if mass <= 0.0:
        return np.zeros((3, 3))
    r2 = float(offset @ offset)
    return mass * (r2 * np.eye(3) - np.outer(offset, offset))
