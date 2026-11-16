"""Build the wheel with SWAT+ inside and check it the way a user gets it: installed with nothing else into a new
environment, no SWAT+ build of their own, no source checkout, and run.

    uv run python tools/check_wheel.py bin/swatplus-62.0.1-exchange [--out dist]

The wheel is built with SWATPLUS_BMI_BUNDLE (hatch_build.py), tagged for this platform (tools/wheel_tag.py), and
installed by uv into a new virtual environment. There, with a home folder of its own (so no SWAT+ that
``swatplus-bmi build`` put there is found) and from a folder outside the checkout, a copy of Osu_1hru is run 30 days
through the wrapper, and the executable it used must be the wheel's own. Prints the wheel's path.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUN = """
import sys
from pathlib import Path
from swatplus_bmi import SwatPlusBmi
from swatplus_bmi.bmi import find_executable
exe = find_executable()
assert "_bin" in exe.parts, f"not the wheel's own executable: {exe}"
m = SwatPlusBmi()
m.initialize(sys.argv[1])
m.update_until(30)
q = m.get_value("channel_water__volume_outflow_rate", __import__("numpy").zeros(1))
m.finalize()
print(f"ran 30 days with {exe.name}; channel outflow {q[0]:.4g}")
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--refdata", type=Path, help="SWAT+'s refdata folder (default: the build's)")
    a = ap.parse_args(argv)
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found")
    tag = os.environ.get("SWATPLUS_TAG", "62.0.1")
    refdata = a.refdata or Path(os.environ.get("SWATPLUS_SRC", ROOT / "build" / tag / "swatplus")) / "refdata"

    a.out.mkdir(parents=True, exist_ok=True)
    before = set(a.out.glob("*.whl"))
    env = dict(os.environ, SWATPLUS_BMI_BUNDLE=str(a.exe.resolve()))
    subprocess.run([uv, "build", "--wheel", "-o", str(a.out), str(ROOT)], check=True, env=env)
    wheel, = set(a.out.glob("*.whl")) - before
    print(f"built {wheel.name}")

    with tempfile.TemporaryDirectory(prefix="swatplus-wheel-") as tmp:
        tmp = Path(tmp)
        venv = tmp / "venv"
        subprocess.run([uv, "venv", "-q", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", str(venv)],
                       check=True)
        py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run([uv, "pip", "install", "-q", "--python", str(py), str(wheel)], check=True)
        shutil.copytree(refdata / "Osu_1hru", tmp / "Osu_1hru")
        home = tmp / "home"
        home.mkdir()
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("SWATPLUS_", "VIRTUAL_ENV", "PYTHON", "UV_"))}
        clean.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "AppData" / "Local"),
                     XDG_DATA_HOME=str(home / ".local" / "share"))
        r = subprocess.run([str(py), "-I", "-c", RUN, str(tmp / "Osu_1hru")], cwd=tmp, env=clean,
                           capture_output=True, text=True)
        print(r.stdout, end="")
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return r.returncode
    print(wheel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
