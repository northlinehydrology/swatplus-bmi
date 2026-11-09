"""The wrapper's claim, tested as a whole.

1. With nothing set, every output file is stock SWAT+'s, however the run is
   driven: stepped while every variable is read every day, run in uneven
   chunks, branched, or saved and loaded.
2. A value set changes results only through that input: setting SWAT+'s own
   value back (a flux or a state) gives stock output, to the byte.
3. A set SWAT+ would not use as given, and a branch or a save the wrapper
   could not carry, is refused with an error, never accepted and dropped.

Projects: Osu_1hru with daily printing, with a point source, with water
allocation, and with its outputs in an out_path folder; Ames_sub1 (``-m
full``); and any SWAT+ projects in $SWATPLUS_BMI_PROJECTS (folders separated
by the path separator), such as a study's own build.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from conftest import (CHECK, EXCHANGE, RUNS, STOCK, ames_light, differences, fresh, made, need,
                      project, run_stock)
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_allocation import DMD, add_allocation
from test_setting import add_point_source
from salts import with_salts_channel

PS = "point_source_water__volume_flow_rate"
GW = "channel_water__groundwater_volume_exchange_rate"
TS = "soil_layer__temperature"
GWS = "soil_layer_water__groundwater_inflow_rate"


def out_path(folder):
    """Point SWAT+'s out_path (the last line of file.cio) at ``folder``."""
    def edit(run):
        cio = (run / "file.cio").read_text().splitlines()
        cio = [line for line in cio if not line.startswith("out_path")]
        (run / "file.cio").write_text("\n".join(cio + [f"out_path          {folder}"]) + "\n")
    return edit


def both(*edits):
    def edit(run):
        for e in edits:
            e(run)
    return edit


# name: (project, kwargs for conftest.project)
PROJECTS = {
    "osu-daily": ("Osu_1hru", {"daily": True}),
    "osu-point-source": ("Osu_1hru", {"daily": True, "edit": add_point_source}),
    "osu-allocation": ("Osu_1hru", {"daily": True, "edit": add_allocation}),
    "osu-out-path": ("Osu_1hru", {"daily": True, "edit": out_path("out")}),
    "osu-salts": ("Osu_1hru", {"daily": True, "edit": with_salts_channel}),
}


def copy_of(name: str, dest: str) -> Path:
    if name in PROJECTS:
        base, kw = PROJECTS[name]
        return project(base, dest, **kw)
    src = Path(name)                                    # a folder from $SWATPLUS_BMI_PROJECTS
    out = RUNS / dest
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    return made(out)


def extra_projects() -> list[str]:
    return [p for p in os.environ.get("SWATPLUS_BMI_PROJECTS", "").split(os.pathsep) if p]


def label(name: str) -> str:
    return name if name in PROJECTS else Path(name).name


def step_reading_everything(m):
    """One day at a time, reading every variable after every day."""
    while m.get_current_time() < m.get_end_time():
        m.update()
        for v in m.get_output_var_names():
            m.get_value(v, np.empty(m.get_grid_size(m.get_var_grid(v))))


def drive(mode: str, name: str, tag: str) -> list[Path]:
    """Run a copy of the project the way ``mode`` says; return the run folders that
    reached the end, each to be compared with stock."""
    run = copy_of(name, f"{tag}-{mode}")
    m = SwatPlusBmi()
    m.initialize(str(run))
    end = int(m.get_end_time())
    k = max(1, end // 3) + 17                           # partway through a year
    if mode == "read":
        step_reading_everything(m)
        m.finalize()
        return [run]
    if mode == "chunks":
        t = 0
        for n in (1, 2, 30, 364, 365, 366, 1000):
            t = min(end, t + n)
            m.update_until(t)
        m.update_until(end)
        m.finalize()
        return [run]
    m.update_until(k)
    if mode == "branch":
        b = m.branch(fresh(f"{tag}-branch-b"))
        b.update_until(end)
        b.finalize()
        m.update_until(end)
        m.finalize()
        return [run, RUNS / f"{tag}-branch-b"]
    if mode == "save":
        snap = m.save_state(fresh(f"{tag}-save-snap"))
        m.update_until(end)
        m.finalize()
        n = SwatPlusBmi()
        n.initialize(str(copy_of(name, f"{tag}-save-loaded")))
        n.load_state(snap)
        assert n.get_current_time() == k
        n.update_until(end)
        n.finalize()
        return [run, RUNS / f"{tag}-save-loaded"]
    raise ValueError(mode)


MODES = ["read", "chunks", "branch", "save"]


@pytest.mark.parametrize("name", list(PROJECTS) + extra_projects(), ids=label)
def test_nothing_set_is_stock_however_driven(name):
    need(STOCK, EXCHANGE)
    tag = f"id-{label(name)}"
    stock = copy_of(name, f"{tag}-stock")
    run_stock(stock)
    for mode in MODES:
        for run in drive(mode, name, tag):
            assert differences(stock, run) == [], (mode, run.name)


@pytest.mark.full
def test_nothing_set_is_stock_however_driven_ames():
    need(STOCK, EXCHANGE)
    PROJECTS["ames"] = ("Ames_sub1", {"edit": ames_light})
    try:
        stock = copy_of("ames", "id-ames-stock")
        run_stock(stock)
        for mode in ("read", "save"):
            for run in drive(mode, "ames", "id-ames"):
                assert differences(stock, run) == [], (mode, run.name)
    finally:
        del PROJECTS["ames"]


# -- setting SWAT+'s own value back -----------------------------------------

def record(run, name):
    """SWAT+'s own value of ``name`` for every day, read after each day."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    days = []
    while m.get_current_time() < m.get_end_time():
        m.update()
        days.append(m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name)))).copy())
    m.finalize()
    return days


def set_every_day(run, name, days, exe=None):
    if exe is not None:
        os.environ["SWATPLUS_EXCHANGE"] = str(exe)
    try:
        m = SwatPlusBmi()
        m.initialize(str(run))
        while m.get_current_time() < m.get_end_time():
            v = days[int(m.get_current_time())]
            m.set_value(name, np.broadcast_to(v, m.get_grid_size(m.get_var_grid(name))))
            m.update()
        m.finalize()
    finally:
        os.environ.pop("SWATPLUS_EXCHANGE", None)


@pytest.mark.parametrize("name,var", [("osu-point-source", PS), ("osu-allocation", DMD),
                                      ("osu-daily", TS)])
def test_setting_swats_own_value_back_is_stock(name, var):
    """Every day, set the value SWAT+ itself used that day (read in a first run).
    The values cross the protocol as 8-byte reals, so SWAT+ gets its own 4-byte
    value back exactly, and the output is stock to the byte."""
    need(STOCK, EXCHANGE)
    stock = copy_of(name, f"back-{name}-stock")
    run_stock(stock)
    own = record(copy_of(name, f"back-{name}-read"), var)
    assert any(v.any() for v in own)
    setrun = copy_of(name, f"back-{name}-set")
    set_every_day(setrun, var, own)
    assert differences(stock, setrun) == []


def test_a_zero_groundwater_exchange_every_day_is_stock():
    need(STOCK, EXCHANGE)
    stock = copy_of("osu-daily", "back-gw-stock")
    run_stock(stock)
    setrun = copy_of("osu-daily", "back-gw-set")
    set_every_day(setrun, GW, [np.zeros(1)] * 6000)
    assert differences(stock, setrun) == []


@pytest.mark.filterwarnings("ignore:a negative point source")
def test_every_set_stays_in_bounds():
    """The point source, demand, groundwater, soil temperature and groundwater-into-soil
    sets, run on the build with array bounds checked: a write outside SWAT+'s arrays would
    stop it with an error."""
    need(CHECK, STOCK)
    for i, (name, var, value) in enumerate([("osu-point-source", PS, -0.0004),
                                            ("osu-allocation", DMD, 0.0002),
                                            ("osu-daily", GW, 0.0003), ("osu-daily", TS, 0.5),
                                            ("osu-daily", GWS, 1e-8)]):
        run = copy_of(name, f"bounds-{name}-{i}")
        set_every_day(run, var, [value] * 6000, exe=CHECK)
        assert (run / "success.fin").exists() or (run / "out" / "success.fin").exists()


# -- a value set and then saved goes with the state --------------------------

def test_a_set_value_saved_before_its_day_applies_after_loading():
    need(EXCHANGE)
    day = 777
    unbroken = copy_of("osu-point-source", "pending-unbroken")
    m = SwatPlusBmi()
    m.initialize(str(unbroken))
    m.update_until(day)
    m.set_value(PS, np.array([0.004]))
    snap = m.save_state(fresh("pending-snap"))
    m.update_until(m.get_end_time())
    m.finalize()
    loaded = copy_of("osu-point-source", "pending-loaded")
    n = SwatPlusBmi()
    n.initialize(str(loaded))
    n.load_state(snap)
    n.update()
    assert n.get_value(PS, np.empty(1))[0] == pytest.approx(0.004, rel=1e-6)
    n.update_until(n.get_end_time())
    n.finalize()
    assert differences(unbroken, loaded) == []


# -- refusals ----------------------------------------------------------------

def point_source_file(run, rows, typ):
    (run / "recall.rec").write_text(
        "recall.rec: point source for swatplus-bmi tests\n"
        "NUM     NAME    TYPE     FNAME\n"
        f"   1    pnt01      {typ}    pnt01.rec\n")
    head = ["pnt01.rec: point source", "14",
            "jday mo day_mo yr ob_typ ob_name flo sed orgn sedp no3 solp chla nh3 no2 "
            "cbod dox san sil cla sag lag grv temp"]
    (run / "pnt01.rec").write_text("\n".join(head + rows) + "\n")


def monthly_point_source(run):
    add_point_source(run)
    rows = [f"1 {mo} 1 {yr} sdc cha01 86.4 " + " ".join(["0"] * 17)
            for yr in range(2010, 2024) for mo in range(1, 13)]
    point_source_file(run, rows, 2)


def short_point_source(run):
    """A daily point source with records for 2010-2012 only."""
    add_point_source(run)
    rows, d = [], dt.date(2010, 1, 1)
    while d.year <= 2012:
        rows.append(f"{d.timetuple().tm_yday} {d.month} {d.day} {d.year} sdc cha01 86.4 "
                    + " ".join(["0"] * 17))
        d += dt.timedelta(days=1)
    point_source_file(run, rows, 1)


def started(edit, dest, days=10):
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", dest, edit=edit)))
    m.update_until(days)
    return m


def test_a_point_source_that_isnt_daily_is_refused():
    need(EXCHANGE)
    m = started(monthly_point_source, "refuse-monthly")
    with pytest.raises(ExchangeError, match="recall type 2; only daily"):
        m.set_value(PS, np.array([0.001]))
    m.finalize()


def test_a_point_source_outside_its_record_is_refused():
    need(EXCHANGE)
    m = started(short_point_source, "refuse-years", days=400)
    m.set_value(PS, np.array([0.001]))                  # 2011: in the record
    m.update_until(4 * 365)                             # 2013: past it
    with pytest.raises(ExchangeError, match="no record in 2013"):
        m.set_value(PS, np.array([0.001]))
    m.finalize()


def test_values_that_arent_finite_numbers_are_refused():
    need(EXCHANGE)
    m = started(add_point_source, "refuse-nan")
    for bad in (np.nan, np.inf, -np.inf):
        with pytest.raises(ValueError, match="finite"):
            m.set_value(PS, np.array([bad]))
        m._x._send(f"set {PS} 1 {bad}")                 # SWAT+ refuses it too
        with pytest.raises(ExchangeError, match="finite"):
            m._x._expect("@ok")
    with pytest.raises(ExchangeError, match="too large"):
        m.set_value(PS, np.array([1e35]))
    m.finalize()


def test_index_and_value_counts_must_match():
    need(EXCHANGE)
    m = started(add_point_source, "refuse-lengths")
    with pytest.raises(ValueError, match="1 indices and 2 values"):
        m.set_value_at_indices(PS, np.array([0]), np.array([0.001, 0.002]))
    with pytest.raises(ValueError, match="1 indices and 0 values"):
        m.set_value(PS, np.array([]))
    with pytest.raises(IndexError):
        m.set_value_at_indices(PS, np.array([1]), np.array([0.001]))
    m.finalize()


def test_the_groundwater_exchange_with_sub_daily_routing_is_refused():
    need(EXCHANGE)

    def hourly(run):
        sim = (run / "time.sim").read_text().splitlines()
        f = sim[2].split()
        sim[2] = "  ".join(f[:4] + ["24"])
        (run / "time.sim").write_text("\n".join(sim) + "\n")

    m = started(hourly, "refuse-subdaily", days=3)
    with pytest.raises(ExchangeError, match="daily routing only"):
        m.set_value(GW, np.array([0.001]))
    m.finalize()


def test_branch_and_save_with_outputs_outside_the_run_folder_are_refused():
    need(EXCHANGE)
    outside = RUNS / "refuse-outside-out"
    m = started(out_path("../refuse-outside-out"), "refuse-outside", days=20)
    made(outside)
    with pytest.raises(ExchangeError, match="outside the run folder"):
        m.branch(fresh("refuse-outside-b"))
    assert not (RUNS / "refuse-outside-b").exists()
    with pytest.raises(ExchangeError, match="outside the run folder"):
        m.save_state(fresh("refuse-outside-snap"))
    assert not (RUNS / "refuse-outside-snap").exists()
    m.update()                                          # the run carries on
    m.finalize()


def test_an_executable_from_other_sources_is_refused(monkeypatch):
    need(EXCHANGE)
    import swatplus_bmi.bmi as bmi
    monkeypatch.setattr(bmi, "sources_hash", lambda: "0" * 32)
    m = SwatPlusBmi()
    with pytest.raises(ExchangeError, match="built from other swatplus-bmi sources"):
        m.initialize(str(project("Osu_1hru", "refuse-build")))
