"""Irrigation applied to HRUs from outside SWAT+ (settable, 0.15.0; docs/design.md, "Irrigation
applied from outside").

Four variables on the hru grid, each set for the next day and read after it as what went in:
the water that enters the soil and the water that runs off (m3 s-1), and the nitrate and soluble
P the water carries (kg s-1). A hook right after the day's management operations
(exch_hru_irr, hru_control.f90) adds them where SWAT+'s own irrigation goes: the water into
the soil to irrig(j)%applied (swr_percmain puts it in the soil; printed as irr in hru_wb), the
runoff to irrig(j)%runoff (surface adds it to the day's surface runoff), the nitrate and P to
the top soil layer.

Osu_1hru's one HRU is a rice paddy, which the set refuses (SWAT+ irrigates a paddy's ponded
water). The tests make it upland: no surface storage, and the agrl land use (plant and harvest,
fertilizer when stressed, no irrigation of its own). Two years, everything printed daily.
"""

import os

import numpy as np
import pytest

from conftest import CHECK, EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table

INF = "land_irrigation_water__infiltration_volume_flow_rate"
RUN = "land_irrigation_water__runoff_volume_flow_rate"
NO3 = "land_irrigation_water_no3__mass_flow_rate"
SOLP = "land_irrigation_water_solp__mass_flow_rate"
ALL = (INF, RUN, NO3, SOLP)
IRR = "land_irrigation_water__volume_flow_rate"     # 0.11: the day's irrigation, any source
AREA = 10.0                                         # ha, Osu_1hru's HRU
M3MM = AREA * 10.0                                  # m3 per mm
DAY = 86400.0
YEARS = (2010, 2011)


TABLE = """
name                     conds      alts      acts       !irrigate 25 mm from an unlimited source on three days a year
irr_days                     3         3         1
var                        obj   obj_num           lim_var            lim_op     lim_const      alt1      alt2      alt3
jday                       hru         0              null                 -     175.00000         =         -         -
jday                       hru         0              null                 -     185.00000         -         =         -
jday                       hru         0              null                 -     195.00000         -         -         =
act_typ                    obj   obj_num              name            option         const        const2                fp  outcome
irr_demand                 hru         0         sprinkler     sprinkler_ilm      25.00000      20.00000             unlim  y   y   y
"""
OWN_DAYS = (174, 184, 194)                  # days from the start: jday 175, 185 and 195 of 2010
OWN = 25.0 * 0.85                           # sprinkler_ilm: 25 mm at 85%, none running off


def upland(irrigates=False):
    """An edit: Osu_1hru's HRU without its paddy, on the agrl land use (which never plants on
    Osu_1hru, so nothing is stressed and nothing irrigated); with ``irrigates``, a decision table
    irrigates it on three days a year (TABLE)."""
    def edit(run):
        p = run / "hru-data.hru"
        p.write_text(p.read_text().replace("rice140_lum", "agrl_lum").replace("paddy0001", "null"))
        if irrigates:
            p = run / "lum.dtl"
            lines = p.read_text().splitlines()
            lines[1] = str(int(lines[1]) + 1)           # SWAT+ reads that many tables, from the top
            lines[3:3] = TABLE.strip("\n").splitlines() + [""]
            p.write_text("\n".join(lines) + "\n")
            p = run / "management.sch"
            lines = p.read_text().splitlines()
            i = next(k for k, line in enumerate(lines) if line.split()[:1] == ["agrl_rot"])
            lines[i] = "agrl_rot                          0          3  "
            lines.insert(i + 3, "\tirr_days")
            p.write_text("\n".join(lines) + "\n")
    return edit


def started(dest, days=0, irrigates=False):
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", dest, daily=True, years=YEARS, edit=upland(irrigates))))
    m.update_until(days)
    return m


def value(m, name):
    return m.get_value(name, np.empty(1))[0]


def finish(m):
    while m.get_current_time() < m.get_end_time():
        m.update()
    run = m.run_dir
    m.finalize()
    return run


def set_run(dest, plan, irrigates=False):
    """A run with plan(day) -> {variable: m3/s or kg/s} set for each day; what was set and what
    was read back after each day, by day."""
    m = started(dest, irrigates=irrigates)
    put, back = {}, {}
    while m.get_current_time() < m.get_end_time():
        day = int(m.get_current_time())
        p = plan(day)
        for name in ALL:                                # water first: the mass comes with it
            if name in p:
                m.set_value(name, np.array([p[name]]))
        put[day] = p
        m.update()
        back[day] = {n: value(m, n) for n in (*ALL, IRR)}
    return finish(m), put, back


def stock_upland(dest, irrigates=False):
    need(STOCK)
    run = project("Osu_1hru", dest, daily=True, years=YEARS, edit=upland(irrigates))
    run_stock(run)
    return run


def test_the_variables():
    need(EXCHANGE)
    m = started("ira-grid")
    for name in ALL:
        assert name in m.get_input_var_names() and name in m.get_output_var_names()
        assert m.get_var_grid(name) == m.get_var_grid(IRR)
        assert value(m, name) == 0
    assert m.get_var_units(INF) == m.get_var_units(RUN) == "m3 s-1"
    assert m.get_var_units(NO3) == m.get_var_units(SOLP) == "kg s-1"
    m.finalize()


def test_nothing_set_or_zero_set_is_stock():
    """Identity: stepped with the four read every day, or with 0 set for all four every day, the
    run writes stock SWAT+'s output byte for byte."""
    stock = stock_upland("ira-stock")
    need(EXCHANGE)
    read, _, back = set_run("ira-read", lambda day: {})
    assert all(v == 0 for b in back.values() for k, v in b.items() if k != IRR)
    zero, _, back = set_run("ira-zero", lambda day: {n: 0.0 for n in ALL})
    assert all(v == 0 for b in back.values() for k, v in b.items() if k != IRR)
    assert differences(stock, read) == []
    assert differences(stock, zero) == []


def test_the_water_goes_where_swats_irrigation_goes():
    """Every fourth day from May to August of the first year, water into the soil, and on every
    eighth that has no rain some runoff too. Read back as set; printed as irr in hru_wb (the
    water into the soil, as SWAT+ prints its own irrigation), with
    land_irrigation_water__volume_flow_rate both; the runoff joins that day's surface runoff;
    and the HRU's water balance closes."""
    stock = stock_upland("ira-water-stock")
    need(EXCHANGE)
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]

    def plan(day):
        if 120 <= day < 240 and day % 4 == 0:
            p = {INF: (4.0 + day % 5) * M3MM / DAY}                 # 4 to 8 mm
            if day % 8 == 0 and st["precip"][day] == 0:
                p[RUN] = 1.5 * M3MM / DAY
            return p
        return {}

    run, put, back = set_run("ira-water", plan)
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    inf = np.array([put[d].get(INF, 0.0) for d in sorted(put)]) * DAY / M3MM   # mm
    ro = np.array([put[d].get(RUN, 0.0) for d in sorted(put)]) * DAY / M3MM
    assert (ro > 0).sum() >= 5
    for d, p in put.items():
        for name in (INF, RUN):
            assert back[d][name] == pytest.approx(p.get(name, 0.0), rel=1e-6, abs=0)
        assert back[d][IRR] == pytest.approx(p.get(INF, 0.0) + p.get(RUN, 0.0), rel=1e-6, abs=0)
    assert (st["irr"] == 0).all()
    np.testing.assert_allclose(wb["irr"], inf, rtol=5e-4, atol=6e-4)

    # the days before the first set are the stock run's; on it, the soil holds the water set into
    # it, and the runoff is added to what the day's rain made
    first = min(d for d in put if put[d])
    assert first == 120 and ro[first] > 0
    for c in wb:
        if isinstance(wb[c], np.ndarray):
            assert np.array_equal(wb[c][:first], st[c][:first]), c
    assert wb["sw_final"][first] - st["sw_final"][first] == pytest.approx(inf[first], abs=0.01)
    # surq_cont is the day's runoff before SWAT+'s surface runoff lag (sq_surfst), which sends
    # it on over the next days in surq_gen, as it does SWAT+'s own irrigation runoff
    assert wb["surq_cont"][first] == pytest.approx(st["surq_cont"][first] + ro[first], abs=1e-3)
    assert wb["surq_gen"][first] > st["surq_gen"][first]
    assert (wb["surq_cont"][ro > 0] >= ro[ro > 0] - 1e-3).all()

    # the water closes: over the run, what was put on the field leaves as more runoff, lateral
    # flow, percolation and ET, or is still in the soil and snow at the end
    out = lambda w: sum(w[c].sum() for c in ("et", "surq_gen", "latq", "perc", "qtile"))
    store = lambda w: w["sw_final"][-1] + w["sno_final"][-1]
    added = inf.sum() + ro.sum()
    assert added > 100
    assert out(wb) - out(st) + store(wb) - store(st) == pytest.approx(added, abs=0.5)

    # and day by day, as SWAT+ prints it: precipitation and irr in, ET, runoff, lateral flow,
    # percolation and tile flow out, and the change in soil water and snow. SWAT+ prints an
    # irrigation's runoff in surq_cont but not in irr, its own as the set one, so the printed
    # balance is short by the runoff on those days and closes on the others, as stock's does
    resid = lambda w: (w["precip"] + w["irr"] - w["et"] - w["surq_cont"] - w["latq"] - w["perc"]
                       - w["qtile"] - (w["sw_final"] - w["sw_init"]) - (w["sno_final"] - w["sno_init"]))
    np.testing.assert_allclose(resid(wb) + ro, resid(st), atol=0.02)


def test_runoff_on_a_rainy_day_comes_out_of_the_rain():
    """Found in SWAT+ 62.0.1 and documented, not patched (docs/design.md, Irrigation applied from
    outside): hru_control takes the rain's infiltration as the effective precipitation less
    surfq, and by then surfq holds the irrigation's runoff too. So on a day with rain, an
    irrigation's runoff, SWAT+'s own or set, is taken out of the rain that would have entered the
    soil: it leaves as runoff, the soil gets that much less, and the HRU gains no water from it.
    1.5 mm of runoff set alone on rainy days: the runoff rises by it that day, and over the run
    the HRU's water out and in store are those of the run without it."""
    stock = stock_upland("ira-rain-stock")
    need(EXCHANGE)
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]
    days = [d for d in range(100, 300) if st["precip"][d] - st["ecanopy"][d] > 5.0][:6]
    assert len(days) == 6
    run, put, back = set_run("ira-rain", lambda d: {RUN: 1.5 * M3MM / DAY} if d in days else {})
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    d = days[0]
    assert back[d][RUN] == pytest.approx(1.5 * M3MM / DAY, rel=1e-6)
    assert wb["surq_cont"][d] - st["surq_cont"][d] == pytest.approx(1.5, abs=1e-3)
    assert wb["sw_final"][d] - st["sw_final"][d] == pytest.approx(-1.5, abs=0.01)
    out = lambda w: sum(w[c].sum() for c in ("et", "surq_gen", "latq", "perc", "qtile"))
    store = lambda w: w["sw_final"][-1] + w["sno_final"][-1]
    assert out(wb) - out(st) + store(wb) - store(st) == pytest.approx(0.0, abs=0.05)


def test_a_decision_tables_irrigation_adds_to_the_set_water():
    """A decision table irrigates 21.25 mm on three days a year (TABLE: irr_demand, which sets
    irrig(j)%applied with "="), and 0.5 mm is set into the soil every day of the first summer:
    on the table's days irr prints both, so the set water is added to the table's, not replaced
    by it, and doesn't replace it. The second year, with nothing set, irr is the table's alone,
    on the same days of the year."""
    need(EXCHANGE)

    def plan(day):
        return {INF: 0.5 * M3MM / DAY} if 150 <= day < 250 else {}

    run, put, back = set_run("ira-dtbl", plan, irrigates=True)
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]["irr"]
    want = np.zeros(len(wb))
    want[150:250] = 0.5
    for d in OWN_DAYS:
        want[d] += OWN
        want[d + 365] += OWN
    np.testing.assert_allclose(wb, want, atol=6e-4)
    for d in OWN_DAYS:
        assert back[d][INF] == pytest.approx(0.5 * M3MM / DAY, rel=1e-6)    # what was set
        assert back[d][IRR] == pytest.approx((OWN + 0.5) * M3MM / DAY, rel=1e-5)
        assert back[d + 365][INF] == 0
        assert back[d + 365][IRR] == pytest.approx(OWN * M3MM / DAY, rel=1e-5)


def test_nitrate_and_phosphorus_go_into_the_top_layer_that_day():
    """One day, 10 mm into the soil and 3 mm of runoff, in two runs: the second also carries 20
    kg of nitrate and 10 kg of soluble P (2 and 1 kg/ha). Both read back as set. Until that day
    the runs are the same; on it, the runoff carries more nitrate and P out of the top layer,
    where SWAT+'s runoff takes them from. Nothing grows on the HRU, so the nitrate leaves within
    the run: percolation, denitrification, runoff and lateral flow take 2 kg/ha more than without
    it. The P mostly stays, sorbed in the soil."""
    need(EXCHANGE)
    day = 140
    water = {INF: 10.0 * M3MM / DAY, RUN: 3.0 * M3MM / DAY}
    mass = {NO3: 20.0 / DAY, SOLP: 10.0 / DAY}
    a, _, _ = set_run("ira-mass-a", lambda d: water if d == day else {})
    b, _, back = set_run("ira-mass-b", lambda d: {**water, **mass} if d == day else {})
    assert back[day][NO3] == pytest.approx(mass[NO3], rel=1e-6)
    assert back[day][SOLP] == pytest.approx(mass[SOLP], rel=1e-6)
    assert all(back[d][NO3] == 0 and back[d][SOLP] == 0 for d in back if d != day)
    ls = {k: daily_table(r / "hru_ls_day.txt")["hru0001"] for k, r in (("a", a), ("b", b))}
    nb = {k: daily_table(r / "hru_nb_day.txt")["hru0001"] for k, r in (("a", a), ("b", b))}
    pw = {k: daily_table(r / "hru_pw_day.txt")["hru0001"] for k, r in (("a", a), ("b", b))}
    for c in ("surqno3", "surqsolp", "lat3no3", "lchlabp"):
        assert np.array_equal(ls["a"][c][:day], ls["b"][c][:day]), c
    assert ls["b"]["surqno3"][day] > ls["a"]["surqno3"][day]
    assert ls["b"]["surqsolp"][day] > ls["a"]["surqsolp"][day]
    assert (nb["a"]["nuptake"] == 0).all()
    gone = lambda k: (sum(ls[k][c][day:].sum() for c in ("surqno3", "lat3no3", "tileno3"))
                      + pw[k]["percn"][day:].sum() + nb[k]["denit"][day:].sum())
    assert gone("b") - gone("a") == pytest.approx(2.0, abs=0.02)          # kg/ha
    p_out = lambda k: sum(ls[k][c][day:].sum() for c in ("surqsolp", "lchlabp", "tilelabp"))
    assert 0 < p_out("b") - p_out("a") < 1.0


def test_refusals_keep_nothing():
    need(EXCHANGE)
    m = started("ira-refuse", 30)
    for name in ALL:
        with pytest.raises(ExchangeError, match="can't be negative"):
            m.set_value(name, np.array([-1e-6]))
        with pytest.raises(IndexError):
            m.set_value_at_indices(name, np.array([1]), np.array([1e-6]))
    with pytest.raises(ExchangeError, match=f"set {INF} first"):
        m.set_value(NO3, np.array([1e-6]))
    m.set_value(RUN, np.array([1e-3]))                  # runoff isn't water into the soil
    with pytest.raises(ExchangeError, match="no irrigation water is set into HRU hru0001's soil"):
        m.set_value(SOLP, np.array([1e-7]))
    m.set_value(INF, np.array([1e-3]))
    m.set_value(NO3, np.array([1e-6]))
    m.set_value(SOLP, np.array([0.0]))                  # no mass is always fine
    with pytest.raises(ExchangeError, match="water into the soil can't be set to 0"):
        m.set_value(INF, np.array([0.0]))
    m.update()
    assert value(m, INF) == pytest.approx(1e-3, rel=1e-6)
    assert value(m, NO3) == pytest.approx(1e-6, rel=1e-6)
    m.update()                                          # a set lasts one day
    assert all(value(m, n) == 0 for n in ALL)
    m.finalize()


def test_a_paddy_is_refused():
    """Osu_1hru as shipped: its HRU is a rice paddy. SWAT+ puts a paddy's irrigation into the
    ponded water and its own paddy irrigation replaces the day's, so a set would not be used
    as given. Setting 0 is fine (nothing to use)."""
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "ira-paddy")))
    m.update_until(10)
    for name in (INF, RUN):
        with pytest.raises(ExchangeError, match="is a wetland or paddy"):
            m.set_value(name, np.array([1e-3]))
    m.set_value(INF, np.array([0.0]))
    m.update()
    assert value(m, INF) == 0
    m.finalize()


def test_all_four_every_day_stay_in_bounds():
    """Water, runoff, nitrate and P every day of the run, on the build with array bounds
    checked: a write outside SWAT+'s arrays would stop it with an error."""
    need(CHECK)
    os.environ["SWATPLUS_EXCHANGE"] = str(CHECK)
    try:
        run, _, back = set_run("ira-bounds", lambda d: {INF: 1.0 * M3MM / DAY, RUN: 0.2 * M3MM / DAY,
                                                        NO3: 1e-5, SOLP: 1e-6})
    finally:
        os.environ.pop("SWATPLUS_EXCHANGE", None)
    assert (run / "success.fin").exists()
    assert all(b[NO3] == pytest.approx(1e-5, rel=1e-6) for b in back.values())


def test_set_before_a_save_or_branch_goes_in_after():
    """Set all four for the next day, then save and branch: the loaded run, the branch and the
    original all put them in, and all three finish as the same run."""
    need(EXCHANGE)
    m = started("ira-save", 400)
    v = {INF: 6.0 * M3MM / DAY, RUN: 1.0 * M3MM / DAY, NO3: 1e-4, SOLP: 1e-5}
    for name in ALL:
        m.set_value(name, np.array([v[name]]))
    snap = m.save_state(fresh("ira-snap"))
    b = m.branch(fresh("ira-branch"))
    k = SwatPlusBmi()
    k.initialize(str(project("Osu_1hru", "ira-loaded", daily=True, years=YEARS, edit=upland())))
    k.load_state(snap)
    for x in (m, b, k):
        x.update()
        for name in ALL:
            assert value(x, name) == pytest.approx(v[name], rel=1e-6)
    runs = [finish(x) for x in (m, b, k)]
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[0], runs[2]) == []
