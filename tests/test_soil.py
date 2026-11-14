"""Soil layer temperature (settable) and the snowpack (0.4.0).

The first settable state. SWAT+ computes each layer's temperature in
stmp_solt; a hook right after it (exch_hru_soil, in hru_control.f90)
replaces the day's value for the layers set, before snowmelt, runoff and
percolation use it. Test project: Osu_1hru with everything printed daily
(hru_pw_day.txt prints layer 2's temperature, hru_wb_day.txt the snowpack).
"""

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table

TS = "soil_layer__temperature"
SNOW = "snowpack__liquid-equivalent_depth"


def started(dest, days=0):
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", dest, daily=True)))
    m.update_until(days)
    return m


def values(m, name):
    return m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name))))


def soils_layers(run):
    """Layers of the HRU's soil in soils.sol: SWAT+'s own 10 mm top layer plus the file's."""
    hru = run / "hru-data.hru"
    soil = hru.read_text().splitlines()[2].split()[4]
    lines = (run / "soils.sol").read_text().splitlines()
    for i, line in enumerate(lines):
        f = line.split()
        if f[:1] == [soil]:
            return [float(l.split()[0]) for l in lines[i + 1:i + 1 + int(f[1])]]
    raise ValueError(soil)


def test_the_layer_grid():
    need(EXCHANGE)
    m = started("soil-grid")
    g = m.get_var_grid(TS)
    n = m.get_grid_size(g)
    names = m.get_grid_object_names(g)
    assert m.get_grid_rank(g) == 3
    assert names[0] == "hru0001:1" and names[-1] == f"hru0001:{n}"
    z = m.get_grid_z(g, np.empty(n))
    assert (np.diff(z) > 0).all() and z[0] > 0
    # the deepest layer's mid-depth lies between the file's last two depths
    d = np.array(soils_layers(m.run_dir)) / 1000
    assert d[-2] < z[-1] < d[-1]
    assert m.get_grid_size(m.get_var_grid(SNOW)) == 1
    m.finalize()


def test_soil_temperature_and_snow_read_are_swats():
    """Layer 2 and the snowpack, every day, against SWAT+'s printed output."""
    need(EXCHANGE)
    m = started("soil-read")
    t2, snow = [], []
    while m.get_current_time() < m.get_end_time():
        m.update()
        t2.append(values(m, TS)[1])
        snow.append(values(m, SNOW)[0])
    run = m.run_dir
    m.finalize()
    pw = daily_table(run / "hru_pw_day.txt")["hru0001"]
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    np.testing.assert_allclose(t2, pw["sol_tmp"], rtol=5e-4, atol=5e-4)
    np.testing.assert_allclose(np.array(snow) * 1000, wb["sno_final"], rtol=5e-4, atol=5e-4)
    assert min(t2) < 0 < max(t2)                         # it freezes in winter
    assert max(snow) > 0


def test_a_set_temperature_holds_for_that_day_and_carries_on_as_swats_own():
    """Set every layer to 5 °C on one frozen day. That day's layer 2 prints 5; the days
    before are untouched; the day after differs only through SWAT+'s own lag."""
    need(EXCHANGE)
    base = started("soil-base")
    while base.get_current_time() < base.get_end_time():
        base.update()
    brun = base.run_dir
    base.finalize()
    b = daily_table(brun / "hru_pw_day.txt")["hru0001"]["sol_tmp"]
    day = int(np.flatnonzero(b < -1)[0])                # the first clearly frozen day

    m = started("soil-set", day)
    n = m.get_grid_size(m.get_var_grid(TS))
    m.set_value(TS, np.full(n, 5.0))
    m.update()
    assert (values(m, TS) == 5.0).all()                 # nothing later in the day changes it
    while m.get_current_time() < m.get_end_time():
        m.update()
    srun = m.run_dir
    m.finalize()
    s = daily_table(srun / "hru_pw_day.txt")["hru0001"]["sol_tmp"]
    assert (s[:day] == b[:day]).all()
    assert s[day] == 5.0
    assert s[day + 1] > b[day + 1]                       # carried by stmp_solt's lag


def test_bulk_and_single_sets_give_the_same_run():
    need(EXCHANGE)
    runs = []
    for how in ("all", "some", "one"):
        m = started(f"soil-bulk-{how}", 30)
        n = m.get_grid_size(m.get_var_grid(TS))
        for _ in range(10):
            if how == "all":
                m.set_value(TS, np.full(n, 0.25))                                  # setv
            elif how == "some":
                m.set_value_at_indices(TS, np.arange(n)[::-1], np.full(n, 0.25))   # seti
            else:
                for i in range(n):
                    m.set_value_at_indices(TS, np.array([i]), np.array([0.25]))    # set
            m.update()
        m.update_until(m.get_end_time())
        runs.append(m.run_dir)
        m.finalize()
    assert n > 2
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[1], runs[2]) == []


def test_refusals_keep_nothing():
    need(EXCHANGE)
    m = started("soil-refuse", 30)
    n = m.get_grid_size(m.get_var_grid(TS))
    before = values(m, TS).copy()
    with pytest.raises(ExchangeError, match="value 2: a temperature can't be below absolute zero"):
        m.set_value_at_indices(TS, np.array([0, 1]), np.array([5.0, -300.0]))
    with pytest.raises(ExchangeError, match=f"setv needs all {n} values"):
        m._x.set_all(TS, [1.0, 2.0])                     # refused before the values are sent
    m._x._send("ping")                                   # and the channel is still in step
    m._x._expect("@ok")
    m.update()                                           # neither set kept: layer 1 isn't 5
    assert values(m, TS)[0] != 5.0
    assert before[0] != 5.0
    m.finalize()


def test_a_set_temperature_saved_before_its_day_applies_after_loading():
    need(EXCHANGE)
    m = started("soil-save-a", 400)
    n = m.get_grid_size(m.get_var_grid(TS))
    m.set_value(TS, np.full(n, 3.5))
    snap = m.save_state(fresh("soil-save-snap"))
    m.finalize()
    k = SwatPlusBmi()
    k.initialize(str(project("Osu_1hru", "soil-save-b", daily=True)))
    k.load_state(snap)
    k.update()
    assert (values(k, TS) == 3.5).all()
    k.finalize()


def test_nothing_set_is_still_stock_with_the_hook():
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "soil-stock", daily=True)
    run_stock(stock)
    m = started("soil-stepped")
    while m.get_current_time() < m.get_end_time():
        m.update()
        values(m, TS)
        values(m, SNOW)
    run = m.run_dir
    m.finalize()
    assert differences(stock, run) == []


def test_the_readme_rule_keeps_frozen_layers_under_snow_just_above_zero():
    """The README's example, run: each held layer ends its day at the value held, 0.1 °C as
    SWAT+'s 4-byte real (SWAT+ counts 0 °C itself as frozen)."""
    need(EXCHANGE)
    m = started("soil-rule")
    g = m.get_var_grid(TS)
    hru_of_layer = [n.split(":")[0] for n in m.get_grid_object_names(g)]
    hru_index = {n: i for i, n in enumerate(m.get_grid_object_names(m.get_var_grid(SNOW)))}
    layer_hru = np.array([hru_index[h] for h in hru_of_layer])
    t, snow = np.empty(m.get_grid_size(g)), np.empty(len(hru_index))
    held_days = 0
    while m.get_current_time() < m.get_end_time():
        m.get_value(TS, t)
        m.get_value(SNOW, snow)
        hold = np.flatnonzero((snow[layer_hru] > 0.010) & (t <= 0.0))
        if len(hold):
            m.set_value_at_indices(TS, hold, np.full(len(hold), 0.1))
        m.update()
        if len(hold):
            held_days += 1
            assert (values(m, TS)[hold] == np.float32(0.1)).all()
    m.finalize()
    assert held_days > 5
