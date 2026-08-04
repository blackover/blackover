"""The game: pre-flight setup, then a chase-view flight with a full panel.

The render rate follows whatever the machine can manage; the flight model
always steps at exactly the configured dt. That separation is the whole point
of the frame accumulator -- a simulator whose physics depend on the frame rate
is not the same simulator on two different machines.

The renderer reads the state and feeds nothing back. The simulation kernel is
the sole authority on where the aircraft is.
"""

from __future__ import annotations

import math

import pygame

from ..core.clock import FrameAccumulator
from ..core.frames import dcm_body_to_ned
from ..core.model_package import PackageError, load_aircraft
from ..core.orchestrator import Simulation
from ..core.units import to_ft, to_kt
from ..telemetry.recorder import TelemetryRecorder, default_run_dir
from .config import DATA_ROOT, SimConditions
from .instruments import (
    AMBER,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    RED,
    WHITE,
    Fonts,
    Panel,
    draw_text,
)
from .mesh import build_mesh, gear_facets
from .renderer import Renderer, Runway, Sky
from .setup_screen import SetupScreen

WINDOW_SIZE = (1440, 900)
PANEL_FRACTION = 0.30

HELP_LINES = [
    ("Arrow keys", "pitch and roll  (DOWN pulls the nose up, as a stick does)"),
    ("Q / E", "rudder left and right  (steers the nosewheel on the ground)"),
    ("T / Y", "pitch trim nose up and nose down"),
    ("A / Z", "throttle up and down"),
    ("TAB", "afterburner  (war plane only, above 85 % throttle)"),
    ("G", "landing gear up and down"),
    ("F / V", "flaps extend and retract"),
    ("B", "wheel brakes (hold)"),
    ("SPACE", "speedbrake"),
    ("1 / 2 / 3", "autopilot: altitude hold, heading hold, speed hold"),
    ("4", "AUTO FLY  -- takes off, climbs and cruises by itself"),
    ("5", "AUTOLAND  -- routes to the runway, lands and stops"),
    ("0", "autopilot and autoflight off"),
    ("F5", "start and stop telemetry recording"),
    ("[ / ]", "chase camera closer and further"),
    ("P", "pause"),
    ("H", "this help"),
    ("R", "restart with the same conditions"),
    ("ESC", "back to the setup screen"),
]


class VirtualStick:
    """Keyboard axis with a finite rate and self-centring.

    A key is on or off, but a stick is not. Without a rate and a return to
    centre, every input is a step input, and step inputs into a lightly damped
    airframe make the aircraft look far twitchier than it is.
    """

    def __init__(self, rate: float = 2.4, centring: float = 3.2) -> None:
        self.value = 0.0
        self.rate = rate
        self.centring = centring

    def update(self, dt: float, demand: float) -> float:
        if demand != 0.0:
            self.value += demand * self.rate * dt
        else:
            decay = self.centring * dt
            if abs(self.value) <= decay:
                self.value = 0.0
            else:
                self.value -= math.copysign(decay, self.value)
        self.value = max(-1.0, min(1.0, self.value))
        return self.value


class Game:
    """Owns the window, the simulation and the loop that drives both."""

    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("AeroSim Lab -- flight simulator")
        self.surface = pygame.display.set_mode(WINDOW_SIZE, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.fonts = Fonts(1.0)
        self.conditions = SimConditions()
        self.show_help = False
        self.recorder: TelemetryRecorder | None = None
        self.record_from_start = False
        self.last_run_dir = None

    # ---------------------------------------------------------------------

    def run(self) -> None:
        while True:
            setup = SetupScreen(self.surface, self.conditions)
            chosen = setup.run(self.clock)
            if chosen is None:
                break
            self.conditions = chosen
            self.surface = pygame.display.get_surface()

            if not self.fly():
                break

        pygame.quit()

    # ---------------------------------------------------------------------

    def _build(self):
        model = load_aircraft(DATA_ROOT / self.conditions.aircraft)
        sim = Simulation(model, self.conditions)
        sky = Sky(self.conditions.time_of_day, self.conditions.visibility)
        renderer = Renderer(self.surface, sky)
        mesh = build_mesh(model)

        # Frame the aircraft from its own dimensions rather than a fixed
        # distance: 42 m behind a 37 m airliner fills the screen, and the same
        # 42 m behind a 15 m fighter loses it.
        length = model.get("geometry", "fuselage_length", 30.0)
        renderer.camera.distance = 1.75 * length
        renderer.camera.height = 0.30 * length

        # The scenario origin is the threshold, and a runway start lines the
        # aircraft up on it. The strip itself is drawn with an overrun at each
        # end so the aircraft is standing on pavement, not at its edge.
        runway = Runway(
            length=3200.0,
            width=45.0,
            heading=self.conditions.heading,
            elevation=self.conditions.field_elevation,
        )
        return model, sim, renderer, mesh, runway

    def fly(self) -> bool:
        """Fly one scenario. Returns False if the user closed the window."""
        try:
            model, sim, renderer, mesh, runway = self._build()
        except PackageError as exc:
            self._show_error(str(exc))
            return True

        accumulator = FrameAccumulator(sim.clock.dt, max_steps_per_frame=int(0.05 / sim.clock.dt) + 2)
        pitch_stick = VirtualStick(rate=1.9, centring=2.6)
        roll_stick = VirtualStick(rate=2.8, centring=3.4)
        yaw_stick = VirtualStick(rate=2.6, centring=4.0)

        panel = self._make_panel(model, sim)
        self.recorder = None
        if self.record_from_start:
            self._toggle_recording(sim)

        while True:
            wall_dt = self.clock.tick(60) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._stop_recording("window closed")
                    return False
                if event.type == pygame.VIDEORESIZE:
                    self.surface = pygame.display.set_mode((event.w, event.h), pygame.RESIZABLE)
                    renderer.resize(self.surface)
                    panel = self._make_panel(model, sim)
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self._stop_recording("left the flight")
                        return True
                    action = self._handle_key(event, sim, renderer)
                    if action == "restart":
                        self._stop_recording("restarted")
                        return self.fly()

            self._read_axes(sim, wall_dt, pitch_stick, roll_stick, yaw_stick)

            for _ in range(accumulator.add(wall_dt)):
                sim.step()
                if self.recorder is not None:
                    self.recorder.sample(sim)
                if sim.crashed:
                    self._stop_recording(sim.crash_reason or "crashed")
                    break

            self._draw(sim, renderer, panel, mesh, model, runway, wall_dt)
            pygame.display.flip()

    # -- telemetry ---------------------------------------------------------

    def _toggle_recording(self, sim) -> None:
        if self.recorder is not None:
            self._stop_recording("stopped by the pilot")
            return

        run_dir = default_run_dir()
        try:
            self.recorder = TelemetryRecorder(run_dir, sim.model, sim.conditions)
        except OSError as exc:
            sim.log("CAUTION", f"could not start recording: {exc}")
            self.recorder = None
            return
        self.last_run_dir = run_dir
        sim.log("INFO", f"recording to {run_dir}")

    def _stop_recording(self, reason: str) -> None:
        if self.recorder is None:
            return
        manifest = self.recorder.close(reason)
        self.recorder = None
        print(
            f"recorded {manifest.rows} samples over {manifest.duration_s:.1f} s "
            f"to {self.last_run_dir}"
        )

    def _make_panel(self, model, sim) -> Panel:
        width, height = self.surface.get_size()
        panel_height = int(height * PANEL_FRACTION)
        return Panel(
            pygame.Rect(0, height - panel_height, width, panel_height),
            self.fonts,
            model,
            sim,
        )

    # -- input -------------------------------------------------------------

    def _handle_key(self, event, sim, renderer) -> str | None:
        pilot = sim.pilot
        key = event.key

        if key == pygame.K_p:
            sim.paused = not sim.paused
            sim.log("INFO", "paused" if sim.paused else "resumed")
        elif key == pygame.K_h:
            self.show_help = not self.show_help
        elif key == pygame.K_r:
            return "restart"
        elif key == pygame.K_g:
            pilot.gear_down = not pilot.gear_down
            sim.log("INFO", f"gear {'down' if pilot.gear_down else 'up'} selected")
        elif key == pygame.K_f:
            pilot.flap = min(1.0, round(pilot.flap * 4 + 1) / 4)
            sim.log("INFO", f"flaps {pilot.flap * 100:.0f}%")
        elif key == pygame.K_v:
            pilot.flap = max(0.0, round(pilot.flap * 4 - 1) / 4)
            sim.log("INFO", f"flaps {pilot.flap * 100:.0f}%")
        elif key == pygame.K_SPACE:
            pilot.speedbrake = 0.0 if pilot.speedbrake > 0.5 else 1.0
        elif key == pygame.K_TAB:
            if sim.fdm.propulsion.has_afterburner:
                pilot.afterburner = not pilot.afterburner
                sim.log("INFO", f"afterburner {'armed' if pilot.afterburner else 'off'}")
            else:
                sim.log("CAUTION", "this aircraft has no thrust augmentation")
        elif key == pygame.K_1:
            sim.engage_altitude_hold()
        elif key == pygame.K_2:
            sim.engage_heading_hold()
        elif key == pygame.K_3:
            sim.engage_speed_hold()
        elif key == pygame.K_4:
            sim.engage_autoflight()
        elif key == pygame.K_5:
            sim.engage_autoland()
        elif key == pygame.K_0:
            sim.disengage_autoflight()
            sim.handover_trim()
            sim.autopilot.disengage()
            sim.log("INFO", "autopilot off")
        elif key == pygame.K_F5:
            self._toggle_recording(sim)
        elif key == pygame.K_LEFTBRACKET:
            renderer.camera.distance = max(14.0, renderer.camera.distance - 8.0)
        elif key == pygame.K_RIGHTBRACKET:
            renderer.camera.distance = min(220.0, renderer.camera.distance + 8.0)
        return None

    def _read_axes(self, sim, dt, pitch_stick, roll_stick, yaw_stick) -> None:
        keys = pygame.key.get_pressed()
        pilot = sim.pilot

        # DOWN pulls the nose up, which is what a stick does. Mapping UP to
        # nose-up feels right for about ten seconds and wrong for ever after.
        pitch_demand = (1.0 if keys[pygame.K_DOWN] else 0.0) - (1.0 if keys[pygame.K_UP] else 0.0)
        roll_demand = (1.0 if keys[pygame.K_RIGHT] else 0.0) - (1.0 if keys[pygame.K_LEFT] else 0.0)
        yaw_demand = (1.0 if keys[pygame.K_e] else 0.0) - (1.0 if keys[pygame.K_q] else 0.0)

        pilot.pitch = pitch_stick.update(dt, pitch_demand)
        pilot.roll = roll_stick.update(dt, roll_demand)
        pilot.yaw = yaw_stick.update(dt, yaw_demand)

        if keys[pygame.K_a]:
            sim.throttle = min(1.0, sim.throttle + 0.55 * dt)
        if keys[pygame.K_z]:
            sim.throttle = max(0.0, sim.throttle - 0.55 * dt)

        # Trim moves slowly on purpose: it is a wheel, not a switch, and a
        # trim that snaps is a trim you cannot set finely enough to fly hands off.
        if keys[pygame.K_t]:
            sim.pitch_trim = max(-0.6, sim.pitch_trim - 0.10 * dt)
        if keys[pygame.K_y]:
            sim.pitch_trim = min(0.6, sim.pitch_trim + 0.10 * dt)

        pilot.brake = 1.0 if keys[pygame.K_b] else 0.0

    # -- drawing -----------------------------------------------------------

    def _draw(self, sim, renderer, panel, mesh, model, runway, dt) -> None:
        width, height = self.surface.get_size()
        panel_height = int(height * PANEL_FRACTION)
        view = pygame.Rect(0, 0, width, height - panel_height)

        previous_clip = self.surface.get_clip()
        self.surface.set_clip(view)
        renderer.resize_view(view)

        state = sim.fdm.state
        renderer.camera.follow(state, dt)
        renderer.draw_sky()
        renderer.draw_terrain(state, sim.conditions.field_elevation, runway)

        # Rebuild the combined facet list only when the gear has visibly moved.
        # A fresh list every frame would defeat the renderer's mesh cache, and
        # the gear is stationary for all but a few seconds of a flight.
        detent = round(sim.surfaces.gear_position * 24)
        if detent != getattr(self, "_gear_detent", None):
            self._gear_detent = detent
            self._facets = mesh + gear_facets(model, detent / 24.0)
        renderer.draw_aircraft(state, self._facets, dcm_body_to_ned(state.quaternion))

        self.surface.set_clip(previous_clip)

        self._draw_hud(sim, view)
        panel.draw(self.surface, sim)
        self._draw_messages(sim, view)

        if self.show_help:
            self._draw_help(view)
        if sim.paused:
            self._banner(view, "PAUSED", AMBER)
        if sim.crashed:
            self._banner(view, sim.crash_reason.upper(), RED, subtitle="R to restart, ESC for setup")

    def _draw_hud(self, sim, view) -> None:
        derived = sim.fdm.state.derived
        surface = self.surface

        # Top-left: what the flight is.
        lines = [
            (f"{sim.model.display_name}", WHITE),
            (f"T + {sim.time:7.1f} s     {1 / max(self.clock.get_time() / 1000.0, 1e-3):.0f} fps", DIM),
            (f"step {sim.clock.step_index:,}  at  {sim.clock.rate_hz:.0f} Hz", DIM),
        ]
        y = 12
        for text, colour in lines:
            draw_text(surface, self.fonts.small, text, (14, y), colour)
            y += 19

        if self.recorder is not None:
            pygame.draw.circle(surface, RED, (20, y + 7), 6)
            draw_text(
                surface,
                self.fonts.small,
                f"REC  {self.recorder.rows} samples",
                (34, y),
                RED,
            )
            y += 19

        # Top-right: autoflight phase over the autopilot mode, then the
        # environment.
        auto = getattr(sim, "autoflight", None)
        top = 12
        if auto is not None and auto.annunciation():
            draw_text(
                surface,
                self.fonts.medium,
                auto.annunciation(),
                (view.right - 14, top),
                GREEN,
                "topright",
            )
            top += 24
        draw_text(
            surface,
            self.fonts.medium if top == 12 else self.fonts.small,
            sim.autopilot.annunciation(),
            (view.right - 14, top),
            MAGENTA if sim.autopilot.engaged else DIM,
            "topright",
        )
        wind = sim.wind.mean_wind_ned(derived.altitude)
        wind_speed = math.hypot(float(wind[0]), float(wind[1]))
        wind_from = (math.degrees(math.atan2(-wind[1], -wind[0]))) % 360
        draw_text(
            surface,
            self.fonts.small,
            f"wind {wind_from:03.0f} / {to_kt(wind_speed):.0f} kt    turb {sim.conditions.turbulence}",
            (view.right - 14, 42),
            DIM,
            "topright",
        )
        draw_text(
            surface,
            self.fonts.small,
            f"AGL {to_ft(derived.altitude_agl):,.0f} ft",
            (view.right - 14, 62),
            GREEN if derived.altitude_agl > 300 else AMBER,
            "topright",
        )

        # Centre-bottom of the view: the annunciators that matter right now.
        warnings = []
        if sim.fdm.diagnostics.stalled:
            warnings.append(("STALL", RED))
        if sim.fdm.diagnostics.overspeed:
            warnings.append(("OVERSPEED", RED))
        if sim.fdm.diagnostics.out_of_envelope:
            warnings.append(("ENVELOPE", AMBER))
        if sim.hydraulic_pressure < 0.8:
            warnings.append(("HYD PRESS", AMBER))
        if any(e.failed for e in sim.fdm.propulsion.engines):
            warnings.append(("ENG FAIL", RED))
        if sim.fdm.mass_model.fuel_mass < 0.06 * sim.fdm.mass_model.fuel_capacity:
            warnings.append(("FUEL LOW", AMBER))

        x = view.centerx - (len(warnings) * 92) // 2
        for text, colour in warnings:
            box = pygame.Rect(x, view.bottom - 46, 86, 26)
            pygame.draw.rect(surface, (28, 12, 12) if colour is RED else (32, 26, 10), box, border_radius=3)
            pygame.draw.rect(surface, colour, box, 2, border_radius=3)
            draw_text(surface, self.fonts.small, text, box.center, colour, "center")
            x += 92

    def _draw_messages(self, sim, view) -> None:
        recent = sim.events[-6:]
        y = view.bottom - 60 - 18 * len(recent)
        for event in recent:
            colour = {"WARNING": RED, "CAUTION": AMBER}.get(event.severity, DIM)
            draw_text(
                self.surface,
                self.fonts.tiny,
                f"{event.time:6.1f}  {event.message}",
                (14, y),
                colour,
            )
            y += 18

    def _draw_help(self, view) -> None:
        surface = self.surface
        width = 470
        height = 34 + 21 * len(HELP_LINES)
        box = pygame.Rect(view.centerx - width // 2, view.centery - height // 2, width, height)
        panel = pygame.Surface(box.size, pygame.SRCALPHA)
        panel.fill((12, 14, 19, 238))
        surface.blit(panel, box.topleft)
        pygame.draw.rect(surface, CYAN, box, 1)

        draw_text(surface, self.fonts.small, "CONTROLS", (box.x + 16, box.y + 10), CYAN)
        y = box.y + 32
        for key, description in HELP_LINES:
            draw_text(surface, self.fonts.tiny, key, (box.x + 16, y), WHITE)
            draw_text(surface, self.fonts.tiny, description, (box.x + 108, y), DIM)
            y += 21

    def _banner(self, view, text, colour, subtitle: str = "") -> None:
        surface = self.surface
        image = self.fonts.huge.render(text, True, colour)
        rect = image.get_rect(center=(view.centerx, view.centery - 20))
        backdrop = pygame.Surface((rect.width + 48, rect.height + 24), pygame.SRCALPHA)
        backdrop.fill((10, 10, 14, 210))
        surface.blit(backdrop, backdrop.get_rect(center=rect.center))
        surface.blit(image, rect)
        if subtitle:
            draw_text(
                surface, self.fonts.small, subtitle, (view.centerx, rect.bottom + 16), DIM, "center"
            )

    # -- replay ------------------------------------------------------------

    def replay(self, run_dir) -> None:
        """Animate a recorded run. Nothing here can advance the physics.

        The session exposes the same objects the live simulation does, so the
        renderer and the panel are unchanged -- and there is no code path from
        either of them back into a flight model, because a replay does not have
        one.
        """
        from ..telemetry.replay import ReplayError, ReplaySession, describe, load_run

        try:
            run = load_run(run_dir)
            model = load_aircraft(DATA_ROOT / run.manifest.get("aircraft", ""))
            session = ReplaySession(model, run)
        except (ReplayError, PackageError, OSError) as exc:
            self._show_error(str(exc))
            return

        if not run.hash_verified and run.hash_expected:
            print(
                f"WARNING: telemetry hash does not match the manifest.\n"
                f"  expected {run.hash_expected}\n  actual   {run.hash_actual}\n"
                "  The file has changed since it was recorded."
            )

        sky = Sky(session.conditions.time_of_day, session.conditions.visibility)
        renderer = Renderer(self.surface, sky)
        length = model.get("geometry", "fuselage_length", 30.0)
        renderer.camera.distance = 1.75 * length
        renderer.camera.height = 0.30 * length
        mesh = build_mesh(model)
        runway = Runway(
            length=3200.0,
            width=45.0,
            heading=session.conditions.heading,
            elevation=session.conditions.field_elevation,
        )
        panel = self._make_panel(model, session)
        provenance = describe(run)
        self._gear_detent = None

        while True:
            wall_dt = self.clock.tick(60) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.VIDEORESIZE:
                    self.surface = pygame.display.set_mode(
                        (event.w, event.h), pygame.RESIZABLE
                    )
                    renderer.resize(self.surface)
                    panel = self._make_panel(model, session)
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return
                    if event.key in (pygame.K_SPACE, pygame.K_p):
                        session.paused = not session.paused
                    elif event.key == pygame.K_h:
                        self.show_help = not self.show_help
                    elif event.key == pygame.K_LEFT:
                        session.step_frames(-int(session.clock.rate_hz))
                    elif event.key == pygame.K_RIGHT:
                        session.step_frames(int(session.clock.rate_hz))
                    elif event.key == pygame.K_HOME:
                        session.apply(0)
                    elif event.key == pygame.K_END:
                        session.apply(run.rows - 1)
                    elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                        session.playback_speed = min(8.0, session.playback_speed * 2.0)
                    elif event.key == pygame.K_MINUS:
                        session.playback_speed = max(0.125, session.playback_speed / 2.0)
                    elif event.key == pygame.K_LEFTBRACKET:
                        renderer.camera.distance = max(14.0, renderer.camera.distance - 8.0)
                    elif event.key == pygame.K_RIGHTBRACKET:
                        renderer.camera.distance = min(220.0, renderer.camera.distance + 8.0)

            session.advance(wall_dt)
            self._draw(session, renderer, panel, mesh, model, runway, wall_dt)
            self._draw_replay_overlay(session, run, provenance)
            pygame.display.flip()

    def _draw_replay_overlay(self, session, run, provenance) -> None:
        """Provenance, playhead and integrity, drawn over the replay."""
        surface = self.surface
        width, height = surface.get_size()
        view = pygame.Rect(0, 0, width, height - int(height * PANEL_FRACTION))

        # Provenance sits over sky or terrain depending on attitude, so it gets
        # its own backdrop rather than relying on whatever is behind it.
        card = pygame.Rect(view.centerx - 150, 6, 300, 30 + 15 * len(provenance))
        backdrop = pygame.Surface(card.size, pygame.SRCALPHA)
        backdrop.fill((10, 12, 17, 190))
        surface.blit(backdrop, card.topleft)

        draw_text(surface, self.fonts.medium, "REPLAY", (view.centerx, 10), CYAN, "midtop")
        y = 34
        for line in provenance:
            colour = (
                RED
                if "NOT VERIFIED" in line
                else (GREEN if "verified" in line else (170, 178, 192))
            )
            draw_text(surface, self.fonts.tiny, line, (view.centerx, y), colour, "midtop")
            y += 15

        # Playhead.
        bar = pygame.Rect(view.x + 40, view.bottom - 22, view.width - 80, 8)
        strip = pygame.Surface((view.width, 42), pygame.SRCALPHA)
        strip.fill((10, 12, 17, 175))
        surface.blit(strip, (view.x, bar.y - 22))
        pygame.draw.rect(surface, (30, 34, 42), bar, border_radius=4)
        pygame.draw.rect(
            surface, CYAN, (bar.x, bar.y, int(bar.width * session.progress), bar.height),
            border_radius=4,
        )
        pygame.draw.rect(surface, DIM, bar, 1, border_radius=4)
        draw_text(
            surface,
            self.fonts.tiny,
            f"{session.time:6.1f} / {session.duration:.1f} s"
            f"    x{session.playback_speed:g}"
            f"    {'PAUSED' if session.paused else 'PLAYING'}"
            "    SPACE pause   arrows scrub   +/- speed   ESC exit",
            (bar.x, bar.y - 15),
            AMBER if session.paused else (170, 178, 192),
        )

    def _show_error(self, message: str) -> None:
        self.surface.fill((16, 10, 10))
        y = 40
        draw_text(self.surface, self.fonts.medium, "Aircraft package failed to load", (40, y), RED)
        for line in message.splitlines()[:24]:
            y += 22
            draw_text(self.surface, self.fonts.tiny, line[:150], (40, y), AMBER)
        draw_text(self.surface, self.fonts.small, "any key to continue", (40, y + 40), DIM)
        pygame.display.flip()
        waiting = True
        while waiting:
            for event in pygame.event.get():
                if event.type in (pygame.KEYDOWN, pygame.QUIT):
                    waiting = False
            self.clock.tick(30)


def main() -> None:
    Game().run()


if __name__ == "__main__":
    main()
