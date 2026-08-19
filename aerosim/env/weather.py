"""Cloud, precipitation, icing and surface condition.

The wind field already covers what the air is *doing*; this covers what is
*in* it, and what that does to the aircraft. Three couplings, all of them
physical rather than cosmetic:

* **Cloud and precipitation reduce visibility.** Inside cloud the visibility
  collapses to a few tens of metres, which is what makes an instrument
  approach an instrument approach rather than a visual one with grey paint.
* **Airframe icing costs lift and buys drag.** Accretion is gated on the
  *recovery* temperature rather than the static one, so a fighter at Mach 1
  does not collect ice in air that would ice a transport -- kinetic heating
  keeps the leading edge above freezing, and that falls out of the total
  temperature rather than being special-cased.
* **A contaminated runway has less friction.** Braking and cornering scale
  with the surface state, and loose contaminants add rolling resistance.
  This changes take-off distance, landing distance and how well the autoland
  rollout tracks the centreline.

Everything here is *indicative*. There is no drop-size distribution, no
liquid water content, no accretion geometry, no hydroplaning model and no
runway drainage. ``docs/known_limitations.md`` says so in section 13.
"""

from __future__ import annotations

from dataclasses import dataclass

# Static air temperature is not what an airframe feels. The recovery
# temperature includes the adiabatic rise from bringing the flow to rest, and
# it is why fast aircraft do not ice in air that would ice a slow one.
RECOVERY_FACTOR = 0.89
GAMMA_MINUS_1_OVER_2 = 0.2  # (gamma - 1) / 2 for gamma = 1.4

KELVIN = 273.15

# Icing occurs in a liquid-water window: above freezing there is no ice, and
# far below it the cloud is already glaciated and the crystals bounce off.
ICING_WARM_LIMIT = 0.0  # degC
ICING_COLD_LIMIT = -20.0  # degC
ICING_PEAK = -6.0  # degC, where supercooled liquid water is most abundant

# Seconds of continuous flight in the worst case to reach full contamination,
# at 100 m/s. Chosen so icing is a problem to manage on the timescale of an
# approach rather than a curiosity or an instant death.
ICING_TIME_CONSTANT = 240.0
SHED_TIME_CONSTANT = 150.0  # out of the window, or with anti-ice on

# What full contamination costs. Ice on a leading edge does three things:
# it thickens and roughens the section, which costs maximum lift and moves
# the stall to a lower angle, and it adds pressure drag.
ICE_CL_LOSS = 0.30  # fraction of CL lost at full contamination
ICE_ALPHA_LOSS = 0.35  # fraction of stall angle lost
ICE_CD0 = 0.030  # added parasite drag coefficient
ICE_MASS_FRACTION = 0.010  # of empty mass, at full contamination


@dataclass(frozen=True)
class Precipitation:
    """One precipitation type: how much it obscures, and whether it is frozen."""

    name: str
    label: str
    visibility_factor: float  # multiplies the reported visibility
    frozen: bool = False
    intensity: float = 0.0  # 0 none .. 1 heavy, drives the visual density


PRECIPITATION: dict[str, Precipitation] = {
    "none": Precipitation("none", "none", 1.00),
    "drizzle": Precipitation("drizzle", "drizzle", 0.55, intensity=0.25),
    "rain": Precipitation("rain", "rain", 0.35, intensity=0.55),
    "heavy_rain": Precipitation("heavy_rain", "heavy rain", 0.15, intensity=1.00),
    "snow": Precipitation("snow", "snow", 0.20, frozen=True, intensity=0.70),
    "heavy_snow": Precipitation(
        "heavy_snow", "heavy snow", 0.09, frozen=True, intensity=1.00
    ),
}

# The lowest visibility any precipitation will produce on its own. Below this
# the approach is a Cat III problem and this simulator does not have Cat III
# equipment, so the pre-flight check warns rather than the weather pretending.
PRECIPITATION_FLOOR = 350.0  # m

# Visibility inside cloud. Not zero: the wingtip is still there.
IN_CLOUD_VISIBILITY = 45.0  # m


@dataclass(frozen=True)
class RunwayState:
    """Surface condition, as multipliers on the gear model's own coefficients.

    Braking action figures rather than measured mu: the gear model already
    declares a dry braking coefficient, and these scale it. Loose contaminants
    also *add* rolling resistance, because the wheel has to displace them.
    """

    name: str
    label: str
    braking: float  # multiplies brake_friction
    cornering: float  # multiplies side_friction
    rolling: float  # multiplies rolling_friction


RUNWAY_STATES: dict[str, RunwayState] = {
    "dry": RunwayState("dry", "dry", 1.00, 1.00, 1.0),
    "damp": RunwayState("damp", "damp", 0.90, 0.95, 1.0),
    "wet": RunwayState("wet", "wet", 0.62, 0.78, 1.1),
    "standing_water": RunwayState(
        "standing_water", "standing water", 0.42, 0.60, 1.9
    ),
    "snow": RunwayState("snow", "compacted snow", 0.34, 0.50, 2.4),
    "ice": RunwayState("ice", "ice", 0.16, 0.28, 1.0),
}


def recovery_temperature(static_temperature: float, mach: float) -> float:
    """Temperature the airframe surface actually sees, in kelvin.

    The stagnation rise with a recovery factor for a turbulent boundary layer.
    At Mach 0.8 this is about 30 K above static, which is the whole reason a
    transport in the cruise does not ice in air far below freezing.
    """
    return static_temperature * (
        1.0 + RECOVERY_FACTOR * GAMMA_MINUS_1_OVER_2 * mach * mach
    )


def _bump(value: float, low: float, peak: float, high: float) -> float:
    """A smooth 0-1-0 window: zero outside [low, high], one at ``peak``."""
    if value <= low or value >= high:
        return 0.0
    if value < peak:
        t = (value - low) / (peak - low)
    else:
        t = (high - value) / (high - peak)
    return t * t * (3.0 - 2.0 * t)


class Weather:
    """Cloud, precipitation and surface state for one flight.

    Constructed once from the pre-flight conditions and then read. Like the
    terrain it is shared rather than duplicated: the renderer draws the cloud
    deck this object describes, and the flight model ices up inside the same
    deck.
    """

    def __init__(
        self,
        *,
        precipitation: str = "none",
        cloud_cover: float = 0.35,
        cloud_base: float = 1500.0,
        cloud_thickness: float = 900.0,
        runway_state: str = "dry",
        visibility: float = 45000.0,
        field_elevation: float = 0.0,
    ) -> None:
        self.precipitation = PRECIPITATION[str(precipitation)]
        self.cloud_cover = max(0.0, min(1.0, float(cloud_cover)))
        self.cloud_base = max(field_elevation, float(cloud_base))
        self.cloud_thickness = max(0.0, float(cloud_thickness))
        self.runway = RUNWAY_STATES[str(runway_state)]
        self.reported_visibility = max(100.0, float(visibility))
        self.field_elevation = float(field_elevation)

    # -- cloud -------------------------------------------------------------

    @property
    def cloud_tops(self) -> float:
        return self.cloud_base + self.cloud_thickness

    @property
    def has_deck(self) -> bool:
        """True when the cover is solid enough to fly *into* rather than past.

        Below about six eighths the deck is broken, and an aircraft at that
        level spends most of its time between the clouds rather than in them.
        """
        return self.cloud_cover >= 0.75 and self.cloud_thickness > 1.0

    def in_cloud(self, altitude: float) -> bool:
        if not self.has_deck:
            return False
        return self.cloud_base <= altitude <= self.cloud_tops

    def ceiling(self) -> float:
        """Height of the cloud base above the field, or a large number."""
        if not self.has_deck:
            return float("inf")
        return self.cloud_base - self.field_elevation

    # -- visibility --------------------------------------------------------

    def visibility_at(self, altitude: float) -> float:
        """Slant visibility at an altitude, after cloud and precipitation."""
        if self.in_cloud(altitude):
            return IN_CLOUD_VISIBILITY
        reduced = self.reported_visibility * self.precipitation.visibility_factor
        if self.precipitation.visibility_factor < 1.0:
            reduced = max(reduced, PRECIPITATION_FLOOR)
        return reduced

    # -- icing -------------------------------------------------------------

    def icing_severity(
        self, *, altitude: float, temperature: float, mach: float
    ) -> float:
        """How hard the airframe is icing right now, 0 to 1.

        Requires visible moisture -- cloud or precipitation -- and a recovery
        temperature inside the liquid-water window. Returns 0 when either is
        missing, which is also what makes climbing out of the cloud the answer.
        """
        moisture = 0.0
        if self.in_cloud(altitude):
            moisture = 1.0
        elif self.precipitation.intensity > 0.0 and not self.precipitation.frozen:
            # Freezing rain below the deck: less water than cloud, but it is
            # all liquid and it all sticks.
            moisture = 0.7 * self.precipitation.intensity
        if moisture <= 0.0:
            return 0.0

        surface = recovery_temperature(temperature, mach) - KELVIN
        return moisture * _bump(surface, ICING_COLD_LIMIT, ICING_PEAK, ICING_WARM_LIMIT)

    @staticmethod
    def ice_rate(severity: float, vtas: float, anti_ice: bool) -> float:
        """Rate of change of the contamination fraction, per second.

        Accretion scales with airspeed because a wing sweeps out more air per
        second the faster it goes; shedding does not.
        """
        if anti_ice or severity <= 0.0:
            return -1.0 / SHED_TIME_CONSTANT
        catch = max(0.25, min(3.0, vtas / 100.0))
        return severity * catch / ICING_TIME_CONSTANT

    # -- surface -----------------------------------------------------------

    def describe(self) -> str:
        parts = []
        if self.precipitation.intensity > 0.0:
            parts.append(self.precipitation.label)
        if self.has_deck:
            parts.append(f"overcast {self.ceiling():,.0f} m")
        elif self.cloud_cover > 0.05:
            parts.append(f"cloud {self.cloud_cover * 8:.0f}/8")
        if self.runway.name != "dry":
            parts.append(f"runway {self.runway.label}")
        return ", ".join(parts) if parts else "clear"


def default_runway_state(precipitation: str, surface_temperature: float) -> str:
    """The surface state a given weather would plausibly produce.

    Only a *default*: it can rain on a well-drained runway and it can be icy
    under a clear sky, so the setting stays independent of the weather and the
    pre-flight check merely points out when the two disagree.
    """
    spec = PRECIPITATION[str(precipitation)]
    if spec.intensity <= 0.0:
        return "ice" if surface_temperature < KELVIN - 3.0 else "dry"
    if spec.frozen:
        return "snow"
    if surface_temperature < KELVIN:
        return "ice"
    return "standing_water" if spec.intensity >= 0.9 else "wet"
