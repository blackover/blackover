"""Entry point: ``python -m aerosim``."""

from __future__ import annotations

import sys


def main() -> int:
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
        Game().run()
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


if __name__ == "__main__":
    raise SystemExit(main())
