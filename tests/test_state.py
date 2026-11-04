"""Milestone 4b: saving state to a file and loading it in a new process.

The snapshot test: save at day N, load in a new SWAT+ process, continue to
the end, and match an unbroken stock run bit for bit, every output file.
Works on every platform (no fork). The serializer is generated from
SWAT+'s module declarations by tools/gen_serializer.py at build time.
"""

import shutil

import numpy as np
import pytest

from conftest import EXCHANGE, RUNS, STOCK, differences, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_allocation import DMD, osu_with_allocation


def fresh(dest):
    d = RUNS / dest
    if d.exists():
        shutil.rmtree(d)
    return d


def stock_daily():
    stock = project("Osu_1hru", "Osu_1hru-daily-stock", daily=True)
    run_stock(stock)
    return stock


@pytest.mark.parametrize("day", [0, 365, 1000, 5112])
def test_save_load_continue_is_stock(day):
    need(STOCK, EXCHANGE)
    stock = stock_daily()
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", f"Osu_1hru-st{day}-saved", daily=True)))
    m.update_until(day)
    snap = m.save_state(fresh(f"Osu_1hru-st{day}-snapshot"))
    m.update_until(m.get_end_time())                   # the saved run carries on unchanged
    m.finalize()

    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", f"Osu_1hru-st{day}-loaded", daily=True)))
    n.load_state(snap)
    assert n.get_current_time() == day
    n.update_until(n.get_end_time())
    n.finalize()

    assert differences(stock, RUNS / f"Osu_1hru-st{day}-saved") == []
    assert differences(stock, RUNS / f"Osu_1hru-st{day}-loaded") == []


def test_values_read_after_loading_are_the_saved_ones():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu_with_allocation("Osu_1hru-st-values-a")))
    m.update_until(777)
    want = {v: m.get_value(v, np.empty(m.get_grid_size(m.get_var_grid(v)), np.float32)).copy()
            for v in m.get_output_var_names()}
    snap = m.save_state(fresh("Osu_1hru-st-values-snap"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(osu_with_allocation("Osu_1hru-st-values-b")))
    n.load_state(snap)
    for v, w in want.items():
        got = n.get_value(v, np.empty(n.get_grid_size(n.get_var_grid(v)), np.float32))
        assert np.array_equal(got, w), v
    n.finalize()


def test_scenarios_from_one_saved_warm_up_match_unbroken_runs():
    need(EXCHANGE)
    warm_up = 2000

    def drive(m, rate):
        while m.get_current_time() < m.get_end_time():
            if rate is not None and m.get_current_time() >= warm_up:
                m.set_value(DMD, np.array([rate], np.float32))
            m.update()
        m.finalize()

    base = SwatPlusBmi()
    base.initialize(str(osu_with_allocation("Osu_1hru-st-warmup")))
    base.update_until(warm_up)
    snap = base.save_state(fresh("Osu_1hru-st-warmup-snap"))
    base.finalize()
    for name, rate in {"none": None, "cut": 0.0}.items():
        u = SwatPlusBmi()
        u.initialize(str(osu_with_allocation(f"Osu_1hru-st-{name}-unbroken")))
        drive(u, rate)
        s = SwatPlusBmi()
        s.initialize(str(osu_with_allocation(f"Osu_1hru-st-{name}-loaded")))
        s.load_state(snap)
        drive(s, rate)
        assert differences(RUNS / f"Osu_1hru-st-{name}-unbroken",
                           RUNS / f"Osu_1hru-st-{name}-loaded") == [], name


def test_get_state_set_state_round_trip():
    need(STOCK, EXCHANGE)
    stock = stock_daily()
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-bytes-a", daily=True)))
    m.update_until(1500)
    state = m.get_state()
    assert isinstance(state, bytes) and len(state) > 1000
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-st-bytes-b", daily=True)))
    n.set_state(state)
    assert n.get_current_time() == 1500
    n.update_until(n.get_end_time())
    n.finalize()
    assert differences(stock, RUNS / "Osu_1hru-st-bytes-b") == []


def test_a_state_from_another_build_is_refused(tmp_path):
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-bad-a")))
    m.update_until(10)
    snap = m.save_state(tmp_path / "snap")
    m.finalize()
    data = bytearray((snap / "state.bin").read_bytes())
    data[16:32] = b"0000000000000000"                  # the build digest
    (snap / "state.bin").write_bytes(bytes(data))
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-st-bad-b")))
    with pytest.raises(ExchangeError, match="different build"):
        n.load_state(snap)


@pytest.mark.full
def test_save_load_continue_is_stock_ames():
    """Ames_sub1 prints the soil carbon layer files, whose header flag is a saved
    local that carries state (patches/<tag>/carried-locals.toml)."""
    need(STOCK, EXCHANGE)
    stock = project("Ames_sub1", "Ames_sub1-st-stock")
    run_stock(stock)
    m = SwatPlusBmi()
    m.initialize(str(project("Ames_sub1", "Ames_sub1-st-saved")))
    m.update_until(3000)
    snap = m.save_state(fresh("Ames_sub1-st-snapshot"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Ames_sub1", "Ames_sub1-st-loaded")))
    n.load_state(snap)
    n.update_until(n.get_end_time())
    n.finalize()
    assert differences(stock, RUNS / "Ames_sub1-st-loaded") == []
