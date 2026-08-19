"""Entry point: ``python -m aerosim``."""

from __future__ import annotations

import argparse
import sys


def build_parser(prog: str = "python -m aerosim") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "AeroSim Lab -- a flight simulator with a pre-flight setup screen, "
            "a chase view and a full instrument panel."
        ),
    )
    parser.add_argument(
        "--replay",
        metavar="RUN_DIR",
        help=(
            "animate a recorded run instead of flying. The replay reads "
            "telemetry and feeds nothing back."
        ),
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="start recording telemetry as soon as the flight begins (F5 in flight)",
    )
    parser.add_argument(
        "--design",
        metavar="AIRCRAFT",
        help=(
            "print a design report for an aircraft package and exit. Needs no "
            "display: it interrogates the models rather than flying them."
        ),
    )
    parser.add_argument(
        "--design-altitude",
        type=float,
        default=20000.0,
        metavar="FT",
        help="altitude to grade performance and handling at (default 20000 ft)",
    )
    return parser


def main(argv: list[str] | None = None, prog: str = "python -m aerosim") -> int:
    args = build_parser(prog).parse_args(argv)

    if args.design:
        return _design(args.design, args.design_altitude)

    # Imported purely to check availability and give a useful message rather
    # than a traceback from three modules deep.
    try:
        import pygame  # noqa: F401  (availability probe)
    except ImportError:
        print(
            "pygame is not installed.\n\n"
            "    pip install -r requirements-aerosim.txt\n",
            file=sys.stderr,
        )
        return 1

    from .game.app import Game

    try:
        game = Game()
        if args.replay:
            game.replay(args.replay)
        else:
            game.record_from_start = args.record
            game.run()
    except pygame.error as exc:
        # Almost always a headless machine. SDL's own message ("No available
        # video device") does not suggest what to do about it.
        print(
            f"could not open a window: {exc}\n\n"
            "This is a windowed simulator and needs a display. Over SSH, "
            "forward one with 'ssh -X'; in a container, share the host's "
            "X socket or use a virtual display such as 'xvfb-run -a "
            "python -m aerosim'.\n",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


def _design(aircraft: str, altitude_ft: float) -> int:
    """Print a design report. No window, no flying -- just the models."""
    from .analysis.design import design_report, format_report
    from .core.model_package import PackageError, available_aircraft, load_aircraft
    from .core.units import ft
    from .game.config import DATA_ROOT

    try:
        model = load_aircraft(DATA_ROOT / aircraft)
    except (PackageError, FileNotFoundError, NotADirectoryError) as exc:
        names = ", ".join(sorted(p.name for p in available_aircraft(DATA_ROOT)))
        print(f"{exc}\n\navailable: {names}\n", file=sys.stderr)
        return 1

    altitude = ft(altitude_ft)
    modes = _modes_at(model, altitude)
    print(format_report(design_report(model, altitude=altitude, modes=modes)))
    return 0


def _modes_at(model, altitude: float):
    """Trim the aircraft and linearise about it.

    The modes need a trimmed aircraft, and trimming needs the whole
    simulation, so this builds one -- headless, and thrown away afterwards.
    Returns an empty mapping rather than raising if trim will not converge:
    an un-trimmable design is worth reporting the *rest* of the figures for.
    """
    from .core.orchestrator import Simulation
    from .analysis.modes import natural_modes
    from .game.config import SimConditions, StartMode

    cruise = model.get("limitations", "vmo") * 0.72
    conditions = SimConditions(
        aircraft=model.name,
        start_mode=StartMode.AIRBORNE,
        altitude=altitude,
        airspeed=cruise,
        flight_assist=False,
        cloud_cover=0.0,
    )
    try:
        sim = Simulation(model, conditions)
        for _ in range(int(3.0 / sim.clock.dt)):
            sim.step()
        if sim.crashed:
            return {}
        return natural_modes(sim.fdm)
    except Exception:  # noqa: BLE001 -- a report without modes beats no report
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
