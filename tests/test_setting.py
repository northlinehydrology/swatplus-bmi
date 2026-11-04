"""Milestone 3: setting fluxes before the day runs.

Test project: Osu_1hru with a daily point source (a SWAT+ recall object)
added on its channel (the project's own recall.con, object "inflow"). The
recall file gives a steady 86.4 m3/day (0.001 m3/s), roughly half the
channel's usual flow. SWAT+ prints recall flows in recall_day.txt in m3/day,
although the column header says m^3/s.
"""

import datetime as dt

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, differences, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

PS = "point_source_water__volume_flow_rate"
QOUT = "channel_exit_water__volume_flow_rate"
BASE_M3D = 86.4


def add_point_source(run, m3_per_day=BASE_M3D):
    """Turn on Osu_1hru's recall object as a daily point source into cha01."""
    cnt = (run / "object.cnt").read_text().splitlines()
    f = cnt[2].split()
    #  name ls_area tot_area obj hru lhru rtu mfl aqu cha res rec ...
    f[3] = str(int(f[3]) + 1)
    f[11] = "1"
    cnt[2] = "  ".join(f)
    (run / "object.cnt").write_text("\n".join(cnt) + "\n")
    (run / "recall.rec").write_text(
        "recall.rec: daily point source for swatplus-bmi tests\n"
        "NUM     NAME    TYPE     FNAME\n"
        "   1    pnt01      1    pnt01.rec\n")
    rows = ["pnt01.rec: steady daily point source", "14",
            "jday mo day_mo yr ob_typ ob_name flo sed orgn sedp no3 solp chla nh3 no2 "
            "cbod dox san sil cla sag lag grv temp"]
    d = dt.date(2010, 1, 1)
    while d.year <= 2023:
        rows.append(f"{d.timetuple().tm_yday} {d.month} {d.day} {d.year} sdc cha01 "
                    f"{m3_per_day} " + " ".join(["0"] * 17))
        d += dt.timedelta(days=1)
    (run / "pnt01.rec").write_text("\n".join(rows) + "\n")


def osu_with_point_source(dest):
    return project("Osu_1hru", dest, daily=True, edit=add_point_source)


def step_with(run, rule):
    """Step the run one day at a time; ``rule(m, day)`` may set values before each day."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    log = []
    while m.get_current_time() < m.get_end_time():
        log.append(rule(m, int(m.get_current_time())))
        m.update()
    m.finalize()
    return log


def test_point_source_project_runs_like_stock():
    """The added point source works, and stepping it is still stock output."""
    need(STOCK, EXCHANGE)
    stock = osu_with_point_source("Osu_1hru-ps-stock")
    run_stock(stock)
    rec = daily_table(stock / "recall_day.txt")
    assert len(rec) == 1
    stepped = osu_with_point_source("Osu_1hru-ps-stepped")
    step_with(stepped, lambda m, d: None)
    assert differences(stock, stepped) == []


@pytest.mark.filterwarnings("ignore:a negative point source")
def test_set_point_source_changes_only_that_day_by_exactly_that_water():
    need(EXCHANGE)
    base = osu_with_point_source("Osu_1hru-ps-base")
    step_with(base, lambda m, d: None)
    days = {100: -0.0005, 101: 0.004, 2000: 0.0}            # m3/s, a diversion, a surge, off

    def rule(m, d):
        if d in days:
            i = m.get_object_index(PS, "inflow")
            m.set_value_at_indices(PS, np.array([i]), np.array([days[d]], np.float32))

    setrun = osu_with_point_source("Osu_1hru-ps-set")
    step_with(setrun, rule)

    b = daily_table(base / "recall_day.txt")["inflow"]["flo"] / 86400     # m3/day -> m3/s
    s = daily_table(setrun / "recall_day.txt")["inflow"]["flo"] / 86400
    changed = np.flatnonzero(b != s)
    assert list(changed) == [100, 101, 2000]               # 0-based day index = days done before it
    # the set values arrive as set
    np.testing.assert_allclose(s[changed], [days[k] for k in (100, 101, 2000)], rtol=1e-6, atol=1e-12)

    # channel inflow on those days moves by exactly the change in the point source
    bi = daily_table(base / "channel_sd_day.txt")["cha01"]["flo_in"]
    si = daily_table(setrun / "channel_sd_day.txt")["cha01"]["flo_in"]
    for k in (100, 101, 2000):
        np.testing.assert_allclose(si[k] - bi[k], days[k] - BASE_M3D / 86400, rtol=2e-3, atol=2e-7)
    # the day before the first change is untouched
    assert si[99] == bi[99]


def test_curtailment_rule_triggers_on_the_right_days_and_balances():
    """Cut a withdrawal when yesterday's flow is below a threshold.

    The point source is used as a withdrawal (a negative flow) of 0.0008 m3/s,
    cut to zero on any day after a day whose channel outflow was below the
    threshold. Every day's decision is checked against the flow the rule saw,
    the water taken is what was asked for, and the channel's daily water
    balance closes to the printed precision.

    SWAT+ doesn't limit a recall withdrawal to the water in the channel: on a
    day when the withdrawal is larger than the channel's other inflow, SWAT+
    records the full withdrawal, routes nothing, and that day's channel
    balance is off by the part of the withdrawal the channel couldn't supply.
    The test checks that those are the only days it fails. Withdrawals
    limited to the water available belong in water allocation.
    """
    need(EXCHANGE)
    want, threshold = -0.0008, 0.0012                       # m3/s
    seen = []

    def rule(m, d):
        q = m.get_value(QOUT, np.empty(1, np.float32))[0]   # yesterday's outflow
        cut = d > 0 and q < threshold
        m.set_value(PS, np.array([0.0 if cut else want], np.float32))
        seen.append(q)
        return cut

    run = osu_with_point_source("Osu_1hru-ps-curtail")
    with pytest.warns(UserWarning, match="doesn't limit it to the water"):
        cuts = np.array(step_with(run, rule))
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    flo = daily_table(run / "recall_day.txt")["inflow"]["flo"] / 86400   # m3/day -> m3/s

    # the rule saw SWAT+'s own outflow for the day before
    np.testing.assert_allclose(seen[1:], cha["flo_out"][:-1], rtol=5e-4, atol=1e-12)
    # it cut on exactly the days after a low-flow day, and only those
    expected = np.r_[False, cha["flo_out"][:-1] < threshold]
    near = np.r_[False, np.isclose(cha["flo_out"][:-1], threshold, rtol=5e-4)]   # printed rounding
    assert np.array_equal(cuts[~near], expected[~near])
    assert 50 < cuts.sum() < len(cuts) - 50                 # it really switches
    # what SWAT+ took is what was asked for, day by day, and in total
    np.testing.assert_allclose(flo, np.where(cuts, 0.0, want), rtol=1e-6, atol=1e-12)
    np.testing.assert_allclose(flo.sum() * 86400, (~cuts).sum() * want * 86400, rtol=1e-4)

    # channel balance, m3/day: in + rain - evaporation - seepage - out - change in storage
    q_in, q_out = cha["flo_in"] * 86400, cha["flo_out"] * 86400
    resid = (q_in + cha["precip"] - cha["evap"] - cha["seep"] - q_out
             - np.diff(np.r_[0.0, cha["flo_stor"]]))
    scale = np.abs(q_in) + cha["precip"] + cha["evap"] + cha["seep"] + q_out + cha["flo_stor"]
    closes = np.abs(resid) <= 1e-3 * scale + 1e-3
    short = q_in < 0                                        # withdrawal larger than the other inflow
    assert np.array_equal(~closes, short & (np.abs(resid) > 1e-3 * scale + 1e-3))
    assert closes[~short].all()
    # on those days nothing leaves the channel, and the balance is off by the unmet
    # part of the withdrawal: more than nothing, never more than the withdrawal
    assert (q_out[~closes] == 0).all()
    assert ((resid[~closes] < 0) & (resid[~closes] >= want * 86400 * (1 + 1e-3))).all()
