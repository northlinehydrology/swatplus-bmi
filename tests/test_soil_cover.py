"""The soil cover of SWAT+'s soil evaporation (settable, 0.16.0; docs/design.md, "Setting
states", and "The soil cover").

et_act.f90 computes an HRU's soil cover each day as the plant community's above-ground mass
plus the surface residue (cover = pl_mass(j)%ab_gr_com%m + pl_mass(j)%rsd_tot%m, kg/ha) and
scales the day's potential soil evaporation by eaj = Exp(-5.e-5 * (cover + 0.1)), or 0.5 under
snow. A hook right after the cover is computed (exch_hru_cover) replaces it with the value set
for the day. With it come three reads of SWAT+'s own: the community's above-ground and leaf
mass and the surface residue, which the next day's cover is made from.

Osu_1hru's one HRU is a rice paddy, whose ponded water evaporates without the cover. The tests
make it an evergreen forest that is growing from the start (frse_tecf, 50 t/ha of biomass and
10 t/ha of residue, as a study's forest is initialized), whose wood keeps SWAT+'s soil
evaporation near zero. Two years, everything printed daily.
"""

import os

import numpy as np
import pytest

from conftest import CHECK, EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table

COVER = "soil_surface_cover__mass-per-area_density"
ABGR = "land_vegetation__above-ground_mass-per-area_density"
LEAF = "land_vegetation_leaf__mass-per-area_density"
RSD = "land_surface_residue__mass-per-area_density"
READS = (COVER, ABGR, LEAF, RSD)
ET = "land_surface__evapotranspiration_rate"
YEARS = (2010, 2011)
KGHA = 1e4                                          # kg/ha per kg/m2


def forest(run):
    """An edit: Osu_1hru's HRU as an evergreen forest growing from the start, without its paddy."""
    p = run / "hru-data.hru"
    p.write_text(p.read_text().replace("rice140_lum", "frse_tecf_lum").replace("paddy0001", "null"))
    p = run / "plant.ini"
    lines = p.read_text().splitlines()
    i = next(k for k, line in enumerate(lines) if line.split()[:2] == ["frse_tecf", "n"])
    lines[i] = (f"{'':40}{'frse_tecf':<20}{'y':>2}{2.0:>14.5f}{50000.0:>14.5f}{0.0:>14.5f}"
                f"{0.0:>14.5f}{1.0:>14.5f}{10000.0:>14.5f}")
    p.write_text("\n".join(lines) + "\n")


def started(dest, days=0):
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", dest, daily=True, years=YEARS, edit=forest)))
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


def set_run(dest, plan, exe=None):
    """A run with plan(day, reads at the pause) -> a cover (kg/m2) or None set for each day; the
    four reads after each day, by day."""
    m = started(dest)
    back = {}
    now = {n: value(m, n) for n in READS}
    while m.get_current_time() < m.get_end_time():
        day = int(m.get_current_time())
        c = plan(day, now)
        if c is not None:
            m.set_value(COVER, np.array([c]))
        m.update()
        now = {n: value(m, n) for n in READS}
        back[day] = now
    return finish(m), back


def stock_forest(dest):
    need(STOCK)
    run = project("Osu_1hru", dest, daily=True, years=YEARS, edit=forest)
    run_stock(run)
    return run


def f32(v):
    """SWAT+'s 4-byte value, in kg/ha, behind a read in kg/m2."""
    return np.float32(v * KGHA)


def test_the_variables():
    need(EXCHANGE)
    m = started("cov-grid")
    assert COVER in m.get_input_var_names() and COVER in m.get_output_var_names()
    for name in (ABGR, LEAF, RSD):
        assert name in m.get_output_var_names() and name not in m.get_input_var_names()
    for name in READS:
        assert m.get_var_grid(name) == m.get_var_grid(ET)
        assert m.get_var_units(name) == "kg m-2"
    assert np.isnan(value(m, COVER))                    # no day done, no cover computed
    m.update()
    # SWAT+ sums the community's masses (pl_community) after et_act, so on the first day the
    # cover is the residue alone, 1 t/ha; from the second, the forest's 40 t/ha above ground too
    assert value(m, COVER) == 1.0
    assert 0 < value(m, LEAF) < value(m, ABGR) == 4.0
    m.update_until(3)
    assert value(m, COVER) > 4.5
    m.finalize()


def test_the_reads_are_swats_and_make_the_next_days_cover():
    """The residue read is hru_pw's residue, but on December 31: the read comes after SWAT+'s
    end-of-year processing, which hru_pw prints before. And each day's cover is the above-ground
    mass plus the residue read at the pause before it, added in SWAT+'s 4-byte arithmetic, to
    the bit (no decision table or operation touches the forest at the start of a day)."""
    need(EXCHANGE)
    run, back = set_run("cov-read", lambda day, now: None)
    pw = daily_table(run / "hru_pw_day.txt")["hru0001"]
    days = sorted(back)
    rsd = np.array([back[d][RSD] for d in days]) * KGHA
    mid = ~((pw["mon"] == 12) & (pw["day"] == 31))
    np.testing.assert_allclose(rsd[mid], pw["residue"][mid], rtol=5e-4, atol=0.5)
    made = 0
    for d in days[1:]:
        c = back[d][COVER]
        if np.isnan(c):
            continue
        assert f32(c) == f32(back[d - 1][ABGR]) + f32(back[d - 1][RSD]), d
        made += 1
    assert made > 600
    assert sum(np.isnan(back[d][COVER]) for d in days) < 100


def test_nothing_set_is_stock():
    stock = stock_forest("cov-stock")
    need(EXCHANGE)
    run, _ = set_run("cov-nothing", lambda day, now: None)
    assert differences(stock, run) == []


def test_swats_own_cover_set_back_is_stock():
    """The cover each day's soil evaporation used, read in one run, set back for the same day in
    another: stock SWAT+'s output, byte for byte."""
    stock = stock_forest("cov-own-stock")
    need(EXCHANGE)
    _, own = set_run("cov-own-a", lambda day, now: None)
    run, back = set_run("cov-own-b", lambda day, now: None if np.isnan(own[day][COVER]) else own[day][COVER])
    assert all(np.isnan(back[d][COVER]) == np.isnan(own[d][COVER]) for d in own)
    assert all(back[d][COVER] == own[d][COVER] for d in own if not np.isnan(own[d][COVER]))
    assert differences(stock, run) == []


def summer_day(st):
    """A dry-canopy summer day: no snow, more PET than the canopy can take, rain a few days back
    so the top soil has water to evaporate."""
    for d in range(150, 240):
        if (st["sno_init"][d] == 0 and st["pet"][d] > 2 and st["ecanopy"][d] == 0
                and st["precip"][d - 3:d].sum() > 5):
            return d
    raise AssertionError("no summer day to set")


def test_a_set_cover_changes_that_day_and_then_only_through_the_soil():
    """1 t/ha set on one summer day: the days before are stock's; that day the cover read is the
    set one, to the bit, and the soil evaporates more than stock's; the next day's cover is SWAT+'s
    own again, made from that day's plant mass and residue, so the run goes on differing only
    through what the day's evaporation took from the soil."""
    stock = stock_forest("cov-day-stock")
    need(EXCHANGE)
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]
    day = summer_day(st)
    run, back = set_run("cov-day", lambda d, now: 0.1 if d == day else None)
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    for c in wb:
        if isinstance(wb[c], np.ndarray):
            assert np.array_equal(wb[c][:day], st[c][:day]), c
    assert f32(back[day][COVER]) == np.float32(1000.0)
    assert wb["esoil"][day] > 2 * st["esoil"][day] > 0.4
    assert wb["sw_final"][day] < st["sw_final"][day]
    nxt = back[day + 1][COVER]
    assert f32(nxt) == f32(back[day][ABGR]) + f32(back[day][RSD])
    assert f32(nxt) > 40000


def test_under_snow_a_set_cover_changes_nothing():
    """Under snow SWAT+ takes eaj = 0.5 whatever the cover, so a cover set on a snowy day is used
    as SWAT+'s own is there, not at all: the run is stock's, and the read gives the set cover."""
    stock = stock_forest("cov-snow-stock")
    need(EXCHANGE)
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]
    snowy = [d for d in range(len(st["sno_init"]))
             if st["sno_init"][d] > 5 and st["sno_final"][d] > 5][:5]
    assert len(snowy) == 5
    run, back = set_run("cov-snow", lambda d, now: 0.0 if d in snowy else None)
    assert all(back[d][COVER] == 0.0 for d in snowy if not np.isnan(back[d][COVER]))
    assert differences(stock, run) == []


def test_the_water_balance_closes_with_a_cover_of_leaves_and_residue():
    """A study's rule, every day: the cover is the community's leaves and the residue, not its
    wood. The soil evaporates more and the HRU's ET rises; no water comes from anywhere: day by
    day the printed balance's residual is stock's, and over the run what more leaves as ET is
    what less leaves otherwise or is stored."""
    stock = stock_forest("cov-rule-stock")
    need(EXCHANGE)
    run, back = set_run("cov-rule", lambda d, now: now[LEAF] + now[RSD])
    wb = daily_table(run / "hru_wb_day.txt")["hru0001"]
    st = daily_table(stock / "hru_wb_day.txt")["hru0001"]
    assert wb["esoil"].sum() > st["esoil"].sum() + 50
    assert wb["et"].sum() > st["et"].sum() + 20
    np.testing.assert_array_equal(wb["precip"], st["precip"])
    resid = lambda w: (w["precip"] + w["irr"] - w["et"] - w["surq_cont"] - w["latq"] - w["perc"]
                       - w["qtile"] - (w["sw_final"] - w["sw_init"]) - (w["sno_final"] - w["sno_init"]))
    np.testing.assert_allclose(resid(wb), resid(st), atol=0.02)
    out = lambda w: sum(w[c].sum() for c in ("et", "surq_gen", "latq", "perc", "qtile"))
    store = lambda w: w["sw_final"][-1] + w["sno_final"][-1]
    assert out(wb) - out(st) + store(wb) - store(st) == pytest.approx(0.0, abs=0.5)


def test_refusals_keep_nothing_and_a_set_lasts_one_day():
    need(EXCHANGE)
    m = started("cov-refuse", 150)
    with pytest.raises(ExchangeError, match="a soil cover can't be negative"):
        m.set_value(COVER, np.array([-0.1]))
    with pytest.raises(ValueError, match="finite"):
        m.set_value(COVER, np.array([np.nan]))
    with pytest.raises(IndexError):
        m.set_value_at_indices(COVER, np.array([1]), np.array([0.1]))
    m.update()
    assert value(m, COVER) > 4.0                        # nothing refused was kept
    m.set_value(COVER, np.array([0.2]))
    m.update()
    assert value(m, COVER) == 0.2
    m.update()
    assert value(m, COVER) > 4.0                        # SWAT+'s own again
    m.finalize()


def test_a_cover_every_day_stays_in_bounds():
    """A cover set every day of the run, on the build with array bounds checked."""
    need(CHECK)
    os.environ["SWATPLUS_EXCHANGE"] = str(CHECK)
    try:
        run, back = set_run("cov-bounds", lambda d, now: 0.3)
    finally:
        os.environ.pop("SWATPLUS_EXCHANGE", None)
    assert (run / "success.fin").exists()
    assert all(b[COVER] == 0.3 or np.isnan(b[COVER]) for b in back.values())


def test_set_before_a_save_or_branch_goes_in_after():
    """A cover set for the next day, then a save and a branch: the loaded run, the branch and the
    original all use it, and all three finish as the same run."""
    need(EXCHANGE)
    m = started("cov-save", 520)
    m.set_value(COVER, np.array([0.15]))
    snap = m.save_state(fresh("cov-snap"))
    b = m.branch(fresh("cov-branch"))
    k = SwatPlusBmi()
    k.initialize(str(project("Osu_1hru", "cov-loaded", daily=True, years=YEARS, edit=forest)))
    k.load_state(snap)
    for x in (m, b, k):
        x.update()
        assert value(x, COVER) == 0.15
    runs = [finish(x) for x in (m, b, k)]
    assert differences(runs[0], runs[1]) == []
    assert differences(runs[0], runs[2]) == []
