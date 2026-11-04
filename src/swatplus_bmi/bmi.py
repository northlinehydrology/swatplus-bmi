"""BMI 2.0 for SWAT+, stepping SWAT+ one day at a time.

SWAT+ runs as its own process in exchange mode (see ``exchange.py``); this
class is the BMI face of that process. Each object type SWAT+ exposes
(channels, aquifers, point sources) is a BMI grid of type ``points``, one
point per SWAT+ object, in the order of SWAT+'s connection files, with
longitude as x and latitude as y. ``get_grid_object_names`` gives the SWAT+
object names for a grid.

Only fluxes can be set. A value set between updates applies to the next day
only; after that day SWAT+ goes back to its own inputs. ``get_value_ptr`` is
declined: the values live in the SWAT+ process, not in this one.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import tomllib
from pathlib import Path

import numpy as np
from bmipy import Bmi

from .exchange import Exchange, ExchangeError

__all__ = ["SwatPlusBmi"]

GRID_ORDER = ["cha", "aqu", "rec", "dmd"]


def find_executable(given: str | None = None) -> Path:
    """The exchange-mode SWAT+ executable: given, $SWATPLUS_EXCHANGE, or bin/."""
    for cand in (given, os.environ.get("SWATPLUS_EXCHANGE")):
        if cand:
            return Path(cand).expanduser()
    here = Path(__file__).resolve().parents[2] / "bin"
    found = sorted(here.glob("swatplus-*-exchange"))
    if found:
        return found[-1]
    raise FileNotFoundError("no SWAT+ exchange executable: build one with ./build.sh, "
                            "or set SWATPLUS_EXCHANGE or 'executable' in the config file")


class SwatPlusBmi(Bmi):
    """SWAT+ through BMI 2.0, one day per step."""

    def __init__(self):
        self._x: Exchange | None = None
        self._grid_ids: dict[str, int] = {}
        self._vars = {}

    # -- control --------------------------------------------------------

    def initialize(self, config_file: str | None = None) -> None:
        """Start SWAT+ paused before its first day.

        ``config_file`` is either a SWAT+ project folder (the folder with
        ``file.cio``; SWAT+ runs and writes its outputs there) or a TOML file::

            project = "path/to/TxtInOut"     # the SWAT+ project folder
            run_dir = "runs/scenario-a"      # optional: copy the project here and run the copy
            executable = "bin/swatplus-62.0.1-exchange"   # optional
        """
        if config_file is None:
            raise ValueError("initialize needs a SWAT+ project folder or a TOML config file")
        path = Path(config_file).expanduser()
        cfg = {"project": str(path)} if path.is_dir() else tomllib.loads(path.read_text())
        base = path.parent if path.is_file() else Path.cwd()
        project = (base / cfg["project"]).resolve()
        if not (project / "file.cio").exists():
            raise FileNotFoundError(f"no file.cio in {project}")
        run_dir = project
        if cfg.get("run_dir"):
            run_dir = (base / cfg["run_dir"]).resolve()
            if run_dir.exists():
                shutil.rmtree(run_dir)
            shutil.copytree(project, run_dir)
        exe = find_executable(cfg.get("executable") and str((base / cfg["executable"])))
        self._x = Exchange(exe, run_dir)
        cat = self._x.catalog
        # grids with no objects are left out, and so are their variables
        self._grid_ids = {}
        for g in GRID_ORDER + [g for g in cat.grids if g not in GRID_ORDER]:
            if g in cat.grids and cat.grids[g].size > 0:
                self._grid_ids[g] = len(self._grid_ids)
        self._grids = {i: cat.grids[g] for g, i in self._grid_ids.items()}
        self._vars = {v.name: v for v in cat.variables.values() if v.grid in self._grid_ids}
        self._start = dt.date(cat.start_year, 1, 1) + dt.timedelta(days=cat.start_day - 1)

    def update(self) -> None:
        self._x.run(1)

    def update_until(self, time: float) -> None:
        days = time - self._x.days_done
        if days != int(days):
            raise ValueError("SWAT+ steps whole days; update_until needs a whole number of days")
        if days < 0:
            raise ValueError(f"cannot go back from day {self._x.days_done} to {time}")
        if time > self.get_end_time():
            raise ValueError(f"{time} is past the end of the simulation ({self.get_end_time()})")
        if days > 0:
            self._x.run(int(days))

    def finalize(self) -> None:
        """Let SWAT+ complete its outputs if the simulation has ended; otherwise stop it."""
        if self._x is None:
            return
        if self._x.ended:
            code = self._x.finish()
        else:
            code = self._x.stop()
        self._x = None
        if code != 0:
            raise ExchangeError(f"SWAT+ exited with code {code}")

    # -- extensions -----------------------------------------------------

    @property
    def run_dir(self) -> Path:
        """The folder SWAT+ runs in and writes its outputs to."""
        return self._x.run_dir

    def run_to_end(self) -> None:
        """Run the rest of the simulation without pausing and complete SWAT+'s outputs."""
        code = self._x.finish()
        self._x = None
        if code != 0:
            raise ExchangeError(f"SWAT+ exited with code {code}")

    def get_grid_object_names(self, grid: int) -> list[str]:
        """The SWAT+ object names on a grid, in grid order."""
        return list(self._grids[grid].names)

    def get_object_index(self, name: str, object_name: str) -> int:
        """The grid index of a SWAT+ object, for a variable on that object's grid."""
        return self._grids[self.get_var_grid(name)].names.index(object_name)

    # -- model information ----------------------------------------------

    def get_component_name(self) -> str:
        return "SWAT+"

    def get_input_item_count(self) -> int:
        return len(self.get_input_var_names())

    def get_output_item_count(self) -> int:
        return len(self.get_output_var_names())

    def get_input_var_names(self) -> tuple[str, ...]:
        return tuple(n for n, v in self._vars.items() if v.role == "in")

    def get_output_var_names(self) -> tuple[str, ...]:
        # settable fluxes can be read back too
        return tuple(self._vars)

    # -- variable information -------------------------------------------

    def _var(self, name):
        try:
            return self._vars[name]
        except KeyError:
            raise KeyError(f"unknown variable: {name}") from None

    def get_var_grid(self, name: str) -> int:
        return self._grid_ids[self._var(name).grid]

    def get_var_type(self, name: str) -> str:
        self._var(name)
        return "float32"

    def get_var_units(self, name: str) -> str:
        return self._var(name).units

    def get_var_itemsize(self, name: str) -> int:
        self._var(name)
        return 4

    def get_var_nbytes(self, name: str) -> int:
        return 4 * self.get_grid_size(self.get_var_grid(name))

    def get_var_location(self, name: str) -> str:
        self._var(name)
        return "node"

    # -- time -----------------------------------------------------------

    def get_current_time(self) -> float:
        return float(self._x.days_done)

    def get_start_time(self) -> float:
        return 0.0

    def get_end_time(self) -> float:
        return float(self._x.catalog.days)

    def get_time_units(self) -> str:
        return f"days since {self._start.isoformat()} 00:00:00"

    def get_time_step(self) -> float:
        return 1.0

    # -- getters and setters --------------------------------------------

    def get_value(self, name: str, dest: np.ndarray) -> np.ndarray:
        self._var(name)
        dest[:] = self._x.get(name)
        return dest

    def get_value_ptr(self, name: str) -> np.ndarray:
        raise NotImplementedError("get_value_ptr is declined: the values live in the SWAT+ "
                                  "process; use get_value")

    def get_value_at_indices(self, name: str, dest: np.ndarray, inds: np.ndarray) -> np.ndarray:
        self._var(name)
        dest[:] = self._x.get(name)[np.asarray(inds)]
        return dest

    def set_value(self, name: str, src: np.ndarray) -> None:
        self.set_value_at_indices(name, np.arange(self.get_grid_size(self.get_var_grid(name))), src)

    def set_value_at_indices(self, name: str, inds: np.ndarray, src: np.ndarray) -> None:
        if self._var(name).role != "in":
            raise ValueError(f"{name} can't be set: only fluxes SWAT+ takes as inputs can")
        for i, v in zip(np.asarray(inds).ravel(), np.asarray(src, dtype=np.float32).ravel()):
            self._x.set(name, int(i), float(v))

    # -- grids ----------------------------------------------------------

    def get_grid_type(self, grid: int) -> str:
        self._grids[grid]
        return "points"

    def get_grid_rank(self, grid: int) -> int:
        self._grids[grid]
        return 2

    def get_grid_size(self, grid: int) -> int:
        return self._grids[grid].size

    def get_grid_x(self, grid: int, x: np.ndarray) -> np.ndarray:
        x[:] = self._grids[grid].x
        return x

    def get_grid_y(self, grid: int, y: np.ndarray) -> np.ndarray:
        y[:] = self._grids[grid].y
        return y

    def get_grid_node_count(self, grid: int) -> int:
        return self.get_grid_size(grid)

    def get_grid_edge_count(self, grid: int) -> int:
        self._grids[grid]
        return 0

    def get_grid_face_count(self, grid: int) -> int:
        self._grids[grid]
        return 0

    def _not_points(self, *args, **kwargs):
        raise NotImplementedError("SWAT+ grids are points; this applies to other grid types")

    get_grid_shape = get_grid_spacing = get_grid_origin = get_grid_z = _not_points
    get_grid_edge_nodes = get_grid_face_edges = get_grid_face_nodes = _not_points
    get_grid_nodes_per_face = _not_points
