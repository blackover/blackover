"""Aircraft geometry for the chase view.

Meshes are built in body axes -- x forward, y right, z down, metres, origin at
the aerodynamic reference point -- the same frame the flight model uses. That
means the rendered aircraft sits where the physics says it is: the wingtip that
touches the ground on screen is the wingtip the crash check tested, because
both read the same span out of the same data package.

The shapes are dimensioned from the aircraft's own geometry.yaml, so a data
package with a different span draws a different aeroplane without touching
this file.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class Facet:
    """One flat polygon in body axes, with its unlit colour."""

    points: np.ndarray  # (n, 3)
    colour: tuple[int, int, int]
    highlight: bool = False  # ignore lighting, e.g. for lights and markings


def _quad(a, b, c, d, colour, highlight=False) -> Facet:
    return Facet(np.array([a, b, c, d], dtype=float), colour, highlight)


def _tri(a, b, c, colour, highlight=False) -> Facet:
    return Facet(np.array([a, b, c], dtype=float), colour, highlight)


def _mirror_y(facets: list[Facet]) -> list[Facet]:
    """Reflect a list of facets about the plane of symmetry.

    The vertex order is reversed as well as mirrored: reflecting a polygon
    without reversing it flips its winding, and the lighting then treats the
    left wing as facing away from a sun the right wing faces.
    """
    out = []
    for facet in facets:
        points = facet.points.copy()
        points[:, 1] *= -1.0
        out.append(Facet(points[::-1].copy(), facet.colour, facet.highlight))
    return out


def _slab(
    profile: list[tuple[float, float]],
    half_thickness: float,
    colour: tuple[int, int, int],
) -> list[Facet]:
    """Extrude an x-z profile sideways into a thin solid.

    Used for fins. A zero-thickness polygon in the plane of symmetry is
    geometrically correct and renders as a bare line from directly astern,
    which is exactly where the chase camera sits.
    """
    left = [(x, -half_thickness, z) for x, z in profile]
    right = [(x, half_thickness, z) for x, z in profile]

    facets = [
        Facet(np.array(right, dtype=float), colour),
        Facet(np.array(left[::-1], dtype=float), colour),
    ]
    for i in range(len(profile)):
        j = (i + 1) % len(profile)
        facets.append(_quad(left[i], right[i], right[j], left[j], colour))
    return facets


def _tube(
    stations: list[tuple[float, float, float]],
    colour: tuple[int, int, int],
    segments: int = 8,
) -> list[Facet]:
    """A body of revolution through (x, radius, z_centre) stations."""
    facets: list[Facet] = []
    rings = []
    for x, radius, z_centre in stations:
        ring = []
        for i in range(segments):
            theta = 2.0 * math.pi * i / segments
            ring.append((x, radius * math.sin(theta), z_centre + radius * math.cos(theta)))
        rings.append(ring)

    for front, back in zip(rings, rings[1:]):
        for i in range(segments):
            j = (i + 1) % segments
            facets.append(_quad(front[i], front[j], back[j], back[i], colour))
    return facets


# --------------------------------------------------------------------------


def build_airliner(model) -> list[Facet]:
    """Twin-turbofan narrow-body, dimensioned from its data package."""
    span = model.get("geometry", "wing_span")
    area = model.get("geometry", "wing_area")
    length = model.get("geometry", "fuselage_length", 37.6)
    diameter = model.get("geometry", "fuselage_diameter", 3.95)
    sweep = model.get("geometry", "wing_sweep_quarter_chord", math.radians(25.0))
    dihedral = model.get("geometry", "wing_dihedral", math.radians(5.0))
    tail_arm = model.get("geometry", "tail_arm", 15.2)
    fin_height = model.get("geometry", "tail_height", 11.8) - diameter

    half_span = 0.5 * span
    taper = 0.24
    root_chord = 2.0 * area / (span * (1.0 + taper))
    tip_chord = root_chord * taper

    radius = 0.5 * diameter
    nose_x = 0.45 * length
    tail_x = nose_x - length

    hull = (240, 242, 246)
    wing = (222, 226, 234)
    accent = (28, 74, 140)
    engine = (198, 202, 210)
    dark = (52, 58, 70)

    facets: list[Facet] = []

    # -- fuselage --------------------------------------------------------
    facets += _tube(
        [
            (nose_x, 0.10, 0.30),
            (nose_x - 1.6, radius * 0.62, 0.10),
            (nose_x - 4.0, radius * 0.96, 0.0),
            (nose_x - 9.0, radius, 0.0),
            (tail_x + 12.0, radius, 0.0),
            (tail_x + 5.0, radius * 0.72, -0.75),
            (tail_x + 1.2, radius * 0.26, -1.55),
        ],
        hull,
        segments=10,
    )

    # Cheatline along the waterline, so roll is readable at a glance.
    for side in (1.0, -1.0):
        facets.append(
            _quad(
                (nose_x - 3.2, side * radius * 0.94, -0.35),
                (tail_x + 5.5, side * radius * 0.94, -0.95),
                (tail_x + 5.5, side * radius * 0.90, -0.35),
                (nose_x - 3.2, side * radius * 0.90, 0.05),
                accent,
            )
        )

    # -- wing ------------------------------------------------------------
    root_le, root_te = 0.25 * root_chord, 0.25 * root_chord - root_chord
    tip_offset = half_span * math.tan(sweep)
    tip_le = -tip_offset + 0.25 * tip_chord
    tip_te = tip_le - tip_chord
    root_z = 0.55
    tip_z = root_z - half_span * math.sin(dihedral)

    right_wing = [
        _quad(
            (root_le, radius * 0.55, root_z),
            (tip_le, half_span, tip_z),
            (tip_te, half_span, tip_z),
            (root_te, radius * 0.55, root_z),
            wing,
        ),
        # Winglet.
        _quad(
            (tip_le, half_span, tip_z),
            (tip_le + 0.30, half_span + 0.35, tip_z - 2.10),
            (tip_te + 0.95, half_span + 0.35, tip_z - 2.10),
            (tip_te, half_span, tip_z),
            accent,
        ),
    ]
    facets += right_wing + _mirror_y(right_wing)

    # -- engines ---------------------------------------------------------
    for spec in model.raw("propulsion", "positions", []) or []:
        from ..core.units import to_si

        ex = to_si(spec.get("x", 0.0))
        ey = to_si(spec.get("y", 0.0))
        ez = to_si(spec.get("z", 0.0))
        facets += [
            Facet(f.points + np.array([ex, ey, ez]), f.colour, f.highlight)
            for f in _tube(
                [(2.30, 1.02, 0.0), (1.60, 1.24, 0.0), (-1.70, 1.20, 0.0), (-2.40, 0.86, 0.0)],
                engine,
                segments=8,
            )
        ]
        # Pylon up to the wing.
        facets.append(
            _quad(
                (ex + 1.4, ey, ez - 0.9),
                (ex - 1.2, ey, ez - 0.9),
                (ex - 1.9, ey, ez - 2.3),
                (ex + 0.4, ey, ez - 2.3),
                engine,
            )
        )
        # Intake face, dark, so the nacelle reads as a tube not a cylinder.
        facets.append(
            Facet(
                np.array(
                    [
                        (ex + 2.30, ey + 1.02 * math.sin(t), ez + 1.02 * math.cos(t))
                        for t in np.linspace(0, 2 * math.pi, 9)[:-1]
                    ]
                ),
                dark,
            )
        )

    # -- empennage -------------------------------------------------------
    tail_root = -tail_arm + 3.4
    stab_span = 0.185 * span
    right_stab = [
        _quad(
            (tail_root + 1.0, radius * 0.35, -0.55),
            (tail_root - 1.9, stab_span, -1.15),
            (tail_root - 3.6, stab_span, -1.15),
            (tail_root - 3.1, radius * 0.35, -0.55),
            wing,
        )
    ]
    facets += right_stab + _mirror_y(right_stab)

    facets += _slab(
        [
            (tail_root + 2.6, -1.30),
            (tail_root - 2.4, -1.30 - fin_height),
            (tail_root - 4.4, -1.30 - fin_height),
            (tail_root - 3.4, -1.30),
        ],
        0.16,
        accent,
    )

    return facets


def build_fighter(model) -> list[Facet]:
    """Single-engine multirole fighter, dimensioned from its data package.

    Airframe only. There is nothing on the pylons but the mass and drag of a
    loaded pylon, and nothing in this mesh that represents a weapon.
    """
    span = model.get("geometry", "wing_span")
    area = model.get("geometry", "wing_area")
    length = model.get("geometry", "fuselage_length", 15.1)
    diameter = model.get("geometry", "fuselage_diameter", 1.6)
    sweep = model.get("geometry", "wing_sweep_quarter_chord", math.radians(40.0))
    fin_height = model.get("geometry", "tail_height", 5.1) - diameter

    half_span = 0.5 * span
    taper = 0.20
    root_chord = 2.0 * area / (span * (1.0 + taper))
    tip_chord = root_chord * taper

    radius = 0.5 * diameter
    nose_x = 0.50 * length
    tail_x = nose_x - length

    hull = (118, 128, 140)
    upper = (96, 106, 120)
    wing = (104, 114, 128)
    canopy = (46, 62, 84)
    dark = (34, 38, 46)
    accent = (72, 80, 92)

    facets: list[Facet] = []

    # -- fuselage: long, slender, blended ---------------------------------
    facets += _tube(
        [
            (nose_x, 0.06, 0.05),
            (nose_x - 1.1, radius * 0.55, 0.0),
            (nose_x - 2.8, radius * 0.92, 0.0),
            (nose_x - 5.5, radius * 1.05, 0.10),
            (tail_x + 4.5, radius * 1.02, 0.10),
            (tail_x + 1.0, radius * 0.88, 0.05),
            (tail_x, radius * 0.72, 0.05),
        ],
        hull,
        segments=8,
    )

    # Exhaust nozzle: a dark disc, which is what stops the tail reading as a
    # blunt cylinder from behind.
    facets.append(
        Facet(
            np.array(
                [
                    (tail_x, radius * 0.72 * math.sin(t), 0.05 + radius * 0.72 * math.cos(t))
                    for t in np.linspace(0, 2 * math.pi, 9)[:-1]
                ]
            ),
            dark,
        )
    )

    # -- canopy ------------------------------------------------------------
    facets.append(
        _quad(
            (nose_x - 1.9, 0.0, -0.42),
            (nose_x - 3.1, 0.46, -0.86),
            (nose_x - 5.4, 0.40, -0.80),
            (nose_x - 5.9, 0.0, -0.62),
            canopy,
        )
    )
    facets.append(
        _quad(
            (nose_x - 1.9, 0.0, -0.42),
            (nose_x - 5.9, 0.0, -0.62),
            (nose_x - 5.4, -0.40, -0.80),
            (nose_x - 3.1, -0.46, -0.86),
            canopy,
        )
    )

    # -- ventral intake ----------------------------------------------------
    facets += _tube(
        [(nose_x - 3.4, 0.52, 1.05), (nose_x - 5.6, 0.60, 1.10), (nose_x - 8.0, 0.58, 0.95)],
        accent,
        segments=6,
    )
    facets.append(
        Facet(
            np.array(
                [
                    (nose_x - 3.4, 0.52 * math.sin(t), 1.05 + 0.52 * math.cos(t))
                    for t in np.linspace(0, 2 * math.pi, 7)[:-1]
                ]
            ),
            dark,
        )
    )

    # -- wing with leading edge strake -------------------------------------
    root_le, root_te = 0.25 * root_chord, 0.25 * root_chord - root_chord
    tip_offset = half_span * math.tan(sweep)
    tip_le = -tip_offset + 0.25 * tip_chord
    tip_te = tip_le - tip_chord

    right = [
        _quad(
            (root_le, radius * 0.9, 0.10),
            (tip_le, half_span, -0.05),
            (tip_te, half_span, -0.05),
            (root_te, radius * 0.9, 0.10),
            wing,
        ),
        # Strake blending forward along the fuselage.
        _tri(
            (root_le + 3.4, radius * 0.55, 0.05),
            (root_le, half_span * 0.34, 0.08),
            (root_le - 0.6, radius * 0.75, 0.10),
            upper,
        ),
        # Wing pylon: mass and drag only, carrying nothing.
        _quad(
            (root_le - 1.4, half_span * 0.52, 0.20),
            (root_le - 2.6, half_span * 0.52, 0.20),
            (root_le - 2.4, half_span * 0.52, 0.62),
            (root_le - 1.5, half_span * 0.52, 0.62),
            accent,
        ),
    ]
    facets += right + _mirror_y(right)

    # -- stabilators -------------------------------------------------------
    stab_span = 0.34 * span
    right_stab = [
        _quad(
            (tail_x + 3.5, radius * 0.9, 0.05),
            (tail_x + 1.9, stab_span, 0.0),
            (tail_x + 0.5, stab_span, 0.0),
            (tail_x + 0.9, radius * 0.9, 0.05),
            wing,
        )
    ]
    facets += right_stab + _mirror_y(right_stab)

    # -- single fin --------------------------------------------------------
    facets += _slab(
        [
            (tail_x + 5.2, -0.55),
            (tail_x + 2.2, -0.55 - fin_height),
            (tail_x + 0.4, -0.55 - fin_height),
            (tail_x + 0.2, -0.55),
        ],
        0.10,
        upper,
    )
    # Ventral fins.
    for side in (1.0, -1.0):
        facets.append(
            _tri(
                (tail_x + 3.0, side * radius * 0.7, 0.75),
                (tail_x + 0.4, side * radius * 1.5, 1.75),
                (tail_x + 0.3, side * radius * 0.7, 0.75),
                accent,
            )
        )

    return facets


def build_mesh(model) -> list[Facet]:
    """Pick the mesh matching the package's declared category."""
    if model.category == "military":
        return build_fighter(model)
    return build_airliner(model)


def gear_facets(model, extension: float) -> list[Facet]:
    """Landing gear, drawn only as far as it has actually extended."""
    if extension <= 0.01:
        return []

    from ..core.units import to_si

    facets: list[Facet] = []
    strut = (58, 62, 70)
    tyre = (28, 28, 32)

    for spec in model.raw("landing_gear", "gear", []) or []:
        position = spec.get("position", {})
        x = to_si(position.get("x", 0.0))
        y = to_si(position.get("y", 0.0))
        z = to_si(position.get("z", 2.0))

        # Retracted gear sits in the bay; extended gear reaches the ground.
        top_z = z * 0.35
        bottom_z = top_z + (z - top_z) * extension
        thickness = 0.13

        facets.append(
            _quad(
                (x - thickness, y, top_z),
                (x + thickness, y, top_z),
                (x + thickness, y, bottom_z),
                (x - thickness, y, bottom_z),
                strut,
            )
        )
        wheel = 0.42 if abs(y) > 0.1 else 0.34
        facets.append(
            Facet(
                np.array(
                    [
                        (x + wheel * math.sin(t), y, bottom_z + wheel * math.cos(t))
                        for t in np.linspace(0, 2 * math.pi, 9)[:-1]
                    ]
                ),
                tyre,
            )
        )

    return facets
