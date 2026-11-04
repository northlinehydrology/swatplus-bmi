"""Build SWAT+ with the exchange mode: the ``swatplus-bmi`` command.

    swatplus-bmi build            # fetch SWAT+ 62.0.1, patch it, build the exchange executable
    swatplus-bmi build --stock    # also the unpatched release, for comparisons
    swatplus-bmi where            # where the executable is

The patch, the exchange module, the C helper and the generators ship inside
the package; the build goes to a folder of your own (``SWATPLUS_BMI_HOME``,
or by default ``~/Library/Application Support/swatplus-bmi`` on macOS and
``~/.local/share/swatplus-bmi`` elsewhere). Needs git, make and gfortran;
CMake comes with the package.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TAG = "62.0.1"


def assets() -> Path:
    """The folder with build.sh, patches/, fortran/ and tools/: inside the installed
    package, or the repository root in a source checkout."""
    inside = Path(__file__).resolve().parent / "_assets"
    if (inside / "build.sh").exists():
        return inside
    repo = Path(__file__).resolve().parents[2]
    if (repo / "build.sh").exists():
        return repo
    raise FileNotFoundError("swatplus-bmi's build files are missing from this installation")


def home() -> Path:
    """Where builds and executables go."""
    if os.environ.get("SWATPLUS_BMI_HOME"):
        return Path(os.environ["SWATPLUS_BMI_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "swatplus-bmi"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "swatplus-bmi"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "swatplus-bmi"


def cmake() -> str:
    """CMake: the one installed with the package, else the one on PATH."""
    try:
        import cmake as _cmake                          # the PyPI cmake package
        exe = Path(_cmake.CMAKE_BIN_DIR) / ("cmake.exe" if sys.platform == "win32" else "cmake")
        if exe.exists():
            return str(exe)
    except ImportError:
        pass
    found = shutil.which("cmake")
    if found:
        return found
    raise FileNotFoundError("CMake not found: pip install cmake, or install it from cmake.org")


def build(tag: str = DEFAULT_TAG, jobs: int = 2, stock: bool = False, nosave: bool = False,
          patch: str | None = None) -> Path:
    """Build SWAT+ ``tag`` with the exchange mode; returns the executable's path."""
    for tool in ("git", "make", "gfortran"):
        if shutil.which(tool) is None and not (tool == "gfortran" and os.environ.get("FC")):
            raise FileNotFoundError(f"{tool} not found; swatplus-bmi build needs git, make and gfortran")
    out = home()
    env = dict(os.environ, SWATPLUS_BMI_WORK=str(out / "build"), SWATPLUS_BMI_BIN=str(out / "bin"),
               PYTHON=sys.executable, CMAKE=cmake())
    args = ["bash", str(assets() / "build.sh"), "--tag", tag, "--jobs", str(jobs)]
    if stock:
        args.append("--stock")
    if nosave:
        args.append("--nosave")
    if patch:
        args += ["--patch", patch]
    subprocess.run(args, env=env, check=True)
    return out / "bin" / f"swatplus-{tag}-exchange"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="swatplus-bmi", description="Build SWAT+ with the exchange mode.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="fetch, patch and build SWAT+")
    b.add_argument("--tag", default=DEFAULT_TAG, help=f"SWAT+ release (default {DEFAULT_TAG})")
    b.add_argument("--jobs", type=int, default=2, help="parallel compile jobs (default 2)")
    b.add_argument("--stock", action="store_true", help="also build the unpatched release")
    b.add_argument("--nosave", action="store_true", help="also build the saved-locals check variant")
    b.add_argument("--patch", help="build --tag with another supported release's patch")
    sub.add_parser("where", help="print where the exchange executable is")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        exe = build(a.tag, a.jobs, a.stock, a.nosave, a.patch)
        print(f"SWAT+ with the exchange mode: {exe}")
    else:
        from .bmi import find_executable
        try:
            print(find_executable())
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
