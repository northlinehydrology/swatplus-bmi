"""HRU water: percolation, evapotranspiration, potential ET and soil water (0.6.0).

Read-only, one value per HRU on the hru grid, each the day just done: what
SWAT+ prints in hru_wb_day.txt as perc, et, pet and sw_final. Percolation is
the water leaving the bottom of the soil profile, what a groundwater model
takes as recharge cell by cell. Test projects: Osu_1hru (1 HRU, 14 years)
and Ames_sub1 (12 HRUs, one year, so the grid's order is checked against
SWAT+'s names).
"""

import numpy as np

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

PER_DAY = {"soil_water__percolation_rate": ("perc", 86400000.0),
           "land_surface__evapotranspiration_rate": ("et", 86400000.0),
           "land_surface__potential_evapotranspiration_rate": ("pet", 86400000.0),
           "soil_water__depth": ("sw_final", 1000.0)}            # to SWAT+'s mm


def read_every_day(name, dest, **kw):
    """Every HRU variable every day, in SWAT+'s units, and the run's hru_wb_day.txt."""
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project(name, dest, daily=True, **kw)))
    grid = m.get_var_grid("soil_water__percolation_rate")
    n = m.get_grid_size(grid)
    names = list(m.get_grid_object_names(grid))
    got = {v: [] for v in PER_DAY}
    while m.get_current_time() < m.get_end_time():
        m.update()
        for v, (_, k) in PER_DAY.items():
            assert m.get_var_grid(v) == grid
            got[v].append(m.get_value(v, np.empty(n)) * k)
    run = m.run_dir
    m.finalize()
    return names, {v: np.array(a) for v, a in got.items()}, daily_table(run / "hru_wb_day.txt")


def check(names, got, wb):
    assert sorted(names) == sorted(wb)
    for v, (col, _) in PER_DAY.items():
        for i, h in enumerate(names):
            np.testing.assert_allclose(got[v][:, i], wb[h][col], rtol=5e-4, atol=5e-4, err_msg=f"{v} {h}")


def test_osu_hru_water_read_is_swats():
    names, got, wb = read_every_day("Osu_1hru", "Osu_1hru-hru-water")
    check(names, got, wb)
    for v in PER_DAY:
        assert got[v].max() > 0, v


def test_ames_hru_water_read_is_swats_hru_by_hru():
    names, got, wb = read_every_day("Ames_sub1", "Ames_sub1-hru-water", years=(1975, 1975))
    assert len(names) == 12
    check(names, got, wb)
    # the HRUs differ, so a mixed-up order would show
    assert len({tuple(got["soil_water__depth"][:, i].round(3)) for i in range(12)}) > 1
