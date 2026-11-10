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
#
# pygame comes from pygame-ce, the community edition, which installs the same
# ``pygame`` module and is a drop-in replacement for everything this game
# uses. The original ``pygame`` package has not been released since 2.6.1 in
# September 2024 and has no ready-made build for Python 3.14 on Windows, so
# pip falls back to compiling it from source -- which needs a C compiler and
# fails on current setuptools with "No module named
# 'setuptools._distutils.msvccompiler'". pygame-ce ships builds for 3.10
# through 3.15.
NEEDED = {
    "pygame": "pygame-ce>=2.5",
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


def _upstream_pygame_installed() -> bool:
    """Is the original ``pygame`` distribution present, rather than pygame-ce?

    Both install into the same ``pygame`` folder. Putting pygame-ce on top of
    an existing pygame leaves a mixture of the two that fails in confusing
    ways, so if the original is there it has to come out first.
    """
    try:
        from importlib import metadata

        metadata.distribution("pygame")
        return True
    except Exception:
        return False


def install(requirements: list[str]) -> bool:
    if any(r.startswith("pygame-ce") for r in requirements) and _upstream_pygame_installed():
        print(
            "\nThe original 'pygame' package is installed but cannot be imported,\n"
            "and pygame-ce cannot safely be installed on top of it. Remove it\n"
            "first, then run this again:\n"
            f"    {sys.executable} -m pip uninstall pygame\n",
            file=sys.stderr,
        )
        return False

    print("Installing: " + ", ".join(requirements))
    # --prefer-binary: take the newest version that has a ready-made build
    # over a newer one that would have to be compiled. Compiling needs a C
    # toolchain most machines do not have, and the error it produces when it
    # fails is a page of build-system traceback that says nothing useful.
    command = [
        sys.executable, "-m", "pip", "install", "--prefer-binary", *requirements
    ]
    try:
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"could not run pip: {exc}", file=sys.stderr)
        return False
    if completed.returncode != 0:
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(
            "\npip could not install the packages above.\n"
            "\n"
            "The usual cause is that one of them has no ready-made build for\n"
            f"Python {version} on this computer yet, so pip tried to compile it\n"
            "from source, which needs a C compiler. If the output above mentions\n"
            "'Failed to build' or 'msvccompiler', that is what happened.\n"
            "\n"
            "Python 3.12 or 3.13 is the safe choice: every package this game\n"
            "uses has ready-made builds for both. Or try installing directly:\n"
            f"    {sys.executable} -m pip install --prefer-binary -r {REQUIREMENTS}\n",
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
