"""Milestone 3: setting a water allocation demand from a channel.

Test project: Osu_1hru with one water allocation place of use, user01, taking
water from cha01 with no returns (a consumptive use), at SWAT+'s own rate of
0.0005 m3/s every day. SWAT+ 62.0.1 ships no water allocation example; this
one is written from the input readers (water_allocation_read.f90). user01's
type, "out", is one SWAT+ doesn't deliver to, so the water it takes leaves
the model.
"""

import re

import numpy as np

from conftest import EXCHANGE, STOCK, differences, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

DMD = "place_of_use_water__volume_demand_rate"
WDR = "place_of_use_water__volume_withdrawal_rate"
QOUT = "channel_water__volume_outflow_rate"
RATE = 0.0005                                           # m3/s
EPS = np.finfo(np.float32).eps


def same_water(dq, dw, q):
    """dq == dw to float32 rounding of flows of size q (m3/s)."""
    assert (np.abs(dq - dw) <= 2 * EPS * np.abs(q) + 1e-12).all()


def add_allocation(run):
    cio = (run / "file.cio").read_text().splitlines()
    cio = ["water_rights      place_of_use.wro  point_of_diver.wro  null"
           if line.startswith("water_rights") else line for line in cio]
    (run / "file.cio").write_text("\n".join(cio) + "\n")
    (run / "place_of_use.wro").write_text(
        "place_of_use.wro: one place of use taking water from cha01, no returns\n"
        "1\n"
        "num name typ typ_num pods pors dtbl_mx rate_max dtbl_pod_fr dtbl_por_fr\n"
        f"1 user01 out 1 1 0 null {RATE} null null\n"
        "1 1 cha01 cha 1 null 0 null 0.0 null 1000000. 1.0 n\n")
    (run / "point_of_diver.wro").write_text(
        "point_of_diver.wro: cha01 supplies user01\n"
        "1\n"
        "num name typ typ_num pous pou_num pou_name pod_num typ typ_num right\n"
        "1 cha01 cha 1 1 1 user01 1 out 1 senior\n")


def osu_with_allocation(dest):
    return project("Osu_1hru", dest, daily=True, edit=add_allocation)


def step_with(run, rule):
    """Step one day at a time. ``rule(m, day)`` may set values before each
    day; after each day the channel outflow and the withdrawal are read."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    out = {"rule": [], "q": [], "w": [], "d": []}
    while m.get_current_time() < m.get_end_time():
        out["rule"].append(rule(m, int(m.get_current_time())))
        m.update()
        out["q"].append(m.get_value(QOUT, np.empty(1, np.float32))[0])
        out["w"].append(m.get_value(WDR, np.empty(1, np.float32))[0])
        out["d"].append(m.get_value(DMD, np.empty(1, np.float32))[0])
    m.finalize()
    return {k: np.array(v) for k, v in out.items()}


def test_allocation_project_runs_like_stock():
    need(STOCK, EXCHANGE)
    stock = osu_with_allocation("Osu_1hru-wa-stock")
    run_stock(stock)
    stepped = osu_with_allocation("Osu_1hru-wa-stepped")
    got = step_with(stepped, lambda m, d: None)
    assert differences(stock, stepped) == []
    # SWAT+ asks for its rate every day and gets it when the channel has it
    np.testing.assert_allclose(got["d"], RATE, rtol=1e-6)
    assert (got["w"] <= got["d"] * (1 + 1e-6)).all()
    assert (got["w"] > 0.99 * RATE).sum() > 1000


def test_read_withdrawal_matches_water_allo_day():
    need(EXCHANGE)
    run = osu_with_allocation("Osu_1hru-wa-read")
    got = step_with(run, lambda m, d: None)
    text = (run / "water_allo_day.txt").read_text()
    # POU lines: jday mon day yr unit POU name demand withdraw (m3/day)
    rows = re.findall(r"^\s*\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+POU\s+\S+\s+(\S+)\s+(\S+)", text, re.M)
    demand, withdraw = (np.array(c, dtype=float) / 86400 for c in zip(*rows))
    assert len(withdraw) == len(got["w"])
    np.testing.assert_allclose(got["w"], withdraw, rtol=1e-6, atol=1e-15)
    np.testing.assert_allclose(got["d"], demand, rtol=1e-6)


def test_set_withdrawal_removes_exactly_that_water():
    """A set demand is taken from the channel that day, and nothing else changes."""
    need(EXCHANGE)
    base = step_with(osu_with_allocation("Osu_1hru-wa-base"), lambda m, d: None)
    sets = {}

    def rule(m, d):
        # on every 97th day, ask for a third of the water the channel had the day before
        if d > 0 and d % 97 == 0:
            q = m.get_value(QOUT, np.empty(1, np.float32))[0]
            sets[d] = min(RATE, q / 3)
            m.set_value(DMD, np.array([sets[d]], np.float32))

    got = step_with(osu_with_allocation("Osu_1hru-wa-set"), rule)
    days = np.array(sorted(sets))
    assert len(days) > 40
    # SWAT+ took the demand that was set, no more, on the days it was set
    np.testing.assert_allclose(got["d"][days], [sets[d] for d in days], rtol=1e-6)
    assert (got["w"][days] <= got["d"][days] * (1 + 1e-6)).all()
    # the channel lost exactly the change in withdrawal, every day (m3/s, float32)
    same_water(base["q"] - got["q"], got["w"] - base["w"], np.maximum(base["q"], got["q"]))
    other = np.setdiff1d(np.arange(len(got["q"])), days)
    assert np.array_equal(got["q"][other], base["q"][other])
    assert np.array_equal(got["w"][other], base["w"][other])


def test_curtailment_rule_triggers_on_the_right_days_and_balances():
    """Cut the demand to zero the day after a day whose outflow was below a threshold."""
    need(EXCHANGE)
    threshold = 0.0012                                     # m3/s
    base = step_with(osu_with_allocation("Osu_1hru-wa-base2"), lambda m, d: None)

    def rule(m, d):
        q = m.get_value(QOUT, np.empty(1, np.float32))[0]   # yesterday's outflow
        cut = bool(d > 0 and q < threshold)
        if cut:
            m.set_value(DMD, np.array([0.0], np.float32))
        return cut

    run = osu_with_allocation("Osu_1hru-wa-curtail")
    got = step_with(run, rule)
    cuts = got["rule"].astype(bool)

    # cuts fell on exactly the days after a day below the threshold
    assert np.array_equal(cuts, np.r_[False, got["q"][:-1] < threshold])
    assert 50 < cuts.sum() < len(cuts) - 50
    # nothing taken on cut days; SWAT+'s own demand on the others
    assert (got["w"][cuts] == 0).all() and (got["d"][cuts] == 0).all()
    np.testing.assert_allclose(got["d"][~cuts], RATE, rtol=1e-6)
    # the water not taken stayed in the channel, day by day
    same_water(got["q"] - base["q"], base["w"] - got["w"], np.maximum(base["q"], got["q"]))

    # the channel's daily balance closes, withdrawal included, to printed precision (m3/day)
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    q_in, q_out, w = cha["flo_in"] * 86400, cha["flo_out"] * 86400, got["w"] * 86400
    resid = (q_in + cha["precip"] - cha["evap"] - cha["seep"] - q_out - w
             - np.diff(np.r_[0.0, cha["flo_stor"]]))
    scale = q_in + cha["precip"] + cha["evap"] + cha["seep"] + q_out + w + cha["flo_stor"]
    assert (np.abs(resid) <= 1e-3 * scale + 1e-3).all()


def add_aquifer_allocation(rate):
    def edit(run):
        add_allocation(run)
        (run / "place_of_use.wro").write_text(
            "place_of_use.wro: one well taking water from aqu001, no returns\n"
            "1\n"
            "num name typ typ_num pods pors dtbl_mx rate_max dtbl_pod_fr dtbl_por_fr\n"
            f"1 well01 out 1 1 0 null {rate} null null\n"
            "1 1 aqu001 aqu 1 null 0 null 100.0 null 1000000. 1.0 n\n")
        (run / "point_of_diver.wro").write_text(
            "point_of_diver.wro: aqu001 supplies well01\n"
            "1\n"
            "num name typ typ_num pous pou_num pou_name pod_num typ typ_num right\n"
            "1 aqu001 aqu 1 1 1 well01 1 out 1 senior\n")
    return edit


def test_aquifer_withdrawal_delivers_less_than_it_removes_swatplus_62_0_1():
    """Documents a SWAT+ 62.0.1 bug, so the test fails when it's fixed.

    wallo_withdraw.f90 (aquifer point of diversion) reduces storage first,
    aqu_d%stor = (1 - rto) * stor, then computes the delivery from the
    reduced storage, rto * stor. The aquifer loses rto x S, but the user
    receives rto x (1 - rto) x S. Here, a 0.002 m3/s well on aqu001 for 100
    days, against the same run with no well.
    """
    need(EXCHANGE)
    rate, days = 0.002, 100
    vol, got = {}, {}
    for tag, r in (("none", 0.0), ("well", rate)):
        run = project("Osu_1hru", f"Osu_1hru-aqu-{tag}", edit=add_aquifer_allocation(r))
        m = SwatPlusBmi()
        m.initialize(str(run))
        v, w = [m.get_value("aquifer_water__volume", np.empty(1, np.float32))[0]], []
        while m.get_current_time() < days:
            m.update()
            v.append(m.get_value("aquifer_water__volume", np.empty(1, np.float32))[0])
            w.append(m.get_value(WDR, np.empty(1, np.float32))[0] * 86400)
        m.finalize()
        vol[tag], got[tag] = np.array(v, dtype=float), np.array(w, dtype=float)

    removed = np.diff(vol["none"] - vol["well"])                # m3/day the well took from the aquifer
    delivered = got["well"]                                      # m3/day the user received
    np.testing.assert_allclose(removed, rate * 86400, rtol=1e-4)
    # the aquifer loses the demand, the user gets less: by rto = removed / storage before
    stor = vol["well"][:-1] + removed                            # storage before the withdrawal (recharge ~0)
    rto = removed / stor
    assert (delivered < removed).all()
    np.testing.assert_allclose(delivered, removed * (1 - rto), rtol=2e-3)


# Places of use read with the right indices (patches/62.0.1/0002-wallo-withdraw-duty.patch).
# SWAT+ 62.0.1's wallo_withdraw.f90 reads a place of use's duty as pou(ipou)%pod(ipod)%duty, with
# ipou its position in the point of diversion's list and ipod the point of diversion's number,
# where pou(pou_num)%pod(pod_num)%duty is meant. Stock 62.0.1 gives places of use sharing a point
# of diversion each other's demands, and a place of use on any point of diversion but the first a
# read past the end of its list (0, or a stray value). docs/upstream-issue-wallo-withdraw.md.

POU_HDR = "num name typ typ_num pods pors dtbl_mx rate_max dtbl_pod_fr dtbl_por_fr"
POD_HDR = "num name typ typ_num pous pou_num pou_name pod_num typ typ_num right"
WANT = {"user01": 0.0010, "user02": 0.0002}             # m3/s; cha01 carries about 0.003 to 0.03


def add_places(pous, pods):
    """``pous``: (name, the point of diversion it takes from), numbered in order; ``pods``: the
    rows of point_of_diver.wro. Each place of use takes from cha01 with no returns."""
    def edit(run):
        add_allocation(run)
        rows = ["place_of_use.wro", str(len(pous)), POU_HDR]
        for i, (name, pod) in enumerate(pous, 1):
            rows += [f"{i} {name} out {i} 1 0 null 100.0 null null",
                     f"1 {pod} cha01 cha 1 null 0 null 0.0 null 1000000. 1.0 n"]
        (run / "place_of_use.wro").write_text("\n".join(rows) + "\n")
        (run / "point_of_diver.wro").write_text("\n".join(["point_of_diver.wro", str(len(pods)), POD_HDR, *pods])
                                                + "\n")
    return edit


def run_places(dest, pous, pods, days=365):
    """Each place of use asks WANT every day for ``days`` days; returns its names, demands and
    withdrawals (m3/s, day by day) and cha01's outflow."""
    run = project("Osu_1hru", dest, edit=add_places(pous, pods))
    m = SwatPlusBmi()
    m.initialize(str(run))
    names = list(m.get_grid_object_names(m.get_var_grid(DMD)))
    want = np.array([WANT[n] for n in names])
    w, d, q = [], [], []
    while m.get_current_time() < days:
        m.set_value(DMD, want)
        m.update()
        w.append(m.get_value(WDR, np.empty(len(names))))
        d.append(m.get_value(DMD, np.empty(len(names))))
        q.append(m.get_value(QOUT, np.empty(1))[0])
    m.finalize()
    return names, np.array(d), np.array(w), np.array(q)


def test_places_of_use_sharing_a_point_of_diversion_each_get_their_own_demand():
    """user02 listed first on cha01's point of diversion, user01 second: each asks for its own
    demand and gets no more than that. Stock 62.0.1 gives user02 user01's demand (over its own on
    a third of the days) and user01 user02's."""
    need(EXCHANGE)
    names, d, w, q = run_places("Osu_1hru-wa-shared", [("user01", 1), ("user02", 1)],
                                ["1 cha01 cha 1 2 2 user02 1 out 2 senior 1 user01 1 out 1 senior"])
    assert names == ["user01", "user02"]
    np.testing.assert_allclose(d, [[WANT["user01"], WANT["user02"]]] * len(d), rtol=1e-6)
    assert (w <= d * (1 + 1e-6)).all()
    # user02, the senior, is served first; when the channel has enough for both, each gets its own
    full = np.isclose(w, d, rtol=1e-5).all(axis=1)
    assert full.sum() > 100
    # user01's demand is five times user02's, and it gets that, which stock 62.0.1 never gives it
    assert (w[:, 0] > 2 * WANT["user02"]).sum() > 100


def test_place_of_use_on_a_later_point_of_diversion_delivers_as_on_the_first():
    """user01 alone on cha01, its point of diversion the file's second (after one with no places
    of use) delivers what it does when its point of diversion is the first, day by day. Stock
    62.0.1 reads its duty past the end of its list and delivers nothing."""
    need(EXCHANGE)
    pou = [("user01", 1)]
    _, d1, w1, q1 = run_places("Osu_1hru-wa-pod1", pou, ["1 cha01 cha 1 1 1 user01 1 out 1 senior"])
    pou = [("user01", 2)]
    _, d2, w2, q2 = run_places("Osu_1hru-wa-pod2", pou,
                               ["1 nothing cha 1 0", "2 cha01 cha 1 1 1 user01 1 out 1 senior"])
    assert (w1 > 0).sum() > 300
    assert np.array_equal(d2, d1)
    assert np.array_equal(w2, w1)
    assert np.array_equal(q2, q1)
