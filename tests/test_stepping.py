"""Milestone 1: stepping under outside control gives stock SWAT+ output.

Test project: Osu_1hru from SWAT+'s refdata/ (one HRU, a routing unit, one
SWAT-deg channel and one aquifer; 2010-2023). The daily variant prints every
object at every interval from the first day, so every output file SWAT+ can
write for this project is compared.
"""

import shutil

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, differences, need, output_count, project, run_stock
from swatplus_bmi import SwatPlusBmi

CASES = [("Osu_1hru", False), ("Osu_1hru", True)]


def _id(case):
    return f"{case[0]}{'-daily' if case[1] else ''}"


@pytest.fixture(params=CASES, ids=_id)
def case(request):
    return request.param


def stock_run(name, daily, tag="stock"):
    run = project(name, f"{name}{'-daily' if daily else ''}-{tag}", daily=daily)
    run_stock(run)
    return run


def test_exchange_off_is_stock(case):
    need(STOCK, EXCHANGE)
    name, daily = case
    stock = stock_run(name, daily)
    off = project(name, f"{name}{'-daily' if daily else ''}-off", daily=daily)
    run_stock(off, EXCHANGE)                     # no --exchange: the mode is off
    assert output_count(stock) > 20
    assert differences(stock, off) == []


def test_stepped_one_day_at_a_time_is_stock(case):
    need(STOCK, EXCHANGE)
    name, daily = case
    stock = stock_run(name, daily)
    stepped = project(name, f"{name}{'-daily' if daily else ''}-stepped", daily=daily)

    m = SwatPlusBmi()
    m.initialize(str(stepped))
    end = m.get_end_time()
    assert m.get_current_time() == 0.0
    steps = 0
    while m.get_current_time() < end:
        m.update()
        steps += 1
        assert m.get_current_time() == steps
    assert steps == end
    m.finalize()

    assert (stepped / "success.fin").exists()
    assert differences(stock, stepped) == []


def test_reading_values_every_day_changes_nothing():
    """Reading every variable every day must not change SWAT+'s output."""
    need(STOCK, EXCHANGE)
    stock = stock_run("Osu_1hru", True)
    run = project("Osu_1hru", "Osu_1hru-daily-read", daily=True)
    m = SwatPlusBmi()
    m.initialize(str(run))
    while m.get_current_time() < m.get_end_time():
        for name in m.get_output_var_names():
            m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name)), np.float32))
        m.update()
    m.finalize()
    assert differences(stock, run) == []


def test_update_until_in_chunks_is_stock():
    need(STOCK, EXCHANGE)
    stock = stock_run("Osu_1hru", True)
    run = project("Osu_1hru", "Osu_1hru-daily-chunks", daily=True)
    m = SwatPlusBmi()
    m.initialize(str(run))
    for t in (1, 59, 60, 365, 366, 1000, 4000, m.get_end_time()):
        m.update_until(t)
        assert m.get_current_time() == t
    m.finalize()
    assert differences(stock, run) == []


def test_time_and_catalog():
    need(EXCHANGE)
    run = project("Osu_1hru", "Osu_1hru-catalog")
    m = SwatPlusBmi()
    m.initialize(str(run))
    try:
        assert m.get_component_name() == "SWAT+"
        assert m.get_time_units() == "days since 2010-01-01 00:00:00"
        assert m.get_time_step() == 1.0
        assert m.get_start_time() == 0.0
        assert m.get_end_time() == 5113.0              # 2010-2023, three leap years
        cha = m.get_var_grid("channel_water__volume_outflow_rate")
        assert m.get_grid_type(cha) == "points"
        assert m.get_grid_object_names(cha) == ["cha01"]
        assert m.get_var_units("channel_water__volume_outflow_rate") == "m3 s-1"
        with pytest.raises(NotImplementedError):
            m.get_value_ptr("channel_water__volume_outflow_rate")
        with pytest.raises(ValueError):
            m.update_until(1.5)
    finally:
        m.finalize()


def test_finalize_before_the_end_stops_cleanly():
    need(EXCHANGE)
    run = project("Osu_1hru", "Osu_1hru-early-stop")
    m = SwatPlusBmi()
    m.initialize(str(run))
    m.update_until(30)
    m.finalize()
    assert not (run / "success.fin").exists()


def test_comparison_catches_a_one_byte_change(tmp_path):
    """The comparison itself: one changed byte in one output file is caught."""
    need(STOCK)
    stock = stock_run("Osu_1hru", False)
    copy = tmp_path / "copy"
    shutil.copytree(stock, copy)
    p = copy / "basin_wb_yr.txt"
    data = bytearray(p.read_bytes())
    i = len(data) // 2
    data[i] = ord("7") if data[i] != ord("7") else ord("8")
    p.write_bytes(bytes(data))
    assert differences(stock, copy) == ["differs: basin_wb_yr.txt"]


@pytest.mark.full
@pytest.mark.parametrize("years", [None, (1975, 1979)], ids=["full-length", "daily-5yr"])
def test_ames_stepped_is_stock(years):
    """Ames_sub1 (12 HRUs, 1975-2020): stepped one day at a time, every file matches stock.

    The full-length run keeps the project's own printing; the five-year run
    prints everything daily. Opt in with ``pytest -m full``.
    """
    need(STOCK, EXCHANGE)
    daily = years is not None
    tag = "daily-5yr" if daily else "full"
    stock = project("Ames_sub1", f"Ames_sub1-{tag}-stock", daily=daily, years=years)
    run_stock(stock)
    off = project("Ames_sub1", f"Ames_sub1-{tag}-off", daily=daily, years=years)
    run_stock(off, EXCHANGE)
    assert differences(stock, off) == []

    stepped = project("Ames_sub1", f"Ames_sub1-{tag}-stepped", daily=daily, years=years)
    m = SwatPlusBmi()
    m.initialize(str(stepped))
    while m.get_current_time() < m.get_end_time():
        m.update()
    m.finalize()
    assert differences(stock, stepped) == []
