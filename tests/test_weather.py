"""Weather records (0.6.0): precipitation, temperature, solar radiation, humidity and wind.

A set writes into a gage's record, as if its file had held that value, for a
day SWAT+ hasn't read yet, and SWAT+ builds everything else from the record
itself. So the test of faithfulness is exact: a run given a series through the
BMI is byte for byte the run whose weather files hold that series. Test
project: Osu_1hru (one gage each for precipitation, temperature, solar
radiation and wind; humidity is generated), printing everything daily.
"""

import datetime as dt

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi

P = "atmosphere_water__precipitation_leq-volume_flux"
TX = "atmosphere_bottom_air__max_of_temperature"
TN = "atmosphere_bottom_air__min_of_temperature"
SR = "land_surface_radiation~incoming~shortwave__energy_flux"
RH = "atmosphere_bottom_air_water~vapor__relative_saturation"
WS = "atmosphere_bottom_air_flow__speed"

# each variable's file, the value's column after year and day, and SWAT+'s units per the variable's
FILES = {P: ("Imsilpcp.pcp", 0, 86400000.0), TX: ("Imsiltmp.tmp", 0, 1.0),
         TN: ("Imsiltmp.tmp", 1, 1.0), SR: ("Imsilsol.slr", 0, 0.0864), WS: ("Imsilwind.wnd", 0, 1.0)}

# the change written into the files and set through the BMI, 2011 to 2012
CHANGE = {P: lambda v: v * 1.3 + 0.5, TX: lambda v: v + 2.0, TN: lambda v: v + 1.0,
          SR: lambda v: v * 0.9, WS: lambda v: v * 1.2}
FIRST, LAST = dt.date(2011, 1, 1), dt.date(2012, 12, 31)


def read_file(path):
    """A weather file's rows after its three header lines: {date: [values]}."""
    out = {}
    for line in path.read_text().splitlines()[3:]:
        f = line.split()
        if len(f) >= 3:
            d = dt.date(int(f[0]), 1, 1) + dt.timedelta(days=int(f[1]) - 1)
            out[d] = [float(x) for x in f[2:]]
    return out


def changed(run):
    """{var: (dates, values in SWAT+'s units as the file holds them)} for the changed years,
    with the values rounded to the file's five decimals."""
    out = {}
    for var, (name, col, _) in FILES.items():
        rows = read_file(run / name)
        dates = [d for d in sorted(rows) if FIRST <= d <= LAST]
        out[var] = (dates, [round(CHANGE[var](rows[d][col]), 5) for d in dates])
    return out


def rewrite(run, ch):
    """Write the changed values into the run's weather files."""
    for name in {n for n, _, _ in FILES.values()}:
        path = run / name
        lines = path.read_text().splitlines()
        for i in range(3, len(lines)):
            f = lines[i].split()
            if len(f) < 3:
                continue
            d = dt.date(int(f[0]), 1, 1) + dt.timedelta(days=int(f[1]) - 1)
            for var, (fname, col, _) in FILES.items():
                if fname == name and d in ch[var][0]:
                    f[2 + col] = f"{ch[var][1][ch[var][0].index(d)]:.5f}"
            lines[i] = "  ".join(f)
        path.write_text("\n".join(lines) + "\n")


def outputs_differ(files_run, run):
    """Differences from the run on rewritten files, less the weather files rewritten."""
    return [d for d in differences(files_run, run)
            if d.removeprefix("differs: ") not in {n for n, _, _ in FILES.values()}]


def started(dest, days=0):
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", dest, daily=True)))
    m.update_until(days)
    return m


def finish(m):
    while m.get_current_time() < m.get_end_time():
        m.update()
    run = m.run_dir
    m.finalize()
    return run


def set_changed(m, ch):
    for var, (dates, vals) in ch.items():
        m.set_weather(var, dates[0], np.array(vals) / FILES[var][2])


@pytest.fixture(scope="module")
def files_run():
    """Stock SWAT+ on files holding the changed weather."""
    need(STOCK)
    run = project("Osu_1hru", "Osu_1hru-wx-files", daily=True)
    ch = changed(run)
    rewrite(run, ch)
    run_stock(run)
    plain = project("Osu_1hru", "Osu_1hru-wx-plain", daily=True)
    run_stock(plain)
    changed_outputs = {d for d in differences(plain, run)}
    assert {"differs: channel_sd_day.txt", "differs: hru_wb_day.txt"} <= changed_outputs  # it matters
    return run, ch


def test_the_gage_grids():
    need(EXCHANGE)
    m = started("wx-grids")
    for var in FILES:
        g = m.get_var_grid(var)
        assert m.get_grid_size(g) == 1
        assert list(m.get_grid_object_names(g)) == [FILES[var][0]]
        assert var in m.get_input_var_names()
    assert m.get_var_grid(TX) == m.get_var_grid(TN)
    assert RH not in m.get_output_var_names()          # Osu_1hru generates humidity
    assert m.get_grid_x(m.get_var_grid(P), np.empty(1))[0] == pytest.approx(127.29)
    m.finalize()


def test_reading_gives_the_record_for_the_day_just_done():
    need(EXCHANGE)
    m = started("wx-read")
    assert np.isnan(m.get_value(P, np.empty(1))).all()  # no day done yet
    rows = {v: read_file(m.run_dir / FILES[v][0]) for v in FILES}
    for _ in range(400):
        m.update()
        day = m._start + dt.timedelta(days=m.get_current_time() - 1)
        for var, (_, col, k) in FILES.items():
            got = m.get_value(var, np.empty(1))[0] * k
            assert got == pytest.approx(rows[var][day][col], abs=1e-4), (var, day)
    m.finalize()


def test_the_first_day_that_can_be_set():
    need(EXCHANGE)
    m = started("wx-first", 10)
    coming = m._start + dt.timedelta(days=10)
    assert m.first_weather_day(TX) == coming + dt.timedelta(days=1)
    assert m.first_weather_day(P) == coming + dt.timedelta(days=2)
    m.finalize()


def test_setting_the_files_own_values_back_is_stock():
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "Osu_1hru-wx-stock", daily=True)
    run_stock(stock)
    m = started("wx-own")
    for var, (name, col, k) in FILES.items():
        rows = read_file(m.run_dir / name)
        first = m.first_weather_day(var)
        dates = [d for d in sorted(rows) if first <= d <= dt.date(2023, 12, 31)]
        m.set_weather(var, dates[0], np.array([rows[d][col] for d in dates]) / k)
    assert differences(stock, finish(m)) == []


def test_a_series_set_at_the_start_is_the_run_with_those_files(files_run):
    need(EXCHANGE)
    stock, ch = files_run
    m = started("wx-series")
    set_changed(m, ch)
    assert outputs_differ(stock, finish(m)) == []


def test_day_by_day_sets_are_the_same_run(files_run):
    """set_value each day writes the first record SWAT+ hasn't read: the same run again."""
    need(EXCHANGE)
    stock, ch = files_run
    m = started("wx-daily")
    while m.get_current_time() < m.get_end_time():
        for var, (dates, vals) in ch.items():
            d = m.first_weather_day(var)
            if d in dates:
                m.set_value(var, np.array([vals[dates.index(d)] / FILES[var][2]]))
        m.update()
    run = m.run_dir
    m.finalize()
    assert outputs_differ(stock, run) == []


def test_a_record_can_be_set_again_until_it_is_read(files_run):
    need(EXCHANGE)
    stock, ch = files_run
    m = started("wx-again")
    for var, (dates, vals) in ch.items():
        m.set_weather(var, dates[0], np.zeros(len(dates)) + (0.0 if var == P else 1.0))
    set_changed(m, ch)
    assert outputs_differ(stock, finish(m)) == []


def test_saved_and_branched_runs_keep_the_records(files_run):
    need(EXCHANGE)
    stock, ch = files_run
    m = started("wx-save", 200)                          # before the changed years
    set_changed(m, ch)
    state = m.save_state(fresh("wx-state"))
    b = m.branch(fresh("wx-branch"))
    assert outputs_differ(stock, finish(m)) == []
    assert outputs_differ(stock, finish(b)) == []
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-wx-loaded", daily=True)))
    n.load_state(state)
    assert n.get_current_time() == 200
    assert outputs_differ(stock, finish(n)) == []


def test_refusals_keep_nothing():
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "Osu_1hru-wx-refused-stock", daily=True)
    run_stock(stock)
    m = started("wx-refused", 30)
    first = m.first_weather_day(P)
    with pytest.raises(ExchangeError, match="already read"):
        m.set_weather(P, first - dt.timedelta(days=1), np.array([1e-8]))
    with pytest.raises(ExchangeError, match="already read"):
        m.set_weather(TX, m.first_weather_day(TX) - dt.timedelta(days=1), np.array([5.0]))
    with pytest.raises(ExchangeError, match="negative"):
        m.set_weather(P, first, np.array([1e-8, 1e-8, -1e-8]))   # all or nothing
    with pytest.raises(ExchangeError, match="missing"):
        m.set_weather(TN, first, np.array([-98.0]))
    with pytest.raises(ExchangeError, match="after the simulation ends"):
        m.set_weather(P, dt.date(2024, 1, 1), np.array([1e-8]))
    with pytest.raises(KeyError):                       # no humidity gages: no such variable
        m.set_weather(RH, first, np.array([0.5]))
    assert differences(stock, finish(m)) == []
