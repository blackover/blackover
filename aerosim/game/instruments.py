"""Primary flight display and the engine and systems panel.

Every number on this panel is converted from SI at the moment it is drawn and
nowhere earlier. Knots, feet and degrees exist in this file and in the setup
screen; between them the simulator is metric throughout.

The panel shows what the model actually computed, including when that is
inconvenient: an envelope caution appears when the aerodynamic model has left
the region it was validated in, rather than being suppressed because the
aircraft still appears to be flying normally.
"""

from __future__ import annotations

import math

import pygame

from ..core.units import to_deg, to_ft, to_fpm, to_kt

# -- palette ---------------------------------------------------------------

PANEL_BG = (16, 18, 23)
PANEL_EDGE = (44, 50, 60)
INSTRUMENT_BG = (10, 12, 16)
SKY_BLUE = (48, 118, 186)
GROUND_BROWN = (118, 78, 44)
WHITE = (238, 240, 244)
DIM = (128, 136, 150)
GREEN = (86, 214, 122)
CYAN = (96, 208, 232)
MAGENTA = (226, 132, 226)
AMBER = (246, 178, 60)
RED = (238, 78, 78)


class Fonts:
    """Font set, built once. pygame's default font is always available."""

    def __init__(self, scale: float = 1.0) -> None:
        def size(points: int) -> int:
            return max(9, int(points * scale))

        self.tiny = pygame.font.Font(None, size(17))
        self.small = pygame.font.Font(None, size(20))
        self.medium = pygame.font.Font(None, size(25))
        self.large = pygame.font.Font(None, size(33))
        self.huge = pygame.font.Font(None, size(46))


def draw_text(
    surface, font, text: str, position, colour=WHITE, anchor: str = "topleft"
) -> pygame.Rect:
    image = font.render(text, True, colour)
    rect = image.get_rect(**{anchor: position})
    surface.blit(image, rect)
    return rect


class AttitudeIndicator:
    """Artificial horizon with pitch ladder, bank scale and slip indicator."""

    def __init__(self, rect: pygame.Rect, fonts: Fonts) -> None:
        self.rect = rect
        self.fonts = fonts
        self.pixels_per_degree = rect.height / 52.0

    def draw(self, surface, derived, stalled: bool) -> None:
        rect = self.rect
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        cx, cy = rect.width * 0.5, rect.height * 0.5

        roll, pitch = derived.roll, derived.pitch
        offset = math.degrees(pitch) * self.pixels_per_degree

        # Draw sky and ground on an oversized rotating surface so the corners
        # stay covered at any bank angle.
        big = int(math.hypot(rect.width, rect.height)) + 8
        horizon = pygame.Surface((big, big))
        horizon.fill(SKY_BLUE)
        pygame.draw.rect(horizon, GROUND_BROWN, (0, big // 2, big, big // 2))
        pygame.draw.line(horizon, WHITE, (0, big // 2), (big, big // 2), 2)

        # Pitch ladder, drawn before rotation so it rotates with the horizon.
        for degrees in range(-90, 91, 5):
            if degrees == 0:
                continue
            y = big // 2 - degrees * self.pixels_per_degree
            if not (0 <= y <= big):
                continue
            major = degrees % 10 == 0
            half = (46 if major else 24)
            colour = WHITE if degrees > 0 else (214, 206, 196)
            pygame.draw.line(
                horizon, colour, (big // 2 - half, y), (big // 2 + half, y), 2 if major else 1
            )
            if major:
                label = self.fonts.tiny.render(str(abs(degrees)), True, colour)
                horizon.blit(label, label.get_rect(midright=(big // 2 - half - 5, y)))
                horizon.blit(label, label.get_rect(midleft=(big // 2 + half + 5, y)))

        rotated = pygame.transform.rotate(horizon, math.degrees(roll))
        layer.blit(rotated, rotated.get_rect(center=(cx, cy + offset)))

        # -- fixed symbology ------------------------------------------------
        # Bank scale.
        radius = rect.height * 0.44
        for angle in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
            a = math.radians(angle) - math.pi / 2
            length = 13 if angle % 30 == 0 else 8
            x1, y1 = cx + radius * math.cos(a), cy + radius * math.sin(a)
            x2 = cx + (radius - length) * math.cos(a)
            y2 = cy + (radius - length) * math.sin(a)
            pygame.draw.line(layer, WHITE, (x1, y1), (x2, y2), 2 if angle == 0 else 1)

        # Roll pointer.
        a = -roll - math.pi / 2
        tip = (cx + (radius - 15) * math.cos(a), cy + (radius - 15) * math.sin(a))
        left = (cx + (radius - 27) * math.cos(a - 0.055), cy + (radius - 27) * math.sin(a - 0.055))
        right = (cx + (radius - 27) * math.cos(a + 0.055), cy + (radius - 27) * math.sin(a + 0.055))
        pygame.draw.polygon(layer, AMBER if abs(roll) > math.radians(35) else WHITE, [tip, left, right])

        # Slip/skid: the ball, driven by lateral specific force.
        lateral = float(derived.accel_body[1]) if derived.accel_body is not None else 0.0
        slip = max(-1.0, min(1.0, lateral / 4.0))
        ball_x = cx + slip * 42
        ball_y = cy + radius - 6
        pygame.draw.rect(layer, (30, 34, 42), (cx - 52, ball_y - 8, 104, 16), border_radius=8)
        pygame.draw.circle(layer, WHITE if abs(slip) < 0.25 else AMBER, (int(ball_x), int(ball_y)), 6)

        # Aircraft reference symbol.
        pygame.draw.line(layer, AMBER, (cx - 62, cy), (cx - 22, cy), 3)
        pygame.draw.line(layer, AMBER, (cx + 22, cy), (cx + 62, cy), 3)
        pygame.draw.line(layer, AMBER, (cx - 22, cy), (cx - 22, cy + 9), 3)
        pygame.draw.line(layer, AMBER, (cx + 22, cy), (cx + 22, cy + 9), 3)
        pygame.draw.circle(layer, AMBER, (int(cx), int(cy)), 3)

        # Flight path marker: where the aircraft is actually going, which in a
        # slip or a stall is emphatically not where the nose is pointing.
        if derived.vtas > 15.0:
            fpm_x = cx + math.degrees(derived.beta) * self.pixels_per_degree
            fpm_y = cy + math.degrees(derived.alpha) * self.pixels_per_degree * math.cos(roll)
            fpm_y = max(rect.height * 0.08, min(rect.height * 0.92, fpm_y))
            colour = RED if stalled else GREEN
            pygame.draw.circle(layer, colour, (int(fpm_x), int(fpm_y)), 7, 2)
            pygame.draw.line(layer, colour, (fpm_x - 15, fpm_y), (fpm_x - 7, fpm_y), 2)
            pygame.draw.line(layer, colour, (fpm_x + 7, fpm_y), (fpm_x + 15, fpm_y), 2)
            pygame.draw.line(layer, colour, (fpm_x, fpm_y - 7), (fpm_x, fpm_y - 14), 2)

        surface.blit(layer, rect.topleft)
        pygame.draw.rect(surface, PANEL_EDGE, rect, 2)


class Tape:
    """A vertical rolling scale, used for airspeed and altitude."""

    def __init__(
        self,
        rect: pygame.Rect,
        fonts: Fonts,
        *,
        span: float,
        major: float,
        minor: float,
        side: str = "left",
        fmt: str = "{:.0f}",
        floor_at_zero: bool = False,
    ) -> None:
        self.rect = rect
        self.fonts = fonts
        self.span = span  # units visible top to bottom
        self.major = major
        self.minor = minor
        self.side = side
        self.fmt = fmt
        self.floor_at_zero = floor_at_zero

    def draw(self, surface, value: float, *, bands=(), bugs=()) -> None:
        rect = self.rect
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill((10, 12, 16, 210))

        cy = rect.height * 0.5
        scale = rect.height / self.span

        # Coloured bands: stall speed, VMO, whatever the aircraft declares.
        for low, high, colour in bands:
            y_high = cy - (high - value) * scale
            y_low = cy - (low - value) * scale
            top, bottom = min(y_high, y_low), max(y_high, y_low)
            top = max(0.0, top)
            bottom = min(float(rect.height), bottom)
            if bottom > top:
                x = 4 if self.side == "left" else rect.width - 12
                pygame.draw.rect(layer, colour, (x, top, 8, bottom - top))

        first = math.floor((value - self.span * 0.5) / self.minor) * self.minor
        tick = first
        while tick <= value + self.span * 0.5:
            y = cy - (tick - value) * scale
            # A tape whose quantity cannot go negative should not draw negative
            # graduations: -40 kt is not a slow aeroplane, it is a wrong one.
            if self.floor_at_zero and tick < 0.0:
                tick += self.minor
                continue
            if 0 <= y <= rect.height:
                is_major = abs(tick / self.major - round(tick / self.major)) < 1e-6
                length = 16 if is_major else 8
                if self.side == "left":
                    pygame.draw.line(layer, WHITE, (rect.width - length - 14, y), (rect.width - 14, y), 1)
                else:
                    pygame.draw.line(layer, WHITE, (14, y), (14 + length, y), 1)
                if is_major:
                    label = self.fonts.small.render(self.fmt.format(tick), True, WHITE)
                    if self.side == "left":
                        layer.blit(label, label.get_rect(midright=(rect.width - length - 19, y)))
                    else:
                        layer.blit(label, label.get_rect(midleft=(19 + length, y)))
            tick += self.minor

        # Bugs: autopilot targets and other selected values.
        for bug_value, colour in bugs:
            y = cy - (bug_value - value) * scale
            if 0 <= y <= rect.height:
                if self.side == "left":
                    points = [(rect.width - 4, y), (rect.width - 16, y - 7), (rect.width - 16, y + 7)]
                else:
                    points = [(4, y), (16, y - 7), (16, y + 7)]
                pygame.draw.polygon(layer, colour, points)

        surface.blit(layer, rect.topleft)

        # Current-value box, drawn on the panel so it overhangs the tape.
        box_w, box_h = 84, 34
        box_x = rect.right - box_w + 8 if self.side == "left" else rect.left - 8
        box = pygame.Rect(box_x, rect.centery - box_h // 2, box_w, box_h)
        pygame.draw.rect(surface, (6, 8, 12), box)
        pygame.draw.rect(surface, WHITE, box, 2)
        draw_text(
            surface, self.fonts.large, self.fmt.format(value), box.center, WHITE, anchor="center"
        )
        pygame.draw.rect(surface, PANEL_EDGE, rect, 1)


class Panel:
    """The full instrument panel below the chase view."""

    def __init__(self, rect: pygame.Rect, fonts: Fonts, model, sim) -> None:
        self.rect = rect
        self.fonts = fonts
        self.model = model
        self.sim = sim
        self.layout(rect)

        self.vmo = model.get("limitations", "vmo")
        self.mmo = model.get("limitations", "mmo")
        self.load_positive = model.get("limitations", "load_factor_positive", 2.5)
        self.is_military = model.category == "military"

    # The status column carries eleven labelled rows and a stick box beside
    # them. Below this width they overlap each other rather than the column
    # simply looking tight, so the stick block moves under the rows instead.
    STATUS_WIDE = 190

    def layout(self, rect: pygame.Rect) -> None:
        self.rect = rect
        pad = 10
        height = rect.height - 2 * pad
        ai_size = min(height, int(rect.width * 0.26))

        # The status column is the one part of the panel with a hard minimum:
        # everything else can be squeezed, but its rows are text. Placing the
        # attitude indicator at a fixed fraction of the window looked right at
        # the size it was designed against and collided with itself at a
        # smaller one, which is what the demo recordings run at.
        status_width = max(self.STATUS_WIDE, int(rect.width * 0.16))
        # 104 for the speed tape and its gap, 2 * pad for the status margins:
        # the offset is what the layout below actually consumes, not a guess.
        ai_x = max(rect.x + int(rect.width * 0.235), rect.x + status_width + 104 + 2 * pad)

        self.ai_rect = pygame.Rect(ai_x, rect.y + pad, ai_size, height)
        self.speed_rect = pygame.Rect(self.ai_rect.x - 104, rect.y + pad, 96, height)
        self.alt_rect = pygame.Rect(self.ai_rect.right + 8, rect.y + pad, 104, height)
        self.vsi_rect = pygame.Rect(self.alt_rect.right + 8, rect.y + pad, 62, height)

        # Engine gauges take only the width their engines need; whatever is
        # left over goes to the strip charts. On the single-engine fighter
        # that is most of the panel, which is exactly where a wide, mostly
        # empty engine box used to be.
        remaining = rect.right - self.vsi_rect.right - 24
        engines = max(1, len(getattr(getattr(self.sim, "fdm", None), "propulsion").engines))
        engine_width = min(max(150, 96 * engines + 44), remaining)
        self.engine_rect = pygame.Rect(
            self.vsi_rect.right + 12, rect.y + pad, engine_width, height
        )
        chart_width = rect.right - self.engine_rect.right - 22
        self.chart_rect = (
            pygame.Rect(self.engine_rect.right + 10, rect.y + pad, chart_width, height)
            if chart_width >= 90
            else pygame.Rect(0, 0, 0, 0)
        )
        self.status_rect = pygame.Rect(rect.x + pad, rect.y + pad, self.speed_rect.x - rect.x - 2 * pad, height)

        self.attitude = AttitudeIndicator(self.ai_rect, self.fonts)
        self.speed_tape = Tape(
            self.speed_rect,
            self.fonts,
            span=120.0,
            major=20.0,
            minor=10.0,
            side="left",
            floor_at_zero=True,
        )
        self.alt_tape = Tape(
            self.alt_rect, self.fonts, span=2000.0, major=500.0, minor=100.0, side="right"
        )

    # ---------------------------------------------------------------------

    def draw(self, surface, sim) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        pygame.draw.line(surface, PANEL_EDGE, self.rect.topleft, self.rect.topright, 2)

        derived = sim.fdm.state.derived
        diagnostics = sim.fdm.diagnostics

        self.attitude.draw(surface, derived, diagnostics.stalled)

        # -- airspeed ------------------------------------------------------
        vcas_kt = to_kt(derived.vcas)
        stall_kt = to_kt(diagnostics.stall_speed) if diagnostics.stall_speed > 0 else 0.0
        bands = []
        if stall_kt > 0:
            bands.append((0.0, stall_kt, RED))
            bands.append((stall_kt, stall_kt * 1.13, AMBER))
        bands.append((to_kt(self.vmo), to_kt(self.vmo) * 2.0, RED))
        bugs = []
        if sim.autopilot.thrust.value != "OFF":
            bugs.append((to_kt(sim.autopilot.targets.airspeed), MAGENTA))
        self.speed_tape.draw(surface, vcas_kt, bands=bands, bugs=bugs)

        # -- altitude ------------------------------------------------------
        bugs = []
        if sim.autopilot.vertical.value == "ALT":
            bugs.append((to_ft(sim.autopilot.targets.altitude), MAGENTA))
        self.alt_tape.draw(surface, to_ft(derived.altitude), bugs=bugs)

        self._draw_vsi(surface, derived)
        self._draw_engines(surface, sim, derived, diagnostics)
        self._draw_status(surface, sim, derived, diagnostics)

    # ---------------------------------------------------------------------

    def _draw_vsi(self, surface, derived) -> None:
        rect = self.vsi_rect
        pygame.draw.rect(surface, INSTRUMENT_BG, rect)
        pygame.draw.rect(surface, PANEL_EDGE, rect, 1)
        draw_text(surface, self.fonts.tiny, "V/S", (rect.centerx, rect.y + 10), DIM, "center")

        vs = to_fpm(derived.vertical_speed)
        cy = rect.centery
        usable = rect.height * 0.36

        for mark in (-4000, -2000, -1000, 0, 1000, 2000, 4000):
            # Non-linear scale: fine near zero, compressed at the extremes,
            # which is what makes a 200 fpm drift visible at all.
            t = math.copysign(min(1.0, abs(mark) / 4000.0) ** 0.62, mark)
            y = cy - t * usable
            pygame.draw.line(surface, DIM, (rect.x + 6, y), (rect.x + 16, y), 1)
            if mark % 2000 == 0 and mark != 0:
                draw_text(
                    surface, self.fonts.tiny, str(abs(mark) // 1000), (rect.x + 20, y), DIM, "midleft"
                )

        t = math.copysign(min(1.0, abs(vs) / 4000.0) ** 0.62, vs)
        y = cy - t * usable
        colour = WHITE if abs(vs) < 3000 else AMBER
        pygame.draw.line(surface, colour, (rect.x + 6, cy), (rect.right - 8, y), 3)
        pygame.draw.circle(surface, colour, (rect.right - 8, int(y)), 4)

        draw_text(
            surface,
            self.fonts.small,
            f"{vs:+.0f}",
            (rect.centerx, rect.bottom - 14),
            colour,
            "center",
        )

    def _draw_engines(self, surface, sim, derived, diagnostics) -> None:
        rect = self.engine_rect
        pygame.draw.rect(surface, INSTRUMENT_BG, rect)
        pygame.draw.rect(surface, PANEL_EDGE, rect, 1)

        engines = sim.fdm.propulsion.engines
        count = len(engines)
        column = rect.width / max(count, 1)

        for i, engine in enumerate(engines):
            x = rect.x + column * (i + 0.5)
            top = rect.y + 24

            label = f"ENG {i + 1}"
            colour = RED if engine.failed else WHITE
            draw_text(surface, self.fonts.tiny, label, (x, rect.y + 10), colour, "center")

            # N1 arc.
            bar_h = rect.height - 76
            bar = pygame.Rect(x - 17, top, 34, bar_h)
            pygame.draw.rect(surface, (26, 30, 38), bar)
            fill_h = int(bar_h * max(0.0, min(1.0, engine.n1)))
            fill_colour = GREEN
            if engine.afterburner:
                fill_colour = (255, 138, 52)
            elif engine.n1 > 1.0:
                fill_colour = AMBER
            if engine.failed:
                fill_colour = (72, 72, 78)
            pygame.draw.rect(surface, fill_colour, (bar.x, bar.bottom - fill_h, bar.width, fill_h))
            pygame.draw.rect(surface, PANEL_EDGE, bar, 1)

            draw_text(
                surface,
                self.fonts.small,
                f"{engine.n1 * 100:.0f}",
                (x, bar.bottom + 12),
                colour,
                "center",
            )
            draw_text(
                surface,
                self.fonts.tiny,
                f"{engine.egt - 273.15:.0f}C",
                (x, bar.bottom + 30),
                AMBER if engine.egt > 1050.0 else DIM,
                "center",
            )
            if engine.afterburner:
                draw_text(surface, self.fonts.tiny, "A/B", (x, top - 2), (255, 150, 60), "center")

        # Fuel and flow along the bottom.
        mass = sim.fdm.mass_properties
        fuel_fraction = (
            mass.fuel_mass / sim.fdm.mass_model.fuel_capacity
            if sim.fdm.mass_model.fuel_capacity > 0
            else 0.0
        )
        bar = pygame.Rect(rect.x + 8, rect.bottom - 20, rect.width - 16, 12)
        pygame.draw.rect(surface, (26, 30, 38), bar)
        colour = RED if fuel_fraction < 0.06 else (AMBER if fuel_fraction < 0.15 else CYAN)
        pygame.draw.rect(surface, colour, (bar.x, bar.y, int(bar.width * fuel_fraction), bar.height))
        pygame.draw.rect(surface, PANEL_EDGE, bar, 1)
        draw_text(
            surface,
            self.fonts.tiny,
            f"FUEL {mass.fuel_mass:,.0f} kg   FF {diagnostics.fuel_flow * 3600:,.0f} kg/h",
            (bar.centerx, bar.y - 9),
            DIM,
            "center",
        )

    def _draw_status(self, surface, sim, derived, diagnostics) -> None:
        rect = self.status_rect
        x, y = rect.x, rect.y + 2
        line = self.fonts.small.get_height() + 3

        mass = sim.fdm.mass_properties
        rows = [
            ("MACH", f"{derived.mach:0.3f}", AMBER if derived.mach > self.mmo else WHITE),
            ("AOA", f"{to_deg(derived.alpha):+5.1f}", RED if diagnostics.stalled else WHITE),
            ("G", f"{derived.load_factor:+5.2f}", RED if abs(derived.load_factor) > self.load_positive else WHITE),
            ("HDG", f"{(to_deg(derived.yaw) % 360):05.1f}", WHITE),
            ("TRK", f"{(to_deg(derived.track) % 360):05.1f}", DIM),
            ("GS", f"{to_kt(derived.ground_speed):5.0f} kt", DIM),
            ("TAS", f"{to_kt(derived.vtas):5.0f} kt", DIM),
            ("MASS", f"{mass.mass / 1000.0:5.1f} t", DIM),
            ("CG", f"{mass.cg[0]:+5.2f} m", AMBER if not sim.fdm.mass_model.cg_within_limits(mass.cg[0]) else DIM),
            ("L/D", f"{diagnostics.lift_to_drag:5.1f}", DIM),
            ("OAT", f"{derived.temperature - 273.15:+5.1f} C", DIM),
        ]

        # A narrow panel drops the rows that are diagnostic rather than
        # operational, so the ones a pilot flies on stay legible.
        if rect.width < self.STATUS_WIDE:
            rows = [row for row in rows if row[0] not in ("TRK", "L/D", "CG", "OAT")]

        for label, value, colour in rows:
            draw_text(surface, self.fonts.tiny, label, (x, y), DIM)
            draw_text(surface, self.fonts.small, value, (x + 54, y - 2), colour)
            y += line

        # Control positions: stick box and throttle, so a jammed or rate
        # limited surface is visible as a divergence between commanded and
        # actual rather than only as odd handling.
        surfaces = sim.surfaces
        box = pygame.Rect(max(rect.x + 118, rect.right - 78), rect.y + 4, 66, 66)
        pygame.draw.rect(surface, INSTRUMENT_BG, box)
        pygame.draw.rect(surface, PANEL_EDGE, box, 1)
        pygame.draw.line(surface, (40, 46, 56), (box.centerx, box.y), (box.centerx, box.bottom), 1)
        pygame.draw.line(surface, (40, 46, 56), (box.x, box.centery), (box.right, box.centery), 1)
        px = box.centerx + surfaces.aileron.position * box.width * 0.44
        py = box.centery + surfaces.elevator.position * box.height * 0.44
        pygame.draw.circle(surface, CYAN, (int(px), int(py)), 5)
        cmd_x = box.centerx + surfaces.aileron.commanded * box.width * 0.44
        cmd_y = box.centery + surfaces.elevator.commanded * box.height * 0.44
        pygame.draw.circle(surface, DIM, (int(cmd_x), int(cmd_y)), 7, 1)
        draw_text(surface, self.fonts.tiny, "STICK", (box.centerx, box.bottom + 8), DIM, "center")

        # Rudder strip.
        strip = pygame.Rect(box.x, box.bottom + 20, box.width, 10)
        pygame.draw.rect(surface, INSTRUMENT_BG, strip)
        pygame.draw.rect(surface, PANEL_EDGE, strip, 1)
        rx = strip.centerx + surfaces.rudder.position * strip.width * 0.45
        pygame.draw.line(surface, CYAN, (rx, strip.y + 1), (rx, strip.bottom - 1), 3)

        # Configuration.
        cfg_y = strip.bottom + 8
        gear = sim.surfaces.gear_position
        gear_text = "DOWN" if gear > 0.999 else ("UP" if gear < 0.001 else "TRANS")
        gear_colour = GREEN if gear > 0.999 else (AMBER if gear > 0.001 else DIM)
        draw_text(surface, self.fonts.tiny, f"GEAR {gear_text}", (box.x, cfg_y), gear_colour)
        draw_text(
            surface,
            self.fonts.tiny,
            f"FLAP {surfaces.flap.position * 100:.0f}%",
            (box.x, cfg_y + 15),
            GREEN if surfaces.flap.position > 0.01 else DIM,
        )
        draw_text(
            surface,
            self.fonts.tiny,
            f"SPBK {surfaces.speedbrake.position * 100:.0f}%",
            (box.x, cfg_y + 30),
            AMBER if surfaces.speedbrake.position > 0.01 else DIM,
        )
        draw_text(
            surface,
            self.fonts.tiny,
            f"THR  {sim.throttle * 100:.0f}%",
            (box.x, cfg_y + 45),
            WHITE,
        )

        # Airframe ice. Shown only once there is any, because a permanent
        # "ICE 0%" trains the eye to stop reading the line it appears on.
        ice = getattr(sim.fdm.aero, "ice", 0.0)
        anti_ice = getattr(getattr(sim, "pilot", None), "anti_ice", False)
        if ice > 0.02 or anti_ice:
            colour = DIM if ice <= 0.02 else (RED if ice > 0.5 else AMBER)
            draw_text(
                surface,
                self.fonts.tiny,
                f"ICE  {ice * 100:.0f}%" + ("  A/I" if anti_ice else ""),
                (box.x, cfg_y + 60),
                colour,
            )
