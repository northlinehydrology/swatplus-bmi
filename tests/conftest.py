"""Shared helpers: test projects, stock runs and output comparison.

The test projects are the examples that ship with SWAT+ (``refdata/`` in the
SWAT+ repository at the tag being built). ``build.sh --stock`` builds the
stock executable the comparisons need.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TAG = os.environ.get("SWATPLUS_TAG", "62.0.1")
BIN = ROOT / "bin"
STOCK = BIN / f"swatplus-{TAG}-stock"
EXCHANGE = BIN / f"swatplus-{TAG}-exchange"
RUNS = ROOT / "tests" / "runs"

# files that exist only in exchange runs, or hold the console
NOT_OUTPUT = {"exchange.log", "exchange.err", "console.txt"}

# clock readings SWAT+ writes to simulation.out: wall time, run date, build time
CLOCK = [re.compile(rb"\d{1,2}:\s?\d{1,2}:\s?\d{1,2}"),
         re.compile(rb"\d{1,2}/\s?\d{1,2}/\d{4}"),
         re.compile(rb"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")]


def refdata() -> Path:
    for cand in (os.environ.get("SWATPLUS_SRC"), ROOT / "build" / TAG / "swatplus",
                 ROOT / ".cache" / f"swatplus-{TAG}"):
        if cand and (Path(cand) / "refdata").is_dir():
            return Path(cand) / "refdata"
    pytest.skip("no SWAT+ checkout with refdata/: run ./build.sh first")


def need(*exes: Path) -> None:
    for exe in exes:
        if not exe.exists():
            pytest.skip(f"{exe.name} not built: run ./build.sh --stock")


def project(name: str, dest: str, *, daily: bool = False, years: tuple[int, int] | None = None,
            edit=None) -> Path:
    """A fresh copy of a SWAT+ example project in tests/runs/<dest>.

    ``daily`` turns on daily, monthly, yearly and average annual printing for
    every object, from the first day; ``years`` shortens the run.
    """
    src = refdata() / name
    out = RUNS / dest
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    if daily:
        print_everything(out / "print.prt")
    if years:
        set_years(out / "time.sim", *years)
    if edit:
        edit(out)
    return out


def print_everything(prt: Path) -> None:
    lines = prt.read_text().splitlines()
    out = []
    for i, line in enumerate(lines):
        f = line.split()
        if i == 2:                               # nyskip day_start yrc_start day_end yrc_end interval
            f[0] = "0"
            line = "  ".join(f)
        elif len(f) == 5 and all(x in ("y", "n") for x in f[1:]):
            line = f"{f[0]:<24}" + "".join(f"{'y':>14}" for _ in range(4))
        out.append(line)
    prt.write_text("\n".join(out) + "\n")


def set_years(sim: Path, first: int, last: int) -> None:
    lines = sim.read_text().splitlines()
    f = lines[2].split()
    lines[2] = f"{0:>8}{first:>11}{0:>10}{last:>10}{f[4]:>10}"
    sim.write_text("\n".join(lines) + "\n")


def run_stock(run_dir: Path, exe: Path = STOCK, *args: str) -> None:
    with open(run_dir / "console.txt", "wb") as con:
        subprocess.run([str(exe), *args], cwd=run_dir, stdout=con, stderr=subprocess.STDOUT,
                       check=True, timeout=3600)
    assert (run_dir / "success.fin").exists()


def masked(path: Path) -> bytes:
    data = path.read_bytes()
    if path.name == "simulation.out":
        for rx in CLOCK:
            data = rx.sub(b"<clock>", data)
    return data


def differences(a: Path, b: Path) -> list[str]:
    """Every way the outputs in two run folders differ; empty means identical.

    All files are compared byte for byte, inputs included; the only thing
    left out is the clock readings in simulation.out.
    """
    fa = {p.relative_to(a) for p in a.rglob("*") if p.is_file() and p.name not in NOT_OUTPUT}
    fb = {p.relative_to(b) for p in b.rglob("*") if p.is_file() and p.name not in NOT_OUTPUT}
    diffs = [f"only in {a.name}: {p}" for p in sorted(fa - fb)]
    diffs += [f"only in {b.name}: {p}" for p in sorted(fb - fa)]
    for p in sorted(fa & fb):
        if masked(a / p) != masked(b / p):
            diffs.append(f"differs: {p}")
    return diffs


def output_count(run_dir: Path) -> int:
    return sum(1 for p in run_dir.glob("*") if p.suffix in (".txt", ".out", ".csv"))
