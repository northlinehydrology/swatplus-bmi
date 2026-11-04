"""The line protocol between Python and a SWAT+ process in exchange mode.

SWAT+ is started with ``--exchange`` in its project folder. It sends a
catalog, then pauses before each day and answers commands on standard input.
Every reply line starts with ``@``. Lines that don't are SWAT+ console
messages (only seen where SWAT+ can't move them to ``exchange.log``) and are
appended to that log here.

This module knows nothing about BMI; ``swatplus_bmi.bmi`` builds on it.
"""

from __future__ import annotations

import copy
import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

PROTOCOL = 1


class ExchangeError(RuntimeError):
    """SWAT+ refused a command or stopped unexpectedly."""


@dataclasses.dataclass
class Grid:
    name: str
    names: list[str]
    x: np.ndarray        # longitude, degrees
    y: np.ndarray        # latitude, degrees
    area_ha: np.ndarray

    @property
    def size(self) -> int:
        return len(self.names)


@dataclasses.dataclass
class Variable:
    name: str
    grid: str
    role: str            # "in" (settable) or "out"
    units: str


@dataclasses.dataclass
class Catalog:
    protocol: int
    model: str
    start_year: int
    start_day: int       # day of year of the first simulated day
    days: int
    grids: dict[str, Grid]
    variables: dict[str, Variable]


class Exchange:
    """One SWAT+ process in exchange mode.

    ``days_done`` counts completed days. Between commands SWAT+ is paused
    before day ``days_done + 1`` (or has ended, once ``ended`` is true).
    """

    def __init__(self, executable: str | os.PathLike, run_dir: str | os.PathLike):
        self.run_dir = Path(run_dir)
        self.executable = Path(executable)
        self.days_done = 0
        self.ended = False
        self.year = 0
        self.day_of_year = 0
        self.pid = None
        self._fifos = None
        self._log = self.run_dir / "exchange.log"
        self._err = open(self.run_dir / "exchange.err", "wb")
        self.proc = subprocess.Popen(
            [str(self.executable.resolve()), "--exchange"],
            cwd=self.run_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._err)           # buffered: replies are read a line at a time, commands flushed
        self._in, self._out = self.proc.stdin, self.proc.stdout
        self.pid = self.proc.pid
        self.catalog = self._read_catalog()

    # -- low level ------------------------------------------------------

    def _send(self, line: str) -> None:
        try:
            self._in.write((line + "\n").encode())
            self._in.flush()
        except BrokenPipeError:
            raise ExchangeError(self._died("SWAT+ closed its input")) from None

    def _line(self) -> str:
        while True:
            raw = self._out.readline()
            if not raw:
                raise ExchangeError(self._died("SWAT+ stopped"))
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("@"):
                if line.startswith("@error"):
                    raise ExchangeError(line[len("@error"):].strip())
                return line
            with open(self._log, "a") as f:     # console message on a shared stdout
                f.write(line + "\n")

    def _expect(self, tag: str) -> list[str]:
        line = self._line()
        word, *rest = line.split()
        if word != tag:
            raise ExchangeError(f"expected {tag}, got: {line}")
        return rest

    def _died(self, what: str) -> str:
        code = self.proc.wait(timeout=30) if self.proc else "unknown: a branch"
        msg = f"{what} (exit code {code})"
        err = self.run_dir / "exchange.err"
        if err.exists() and err.stat().st_size:
            msg += ":\n" + err.read_text(errors="replace")[-2000:]
        elif self._log.exists():
            msg += "; last lines of exchange.log:\n" + "".join(
                self._log.read_text(errors="replace").splitlines(True)[-15:])
        return msg

    def _read_catalog(self) -> Catalog:
        grids: dict[str, Grid] = {}
        variables: dict[str, Variable] = {}
        info: dict[str, list[str]] = {}
        while True:
            word, *rest = self._line().split(maxsplit=1)
            rest = rest[0] if rest else ""
            if word == "@ready":
                break
            if word == "@grid":
                gname, n = rest.split()
                grids[gname] = Grid(gname, [], np.empty(0), np.empty(0), np.empty(0))
                rows = []
                for _ in range(int(n)):
                    _, obj = self._line().split(maxsplit=1)
                    g, _i, name, x, y, area = obj.split()
                    rows.append((name, float(x), float(y), float(area)))
                grids[gname] = Grid(gname, [r[0] for r in rows],
                                    np.array([r[1] for r in rows]),
                                    np.array([r[2] for r in rows]),
                                    np.array([r[3] for r in rows]))
            elif word == "@var":
                name, grid, role, units = rest.split(maxsplit=3)
                variables[name] = Variable(name, grid, role, units)
            else:
                info[word] = rest.split()
        protocol = int(info["@hello"][1])
        if protocol != PROTOCOL:
            raise ExchangeError(f"SWAT+ speaks exchange protocol {protocol}, this wrapper {PROTOCOL}")
        self._paused()
        return Catalog(protocol=protocol, model=" ".join(info.get("@model", [])),
                       start_year=int(info["@start"][0]), start_day=int(info["@start"][1]),
                       days=int(info["@days"][0]), grids=grids, variables=variables)

    def _paused(self) -> None:
        word, *rest = self._line().split()
        if word == "@paused":
            self.days_done, self.year, self.day_of_year = map(int, rest)
        elif word == "@ended":
            self.days_done = int(rest[0])
            self.ended = True
        else:
            raise ExchangeError(f"expected @paused or @ended, got: {word} {' '.join(rest)}")

    # -- commands -------------------------------------------------------

    def run(self, days: int = 1) -> None:
        """Run ``days`` days, then pause again (or end)."""
        if self.ended:
            raise ExchangeError("the simulation has ended")
        self._send(f"run {int(days)}")
        self._paused()

    def get(self, name: str) -> np.ndarray:
        self._send(f"get {name}")
        rest = self._expect("@value")
        n, vals = int(rest[0]), rest[1:]
        if len(vals) != n:
            raise ExchangeError(f"{name}: expected {n} values, got {len(vals)}")
        return np.array(vals, dtype=np.float32)

    def set(self, name: str, index: int, value: float) -> None:
        """Set one value (0-based index) for the next day only."""
        v = np.float32(value)
        self._send(f"set {name} {int(index) + 1} {np.format_float_scientific(v, unique=True)}")
        self._expect("@ok")

    def branch(self, run_dir: str | os.PathLike) -> "Exchange":
        """A copy of this paused SWAT+ process that goes on in ``run_dir``.

        SWAT+ writes out its buffers, the run folder is copied to
        ``run_dir``, and the process is duplicated with fork. Both copies
        are paused at the same point with the same state; each continues on
        its own and writes its own outputs. Mac and Linux only.
        """
        if sys.platform == "win32":
            raise ExchangeError("branch needs fork, which Windows doesn't have")
        if self.ended:
            raise ExchangeError("the simulation has ended; nothing left to branch")
        new = Path(run_dir).resolve()
        if new.exists():
            raise FileExistsError(f"{new} already exists")
        self._send("flush")
        self._expect("@ok")
        shutil.copytree(self.run_dir, new, ignore=shutil.ignore_patterns("exchange.err"))
        fifo_dir = Path(tempfile.mkdtemp(prefix="swatplus-branch-"))
        f_in, f_out = fifo_dir / "commands", fifo_dir / "replies"
        os.mkfifo(f_in)
        os.mkfifo(f_out)
        q = lambda p: '"' + str(p).replace('"', '') + '"'
        self._send(f"branch {q(new)} {q(self.run_dir.resolve())} {q(f_in)} {q(f_out)}")
        pid = int(self._expect("@branched")[0])

        b = copy.copy(self)
        b.run_dir, b.proc, b.pid, b._err = new, None, pid, None
        b._log = new / "exchange.log"
        b._fifos = fifo_dir
        b._in = open(f_in, "wb")                    # same order the branch opens them
        b._out = open(f_out, "rb")
        b._paused()
        return b

    def save_state(self, path: str | os.PathLike) -> Path:
        """Save SWAT+'s state at this pause to the folder ``path``.

        The folder holds ``state.bin`` (every module variable, written by
        the generated serializer) and ``run/``, a copy of the run folder
        with every output file complete up to this pause.
        """
        if self.ended:
            raise ExchangeError("the simulation has ended; nothing left to save")
        out = Path(path).resolve()
        if out.exists():
            raise FileExistsError(f"{out} already exists")
        out.mkdir(parents=True)
        q = lambda p: '"' + str(p).replace('"', '') + '"'
        self._send(f"save {q(out / 'state.bin')}")
        self._expect("@ok")
        shutil.copytree(self.run_dir, out / "run", ignore=shutil.ignore_patterns("exchange.err"))
        (out / "day").write_text(f"{self.days_done}\n")
        return out

    @classmethod
    def load_state(cls, executable, path: str | os.PathLike, run_dir: str | os.PathLike) -> "Exchange":
        """A new SWAT+ process in ``run_dir`` with the state saved in ``path``.

        ``run_dir`` becomes a copy of the saved run folder. SWAT+ starts
        there, runs forward to the saved day without computing it, reads the
        saved state over its own, and the saved output files are put back,
        so it carries on exactly as the saved run would have.
        """
        src = Path(path).resolve()
        run = Path(run_dir).resolve()
        if run.exists():
            shutil.rmtree(run)
        shutil.copytree(src / "run", run)
        x = cls(executable, run)
        q = lambda p: '"' + str(p).replace('"', '') + '"'
        x._send(f"load {q(src / 'state.bin')}")
        x._expect("@restore")
        for f in (src / "run").iterdir():           # put back the saved files SWAT+ rewrote at startup
            if f.is_file() and f.name != "exchange.err":
                shutil.copy2(f, run / f.name)
        x._send("restored")
        x._paused()
        return x

    def finish(self, timeout: float | None = None) -> int:
        """Let SWAT+ run to the end without pausing and complete its outputs."""
        if self.proc is not None and self.proc.poll() is not None:
            return self.proc.returncode
        self._send("finish")
        return self._wait(timeout)

    def stop(self, timeout: float | None = 30) -> int:
        """Stop SWAT+ where it is; its output files stay incomplete."""
        if self.proc is not None and self.proc.poll() is not None:
            return self.proc.returncode
        self._send("stop")
        self._expect("@bye")
        return self._wait(timeout)

    def _wait(self, timeout):
        # read anything left (console lines on a shared stdout) so SWAT+ never blocks on a full pipe
        while self._out.readline():
            pass
        if self.proc is not None:
            code = self.proc.wait(timeout=timeout)
        else:                    # a branch is not our child: it has ended when its replies close
            code = 0
        self._close()
        return code

    def _close(self):
        for f in (self._in, self._out, self._err):
            try:
                if f:
                    f.close()
            except OSError:
                pass
        if self._fifos is not None:
            shutil.rmtree(self._fifos, ignore_errors=True)
            self._fifos = None

    def __del__(self):
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
