"""Channel depth and the groundwater exchange with channels (0.2.0).

These are what a groundwater model coupled to SWAT+ needs each day: the
water depth in each channel (for a river stage) and a way to put the
stream-aquifer exchange into, or take it out of, each channel. Test project:
Osu_1hru with everything printed daily.
"""

import numpy as np

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

QOUT = "channel_water__volume_outflow_rate"
QIN = "channel_water__volume_inflow_rate"
DEPTH = "channel_water__depth"
GW = "channel_water__groundwater_volume_exchange_rate"
EPS = np.finfo(np.float32).eps


def step(run, rate=None):
    """Step a run, setting the groundwater exchange to ``rate`` (m3/s) every day if given;
    read outflow, depth and the exchange after each day."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    out = {"q": [], "d": [], "x": [], "i": []}
    while m.get_current_time() < m.get_end_time():
        if rate is not None:
            m.set_value(GW, np.array([rate], np.float32))
        m.update()
        for k, v in (("q", QOUT), ("d", DEPTH), ("x", GW), ("i", QIN)):
            out[k].append(m.get_value(v, np.empty(1, np.float32))[0])
    m.finalize()
    return {k: np.array(v, dtype=float) for k, v in out.items()}


def test_depth_follows_the_rating_curve():
    """Depth is zero with no flow, never negative, and rises with flow."""
    need(EXCHANGE)
    r = step(project("Osu_1hru", "gw-depth", daily=True))
    q, d = r["q"], r["d"]
    assert (d >= 0).all()
    assert (d[q == 0] == 0).all()
    assert (d[q > 0] > 0).all()
    order = np.argsort(q)
    assert (np.diff(d[order]) >= -1e-6).all()          # one depth for one flow, rising with it
    assert d.max() > 0.01                               # a real series


def test_a_gain_joins_the_inflow_exactly():
    need(EXCHANGE)
    b = step(project("Osu_1hru", "gw-base", daily=True))
    gain = 0.0005                                       # m3/s
    r = step(project("Osu_1hru", "gw-gain", daily=True), gain)
    np.testing.assert_allclose(r["x"], gain, rtol=1e-6)
    # the inflow read through BMI rises by exactly the gain (float32, flows of size q)
    assert (np.abs((r["i"] - b["i"]) - gain) <= 2 * EPS * r["i"] + 1e-12).all()


def test_a_loss_takes_no_more_than_is_there_and_balances():
    """A loss comes out of the day's outflow, at most all of it; it shows as seepage, so the
    channel's printed balance still closes; later days are unchanged (no channel storage)."""
    need(EXCHANGE)
    b = step(project("Osu_1hru", "gw-base2", daily=True))
    loss = 0.0012                                       # m3/s, more than the flow on many days
    run = project("Osu_1hru", "gw-loss", daily=True)
    r = step(run, -loss)
    taken = -r["x"]
    np.testing.assert_allclose(taken, np.minimum(loss, b["q"]), rtol=1e-5, atol=1e-12)
    assert 100 < (taken < loss * (1 - 1e-6)).sum() < len(taken) - 100   # limited on some days only
    # the outflow is short by exactly what was taken (float32, flows of size q)
    assert (np.abs((b["q"] - r["q"]) - taken) <= 2 * EPS * b["q"] + 1e-12).all()
    # the printed balance closes with the loss counted as seepage (m3/day)
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    q_in, q_out = cha["flo_in"] * 86400, cha["flo_out"] * 86400
    resid = (q_in + cha["precip"] - cha["evap"] - cha["seep"] - q_out
             - np.diff(np.r_[0.0, cha["flo_stor"]]))
    scale = q_in + cha["precip"] + cha["evap"] + cha["seep"] + q_out + cha["flo_stor"]
    assert (np.abs(resid) <= 1e-3 * scale + 1e-3).all()
    np.testing.assert_allclose(cha["seep"], taken * 86400, rtol=1e-3, atol=1e-3)


def test_the_exchange_lasts_one_day():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "gw-oneday", daily=True)))
    m.update_until(100)
    m.set_value(GW, np.array([0.0005], np.float32))
    m.update()
    assert m.get_value(GW, np.empty(1, np.float32))[0] > 0
    m.update()
    assert m.get_value(GW, np.empty(1, np.float32))[0] == 0
    m.finalize()


def test_the_days_exchange_survives_save_and_load():
    from conftest import fresh
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "gw-save-a", daily=True)))
    m.update_until(776)
    m.set_value(GW, np.array([0.0005], np.float32))
    m.update()
    want = m.get_value(GW, np.empty(1, np.float32)).copy()
    snap = m.save_state(fresh("gw-save-snap"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "gw-save-b", daily=True)))
    n.load_state(snap)
    assert np.array_equal(n.get_value(GW, np.empty(1, np.float32)), want)
    assert want[0] > 0
    n.finalize()
