"""The head-up display of a gunfight.

A dogfight is unflyable without a few specific symbols, and each one here
exists because a pilot cannot get the information any other way:

**The target box.** Where the bandit is on screen, drawn even when it is a
single pixel four kilometres away. At the ranges BFM is flown, an aircraft
is a dot that disappears against terrain, and losing sight is losing the
fight. The box is drawn at the bandit's projected position whether or not
its mesh is visible, which is the one piece of help this HUD gives that a
real canopy does not -- and a flat screen with a sixty degree field of view
needs it, because a real pilot has a head that turns and peripheral vision
that notices movement.

**The lead-computing pipper.** A gun fires bullets, which take time to get
there, so the nose must point *ahead* of a crossing target. The pipper is
where the bullets would be when they arrive: the bandit's position
advanced along its own velocity by the time of flight. Putting the pipper
on the bandit is the whole act of gun tracking, and without it the player
is guessing.

**The tracking cone.** The envelope the score is computed from, drawn, so
there is nothing hidden about what counts. A circle at the cone's
half-angle around the nose.

**The closure caret and range bar.** Range alone is not enough: a bandit at
500 m opening at 200 m/s is gone, and one at 500 m closing at 200 m/s is an
overshoot about to happen. The caret shows which.

**Angle off tail.** Who is winning, in one number. Thirty degrees means you
are behind it; a hundred and fifty means it is behind you.

Nothing here is a weapon model. The gun envelope is a scoring volume, and
``docs/known_limitations.md`` says so.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

from ..combat.engagement import (
    TRACK_CONE,
    TRACK_MAX_LOS_RATE,
    TRACK_MAX_RANGE,
    TRACK_MIN_RANGE,
)
from ..core.units import to_kt
from .instruments import AMBER, CYAN, DIM, GREEN, RED, WHITE, draw_text

#: Muzzle velocity used to compute the lead pipper, m/s. A representative
#: 20 mm cannon round. It sets where the pipper sits and nothing else -- no
#: round is ever created, and nothing is ever hit.
MUZZLE_VELOCITY = 1030.0

#: Longest time of flight the pipper is computed for, seconds. Past this the
#: lead angle is meaningless because the bandit will have manoeuvred.
MAX_TIME_OF_FLIGHT = 1.4

#: Colour of the bandit's symbology.
BANDIT = (244, 120, 96)
BANDIT_DIM = (150, 86, 74)


def _project(renderer, position_ned: np.ndarray):
    """Screen position of a world point, or None if it is behind the camera."""
    camera = renderer.camera
    local = (position_ned - camera.position) @ camera.basis.T
    if local[2] < 0.35:
        return None
    x = renderer.centre[0] + renderer.focal * local[0] / local[2]
    y = renderer.centre[1] - renderer.focal * local[1] / local[2]
    return x, y, float(local[2])


class CombatHud:
    """Draws the gunsight, the bandit's symbology and the fight's state."""

    def __init__(self, fonts) -> None:
        self.fonts = fonts

    # -- the gunsight ------------------------------------------------------

    def draw(self, surface, renderer, view, player_state, engagement) -> None:
        state = engagement.state
        geometry = state.geometry
        if geometry is None:
            return

        bandit_state = engagement.bandit.fdm.state
        self._draw_tracking_cone(surface, renderer, player_state, state)
        self._draw_target_box(surface, renderer, bandit_state, geometry, state)
        self._draw_pipper(surface, renderer, player_state, bandit_state, geometry)
        self._draw_range_bar(surface, view, geometry, state)
        self._draw_readout(surface, view, geometry, state, bandit_state)

    def _draw_tracking_cone(self, surface, renderer, player_state, state) -> None:
        """A circle round the nose at the envelope's half-angle.

        Drawn where the nose actually points, which is not the middle of the
        screen: the chase camera sits behind and above, so the boresight
        projects below centre. Drawing it at the centre would put the scoring
        volume somewhere the aircraft is not pointing.
        """
        from ..core.frames import dcm_body_to_ned

        nose = dcm_body_to_ned(player_state.quaternion) @ np.array([1.0, 0.0, 0.0])
        boresight = _project(renderer, player_state.position + nose * 1200.0)
        if boresight is None:
            return
        x, y, _ = boresight

        radius = renderer.focal * math.tan(TRACK_CONE)
        # The cone is a solid angle, so its projected radius is set by the
        # focal length alone and does not change with range -- the same
        # nine degrees is a wide net at four kilometres and a tight one at
        # two hundred metres, which is exactly why range is scored too.
        colour = GREEN if state.player.tracking else DIM
        pygame.draw.circle(surface, colour, (int(x), int(y)), int(radius), 1)
        for angle in (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi):
            dx, dy = math.cos(angle), math.sin(angle)
            pygame.draw.line(
                surface,
                colour,
                (int(x + dx * radius * 0.82), int(y + dy * radius * 0.82)),
                (int(x + dx * radius * 1.18), int(y + dy * radius * 1.18)),
                1,
            )

    def _draw_target_box(self, surface, renderer, bandit_state, geometry, state) -> None:
        """A box round the bandit, sized by range, with a lead line."""
        projected = _project(renderer, bandit_state.position)
        if projected is None:
            # Behind the camera. Point at it rather than drawing nothing: in
            # a fight, where the bandit went is the single thing you most
            # need, and a bandit that vanishes from a flat screen is lost in
            # a way it would not be from a cockpit with a head in it.
            self._draw_offscreen_arrow(surface, renderer, bandit_state)
            return
        x, y, depth = projected

        # The box shrinks with range but never below something findable, and
        # never grows past the aircraft it is drawn around.
        half = max(10.0, min(90.0, 2600.0 / max(depth, 30.0)))
        colour = BANDIT if geometry.range < TRACK_MAX_RANGE * 2.0 else BANDIT_DIM
        if state.bandit.tracking:
            # It has a solution on us. The one thing that must be impossible
            # to miss.
            colour = RED
        rect = pygame.Rect(int(x - half), int(y - half), int(half * 2), int(half * 2))
        corner = int(half * 0.42)
        for cx, cy, sx, sy in (
            (rect.left, rect.top, 1, 1),
            (rect.right, rect.top, -1, 1),
            (rect.left, rect.bottom, 1, -1),
            (rect.right, rect.bottom, -1, -1),
        ):
            pygame.draw.line(surface, colour, (cx, cy), (cx + sx * corner, cy), 2)
            pygame.draw.line(surface, colour, (cx, cy), (cx, cy + sy * corner), 2)

        # Range in hundreds of metres under the box, the way a radar reads.
        draw_text(
            surface,
            self.fonts.small,
            f"{geometry.range:.0f}",
            (int(x), rect.bottom + 4),
            colour,
            "midtop",
        )
        if state.bandit.tracking:
            draw_text(
                surface,
                self.fonts.small,
                "GUNS",
                (int(x), rect.top - 18),
                RED,
                "midbottom",
            )

    def _draw_offscreen_arrow(self, surface, renderer, bandit_state) -> None:
        """A caret at the edge of the view pointing at a bandit behind us.

        Drawn from the direction to the bandit in the camera's own axes,
        projected onto the screen plane. A point behind the camera cannot be
        projected -- that is what makes it off screen -- so the arrow uses
        the direction alone and places itself on the edge.
        """
        camera = renderer.camera
        local = (bandit_state.position - camera.position) @ camera.basis.T
        angle = math.atan2(-float(local[1]), float(local[0]))

        rect = surface.get_clip()
        radius = min(rect.width, rect.height) * 0.36
        cx, cy = renderer.centre
        x = cx + math.cos(angle) * radius
        y = cy + math.sin(angle) * radius

        size = 13.0
        tip = (x + math.cos(angle) * size, y + math.sin(angle) * size)
        left = (
            x + math.cos(angle + 2.4) * size * 0.8,
            y + math.sin(angle + 2.4) * size * 0.8,
        )
        right = (
            x + math.cos(angle - 2.4) * size * 0.8,
            y + math.sin(angle - 2.4) * size * 0.8,
        )
        pygame.draw.polygon(surface, BANDIT, [tip, left, right], 2)
        draw_text(
            surface,
            self.fonts.small,
            "BANDIT",
            (int(x - math.cos(angle) * 22), int(y - math.sin(angle) * 22)),
            BANDIT,
            "center",
        )

    def _draw_pipper(self, surface, renderer, player_state, bandit_state, geometry) -> None:
        """The lead-computing gunsight: where to point to hit it.

        Solved the way a gunsight solves it. Bullets leave the nose at
        muzzle velocity plus the aircraft's own velocity and fly roughly
        straight; the bandit keeps going on its present velocity. The pipper
        is drawn where the bandit will be when a round fired now would get
        there, so flying the pipper onto the bandit is the gun solution.
        """
        if geometry.range > TRACK_MAX_RANGE * 1.6:
            return
        closing = MUZZLE_VELOCITY + float(
            player_state.velocity_ned @ geometry.line_of_sight
        )
        if closing <= 1.0:
            return
        time_of_flight = min(geometry.range / closing, MAX_TIME_OF_FLIGHT)
        future = bandit_state.position + bandit_state.velocity_ned * time_of_flight

        projected = _project(renderer, future)
        if projected is None:
            return
        x, y, _ = projected
        inside = geometry.antenna_train <= TRACK_CONE
        settled = geometry.line_of_sight_rate <= TRACK_MAX_LOS_RATE
        colour = GREEN if (inside and settled) else AMBER
        pygame.draw.circle(surface, colour, (int(x), int(y)), 7, 2)
        pygame.draw.circle(surface, colour, (int(x), int(y)), 1)
        if not settled:
            # Crossing too fast to hold: say so where the eye already is.
            draw_text(
                surface,
                self.fonts.small,
                "RATE",
                (int(x) + 12, int(y) - 7),
                AMBER,
            )

    # -- the numbers -------------------------------------------------------

    def _draw_range_bar(self, surface, view, geometry, state) -> None:
        """A vertical range scale with the envelope marked and a closure caret."""
        top, height = view.top + 150, 230
        x = view.right - 120
        pygame.draw.line(surface, DIM, (x, top), (x, top + height), 1)

        span = 4000.0

        def y_for(metres: float) -> int:
            return int(top + height * min(1.0, max(0.0, metres / span)))

        # The scoring band, drawn as a bracket on the scale.
        band_top, band_bottom = y_for(TRACK_MIN_RANGE), y_for(TRACK_MAX_RANGE)
        pygame.draw.line(surface, GREEN, (x - 5, band_top), (x - 5, band_bottom), 3)
        for metres in (1000.0, 2000.0, 3000.0, 4000.0):
            y = y_for(metres)
            pygame.draw.line(surface, DIM, (x, y), (x + 6, y), 1)
            draw_text(
                surface,
                self.fonts.small,
                f"{metres / 1000.0:.0f}",
                (x + 10, y - 7),
                DIM,
            )

        y = y_for(geometry.range)
        colour = GREEN if state.player.tracking else BANDIT
        pygame.draw.polygon(
            surface, colour, [(x - 14, y), (x - 4, y - 6), (x - 4, y + 6)]
        )

        # Closure as a caret alongside: up is closing, down is opening.
        rate = max(-250.0, min(250.0, -geometry.closure))
        tip = y - int(rate * 0.12)
        pygame.draw.line(surface, CYAN, (x + 22, y), (x + 22, tip), 2)
        draw_text(
            surface,
            self.fonts.small,
            f"{-geometry.closure:+.0f}",
            (x + 28, min(y, tip) - 8),
            CYAN,
        )

    def _draw_readout(self, surface, view, geometry, state, bandit_state) -> None:
        """The BFM numbers, under the range scale on the right.

        On the right rather than the bottom left, where the event log
        already lives: a stall warning and an angle off tail printed over
        each other are two pieces of information lost rather than one
        gained. Keeping all the combat symbology in one column also means
        the eye learns one place to look for it.
        """
        x, y = view.right - 210, view.top + 410
        aot = math.degrees(geometry.angle_off_tail)
        # Angle off tail is the scoreboard of a turning fight: under ninety
        # and you are behind it, over ninety and it is behind you.
        aot_colour = GREEN if aot < 60.0 else (AMBER if aot < 120.0 else RED)

        rows = [
            ("AOT", f"{aot:5.0f} deg", aot_colour),
            ("ATA", f"{math.degrees(geometry.antenna_train):5.0f} deg", WHITE),
            ("RNG", f"{geometry.range:5.0f} m", WHITE),
            (
                "dE",
                f"{geometry.energy_advantage:+5.0f} m",
                GREEN if geometry.energy_advantage > 0.0 else AMBER,
            ),
            ("BANDIT", f"{to_kt(bandit_state.derived.vtas):5.0f} kt", BANDIT),
        ]
        for label, value, colour in rows:
            draw_text(surface, self.fonts.small, label, (x, y), DIM)
            draw_text(surface, self.fonts.small, value, (x + 58, y), colour)
            y += 18

        # What the bandit is doing, and why. Showing the reason turns an
        # opponent that merely behaves into one that can be read, which is
        # the difference between losing and learning why you lost.
        adversary = state.adversary
        if adversary is not None:
            draw_text(
                surface,
                self.fonts.small,
                f"{adversary.manoeuvre.value}",
                (x, y + 4),
                AMBER,
            )
            # On its own line: the reasons run to a couple of dozen
            # characters and this column is against the right edge.
            draw_text(
                surface,
                self.fonts.small,
                adversary.reason,
                (x, y + 22),
                DIM,
            )

    # -- the scoreboard ----------------------------------------------------

    def draw_score(self, surface, view, state) -> None:
        """Tracking time, top centre, with the live stretch called out."""
        x = view.centerx
        draw_text(
            surface,
            self.fonts.small,
            f"YOU {state.player.total:5.1f}s"
            f"     BANDIT {state.bandit.total:5.1f}s",
            (x, 12),
            WHITE,
            "midtop",
        )
        if state.player.tracking:
            draw_text(
                surface,
                self.fonts.medium,
                f"TRACKING  {state.player.current:.1f}s",
                (x, 32),
                GREEN,
                "midtop",
            )
        elif state.bandit.tracking:
            draw_text(
                surface,
                self.fonts.medium,
                "BANDIT TRACKING -- BREAK",
                (x, 32),
                RED,
                "midtop",
            )
