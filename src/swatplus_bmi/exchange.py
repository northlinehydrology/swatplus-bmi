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

PROTOCOL = 4


class ExchangeError(RuntimeError):
    """SWAT+ refused a command or stopped unexpectedly."""


@dataclasses.dataclass
class Grid:
    name: str
    names: list[str]
    x: np.ndarray        # longitude, degrees
    y: np.ndarray        # latitude, degrees
    area_ha: np.ndarray
    z: np.ndarray | None = None      # soil layers: mid-depth below the surface, m

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
    build: str           # hash of the swatplus-bmi sources the executable was built from
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
                rows = []
                for _ in range(int(n)):
                    _, obj = self._line().split(maxsplit=1)
                    g, _i, name, *xyz = obj.split()
                    rows.append((name, *map(float, xyz)))
                col = lambda k: np.array([r[k] for r in rows], dtype=float)
                grids[gname] = Grid(gname, [r[0] for r in rows], col(1), col(2), col(3),
                                    col(4) if rows and len(rows[0]) > 4 else None)
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
                       build=info["@build"][0],
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
        return np.array(vals, dtype=np.float64)

    def set(self, name: str, index: int, value: float) -> None:
        """Set one value (0-based index) for the next day only. SWAT+ refuses, with an
        ExchangeError, a value it would not use as given."""
        v = float(value)
        if not np.isfinite(v):
            raise ExchangeError(f"{name}: a value must be a finite number, not {v}")
        self._send(f"set {name} {int(index) + 1} {v!r}")
        self._expect("@ok")

    def set_all(self, name: str, values) -> None:
        """Set every object on the variable's grid for the next day, in one command.
        All or nothing: if SWAT+ refuses any value, it keeps none."""
        v = np.asarray(values, dtype=np.float64).ravel()
        if not np.isfinite(v).all():
            raise ExchangeError(f"{name}: values must be finite numbers")
        self._send(f"setv {name} {len(v)}\n" + "\n".join(repr(float(x)) for x in v))
        self._expect("@ok")

    def set_records(self, name: str, year: int, day: int, values) -> None:
        """Write weather records (SWAT+'s units converted from the variable's) for
        ``len(values)`` days from day ``day`` of ``year``, one row per day with a value
        for every gage in grid order. All or nothing, as for ``set_all``."""
        v = np.asarray(values, dtype=np.float64)
        if v.ndim != 2:
            raise ValueError(f"{name}: values must be one row per day, one column per gage")
        if not np.isfinite(v).all():
            raise ExchangeError(f"{name}: values must be finite numbers")
        self._send(f"setr {name} {int(year)} {int(day)} {len(v)}\n"
                   + "\n".join(" ".join(repr(float(x)) for x in row) for row in v))
        self._expect("@ok")

    def set_at(self, name: str, indices, values) -> None:
        """Set the objects at ``indices`` (0-based) for the next day, in one command;
        all or nothing, as for ``set_all``."""
        i = np.asarray(indices).ravel()
        v = np.asarray(values, dtype=np.float64).ravel()
        if len(i) != len(v):
            raise ValueError(f"{name}: {len(i)} indices and {len(v)} values")
        if not np.isfinite(v).all():
            raise ExchangeError(f"{name}: values must be finite numbers")
        self._send(f"seti {name} {len(v)}\n"
                   + "\n".join(f"{int(a) + 1} {float(b)!r}" for a, b in zip(i, v)))
        self._expect("@ok")

    def branch(self, run_dir: str | os.PathLike) -> "Exchange":
        """A copy of this paused SWAT+ process that goes on in ``run_dir``.

        SWAT+ writes out its buffers, the run folder is copied to
        ``run_dir``, and the process is duplicated with fork. Both copies
        are paused at the same point with the same state; each continues on
        its own and writes its own outputs. Mac and Linux only. Refused, as
        for ``save_state``, when a file SWAT+ has open couldn't be carried.
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
        try:
            pid = int(self._expect("@branched")[0])
        except ExchangeError:
            shutil.rmtree(new, ignore_errors=True)
            shutil.rmtree(fifo_dir, ignore_errors=True)
            raise

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
        the generated serializer, and the values set for the next day) and
        ``run/``, a copy of the run folder with every output file complete
        up to this pause. SWAT+ refuses to save when a file it has open
        couldn't be carried: one outside the run folder, or one it reads a
        day at a time (gwflow's chan_depth.gw and pond_div.gw).
        """
        if self.ended:
            raise ExchangeError("the simulation has ended; nothing left to save")
        out = Path(path).resolve()
        if out.exists():
            raise FileExistsError(f"{out} already exists")
        out.mkdir(parents=True)
        q = lambda p: '"' + str(p).replace('"', '') + '"'
        self._send(f"save {q(out / 'state.bin')} {q(self.run_dir.resolve())}")
        try:
            self._expect("@ok")
        except ExchangeError:
            shutil.rmtree(out, ignore_errors=True)
            raise
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
        for f in (src / "run").rglob("*"):          # put back the saved files SWAT+ rewrote at startup,
            if f.is_file() and f.name != "exchange.err":     # in out_path's folder too
                dest = run / f.relative_to(src / "run")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
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
