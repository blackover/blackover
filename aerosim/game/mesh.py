"""Aircraft geometry for the chase view.

Meshes are built in body axes -- x forward, y right, z down, metres, origin at
the aerodynamic reference point -- the same frame the flight model uses. That
means the rendered aircraft sits where the physics says it is: the wingtip that
touches the ground on screen is the wingtip the crash check tested, because
both read the same span out of the same data package.

The shapes are dimensioned from the aircraft's own geometry.yaml, so a data
package with a different span draws a different aeroplane without touching
this file. The tail surfaces are sized from ``horizontal_tail_area`` and
``vertical_tail_area``, which means enlarging a fin to fix a handling problem
visibly enlarges the fin.

Solids, not cut-outs
--------------------
Every lifting surface here is a lofted aerofoil with real thickness rather
than a flat polygon. A zero-thickness wing is cheaper and it is what this
module used to draw, and the result is an aeroplane that reads as paper: it
vanishes edge-on, it has no leading edge to catch the sun, and the eye gets
no shading gradient across the chord to tell it the surface is curved. A
wing with a NACA thickness distribution and a tip cap costs about thirteen
quads per bay and fixes all three at once.

The same argument applies to the fuselage, which is lofted through
superelliptic cross-sections rather than circular ones. Real fighters are
not tubes -- they have flat-ish sides, a flat belly and a rounded spine --
and a single exponent per station is enough to say so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------
# Surface finish
# --------------------------------------------------------------------------
#: How light behaves on a facet. A fighter is not uniformly matte: a canopy,
#: a bare metal nozzle and a painted wing catch the sun in visibly different
#: ways, and that difference is most of what makes a shape read as an object
#: rather than a silhouette. The renderer maps these to a sky-reflection
#: amount and a specular lobe.
PAINT = "paint"
METAL = "metal"
GLASS = "glass"
MATTE = "matte"

#: Chordwise sampling of an aerofoil, cosine-spaced so points bunch at the
#: leading edge where the curvature is. Seven stations is enough for the
#: shading gradient to read as curvature and few enough to stay cheap.
CHORDWISE = tuple(
    0.5 * (1.0 - math.cos(math.pi * i / 6.0)) for i in range(7)
)


@dataclass
class Facet:
    """One flat polygon in body axes, with its unlit colour and finish."""

    points: np.ndarray  # (n, 3)
    colour: tuple[int, int, int]
    highlight: bool = False  # ignore lighting, e.g. for lights and markings
    material: str = PAINT
    #: True when this facet is part of a closed body wound outward, which
    #: lets the renderer discard it when it faces away. Roughly half of a
    #: solid is always facing away, and drawing the inside of a fuselage
    #: costs exactly as much as drawing the outside. Left False for open
    #: shells -- a canopy, a disc, a flat panel -- where both sides are
    #: real and culling one would punch a hole in the aeroplane.
    solid: bool = False


def _quad(a, b, c, d, colour, highlight=False, material=PAINT, solid=False) -> Facet:
    return Facet(
        np.array([a, b, c, d], dtype=float), colour, highlight, material, solid
    )


def _tri(a, b, c, colour, highlight=False, material=PAINT, solid=False) -> Facet:
    return Facet(
        np.array([a, b, c], dtype=float), colour, highlight, material, solid
    )


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
        out.append(
            Facet(points[::-1].copy(), facet.colour, facet.highlight,
                  facet.material, facet.solid)
        )
    return out


def _offset(facets: list[Facet], delta) -> list[Facet]:
    """Translate a group of facets bodily."""
    shift = np.asarray(delta, dtype=float)
    return [
        Facet(f.points + shift, f.colour, f.highlight, f.material, f.solid)
        for f in facets
    ]


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------


def _naca_half_thickness(station: float, ratio: float) -> float:
    """Half-thickness of a symmetric NACA four-digit section, as a fraction
    of chord, at a fraction ``station`` along it.

    The standard polynomial. It matters here only for its shape: a rounded
    leading edge that catches light across a wide range of angles, a maximum
    around 30 per cent chord, and a near-closed trailing edge.
    """
    s = min(1.0, max(0.0, station))
    return (
        5.0
        * ratio
        * (
            0.2969 * math.sqrt(s)
            - 0.1260 * s
            - 0.3516 * s * s
            + 0.2843 * s**3
            - 0.1015 * s**4
        )
    )


def _section(le_x, te_x, y, z, ratio):
    """Upper and lower surface points of one chordwise station line.

    ``le_x`` is ahead of ``te_x`` because x is forward, and the upper surface
    is at smaller z because z is down. Getting either backwards builds an
    aeroplane that flies upside down in the picture and nowhere else.
    """
    chord = le_x - te_x
    upper, lower = [], []
    for s in CHORDWISE:
        x = le_x - s * chord
        half = _naca_half_thickness(s, ratio) * chord
        upper.append((x, y, z - half))
        lower.append((x, y, z + half))
    return upper, lower


def _wing_solid(sections, colour, material=PAINT, cap_root=False,
                under=None) -> list[Facet]:
    """Loft a solid aerofoil through spanwise sections.

    Each section is ``(le_x, te_x, y, z, thickness_ratio)``. Consecutive
    sections are joined by quad strips over the upper and lower surfaces and
    closed at the trailing edge, and the last section is capped so the tip is
    a surface rather than a hole.
    """
    built = [_section(*s) for s in sections]
    facets: list[Facet] = []

    for (upper_a, lower_a), (upper_b, lower_b) in zip(built, built[1:]):
        for i in range(len(CHORDWISE) - 1):
            facets.append(
                _quad(
                    upper_a[i], upper_a[i + 1], upper_b[i + 1], upper_b[i],
                    colour, material=material, solid=True,
                )
            )
            facets.append(
                _quad(
                    lower_a[i + 1], lower_a[i], lower_b[i], lower_b[i + 1],
                    under or colour, material=material, solid=True,
                )
            )
        # The NACA polynomial leaves the trailing edge fractionally open;
        # close it rather than leave a slot the sky shows through.
        facets.append(
            _quad(upper_a[-1], lower_a[-1], lower_b[-1], upper_b[-1],
                  colour, material=material, solid=True)
        )

    def cap(upper, lower, flip):
        ring = list(upper) + list(reversed(lower[1:-1]))
        points = ring[::-1] if flip else ring
        return Facet(np.array(points, dtype=float), colour, False, material, True)

    facets.append(cap(*built[-1], flip=False))
    if cap_root:
        facets.append(cap(*built[0], flip=True))
    return facets


def _superellipse(half_width, half_height, z_centre, exponent, segments):
    """One cross-section ring, from a circle at exponent 2 to a box above it."""
    power = 2.0 / max(exponent, 1.0e-3)
    ring = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        s, c = math.sin(theta), math.cos(theta)
        y = half_width * math.copysign(abs(s) ** power, s)
        z = z_centre + half_height * math.copysign(abs(c) ** power, c)
        ring.append((y, z))
    return ring


def _lofted_body(
    stations, colour, segments=10, material=PAINT, under=None
) -> list[Facet]:
    """A body lofted through ``(x, half_width, half_height, z, exponent)``.

    ``under`` paints the facets below the body's own centreline a second
    colour. Nearly every real aircraft is two-tone that way round -- a
    darker top against the ground and a paler underside against the sky --
    and the line between them is one of the things that most reliably says
    "aeroplane" rather than "grey shape".
    """
    rings = []
    for x, half_width, half_height, z_centre, exponent in stations:
        ring = _superellipse(half_width, half_height, z_centre, exponent, segments)
        rings.append(([(x, y, z) for y, z in ring], z_centre))

    facets: list[Facet] = []
    for (front, z_front), (back, z_back) in zip(rings, rings[1:]):
        middle = 0.5 * (z_front + z_back)
        for i in range(segments):
            j = (i + 1) % segments
            quad = (front[i], front[j], back[j], back[i])
            below = sum(p[2] for p in quad) / 4.0 > middle
            facets.append(
                _quad(*quad, under if (below and under) else colour,
                      material=material, solid=True)
            )
    return facets


def _disc(x, half_width, half_height, z_centre, colour, segments=10,
          exponent=2.0, material=MATTE, flip=False) -> Facet:
    """A capping face. What stops a duct reading as a solid rod."""
    ring = _superellipse(half_width, half_height, z_centre, exponent, segments)
    points = [(x, y, z) for y, z in ring]
    if flip:
        points = points[::-1]
    return Facet(np.array(points, dtype=float), colour, False, material)


def _canopy(stations, colour, segments=7, material=GLASS) -> list[Facet]:
    """A glazed half-shell over the cockpit.

    Half a lofted body: the lower half would be inside the fuselage. Drawn in
    glass, which the renderer gives a dark base and a tight bright highlight,
    because a canopy is the one part of an aeroplane that is obviously not
    painted.
    """
    rings = []
    for x, half_width, height, z_top in stations:
        ring = []
        for i in range(segments):
            theta = math.pi * i / (segments - 1)  # 0 .. pi, port to starboard
            ring.append(
                (
                    x,
                    -half_width * math.cos(theta),
                    z_top + height * (1.0 - math.sin(theta)),
                )
            )
        rings.append(ring)

    facets: list[Facet] = []
    for front, back in zip(rings, rings[1:]):
        for i in range(segments - 1):
            facets.append(
                _quad(front[i], front[i + 1], back[i + 1], back[i],
                      colour, material=material)
            )
    return facets


def _slab(
    profile: list[tuple[float, float]],
    half_thickness: float,
    colour: tuple[int, int, int],
    material=PAINT,
) -> list[Facet]:
    """Extrude an x-z profile sideways into a thin solid.

    Kept for small flat details -- pylons, fences, gear doors -- where an
    aerofoil would be a lie. Lifting surfaces use ``_wing_solid``.
    """
    left = [(x, -half_thickness, z) for x, z in profile]
    right = [(x, half_thickness, z) for x, z in profile]

    facets = [
        Facet(np.array(right, dtype=float), colour, False, material),
        Facet(np.array(left[::-1], dtype=float), colour, False, material),
    ]
    for i in range(len(profile)):
        j = (i + 1) % len(profile)
        facets.append(
            _quad(left[i], right[i], right[j], left[j], colour, material=material)
        )
    return facets


def _tube(
    stations: list[tuple[float, float, float]],
    colour: tuple[int, int, int],
    segments: int = 8,
    material=PAINT,
) -> list[Facet]:
    """A body of revolution through (x, radius, z_centre) stations."""
    return _lofted_body(
        [(x, r, r, z, 2.0) for x, r, z in stations],
        colour,
        segments=segments,
        material=material,
    )


# --------------------------------------------------------------------------
# Tail sizing, from the data package
# --------------------------------------------------------------------------


def _fin_plan(area: float, height: float, taper: float = 0.45):
    """Root and tip chord of a fin of a given area and height."""
    root = 2.0 * area / (height * (1.0 + taper))
    return root, root * taper


def _tail_plan(area_total: float, semi_span: float, taper: float = 0.45):
    """Root and tip chord of one side of a horizontal tail."""
    root = area_total / (semi_span * (1.0 + taper))
    return root, root * taper


# --------------------------------------------------------------------------
# The two aircraft
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
    fin_area = model.get("geometry", "vertical_tail_area", 25.0)
    stab_area = model.get("geometry", "horizontal_tail_area", 31.0)

    half_span = 0.5 * span
    taper = 0.24
    root_chord = 2.0 * area / (span * (1.0 + taper))
    tip_chord = root_chord * taper

    radius = 0.5 * diameter
    nose_x = 0.45 * length
    tail_x = nose_x - length

    hull = (238, 240, 245)
    belly = (206, 210, 219)
    wing = (224, 228, 236)
    accent = (28, 74, 140)
    engine = (206, 210, 218)
    dark = (44, 48, 58)
    glass = (38, 54, 76)

    facets: list[Facet] = []

    # -- fuselage ----------------------------------------------------------
    # Near-circular, as a pressure vessel has to be, with the tailcone swept
    # up and the nose drooped.
    facets += _lofted_body(
        [
            (nose_x, 0.10, 0.10, 0.34, 2.0),
            (nose_x - 0.9, radius * 0.42, radius * 0.40, 0.26, 2.1),
            (nose_x - 2.2, radius * 0.74, radius * 0.72, 0.14, 2.1),
            (nose_x - 4.2, radius * 0.95, radius * 0.95, 0.03, 2.05),
            (nose_x - 8.0, radius, radius, 0.0, 2.0),
            (tail_x + 14.0, radius, radius, 0.0, 2.0),
            (tail_x + 9.0, radius * 0.96, radius * 0.96, -0.22, 2.0),
            (tail_x + 5.0, radius * 0.70, radius * 0.74, -0.88, 2.0),
            (tail_x + 2.2, radius * 0.40, radius * 0.46, -1.42, 2.0),
            (tail_x + 0.6, radius * 0.16, radius * 0.20, -1.72, 2.0),
        ],
        hull,
        segments=12,
        under=belly,
    )

    # Flight deck glazing, and a cheatline along the window row so roll is
    # readable at a glance from behind.
    for side in (1.0, -1.0):
        facets.append(
            _quad(
                (nose_x - 1.25, side * radius * 0.40, -0.22),
                (nose_x - 2.45, side * radius * 0.78, -0.52),
                (nose_x - 3.45, side * radius * 0.86, -0.46),
                (nose_x - 2.60, side * radius * 0.52, -0.16),
                glass,
                material=GLASS,
            )
        )
        facets.append(
            _quad(
                (nose_x - 3.2, side * radius * 0.97, -0.30),
                (tail_x + 6.0, side * radius * 0.97, -0.86),
                (tail_x + 6.0, side * radius * 0.93, -0.34),
                (nose_x - 3.2, side * radius * 0.93, 0.10),
                accent,
            )
        )

    # -- wing --------------------------------------------------------------
    root_le, root_te = 0.25 * root_chord, 0.25 * root_chord - root_chord
    root_y = radius * 0.52
    tip_offset = (half_span - root_y) * math.tan(sweep)
    tip_le = root_le - tip_offset - 0.25 * (root_chord - tip_chord)
    tip_te = tip_le - tip_chord
    root_z = 0.58
    tip_z = root_z - (half_span - root_y) * math.sin(dihedral)

    mid = 0.52
    mid_y = root_y + (half_span - root_y) * mid
    mid_chord = root_chord + (tip_chord - root_chord) * mid
    mid_le = root_le - (tip_offset * mid) - 0.25 * (root_chord - mid_chord)
    mid_z = root_z - (mid_y - root_y) * math.sin(dihedral)

    right = _wing_solid(
        [
            (root_le, root_te, root_y, root_z, 0.135),
            (mid_le, mid_le - mid_chord, mid_y, mid_z, 0.115),
            (tip_le, tip_te, half_span, tip_z, 0.095),
        ],
        wing,
        under=belly,
    )
    # Winglet: a small canted surface, which is most of this aeroplane's
    # silhouette from behind.
    right += _wing_solid(
        [
            (tip_le, tip_te, half_span, tip_z, 0.095),
            (tip_le + 0.30, tip_te + 0.95, half_span + 0.38, tip_z - 2.10, 0.09),
        ],
        accent,
    )
    facets += right + _mirror_y(right)

    # -- engines -----------------------------------------------------------
    for spec in model.raw("propulsion", "positions", []) or []:
        from ..core.units import to_si

        ex = to_si(spec.get("x", 0.0))
        ey = to_si(spec.get("y", 0.0))
        ez = to_si(spec.get("z", 0.0))
        nacelle = _lofted_body(
            [
                (2.34, 1.00, 1.00, 0.0, 2.0),
                (2.05, 1.22, 1.22, 0.0, 2.0),
                (1.20, 1.28, 1.28, 0.0, 2.0),
                (-1.40, 1.22, 1.22, 0.0, 2.0),
                (-2.10, 0.98, 0.98, 0.02, 2.0),
                (-2.45, 0.74, 0.74, 0.04, 2.0),
            ],
            engine,
            segments=12,
        )
        # The fan face and the exhaust, both dark: without them a nacelle is
        # a sealed pod, and a jet engine is a hole at each end.
        nacelle.append(_disc(2.34, 0.96, 0.96, 0.0, dark, segments=12))
        nacelle.append(
            _disc(-2.45, 0.70, 0.70, 0.04, (24, 24, 28), segments=12, flip=True)
        )
        facets += _offset(nacelle, (ex, ey, ez))

        # Pylon up to the wing.
        facets += _offset(
            _slab(
                [
                    (1.5, -0.95),
                    (-1.3, -0.95),
                    (-2.0, -2.35),
                    (0.3, -2.35),
                ],
                0.16,
                engine,
            ),
            (ex, ey, ez),
        )

    # -- empennage ---------------------------------------------------------
    stab_semi = 0.185 * span
    stab_root, stab_tip = _tail_plan(0.5 * stab_area, stab_semi - radius * 0.3)
    stab_x = -tail_arm + 3.2
    stab_sweep = math.radians(30.0)
    stab_offset = (stab_semi - radius * 0.3) * math.tan(stab_sweep)

    right_stab = _wing_solid(
        [
            (stab_x + 0.25 * stab_root, stab_x - 0.75 * stab_root,
             radius * 0.3, -0.55, 0.10),
            (stab_x - stab_offset + 0.25 * stab_tip,
             stab_x - stab_offset - 0.75 * stab_tip,
             stab_semi, -1.20, 0.09),
        ],
        wing,
    )
    facets += right_stab + _mirror_y(right_stab)

    fin_root, fin_tip = _fin_plan(fin_area, fin_height)
    fin_x = -tail_arm + 3.0
    fin_sweep = math.radians(38.0)
    fin_offset = fin_height * math.tan(fin_sweep)
    # A fin is a wing rotated into the vertical: build it as one and swap the
    # spanwise axis from y to z, so it gets the same aerofoil section.
    fin_sections = [
        (fin_x + 0.25 * fin_root, fin_x - 0.75 * fin_root, 0.0, -1.25, 0.11),
        (
            fin_x - fin_offset + 0.25 * fin_tip,
            fin_x - fin_offset - 0.75 * fin_tip,
            0.0,
            -1.25 - fin_height,
            0.09,
        ),
    ]
    facets += _vertical(fin_sections, accent)

    return facets


def _vertical(sections, colour, material=PAINT) -> list[Facet]:
    """A fin: ``_wing_solid`` with its span along z instead of y.

    Built by lofting in y and then rotating the result a quarter turn about
    x, so a fin gets the same aerofoil treatment as a wing rather than being
    a flat slab -- which from directly astern, where the chase camera sits,
    is the difference between a surface and a line.
    """
    flat = [(le, te, -z, 0.0, ratio) for le, te, _y, z, ratio in sections]
    facets = _wing_solid(flat, colour, material=material)
    out = []
    for facet in facets:
        points = facet.points.copy()
        y, z = points[:, 1].copy(), points[:, 2].copy()
        points[:, 1] = z
        points[:, 2] = -y
        out.append(
            Facet(points, facet.colour, facet.highlight, facet.material, facet.solid)
        )
    return out


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
    fin_area = model.get("geometry", "vertical_tail_area", 5.1)
    stab_area = model.get("geometry", "horizontal_tail_area", 5.9)
    tail_arm = model.get("geometry", "tail_arm", 4.9)

    half_span = 0.5 * span
    taper = 0.20
    root_chord = 2.0 * area / (span * (1.0 + taper))
    tip_chord = root_chord * taper

    radius = 0.5 * diameter
    nose_x = 0.50 * length
    tail_x = nose_x - length

    hull = (128, 136, 148)
    spine = (104, 112, 126)
    belly = (150, 157, 168)
    wing = (118, 126, 140)
    glass = (40, 56, 78)
    dark = (26, 28, 34)
    metal = (150, 148, 142)
    accent = (82, 88, 100)

    facets: list[Facet] = []

    # -- fuselage ----------------------------------------------------------
    # Superelliptic: a rounded spine over flat sides over a flat belly, which
    # is what a fighter fuselage packed with equipment actually looks like,
    # and quite unlike the tube a body of revolution gives.
    #
    # Split at the radome joint so the two can be different colours, and
    # split by sharing a station rather than by overlapping two bodies --
    # two lofts through the same space z-fight along the seam and the nose
    # flickers between colours as the aircraft turns.
    radome_joint = (nose_x - 2.1, radius * 0.62, radius * 0.56, 0.04, 2.4)
    facets += _lofted_body(
        [
            (nose_x, 0.04, 0.04, 0.02, 2.0),
            (nose_x - 0.55, radius * 0.26, radius * 0.24, 0.03, 2.1),
            (nose_x - 1.25, radius * 0.46, radius * 0.42, 0.035, 2.2),
            radome_joint,
        ],
        (62, 64, 70),
        segments=12,
        material=MATTE,
    )
    facets += _lofted_body(
        [
            radome_joint,
            (nose_x - 3.6, radius * 0.84, radius * 0.70, 0.06, 2.9),
            (nose_x - 5.4, radius * 0.96, radius * 0.82, 0.08, 3.2),
            (nose_x - 7.6, radius * 1.02, radius * 0.86, 0.08, 3.4),
            (tail_x + 5.2, radius * 1.00, radius * 0.86, 0.07, 3.3),
            (tail_x + 3.0, radius * 0.92, radius * 0.82, 0.06, 3.0),
            (tail_x + 1.2, radius * 0.80, radius * 0.76, 0.05, 2.6),
            (tail_x + 0.35, radius * 0.70, radius * 0.70, 0.05, 2.2),
        ],
        hull,
        segments=12,
        under=belly,
    )

    # -- afterburner nozzle ------------------------------------------------
    # Petals and a dark core. From astern -- which is where a chase camera
    # and half a dogfight live -- this is the most looked-at part of the
    # aeroplane, and a flat disc there wastes the view.
    petals = 12
    for i in range(petals):
        a0 = 2.0 * math.pi * i / petals
        a1 = 2.0 * math.pi * (i + 1) / petals
        r_out, r_in = radius * 0.70, radius * 0.44
        facets.append(
            _quad(
                (tail_x + 0.35, r_out * math.sin(a0), 0.05 + r_out * math.cos(a0)),
                (tail_x + 0.35, r_out * math.sin(a1), 0.05 + r_out * math.cos(a1)),
                (tail_x - 0.55, r_in * math.sin(a1), 0.05 + r_in * math.cos(a1)),
                (tail_x - 0.55, r_in * math.sin(a0), 0.05 + r_in * math.cos(a0)),
                metal,
                material=METAL,
                solid=True,
            )
        )
    facets.append(
        _disc(tail_x - 0.60, radius * 0.42, radius * 0.42, 0.05, dark,
              segments=12, flip=True)
    )

    # -- canopy ------------------------------------------------------------
    # A bubble, sitting on the spine rather than lying along it: a canopy is
    # the tallest thing on a fighter's profile and flattening it costs the
    # silhouette more than any other single shape.
    facets += _canopy(
        [
            (nose_x - 2.5, 0.16, 0.05, -0.56),
            (nose_x - 3.1, 0.33, 0.26, -0.84),
            (nose_x - 3.9, 0.42, 0.40, -1.02),
            (nose_x - 5.0, 0.44, 0.42, -1.05),
            (nose_x - 6.1, 0.38, 0.32, -0.95),
            (nose_x - 6.9, 0.30, 0.16, -0.80),
        ],
        glass,
    )
    # Spine fairing from behind the canopy into the fin root.
    facets += _canopy(
        [
            (nose_x - 6.9, 0.30, 0.14, -0.80),
            (nose_x - 8.6, 0.34, 0.12, -0.78),
            (tail_x + 4.2, 0.32, 0.10, -0.74),
            (tail_x + 2.2, 0.24, 0.07, -0.64),
        ],
        spine,
        material=PAINT,
    )

    # -- side intakes ------------------------------------------------------
    # Cheek-mounted, tucked against the fuselage side under the chine. Flat
    # and wide rather than round: a circular pod reads as a tank, and the
    # dark throat is what makes the difference between an intake and a
    # blister.
    for side in (1.0, -1.0):
        duct = _lofted_body(
            [
                (nose_x - 3.9, 0.22, 0.34, 0.34, 3.2),
                (nose_x - 5.0, 0.28, 0.40, 0.33, 3.4),
                (nose_x - 7.2, 0.27, 0.39, 0.30, 3.4),
                (nose_x - 8.8, 0.22, 0.32, 0.26, 3.2),
            ],
            accent,
            segments=8,
        )
        throat = _disc(
            nose_x - 3.9, 0.19, 0.30, 0.34, dark, segments=8, exponent=3.2
        )
        facets += _offset(duct + [throat], (0.0, side * radius * 0.96, 0.0))

    # -- wing: a cranked delta with the chine built in ---------------------
    # The leading-edge root extension is part of the wing loft, not a
    # separate surface bolted to it. Built separately they meet at an angle
    # and leave a visible step in the leading edge, which is the one line on
    # a fighter the eye follows all the way from the nose.
    #
    # The drawn planform is therefore larger than ``wing_area``, which is the
    # trapezoidal reference the aerodynamic coefficients are normalised
    # against. That is the usual convention for a chined wing rather than a
    # discrepancy: the reference area is bookkeeping, and the coefficients
    # in aerodynamics.yaml are defined with respect to it.
    root_y = radius * 0.98
    root_le, root_te = 0.25 * root_chord, 0.25 * root_chord - root_chord
    tip_offset = (half_span - root_y) * math.tan(sweep)
    tip_le = root_le - tip_offset - 0.25 * (root_chord - tip_chord)
    tip_te = tip_le - tip_chord
    wing_z = 0.16

    def between(a, b, t):
        return a + (b - a) * t

    chine = root_le + 3.35
    sections = []
    for t, chine_share, ratio in (
        (0.00, 1.00, 0.030),
        (0.18, 0.42, 0.038),
        (0.42, 0.08, 0.048),
        (0.70, 0.00, 0.044),
        (1.00, 0.00, 0.038),
    ):
        y = between(root_y, half_span, t)
        le = between(root_le, tip_le, t)
        te = between(root_te, tip_te, t)
        sections.append((le + (chine - root_le) * chine_share, te, y, wing_z, ratio))

    right = _wing_solid(sections, wing, under=belly)
    # Pylon: mass and drag only, carrying nothing.
    right += _offset(
        _slab(
            [
                (root_le - 1.4, 0.22),
                (root_le - 2.7, 0.22),
                (root_le - 2.5, 0.62),
                (root_le - 1.5, 0.62),
            ],
            0.09,
            accent,
        ),
        (0.0, half_span * 0.54, 0.0),
    )
    facets += right + _mirror_y(right)

    # -- stabilators -------------------------------------------------------
    stab_semi = 0.34 * span
    stab_root, stab_tip = _tail_plan(0.5 * stab_area, stab_semi - radius * 0.9)
    stab_x = -tail_arm
    stab_offset = (stab_semi - radius * 0.9) * math.tan(math.radians(42.0))

    right_stab = _wing_solid(
        [
            (stab_x + 0.25 * stab_root, stab_x - 0.75 * stab_root,
             radius * 0.9, 0.08, 0.055),
            (stab_x - stab_offset + 0.25 * stab_tip,
             stab_x - stab_offset - 0.75 * stab_tip,
             stab_semi, 0.26, 0.045),
        ],
        wing,
        under=belly,
    )
    facets += right_stab + _mirror_y(right_stab)

    # -- fin and ventral strakes -------------------------------------------
    fin_root, fin_tip = _fin_plan(fin_area, fin_height)
    fin_x = -tail_arm + 1.9
    fin_offset = fin_height * math.tan(math.radians(42.0))
    facets += _vertical(
        [
            (fin_x + 0.25 * fin_root, fin_x - 0.75 * fin_root, 0.0, -0.60, 0.060),
            (
                fin_x - fin_offset + 0.25 * fin_tip,
                fin_x - fin_offset - 0.75 * fin_tip,
                0.0,
                -0.60 - fin_height,
                0.048,
            ),
        ],
        spine,
    )
    # Ventral strakes: small and swept, which is what they are. Drawn long
    # they read as undercarriage legs hanging out of a clean aeroplane.
    strake = _slab(
        [
            (tail_x + 2.6, 0.70),
            (tail_x + 1.5, 1.16),
            (tail_x + 0.7, 1.16),
            (tail_x + 0.6, 0.70),
        ],
        0.05,
        accent,
    )
    for side in (1.0, -1.0):
        facets += _offset(strake, (0.0, side * radius * 0.74, 0.0))

    return facets



def build_mesh(model) -> list[Facet]:
    """Pick the mesh matching the package's declared category."""
    category = (model.raw("manifest", "category", "") or "").lower()
    if "fighter" in category or "military" in category:
        return build_fighter(model)
    return build_airliner(model)


def exhaust_ports(model) -> list[tuple[np.ndarray, float]]:
    """Where the exhaust leaves the aircraft, and how wide the plume is.

    Read from the propulsion data rather than guessed, so an aircraft with
    its engines somewhere else draws its plumes there.
    """
    from ..core.units import to_si

    length = model.get("geometry", "fuselage_length", 30.0)
    diameter = model.get("geometry", "fuselage_diameter", 3.0)
    nose_x = 0.47 * length
    tail_x = nose_x - length
    category = (model.raw("manifest", "category", "") or "").lower()

    if "fighter" in category or "military" in category:
        return [(np.array([tail_x - 0.1, 0.0, 0.05]), 0.5 * diameter * 0.62)]

    ports = []
    for spec in model.raw("propulsion", "positions", []) or []:
        ports.append(
            (
                np.array(
                    [
                        to_si(spec.get("x", 0.0)) - 2.5,
                        to_si(spec.get("y", 0.0)),
                        to_si(spec.get("z", 0.0)) + 0.04,
                    ]
                ),
                0.70,
            )
        )
    return ports


def shadow_outline(model) -> list[Facet]:
    """A coarse planform, used only for casting the shadow.

    Six quads instead of the full mesh's several hundred. A shadow is a
    filled silhouette on the ground -- nobody can tell which facet made which
    part of it, and projecting the whole aircraft costs as much again as
    drawing it.
    """
    span = model.get("geometry", "wing_span")
    area = model.get("geometry", "wing_area")
    length = model.get("geometry", "fuselage_length", 30.0)
    diameter = model.get("geometry", "fuselage_diameter", 3.0)
    sweep = model.get("geometry", "wing_sweep_quarter_chord", math.radians(25.0))
    tail_arm = model.get("geometry", "tail_arm", 0.4 * length)

    half_span = 0.5 * span
    taper = 0.24
    root_chord = 2.0 * area / (span * (1.0 + taper))
    tip_chord = root_chord * taper
    nose_x = 0.47 * length
    tail_x = nose_x - length

    root_le, root_te = 0.25 * root_chord, 0.25 * root_chord - root_chord
    tip_offset = half_span * math.tan(sweep)
    tip_le = -tip_offset + 0.25 * tip_chord
    tip_te = tip_le - tip_chord

    half_body = 0.5 * diameter
    stab_span = 0.20 * span
    stab_x = -tail_arm + 3.0

    facets = [
        _quad(
            (nose_x, -half_body * 0.5, 0.0),
            (nose_x, half_body * 0.5, 0.0),
            (tail_x, half_body * 0.6, 0.0),
            (tail_x, -half_body * 0.6, 0.0),
            (0, 0, 0),
        )
    ]
    for side in (1.0, -1.0):
        facets.append(
            _quad(
                (root_le, side * half_body, 0.0),
                (tip_le, side * half_span, 0.0),
                (tip_te, side * half_span, 0.0),
                (root_te, side * half_body, 0.0),
                (0, 0, 0),
            )
        )
        facets.append(
            _quad(
                (stab_x + 1.4, side * half_body, 0.0),
                (stab_x - 0.6, side * stab_span, 0.0),
                (stab_x - 2.4, side * stab_span, 0.0),
                (stab_x - 2.6, side * half_body, 0.0),
                (0, 0, 0),
            )
        )
    return facets


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

        # A square-section leg, built as four faces around the axis. The
        # lofted-body helper cannot make this: it lofts along x, and a gear
        # leg runs along z.
        for dx, dy in ((thickness, 0.0), (-thickness, 0.0),
                       (0.0, thickness), (0.0, -thickness)):
            nx, ny = dy, dx  # the face's own in-plane direction
            facets.append(
                _quad(
                    (x + dx - nx * 0.5, y + dy - ny * 0.5, top_z),
                    (x + dx + nx * 0.5, y + dy + ny * 0.5, top_z),
                    (x + dx + nx * 0.5, y + dy + ny * 0.5, bottom_z),
                    (x + dx - nx * 0.5, y + dy - ny * 0.5, bottom_z),
                    strut,
                    material=METAL,
                )
            )

        # A tyre, as a short cylinder rather than a disc: a disc vanishes
        # when the aircraft is viewed from ahead, which on the runway is
        # most of the time.
        wheel = 0.42 if abs(y) > 0.1 else 0.34
        rim = [
            (wheel * math.sin(t), wheel * math.cos(t))
            for t in np.linspace(0, 2 * math.pi, 13)[:-1]
        ]
        for offset in (-0.11, 0.11):
            facets.append(
                Facet(
                    np.array([(x + sx, y + offset, bottom_z + sz) for sx, sz in rim]),
                    tyre, False, MATTE,
                )
            )
        for i in range(len(rim)):
            j = (i + 1) % len(rim)
            facets.append(
                _quad(
                    (x + rim[i][0], y - 0.11, bottom_z + rim[i][1]),
                    (x + rim[j][0], y - 0.11, bottom_z + rim[j][1]),
                    (x + rim[j][0], y + 0.11, bottom_z + rim[j][1]),
                    (x + rim[i][0], y + 0.11, bottom_z + rim[i][1]),
                    tyre,
                    material=MATTE,
                )
            )

    return facets
