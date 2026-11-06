"""Shared helpers: test projects, stock runs and output comparison.

The test projects are the examples that ship with SWAT+ (``refdata/`` in the
SWAT+ repository at the tag being built). ``build.sh --stock`` builds the
stock executable the comparisons need. A missing executable or project skips
a test on a workstation and fails it in CI (where $CI is set), so a CI run
can't pass by testing nothing.
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
CHECK = BIN / f"swatplus-{TAG}-check"          # exchange with array bounds checked
IN_CI = bool(os.environ.get("CI"))
RUNS = ROOT / "tests" / "runs"

# files that exist only in exchange runs, or hold the console
NOT_OUTPUT = {"exchange.log", "exchange.err", "console.txt"}

# what exchange.log says when a branch or a load couldn't carry an open file
NOT_CARRIED = (b"partway through", b"could not be opened again")

# clock readings SWAT+ writes to simulation.out: wall time, run date, build time
CLOCK = [re.compile(rb"\d{1,2}:\s?\d{1,2}:\s?\d{1,2}"),
         re.compile(rb"\d{1,2}/\s?\d{1,2}/\d{4}"),
         re.compile(rb"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")]


def refdata() -> Path:
    for cand in (os.environ.get("SWATPLUS_SRC"), ROOT / "build" / TAG / "swatplus",
                 ROOT / ".cache" / f"swatplus-{TAG}"):
        if cand and (Path(cand) / "refdata").is_dir():
            return Path(cand) / "refdata"
    missing("no SWAT+ checkout with refdata/: run ./build.sh first")


def missing(why: str) -> None:
    """Skip on a workstation, fail in CI."""
    if IN_CI:
        pytest.fail(why)
    pytest.skip(why)


def need(*exes: Path) -> None:
    for exe in exes:
        if not exe.exists():
            flag = {CHECK: " --check"}.get(exe, " --stock")
            missing(f"{exe.name} not built: run ./build.sh{flag}")


# Run folders made during a test, removed once it passes (kept when it fails, or with
# SWATPLUS_BMI_KEEP_RUNS=1). A single Ames_sub1 run can write gigabytes.
_made: list[Path] = []


def made(path: Path) -> Path:
    """Register a folder a test makes under tests/runs, so it's cleaned up after the test."""
    _made.append(Path(path))
    return Path(path)


def fresh(dest: str) -> Path:
    """An empty path in tests/runs for a branch or a saved state, cleaned up after the test."""
    d = RUNS / dest
    if d.exists():
        shutil.rmtree(d)
    return made(d)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


@pytest.fixture(autouse=True)
def _clean_runs(request):
    """Remove the run folders a test made, once it has passed. Folders made by module
    fixtures are registered before this starts, so they stay for the module's tests."""
    start = len(_made)
    yield
    rep = getattr(request.node, "rep_call", None)
    if os.environ.get("SWATPLUS_BMI_KEEP_RUNS") or rep is None or not rep.passed:
        return
    for d in _made[start:]:
        shutil.rmtree(d, ignore_errors=True)
    del _made[start:]


def ames_light(run: Path) -> None:
    """Ames_sub1 asks for daily per-layer soil carbon diagnostics, about 8.5 GB a run.
    Turn the daily ones off; monthly and yearly stay, the soil carbon layer files with them."""
    from swatplus_bmi.project import set_print
    set_print(run, ["hru_cb", "hru_cb_vars"], intervals=["daily"], on=False)


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
    return made(out)


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
    left out is the clock readings in simulation.out. An exchange.log that
    says a branch or a load left a file behind counts as a difference too.
    """
    fa = {p.relative_to(a) for p in a.rglob("*") if p.is_file() and p.name not in NOT_OUTPUT}
    fb = {p.relative_to(b) for p in b.rglob("*") if p.is_file() and p.name not in NOT_OUTPUT}
    diffs = [f"{d.name}/exchange.log: {line.decode(errors='replace').strip()}"
             for d in (a, b) if (d / "exchange.log").exists()
             for line in (d / "exchange.log").read_bytes().splitlines()
             if any(w in line for w in NOT_CARRIED)]
    diffs += [f"only in {a.name}: {p}" for p in sorted(fa - fb)]
    diffs += [f"only in {b.name}: {p}" for p in sorted(fb - fa)]
    for p in sorted(fa & fb):
        if masked(a / p) != masked(b / p):
            diffs.append(f"differs: {p}")
    return diffs


def output_count(run_dir: Path) -> int:
    return sum(1 for p in run_dir.glob("*") if p.suffix in (".txt", ".out", ".csv"))
