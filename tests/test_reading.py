"""Milestone 2: values read through get_value match SWAT+'s own daily output.

Osu_1hru with daily printing. After each day, every output variable is read
through BMI; at the end the series are checked against channel_sd_day.txt
and aquifer_day.txt, which SWAT+ writes with four significant digits
(E10.4) and three decimals (F15.3), so they agree to half a unit in the
last printed digit: 5e-4 relative, 5e-4 absolute.
"""

import numpy as np
import pytest

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi


def daily_table(path, name_col="name"):
    """Rows of a SWAT+ daily output file as {object name: {column: array}}."""
    lines = path.read_text().splitlines()
    head = lines[1].split()
    out = {}
    for line in lines[3:]:
        f = line.split()
        if len(f) != len(head):
            continue
        row = dict(zip(head, f))
        cols = out.setdefault(row[name_col], {h: [] for h in head})
        for h in head:
            cols[h].append(row[h])
    def arr(v):
        try:
            return np.array(v, dtype=float)
        except ValueError:                               # a text column
            return v
    return {k: {h: arr(v) for h, v in c.items()} for k, c in out.items()}


@pytest.fixture(scope="module")
def series():
    need(EXCHANGE)
    run = project("Osu_1hru", "Osu_1hru-daily-get", daily=True)
    m = SwatPlusBmi()
    m.initialize(str(run))
    names = m.get_output_var_names()
    grids = {n: m.get_var_grid(n) for n in names}
    objs = {n: m.get_grid_object_names(grids[n]) for n in names}
    area = {g: m._grids[g].area_ha for g in set(grids.values())}
    got = {n: [] for n in names}
    while m.get_current_time() < m.get_end_time():
        m.update()
        for n in names:
            got[n].append(m.get_value(n, np.empty(len(objs[n]), np.float32)).copy())
    m.finalize()
    return run, {n: np.array(v) for n, v in got.items()}, objs, {n: area[grids[n]] for n in names}


def test_channel_flow_matches_daily_output(series):
    run, got, objs, _ = series
    cha = daily_table(run / "channel_sd_day.txt")
    name = "channel_water__volume_outflow_rate"
    for j, obj in enumerate(objs[name]):
        ref = cha[obj]["flo_out"]                       # m3/s
        assert len(ref) == len(got[name])
        np.testing.assert_allclose(got[name][:, j], ref, rtol=5e-4, atol=1e-12)
        assert np.count_nonzero(ref) > 1000               # a real series, not zeros


def test_channel_inflow_matches_daily_output(series):
    run, got, objs, _ = series
    cha = daily_table(run / "channel_sd_day.txt")
    name = "channel_water__volume_inflow_rate"
    for j, obj in enumerate(objs[name]):
        np.testing.assert_allclose(got[name][:, j], cha[obj]["flo_in"], rtol=5e-4, atol=1e-12)


def test_channel_storage_matches_daily_output(series):
    run, got, objs, _ = series
    cha = daily_table(run / "channel_sd_day.txt")
    name = "channel_water__volume"
    for j, obj in enumerate(objs[name]):
        np.testing.assert_allclose(got[name][:, j], cha[obj]["flo_stor"], rtol=5e-4, atol=1e-12)


def test_aquifer_matches_daily_output(series):
    run, got, objs, area = series
    aqu = daily_table(run / "aquifer_day.txt")
    for j, obj in enumerate(objs["aquifer_water__volume"]):
        ref = aqu[obj]
        a = area["aquifer_water__volume"][j]
        stor_mm = got["aquifer_water__volume"][:, j] / (10.0 * a)
        np.testing.assert_allclose(stor_mm, ref["stor"], atol=5e-4, rtol=1e-6)
        np.testing.assert_allclose(got["soil_water_table_surface__depth"][:, j], ref["dep_wt"],
                                   atol=5e-4, rtol=1e-6)
        flo_mm = got["aquifer_water__volume_outflow_rate"][:, j] * 86400.0 / (10.0 * a)
        np.testing.assert_allclose(flo_mm, ref["flo"], atol=5e-4, rtol=1e-6)
        assert np.ptp(ref["stor"]) > 10                    # storage moves over the run
