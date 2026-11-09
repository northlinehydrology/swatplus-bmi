"""Groundwater into the soil (settable, 0.7.0), the way back from a groundwater model.

SWAT+'s own groundwater model, gwflow, puts groundwater into an HRU's soil
profile when the water table is within it: gwflow_soil, called at the top of
swr_percmain, adds water to the layers below the water table and counts it as
gwsoilq (hru_wb). A hook right after that call (exch_hru_gwsoil) adds the water
set for each layer the same way, so a groundwater model coupled through the BMI
gives water back to the soil where gwflow would. Test projects: Osu_1hru (one
HRU) and Ames_sub1 (12 HRUs), everything printed daily.
"""

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table

GW = "soil_layer_water__groundwater_inflow_rate"
GWH = "soil_water__groundwater_inflow_rate"
PERC = "soil_water__percolation_rate"
MM_DAY = 86_400_000.0          # m/s -> mm/day


def started(dest, days=0, name="Osu_1hru", **kw):
    m = SwatPlusBmi()
    m.initialize(str(project(name, dest, daily=True, **kw)))
    m.update_until(days)
    return m


def values(m, name):
    return m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name))))


def finish(m):
    while m.get_current_time() < m.get_end_time():
        m.update()
    run = m.run_dir
    m.finalize()
    return run


def test_the_variables():
    need(EXCHANGE)
    m = started("gws-grid")
    assert GW in m.get_input_var_names() and GW in m.get_output_var_names()
    assert GWH in m.get_output_var_names() and GWH not in m.get_input_var_names()
    assert m.get_var_grid(GW) == m.get_var_grid("soil_layer__temperature")
    assert m.get_var_grid(GWH) == m.get_var_grid(PERC)
    assert m.get_var_units(GW) == m.get_var_units(GWH) == "m s-1"
    assert (values(m, GW) == 0).all() and (values(m, GWH) == 0).all()
    m.finalize()


def test_nothing_set_is_stock_with_both_read_every_day():
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "gws-stock", daily=True)
    run_stock(stock)
    m = started("gws-read")
    while m.get_current_time() < m.get_end_time():
        m.update()
        assert (values(m, GW) == 0).all() and (values(m, GWH) == 0).all()
    assert differences(stock, finish(m)) == []


def set_run(dest, plan, days=0, name="Osu_1hru", **kw):
    """A run with groundwater set by plan(day, n) -> per-layer m/s or None, every day; the
    values set and read back, by day."""
    m = started(dest, days, name, **kw)
    n = m.get_grid_size(m.get_var_grid(GW))
    put, back, hru = {}, {}, {}
    while m.get_current_time() < m.get_end_time():
        day = int(m.get_current_time())
        v = plan(day, n)
        if v is not None:
            m.set_value(GW, v)
            put[day] = np.asarray(v, dtype=float)
        m.update()
        back[day] = values(m, GW)
        hru[day] = values(m, GWH)
    return m, finish(m), put, back, hru


def test_the_water_set_goes_in_and_is_counted_as_swat_counts_gwflows():
    """Every third day of the first two years, a different amount into each layer. Read back
    and printed (gwsoilq) as set; nothing on the other days; the soil holds more water than
    the stock run's from the first day set."""
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "gws-set-stock", daily=True)
    run_stock(stock)

    def plan(day, n):
        if day < 730 and day % 3 == 0:
            return (0.2 + 0.1 * np.arange(n)) * (1 + day % 7) / MM_DAY   # 0.2.. mm/day
        return None

    m, run, put, back, hru = set_run("gws-set", plan)
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]
    want = np.zeros(len(wb["gwsoilq"]))
    for day, v in put.items():
        want[day] = v.sum() * MM_DAY
        np.testing.assert_allclose(back[day], v, rtol=1e-6)
    for day in back:
        if day not in put:
            assert (back[day] == 0).all()
    np.testing.assert_allclose(wb["gwsoilq"], want, rtol=5e-4, atol=6e-4)
    np.testing.assert_allclose([hru[d][0] * MM_DAY for d in sorted(hru)], want, rtol=1e-5, atol=1e-5)
    assert (st["gwsoilq"] == 0).all()
    first = min(put)
    assert (wb["sw_final"][:first] == st["sw_final"][:first]).all()
    assert wb["sw_final"][first] > st["sw_final"][first]

    # the water closes: over the run, what was added plus the change in irrigation (Osu_1hru
    # irrigates on demand, and wetter soil needs less) leaves as more runoff, lateral flow,
    # percolation and ET, or is still in the soil and snow at the end
    out = lambda w: sum(w[c].sum() for c in ("et", "surq_gen", "latq", "perc", "qtile"))
    store = lambda w: w["sw_final"][-1] + w["sno_final"][-1]
    added = wb["gwsoilq"].sum() + wb["irr"].sum() - st["irr"].sum()
    assert out(wb) - out(st) + store(wb) - store(st) == pytest.approx(added, abs=0.5)
    assert wb["irr"].sum() < st["irr"].sum()


def test_bulk_and_single_sets_give_the_same_run():
    need(EXCHANGE)
    runs = []
    for how in ("all", "some", "one"):
        m = started(f"gws-bulk-{how}", 100)
        n = m.get_grid_size(m.get_var_grid(GW))
        v = np.linspace(0.5, 2.0, n) / MM_DAY
        for _ in range(10):
            if how == "all":
                m.set_value(GW, v)
            elif how == "some":
                m.set_value_at_indices(GW, np.arange(n)[::-1], v[::-1])
            else:
                for i in range(n):
                    m.set_value_at_indices(GW, np.array([i]), v[i:i + 1])
            m.update()
        runs.append(finish(m))
    assert n > 2
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[1], runs[2]) == []


def test_one_hru_one_layer_on_a_basin():
    """Ames_sub1: 2 mm/day into one layer of one HRU for a month. Only that HRU's gwsoilq
    prints it, and only that HRU's printed output differs from a run with nothing set."""
    need(EXCHANGE)
    plain = finish(started("gws-ames-plain", name="Ames_sub1", years=(1975, 1975)))
    m = started("gws-ames", 120, name="Ames_sub1", years=(1975, 1975))
    g = m.get_var_grid(GW)
    names = m.get_grid_object_names(g)
    hrus = m.get_grid_object_names(m.get_var_grid(GWH))
    target = hrus[5]
    i = names.index(f"{target}:2")
    for _ in range(30):
        m.set_value_at_indices(GW, np.array([i]), np.array([2.0 / MM_DAY]))
        m.update()
        assert values(m, GWH)[5] * MM_DAY == pytest.approx(2.0, rel=1e-5)
        assert (np.delete(values(m, GWH), 5) == 0).all()
    run = finish(m)
    wb = daily_table(run / "hru_wb_day.txt")
    wp = daily_table(plain / "hru_wb_day.txt")
    for h in hrus:
        if h == target:
            assert wb[h]["gwsoilq"][120:150] == pytest.approx(np.full(30, 2.0), abs=6e-4)
            assert wb[h]["gwsoilq"].sum() == pytest.approx(60.0, abs=0.02)
        else:
            assert (wb[h]["gwsoilq"] == 0).all()
            assert all(np.array_equal(wb[h][c], wp[h][c]) for c in wb[h])


def test_refusals_keep_nothing():
    need(EXCHANGE)
    m = started("gws-refuse", 30)
    n = m.get_grid_size(m.get_var_grid(GW))
    with pytest.raises(ExchangeError, match="value 2: groundwater into the soil can't be negative"):
        m.set_value_at_indices(GW, np.array([0, 1]), np.array([1e-8, -1e-8]))
    with pytest.raises(IndexError):
        m.set_value_at_indices(GW, np.array([n]), np.array([1e-8]))
    with pytest.raises(ValueError, match="output only"):
        m.set_value(GWH, np.zeros(1))
    m._x._send(f"setv {GW} 2\n1e-8\n2e-8")                 # SWAT+ itself wants every layer
    with pytest.raises(ExchangeError, match=f"setv needs all {n} values"):
        m._x._expect("@ok")
    m.update()                                           # nothing kept
    assert (values(m, GW) == 0).all() and values(m, GWH)[0] == 0
    m.finalize()


def test_set_before_a_save_or_branch_goes_in_after():
    """Set for the next day, then save and branch: the loaded run, the branch and the
    original all put it in, and all three finish as the same run."""
    need(EXCHANGE)
    m = started("gws-save", 400)
    n = m.get_grid_size(m.get_var_grid(GW))
    v = np.linspace(1.0, 3.0, n) / MM_DAY
    m.set_value(GW, v)
    snap = m.save_state(fresh("gws-snap"))
    b = m.branch(fresh("gws-branch"))
    k = SwatPlusBmi()
    k.initialize(str(project("Osu_1hru", "gws-loaded", daily=True)))
    k.load_state(snap)
    for x in (m, b, k):
        x.update()
        np.testing.assert_allclose(values(x, GW), v, rtol=1e-6)
    runs = [finish(x) for x in (m, b, k)]
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[0], runs[2]) == []
