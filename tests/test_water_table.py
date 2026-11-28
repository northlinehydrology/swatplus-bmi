"""A water table in an HRU's soil (0.17.0): held layers, the groundwater inflow before runoff,
and the saturation excess.

For a groundwater model coupled through the BMI (Basin Ledger's link to MODFLOW 6):

- soil_layer__saturated_zone_flag holds an HRU's layers below a water table, a state kept until
  set again. A held layer doesn't drain: swr_percmain's layer loop gives back its percolation,
  and its lateral flow unless the run has held_lateral = "swat" (exch_hru_held). Water that
  percolates into a full held zone leaves the profile as SWAT+'s percolation, the recharge.
- The groundwater inflow (soil_layer_water__groundwater_inflow_rate) goes in after the day's ET
  and management, before surface runoff, for every run (exch_hru_gwsoil, now in hru_control),
  so the day's curve number sees it. With held layers, the HRU's set inflow fills them first,
  bottom up, to saturation, and only them; what they can't take is read as not taken.
- land_surface_water__saturation_excess_runoff_rate reads what swr_satexcess sends out of the
  top of the profile, whatever gw_soil_flag is (exch_hru_satex).

Test project: Osu_1hru (one HRU, six layers) made an evergreen forest without its paddy
(test_soil_cover), so the HRU has a curve number and its runoff is the soil's, 2010-2011,
everything printed daily.
"""

import os

import numpy as np
import pytest

from conftest import CHECK, EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table
from test_soil_cover import forest

HELD = "soil_layer__saturated_zone_flag"
SATEX = "land_surface_water__saturation_excess_runoff_rate"
GW = "soil_layer_water__groundwater_inflow_rate"
GWH = "soil_water__groundwater_inflow_rate"
NOT = "soil_water__groundwater_inflow_not_taken_rate"
ST = "soil_layer_water__depth"
UL = "soil_layer_water__saturation_depth"
FC = "soil_layer_water__field_capacity_depth"
PERC = "soil_water__percolation_rate"
MM_DAY = 86_400_000.0          # m/s -> mm/day
YEARS = (2010, 2011)


def config(dest, held_lateral=None):
    """The project, or a TOML config with held_lateral, for initialize."""
    p = project("Osu_1hru", dest, daily=True, years=YEARS, edit=forest)
    if held_lateral is None:
        return str(p)
    toml = p.parent / f"{dest}.toml"
    toml.write_text(f"project = '{p}'\nheld_lateral = \"{held_lateral}\"\n")   # literal: D:\\ on Windows
    return str(toml)


def started(dest, days=0, held_lateral=None):
    m = SwatPlusBmi()
    m.initialize(config(dest, held_lateral))
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


def stock_run(dest):
    need(STOCK)
    p = project("Osu_1hru", dest, daily=True, years=YEARS, edit=forest)
    run_stock(p)
    return p


def wb_of(run):
    return daily_table(run / "hru_wb_day.txt")["hru0001"]


def resid(w):
    """The printed day's water balance: what is left over, mm."""
    return (w["precip"] + w["irr"] + w["gwsoilq"] - w["et"] - w["surq_cont"] - w["latq"] - w["perc"]
            - w["qtile"] - (w["sw_final"] - w["sw_init"]) - (w["sno_final"] - w["sno_init"]))


def held_run(dest, plan, held_lateral=None, read=()):
    """A run that sets, every day, plan(day, m) -> (flags or None, inflow mm/day or None),
    and reads the flags, the inflow went in, the layers' water and the names in read after
    each day."""
    m = started(dest, held_lateral=held_lateral)
    back = {}
    while m.get_current_time() < m.get_end_time():
        day = int(m.get_current_time())
        flags, gw = plan(day, m)
        if flags is not None:
            m.set_value(HELD, np.asarray(flags, dtype=float))
        if gw is not None:
            m.set_value(GW, np.asarray(gw, dtype=float) / MM_DAY)
        m.update()
        back[day] = {n: values(m, n) for n in (HELD, GW, GWH, NOT, ST, SATEX, PERC) + tuple(read)}
    ul = values(m, UL) * 1000
    return finish(m), back, ul


BOTTOM3 = np.array([0, 0, 0, 1, 1, 1.0])         # the water table at 300 mm
SPRING = 60                                       # held from March


def test_the_variables():
    need(EXCHANGE)
    m = started("wt-vars")
    assert HELD in m.get_input_var_names() and HELD in m.get_output_var_names()
    assert SATEX in m.get_output_var_names() and SATEX not in m.get_input_var_names()
    assert NOT in m.get_output_var_names() and NOT not in m.get_input_var_names()
    assert m.get_var_grid(NOT) == m.get_var_grid(GWH) and m.get_var_units(NOT) == "m s-1"
    assert m.get_var_grid(HELD) == m.get_var_grid(ST)
    assert m.get_var_grid(SATEX) == m.get_var_grid(GWH)
    assert m.get_var_units(HELD) == "1" and m.get_var_units(SATEX) == "m s-1"
    assert (values(m, HELD) == 0).all() and (values(m, SATEX) == 0).all()
    m.set_value(HELD, BOTTOM3)
    for _ in range(3):
        m.update()
        np.testing.assert_array_equal(values(m, HELD), BOTTOM3)   # a state: kept until set again
    m.set_value(HELD, np.zeros(6))
    m.update()
    assert (values(m, HELD) == 0).all()
    m.finalize()


def test_nothing_held_is_stock_with_the_new_reads_every_day():
    """Nothing set, or the flags set to 0 every day, with every new value read every day:
    stock byte for byte."""
    stock = stock_run("wt-stock")
    need(EXCHANGE)
    for how in ("none", "zeros"):
        run, back, _ = held_run(f"wt-off-{how}",
                                lambda d, m: (np.zeros(6) if how == "zeros" else None, None))
        assert all((b[HELD] == 0).all() and (b[GW] == 0).all() for b in back.values())
        assert differences(stock, run) == []


def test_the_inflow_goes_in_before_the_curve_number():
    """Groundwater set into the soil on one rainy day, nothing held: that day's curve number
    (cn in hru_wb) is higher than stock's, so the water was in the soil before surface runoff
    (until 0.16 it went in after), and the printed balance still closes as stock's does."""
    stock = stock_run("wt-cn-stock")
    need(EXCHANGE)
    day0 = 164                                                       # 2010-06-14, rain
    run, back, _ = held_run("wt-cn", lambda d, m: (None, np.full(6, 2.0) if d == day0 else None))
    wb, st = wb_of(run), wb_of(stock)
    assert st["precip"][day0] > 8
    assert (wb["cn"][:day0] == st["cn"][:day0]).all()
    assert wb["cn"][day0] > st["cn"][day0]
    assert wb["gwsoilq"][day0] == pytest.approx(12.0, abs=6e-4)
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)


def test_held_layers_keep_their_water_and_the_balance_closes():
    """The bottom three layers held from March, nothing set into them: they never drain (their
    water only goes down by ET, never by percolation), nothing percolates out of the profile
    until they are full, and the printed balance closes every day as stock's."""
    stock = stock_run("wt-hold-stock")
    need(EXCHANGE)
    run, back, ul = held_run("wt-hold", lambda d, m: (BOTTOM3 if d == SPRING else None, None),
                             read=("land_surface__evapotranspiration_rate",))
    wb, st = wb_of(run), wb_of(stock)
    days = sorted(back)
    for d0, d1 in zip(days[SPRING:], days[SPRING + 1:]):
        held, before = back[d1][ST][3:] * 1000, back[d0][ST][3:] * 1000
        assert (held <= ul[3:] * (1 + 1e-6)).all()
        assert before.sum() - held.sum() <= wb["et"][d1] + 1e-3        # down only by ET
        if wb["perc"][d1] > 0:                                          # only from a full zone,
            assert ul[3:].sum() - held.sum() <= wb["eplant"][d1] + 1e-3  # less what plants took
    assert wb["perc"][SPRING + 1:].sum() > 0
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)


def test_the_inflow_fills_only_held_layers_and_the_rest_is_not_taken():
    """The bottom three layers held, 20 mm/day set into the bottom layer from May: the inflow
    fills the held layers, bottom up, and none goes above the water table. What they can't take
    isn't taken, read as not taken, and then they are full but for what the plants took after
    (eplant: pl_grow comes after the hook). What went in plus what wasn't taken is what was set."""
    stock = stock_run("wt-fill-stock")
    need(EXCHANGE)
    put = np.array([0, 0, 0, 0, 0, 20.0])
    run, back, ul = held_run("wt-fill", lambda d, m: (BOTTOM3 if d == SPRING else None,
                                                      put if d >= 120 else None))
    wb, st = wb_of(run), wb_of(stock)
    days = [d for d in back if d >= 120]
    for d in days:
        assert (back[d][GW][:3] == 0).all()                          # nothing above the table
        assert (back[d][GW].sum() + back[d][NOT][0]) * MM_DAY == pytest.approx(20.0, abs=2e-5)
        assert back[d][GWH][0] == pytest.approx(back[d][GW].sum(), rel=1e-5)
    spill = [d for d in days if back[d][NOT][0] > 0]
    assert spill                                                     # the zone filled
    for d in spill:
        assert ul[3:].sum() - back[d][ST][3:].sum() * 1000 <= wb["eplant"][d] + 1e-3
    assert all(back[d][NOT][0] == 0 for d in back if d < 120)
    np.testing.assert_allclose(wb["gwsoilq"], [back[d][GWH][0] * MM_DAY for d in sorted(back)],
                               rtol=5e-4, atol=6e-4)
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)


def test_a_full_profile_makes_saturation_excess_runoff():
    """Every layer held and kept full by the inflow from May, the water table at the surface:
    storms run off as saturation excess (read, and part of surq_gen), more runs off than in
    stock, and nothing recharges."""
    stock = stock_run("wt-satex-stock")
    need(EXCHANGE)
    run, back, ul = held_run("wt-satex", lambda d, m: (np.ones(6) if d == 120 else None,
                                                       np.full(6, 10.0) if d >= 120 else None))
    wb, st = wb_of(run), wb_of(stock)
    sat = np.array([back[d][SATEX][0] * MM_DAY for d in sorted(back)])
    # part of the surface runoff, which SWAT+ prints after its surface lag (lagsurf)
    assert sat.sum() <= wb["surq_gen"].sum() + wb["lagsurf"][-1] + 1e-2
    assert sat[150:].sum() > 10
    assert wb["surq_gen"][150:].sum() > st["surq_gen"][150:].sum() + 10
    assert (wb["perc"][121:] == 0).all()
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)


def test_a_released_layer_drains_through_percolation():
    """The bottom three layers held and filled for the summer, then released one by one, top
    first, with nothing more set: the water they held leaves as percolation in the weeks after,
    and the printed balance closes every day as stock's, through the release and after."""
    stock = stock_run("wt-release-stock")
    need(EXCHANGE)
    steps = {SPRING: BOTTOM3, 300: np.array([0, 0, 0, 0, 1, 1.0]), 303: np.array([0, 0, 0, 0, 0, 1.0]),
             306: np.zeros(6)}

    def plan(d, m):
        return steps.get(d), np.array([0, 0, 0, 0, 0, 10.0]) if 120 <= d < 300 else None

    run, back, ul = held_run("wt-release", plan)
    wb, st = wb_of(run), wb_of(stock)
    np.testing.assert_array_equal(back[306][HELD], np.zeros(6))
    assert ul[3:].sum() - back[299][ST][3:].sum() * 1000 <= wb["eplant"][299] + 1e-3
    assert wb["perc"][300:340].sum() > 5
    assert back[340][ST][3:].sum() < back[299][ST][3:].sum()
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)
    assert abs(resid(wb)[300:].sum() - resid(st)[300:].sum()) < 0.1


def test_on_a_paddy_the_balance_closes_with_its_ponded_water():
    """Osu_1hru as it ships, a rice paddy: the whole profile held and filled from February, then
    released one layer a day, top first, in April, nothing set after. On a ponded HRU the
    saturation excess goes to the paddy's water (wet_stor), not to surface runoff, and after the
    release the paddy, irrigated again, seeps into the soil: with the ponded water's change in
    it, the printed balance misses by no more than stock's does, day by day."""
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "wt-paddy-stock", daily=True, years=YEARS)
    run_stock(stock)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "wt-paddy", daily=True, years=YEARS)))
    sat = []
    while m.get_current_time() < m.get_end_time():
        d = int(m.get_current_time())
        if d == 35:
            m.set_value(HELD, np.ones(6))
        if 100 <= d <= 105:
            m.set_value(HELD, (np.arange(6) > d - 100).astype(float))
        if 35 <= d < 100:
            m.set_value(GW, np.full(6, 5.0) / MM_DAY)
        m.update()
        sat.append(values(m, SATEX)[0] * MM_DAY)
    run = finish(m)
    wb, st = wb_of(run), wb_of(stock)
    pond = lambda w: resid(w) - np.diff(np.r_[0.0, w["wet_stor"]])
    assert sum(sat) > 10                                            # spilled into the paddy
    # SWAT+'s printed paddy balance doesn't close by itself (-38 mm over the two years, stock),
    # so the check is that holding and releasing add nothing to what it misses, day by day
    np.testing.assert_allclose(np.cumsum(pond(wb)), np.cumsum(pond(st)), atol=0.1)


def test_held_lateral_none_or_swats_own():
    """The same held and filled zone with held_lateral "none" (the default) and "swat": with
    SWAT+'s own, the held layers give lateral flow, so more leaves as latq; with none, the held
    layers give none."""
    need(EXCHANGE)
    plan = lambda d, m: (BOTTOM3 if d == SPRING else None,
                         np.array([0, 0, 0, 0, 0, 10.0]) if d >= 120 else None)
    runs = {h: held_run(f"wt-lat-{h}", plan, held_lateral=h)[0] for h in ("none", "swat")}
    default = held_run("wt-lat-default", plan)[0]
    assert differences(runs["none"], default) == []
    assert wb_of(runs["swat"])["latq"].sum() > wb_of(runs["none"])["latq"].sum() + 1
    with pytest.raises(ValueError, match='held_lateral is "none" or "swat"'):
        SwatPlusBmi().initialize(config("wt-lat-bad", "both"))


def test_refusals_keep_nothing():
    need(EXCHANGE)
    m = started("wt-refuse", 30)
    with pytest.raises(ExchangeError, match="a saturated zone flag is 0"):
        m.set_value(HELD, np.full(6, 0.5))
    with pytest.raises(ExchangeError, match="layer 1 would be held and layer 2 below it not"):
        m.set_value(HELD, np.array([1, 0, 0, 0, 0, 1.0]))
    with pytest.raises(ExchangeError, match="layer 4 would be held and layer 5 below it not"):
        m.set_value_at_indices(HELD, np.array([3]), np.array([1.0]))
    with pytest.raises(IndexError):
        m.set_value_at_indices(HELD, np.array([6]), np.array([1.0]))
    with pytest.raises(ValueError, match="output only"):
        m.set_value(SATEX, np.zeros(1))
    m.update()
    assert (values(m, HELD) == 0).all()                              # nothing refused was kept
    m.set_value_at_indices(HELD, np.array([5]), np.array([1.0]))    # the bottom layer alone
    m.set_value_at_indices(HELD, np.array([4]), np.array([1.0]))    # then the one above it
    with pytest.raises(ExchangeError, match="layer 5 would be held and layer 6 below it not"):
        m.set_value_at_indices(HELD, np.array([5]), np.array([0.0]))
    m.update()
    np.testing.assert_array_equal(values(m, HELD), [0, 0, 0, 0, 1, 1])
    m.finalize()


def test_held_layers_every_day_stay_in_bounds():
    """Held layers and an inflow every day of the run, on the build with array bounds checked."""
    need(CHECK)
    os.environ["SWATPLUS_EXCHANGE"] = str(CHECK)
    try:
        run, back, _ = held_run("wt-bounds", lambda d, m: (BOTTOM3 if d % 50 else np.ones(6),
                                                           np.full(6, 3.0)), held_lateral="swat")
    finally:
        os.environ.pop("SWATPLUS_EXCHANGE", None)
    assert (run / "success.fin").exists()


def test_held_layers_and_the_option_go_through_a_save_load_and_branch():
    """Layers held in a run with held_lateral "swat", then a save and a branch: the loaded run
    (started without the option), the branch and the original keep the flags and the option,
    and all three finish as the same run."""
    need(EXCHANGE)
    m = started("wt-save", 200, held_lateral="swat")
    m.set_value(HELD, BOTTOM3)
    for _ in range(20):
        m.set_value(GW, np.array([0, 0, 0, 0, 0, 10.0]) / MM_DAY)
        m.update()
    snap = m.save_state(fresh("wt-snap"))
    b = m.branch(fresh("wt-branch"))
    k = started("wt-loaded")
    k.load_state(snap)
    for x in (m, b, k):
        np.testing.assert_array_equal(values(x, HELD), BOTTOM3)
        for _ in range(30):
            x.set_value(GW, np.array([0, 0, 0, 0, 0, 10.0]) / MM_DAY)
            x.update()
    runs = [finish(x) for x in (m, b, k)]
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[0], runs[2]) == []
