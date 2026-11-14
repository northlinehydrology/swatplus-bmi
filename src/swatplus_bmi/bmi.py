"""BMI 2.0 for SWAT+, stepping SWAT+ one day at a time.

SWAT+ runs as its own process in exchange mode (see ``exchange.py``); this
class is the BMI face of that process. Each object type SWAT+ exposes
(channels, aquifers, point sources, water allocation places of use, HRUs,
HRU soil layers, and weather gages) is a BMI grid of type ``points``, one point per SWAT+
object, in the order of SWAT+'s input files, with longitude as x and latitude
as y (places of use have no location; their coordinates are NaN; soil layers
are at their HRU's location, with z their mid-depth below the surface). ``get_grid_object_names`` gives the SWAT+
object names for a grid.

Fluxes and a few states can be set (a state is replaced right after SWAT+
computes it, before anything uses it), and weather records ahead of the day
SWAT+ reads them (``set_weather``). A value set between updates applies to
the next day only; after that day SWAT+ goes back to its own inputs. A set SWAT+ would not
use as given (a point source that isn't daily, a value that isn't a number) is
refused with an error rather than dropped. Values are 8-byte reals
(``float64``), so a value read and set back is SWAT+'s own to the bit, though
SWAT+ keeps them in 4-byte reals. ``get_value_ptr`` is declined: the values
live in the SWAT+ process, not in this one.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import os
import shutil
import tarfile
import tempfile
import tomllib
import warnings
from pathlib import Path

import numpy as np
from bmipy import Bmi

from .calibration import Change, check_changes, write_changes
from .exchange import Exchange, ExchangeError

__all__ = ["SwatPlusBmi"]

GRID_ORDER = ["cha", "aqu", "rec", "pou", "hru", "hly", "pcp", "tmp", "slr", "hmd", "wnd",
              "lsu", "bsn"]

# weather record variables, and the days from the coming day to the first record
# SWAT+ hasn't read (precipitation is read a day ahead)
WEATHER = {"atmosphere_water__precipitation_leq-volume_flux": 2,
           "atmosphere_bottom_air__max_of_temperature": 1,
           "atmosphere_bottom_air__min_of_temperature": 1,
           "land_surface_radiation~incoming~shortwave__energy_flux": 1,
           "atmosphere_bottom_air_water~vapor__relative_saturation": 1,
           "atmosphere_bottom_air_flow__speed": 1}


def sources_hash() -> str:
    """The hash of this package's build files, which an exchange executable built from
    them carries (tools/source_hash.py)."""
    import runpy
    from .build import assets
    return runpy.run_path(str(assets() / "tools" / "source_hash.py"))["source_hash"](assets())


def find_executable(given: str | None = None) -> Path:
    """The exchange-mode SWAT+ executable: given, $SWATPLUS_EXCHANGE, bin/ in a source
    checkout, or the folder ``swatplus-bmi build`` writes to; there, the supported release's
    build if it exists, else the only one."""
    for cand in (given, os.environ.get("SWATPLUS_EXCHANGE")):
        if cand:
            return Path(cand).expanduser()
    from .build import DEFAULT_TAG, home
    for folder in (Path(__file__).resolve().parents[2] / "bin", home() / "bin"):
        if (folder / f"swatplus-{DEFAULT_TAG}-exchange").exists():
            return folder / f"swatplus-{DEFAULT_TAG}-exchange"
        found = sorted(folder.glob("swatplus-*-exchange"))
        if len(found) == 1:
            return found[0]
        if found:
            raise FileNotFoundError(f"several SWAT+ exchange executables in {folder} and none for "
                                    f"{DEFAULT_TAG}: name one, or set SWATPLUS_EXCHANGE")
    raise FileNotFoundError("no SWAT+ exchange executable: run `swatplus-bmi build` "
                            "(or ./build.sh in a source checkout), or set SWATPLUS_EXCHANGE")


class SwatPlusBmi(Bmi):
    """SWAT+ through BMI 2.0, one day per step."""

    def __init__(self):
        self._x: Exchange | None = None
        self._grid_ids: dict[str, int] = {}
        self._vars = {}
        self._warned_diversion = False

    # -- control --------------------------------------------------------

    def initialize(self, config_file: str | None = None) -> None:
        """Start SWAT+ paused before its first day.

        ``config_file`` is either a SWAT+ project folder (the folder with
        ``file.cio``; SWAT+ runs and writes its outputs there) or a TOML file::

            project = "path/to/TxtInOut"     # the SWAT+ project folder
            run_dir = "runs/scenario-a"      # optional: copy the project here and run the copy
            executable = "bin/swatplus-62.0.1-exchange"   # optional

            [[parameter]]                    # optional, any number; needs run_dir
            name = "cn2"
            value = -10
            change = "pctchg"

        Parameter changes go into the run copy's calibration.cal, after the
        project's own, and SWAT+ makes them before its first day. A change
        SWAT+ wouldn't make as asked is refused (``swatplus_bmi.calibration``).
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
        changes = [Change.from_dict(p) for p in cfg.get("parameter", [])]
        if changes and not cfg.get("run_dir"):
            raise ValueError("[[parameter]] changes are written into calibration.cal: give run_dir, "
                             "so the project's own isn't changed")
        if changes:
            problems, _ = check_changes(project, changes)
            if problems:
                raise ValueError("parameter changes SWAT+ wouldn't make as asked:\n  "
                                 + "\n  ".join(problems))
        if cfg.get("run_dir"):
            run_dir = (base / cfg["run_dir"]).resolve()
            if run_dir.exists():
                shutil.rmtree(run_dir)
            shutil.copytree(project, run_dir)
        for note in write_changes(run_dir, changes) if changes else []:
            warnings.warn(f"parameter {note}", stacklevel=2)
        exe = find_executable(cfg.get("executable") and str((base / cfg["executable"])))
        self._x = Exchange(exe, run_dir)
        cat = self._x.catalog
        want = sources_hash()
        if cat.build != want:
            self._x.stop()
            self._x = None
            raise ExchangeError(
                f"{exe} was built from other swatplus-bmi sources (build {cat.build}, these "
                f"sources {want}): rebuild it with `swatplus-bmi build` (./build.sh in a source "
                f"checkout)")
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

    def branch(self, run_dir: str | os.PathLike) -> "SwatPlusBmi":
        """A second model that continues from this one's current state.

        The paused SWAT+ process is duplicated (fork), so a warm-up can be
        run once and every scenario started from it. The new model runs and
        writes its outputs in ``run_dir``, a copy of this model's run
        folder made now; both models carry on independently. Mac and Linux
        only. Not part of BMI 2.0.
        """
        other = copy.copy(self)
        other._x = self._x.branch(run_dir)
        return other

    def save_state(self, path: str | os.PathLike) -> Path:
        """Save the model's state to the folder ``path``; see ``load_state``.

        Works on every platform. Not part of BMI 2.0; the name follows the
        CSDMS proposal for get_state/set_state (bmi PR #112).
        """
        return self._x.save_state(path)

    def load_state(self, path: str | os.PathLike) -> None:
        """Replace this model's run with the state saved in the folder ``path``.

        The SWAT+ process running now is stopped, the run folder becomes a
        copy of the saved one, and a new process carries on from the saved
        day exactly as the saved run would have.
        """
        exe, run_dir = self._x.executable, self._x.run_dir
        self._x.stop()
        self._x = Exchange.load_state(exe, path, run_dir)

    def get_state(self) -> bytes:
        """The model's whole state as bytes (a tar of a save_state folder)."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.save_state(Path(tmp) / "state")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(folder, arcname="state")
            return buf.getvalue()

    def set_state(self, state: bytes) -> None:
        """Replace this model's run with a state from get_state."""
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(state), mode="r:gz") as tar:
                tar.extractall(tmp, filter="data")
            self.load_state(Path(tmp) / "state")

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

    # -- weather records ---------------------------------------------------

    def first_weather_day(self, name: str) -> dt.date:
        """The first day whose weather record ``name`` can still be set.

        SWAT+ reads a day's weather before the pause that precedes it, and
        precipitation a day earlier still (its decision tables look at the next
        day's rain), so this is the day after the coming one, or for
        precipitation the day after that.
        """
        if name not in WEATHER:
            raise ValueError(f"{name} isn't a weather record variable ({', '.join(WEATHER)})")
        self._var(name)
        return self._start + dt.timedelta(days=self._x.days_done + WEATHER[name])

    def set_weather(self, name: str, start: dt.date, values) -> None:
        """Write a weather series into SWAT+'s gage records, from ``start`` on.

        ``values`` has one row per day and one column per gage on the variable's
        grid (a 1-D array for a single gage), in the variable's units. Each value
        is written where the gage's file would hold it, so the run is the one a
        file with those values would give: SWAT+ builds the mean temperature,
        PET, the generator's wet and dry days, heat units and the rest from it.
        Any day from ``first_weather_day(name)`` on can be set, and set again,
        within the gage's records; a day already read, outside the records or
        after the run ends is refused, and then nothing is written.
        """
        if name not in WEATHER:
            raise ValueError(f"{name} isn't a weather record variable ({', '.join(WEATHER)})")
        n = self.get_grid_size(self.get_var_grid(name))
        v = np.asarray(values, dtype=np.float64)
        if v.ndim == 1 and n == 1:
            v = v[:, None]
        if v.ndim != 2 or v.shape[1] != n:
            raise ValueError(f"{name}: values must be (days, {n} gages), not {v.shape}")
        if not np.isfinite(v).all():
            raise ValueError(f"{name}: values must be finite numbers")
        start = dt.date(start.year, start.month, start.day)
        self._x.set_records(name, start.year, start.timetuple().tm_yday, v)

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
        return "float64"

    def get_var_units(self, name: str) -> str:
        return self._var(name).units

    def get_var_itemsize(self, name: str) -> int:
        self._var(name)
        return 8

    def get_var_nbytes(self, name: str) -> int:
        return 8 * self.get_grid_size(self.get_var_grid(name))

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
        # numpy's indexing rules (a negative index counts from the end), as before; then
        # only those values cross
        inds = np.arange(self.get_grid_size(self.get_var_grid(name)))[np.asarray(inds)]
        if inds.size:
            dest[:] = self._x.get_at(name, inds)
        return dest

    def set_value(self, name: str, src: np.ndarray) -> None:
        self.set_value_at_indices(name, np.arange(self.get_grid_size(self.get_var_grid(name))), src)

    def set_value_at_indices(self, name: str, inds: np.ndarray, src: np.ndarray) -> None:
        if self._var(name).role != "in":
            raise ValueError(f"{name} can't be set: it is an output only (see get_input_var_names)")
        inds = np.asarray(inds).ravel()
        src = np.asarray(src, dtype=np.float64).ravel()
        if len(inds) != len(src):
            raise ValueError(f"{name}: {len(inds)} indices and {len(src)} values")
        n = self.get_grid_size(self.get_var_grid(name))
        if len(inds) and (inds.min() < 0 or inds.max() >= n):
            raise IndexError(f"{name}: indices must be 0 to {n - 1}")
        if not np.isfinite(src).all():
            raise ValueError(f"{name}: values must be finite numbers")
        if (name == "point_source_water__volume_flow_rate" and np.any(src < 0)
                and not self._warned_diversion):
            self._warned_diversion = True
            warnings.warn(
                "a negative point source is a diversion, and SWAT+ 62.0.1 doesn't limit it to the "
                "water in the channel: when it is larger than the channel's other inflow, SWAT+ "
                "records it in full but takes none of the excess, and the channel balance doesn't "
                "close (see channel_water__volume_inflow_rate). For withdrawals limited to "
                "the water available, set place_of_use_water__volume_demand_rate instead.",
                UserWarning, stacklevel=2)
        if len(inds) == n and (inds == np.arange(n)).all():
            self._x.set_all(name, src)
        elif len(inds) > 1:
            self._x.set_at(name, inds, src)
        elif len(inds) == 1:
            self._x.set(name, int(inds[0]), float(src[0]))

    # -- grids ----------------------------------------------------------

    def get_grid_type(self, grid: int) -> str:
        self._grids[grid]
        return "points"

    def get_grid_rank(self, grid: int) -> int:
        return 2 if self._grids[grid].z is None else 3

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

    def get_grid_z(self, grid: int, z: np.ndarray) -> np.ndarray:
        """Soil layers (grid ``hly``): each layer's mid-depth below the surface, m."""
        g = self._grids[grid]
        if g.z is None:
            raise NotImplementedError(f"grid {grid} ({g.name}) has no z: its points are on the surface")
        z[:] = g.z
        return z

    get_grid_shape = get_grid_spacing = get_grid_origin = _not_points
    get_grid_edge_nodes = get_grid_face_edges = get_grid_face_nodes = _not_points
    get_grid_nodes_per_face = _not_points
