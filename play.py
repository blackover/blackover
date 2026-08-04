#!/usr/bin/env python3
"""Start the flight simulator.

    python play.py

That is the whole thing. If pygame, NumPy or PyYAML are missing it offers to
install them first, so a fresh checkout needs no setup step and no reading.

Everything the game itself accepts still works here:

    python play.py --record             record telemetry from the start
    python play.py --replay runs/<dir>  watch a recorded flight back
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements-aerosim.txt"

# import name -> pip name
NEEDED = {
    "pygame": "pygame>=2.5",
    "numpy": "numpy>=1.24",
    "yaml": "PyYAML>=6.0",
}
MINIMUM_PYTHON = (3, 10)


def missing() -> list[str]:
    absent = []
    for module, requirement in NEEDED.items():
        try:
            importlib.import_module(module)
        except ImportError:
            absent.append(requirement)
    return absent


def install(requirements: list[str]) -> bool:
    print("Installing: " + ", ".join(requirements))
    command = [sys.executable, "-m", "pip", "install", *requirements]
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"could not run pip: {exc}", file=sys.stderr)
        return False
    if completed.returncode != 0:
        print(
            "\npip failed. Install the dependencies yourself with:\n"
            f"    {sys.executable} -m pip install -r {REQUIREMENTS}\n",
            file=sys.stderr,
        )
        return False
    # A package installed into the running interpreter is not on the import
    # path caches yet.
    importlib.invalidate_caches()
    return True


def main() -> int:
    if sys.version_info < MINIMUM_PYTHON:
        print(
            f"AeroSim Lab needs Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or "
            f"newer; this is {sys.version.split()[0]}.",
            file=sys.stderr,
        )
        return 1

    absent = missing()
    if absent:
        print("AeroSim Lab needs a few packages that are not installed yet:")
        for requirement in absent:
            print(f"  - {requirement}")
        # Non-interactive shells (CI, a double-clicked launcher with no
        # console) get on with it rather than blocking on a prompt nobody
        # will ever see.
        if sys.stdin is not None and sys.stdin.isatty():
            answer = input("\nInstall them now? [Y/n] ").strip().lower()
            if answer not in ("", "y", "yes"):
                print(f"\n    {sys.executable} -m pip install -r {REQUIREMENTS}\n")
                return 1
        if not install(absent):
            return 1
        print()

    sys.path.insert(0, str(ROOT))
    from aerosim.__main__ import main as run_game

    return run_game(sys.argv[1:], prog="python play.py")


if __name__ == "__main__":
    raise SystemExit(main())
