"""Chemistry that travels with the water (0.5.0).

For each salt ion SWAT+'s salt module simulates, and for nitrate (no3, as N)
and soluble phosphorus (solp, as P): a channel's outflow concentration and
its groundwater exchange mass, an aquifer's recharge mass, and a point
source's load. A mass set with a groundwater gain enters where SWAT+ takes
that constituent in; a loss takes the outflow's own concentration. Test
projects: Osu_1hru with its daily point source (test_setting), with nitrate
and phosphorus in it, and the same with the salt module on (salts.py).
"""

import numpy as np
import pytest

from conftest import CHECK, EXCHANGE, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_reading import daily_table
from test_setting import add_point_source

GW = "channel_water__groundwater_volume_exchange_rate"
PS = "point_source_water__volume_flow_rate"
NUTRIENTS = ("no3", "solp")
NO3_KG_DAY, SOLP_KG_DAY = 1.0, 0.1                      # the point source's steady loads


def conc(sp):
    return f"channel_water_{sp}__mass_concentration"


def exch(sp):
    return f"channel_water_{sp}__groundwater_mass_exchange_rate"


def rchg(sp):
    return f"aquifer_water_{sp}__recharge_mass_rate"


def load(sp):
    return f"point_source_water_{sp}__mass_flow_rate"


def with_nutrients(run):
    """Osu_1hru's daily point source, carrying steady nitrate and phosphorus."""
    add_point_source(run)
    rec = run / "pnt01.rec"
    lines = rec.read_text().splitlines()
    head = lines[2].split()
    i3, ip = head.index("no3"), head.index("solp")
    for k in range(3, len(lines)):
        f = lines[k].split()
        f[i3], f[ip] = str(NO3_KG_DAY), str(SOLP_KG_DAY)
        lines[k] = " ".join(f)
    rec.write_text("\n".join(lines) + "\n")


def osu(dest, edit=with_nutrients):
    return project("Osu_1hru", dest, daily=True, edit=edit)


def one(m, name):
    return m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name))))


def step(run, rule=None, read=()):
    """Step a run a day at a time; ``rule(m, day)`` may set values before each day;
    the variables in ``read`` are read after each day."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    got = {v: [] for v in read}
    while m.get_current_time() < m.get_end_time():
        if rule:
            rule(m, int(m.get_current_time()))
        m.update()
        for v in read:
            got[v].append(one(m, v).copy())
    m.finalize()
    return {v: np.array(x) for v, x in got.items()}


def test_the_species_are_in_the_catalog():
    """Without the salt module, nitrate and phosphorus only: four variables each."""
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu("chem-catalog")))
    for sp in NUTRIENTS:
        for name, grid, units, settable in ((conc(sp), "cha", "kg m-3", False),
                                            (exch(sp), "cha", "kg s-1", True),
                                            (rchg(sp), "aqu", "kg s-1", False),
                                            (load(sp), "rec", "kg s-1", True)):
            assert m.get_var_units(name) == units
            assert m.get_grid_size(m.get_var_grid(name)) == m.get_grid_size(
                m.get_var_grid({"cha": GW, "aqu": "aquifer_water__volume", "rec": PS}[grid]))
            assert (name in m.get_input_var_names()) == settable
            assert name in m.get_output_var_names()
    chem = [v for v in m.get_output_var_names() if "__mass" in v or "_mass_" in v]
    assert len(chem) == 8
    m.finalize()


def test_nutrient_reads_are_swats():
    """Each day's concentration, recharge mass and load, against SWAT+'s printed outputs."""
    need(EXCHANGE)
    run = osu("chem-read")
    names = [f(sp) for sp in NUTRIENTS for f in (conc, rchg, load)]
    got = step(run, read=names)
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    aqu = daily_table(run / "aquifer_day.txt")
    (aname, a), = aqu.items()
    rec = daily_table(run / "recall_day.txt")["inflow"]
    m = SwatPlusBmi()
    m.initialize(str(run))
    area = m._x.catalog.grids["aqu"].area_ha[0]
    m.finalize()
    out_m3 = cha["flo_out"] * 86400
    for sp in NUTRIENTS:
        c = got[conc(sp)][:, 0]
        want = np.divide(cha[f"{sp}_out"], out_m3, out=np.zeros_like(out_m3), where=out_m3 > 0)
        np.testing.assert_allclose(c, want, rtol=2e-3, atol=1e-9)
        np.testing.assert_allclose(got[load(sp)][:, 0] * 86400, rec[sp], rtol=1e-5, atol=1e-9)
    assert (got[conc("no3")] > 0).any() and (got[conc("solp")] > 0).any()
    np.testing.assert_allclose(got[load("no3")] * 86400, NO3_KG_DAY, rtol=1e-6)
    # the aquifer's nitrate recharge, printed in kg/ha to three decimals
    np.testing.assert_allclose(got[rchg("no3")][:, 0] * 86400 / area, a["no3_rchg"], rtol=0, atol=5.01e-4)
    assert got[rchg("no3")].max() > 0


def test_nothing_set_is_stock_with_the_chemistry_read():
    need(STOCK, EXCHANGE)
    stock = osu("chem-stock")
    run_stock(stock)
    stepped = osu("chem-stepped")
    names = [f(sp) for sp in NUTRIENTS for f in (conc, exch, rchg, load)]
    step(stepped, read=names)
    assert differences(stock, stepped) == []


def test_loads_set_back_are_stock():
    """Each day's load read in one run and set before that day in another: stock output."""
    need(STOCK, EXCHANGE)
    stock = osu("chem-load-stock")
    run_stock(stock)
    first = step(osu("chem-load-first"), read=[load(sp) for sp in NUTRIENTS])

    def rule(m, d):
        for sp in NUTRIENTS:
            m.set_value(load(sp), first[load(sp)][d])

    again = osu("chem-load-again")
    step(again, rule)
    assert differences(stock, again) == []


def test_a_set_load_arrives_that_day_only():
    need(EXCHANGE)
    base = osu("chem-load-base")
    step(base)

    def rule(m, d):
        if d == 300:
            m.set_value(load("no3"), np.array([5.0 / 86400]))     # 5 kg/day

    setrun = osu("chem-load-set")
    step(setrun, rule)
    b = daily_table(base / "recall_day.txt")["inflow"]
    s = daily_table(setrun / "recall_day.txt")["inflow"]
    assert list(np.flatnonzero(b["no3"] != s["no3"])) == [300]
    np.testing.assert_allclose(s["no3"][300], 5.0, rtol=1e-6)
    assert (s["solp"] == b["solp"]).all() and (s["flo"] == b["flo"]).all()
    bi = daily_table(base / "channel_sd_day.txt")["cha01"]["no3_in"]
    si = daily_table(setrun / "channel_sd_day.txt")["cha01"]["no3_in"]
    np.testing.assert_allclose(si[300] - bi[300], 5.0 - NO3_KG_DAY, rtol=2e-3)
    assert (si[:300] == bi[:300]).all()


def test_a_gain_carries_its_mass_into_the_channel():
    """A gain with nitrate and phosphorus: the channel's inflow rises by exactly that mass,
    and the exchange read back is the mass set."""
    need(EXCHANGE)
    gain, mass = 0.0005, {"no3": 2e-5, "solp": 2e-6}         # m3/s; kg/s

    def rule(m, d):
        m.set_value(GW, np.array([gain]))
        for sp, v in mass.items():
            m.set_value(exch(sp), np.array([v]))

    base = osu("chem-gain-base")
    step(base)
    run = osu("chem-gain")
    got = step(run, rule, read=[exch(sp) for sp in NUTRIENTS])
    b = daily_table(base / "channel_sd_day.txt")["cha01"]
    s = daily_table(run / "channel_sd_day.txt")["cha01"]
    for sp, v in mass.items():
        np.testing.assert_allclose(got[exch(sp)][:, 0], v, rtol=1e-6)
        np.testing.assert_allclose(s[f"{sp}_in"] - b[f"{sp}_in"], v * 86400, rtol=2e-3,
                                   atol=2e-3 * b[f"{sp}_in"].max())


def test_a_loss_takes_nutrients_at_the_outflows_concentration():
    """The share of the day's nitrate and phosphorus a loss takes is the share of the water."""
    need(EXCHANGE)
    loss = 0.0008                                           # m3/s, all of the flow on some days

    def rule(m, d):
        m.set_value(GW, np.array([-loss]))

    run = osu("chem-loss")
    got = step(run, rule, read=[GW] + [exch(sp) for sp in NUTRIENTS])
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    w_taken = -got[GW][:, 0] * 86400
    w_share = w_taken / (w_taken + cha["flo_out"] * 86400)
    some = (w_taken > 0) & (cha["flo_out"] > 0)
    assert some.sum() > 100
    for sp in NUTRIENTS:
        m_taken = -got[exch(sp)][:, 0] * 86400
        assert (m_taken >= 0).all()
        ok = some & (m_taken + cha[f"{sp}_out"] > 1e-6)
        np.testing.assert_allclose(m_taken[ok] / (m_taken[ok] + cha[f"{sp}_out"][ok]), w_share[ok],
                                   rtol=5e-3, atol=1e-4)


def test_refusals_keep_nothing():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu("chem-refuse")))
    m.update_until(100)
    with pytest.raises(ExchangeError, match="set channel cha01's groundwater gain for the day first"):
        m.set_value(exch("no3"), np.array([1e-5]))
    m.set_value(GW, np.array([-0.0005]))
    with pytest.raises(ExchangeError, match="gain for the day first"):
        m.set_value(exch("no3"), np.array([1e-5]))          # a loss can't carry a mass
    m.set_value(GW, np.array([0.0005]))
    with pytest.raises(ExchangeError, match="a mass can't be negative"):
        m.set_value(exch("no3"), np.array([-1e-5]))
    m.set_value(exch("no3"), np.array([1e-5]))
    with pytest.raises(ExchangeError, match="a mass is set with channel cha01's gain"):
        m.set_value(GW, np.array([-0.0005]))
    with pytest.raises(ValueError, match="can't be set"):
        m.set_value(conc("no3"), np.array([1e-3]))
    with pytest.raises(ValueError, match="can't be set"):
        m.set_value(rchg("no3"), np.array([1e-3]))
    with pytest.raises(ExchangeError, match="not a settable variable"):
        m._x.set(conc("no3"), 0, 1e-3)                    # SWAT+ refuses it too
    # a diversion takes the channel's concentration: no load with it, either way round
    m.set_value(PS, np.array([-0.0002]))
    with pytest.raises(ExchangeError, match="is a diversion on that day"):
        m.set_value(load("no3"), np.array([1e-5]))
    m.set_value(PS, np.array([0.001]))
    m.set_value(load("no3"), np.array([1e-5]))
    with pytest.raises(ExchangeError, match="a load is set for point source"):
        m.set_value(PS, np.array([-0.0002]))
    m.update()
    np.testing.assert_allclose(one(m, exch("no3"))[0], 1e-5, rtol=1e-6)   # what was kept arrived
    np.testing.assert_allclose(one(m, load("no3"))[0], 1e-5, rtol=1e-6)
    m.finalize()


def test_set_masses_and_loads_survive_save_and_load():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu("chem-save-a")))
    m.update_until(400)
    m.set_value(GW, np.array([0.0005]))
    m.set_value(exch("solp"), np.array([3e-6]))
    m.set_value(load("no3"), np.array([4e-5]))
    snap = m.save_state(fresh("chem-save-snap"))
    m.finalize()
    k = SwatPlusBmi()
    k.initialize(str(osu("chem-save-b")))
    k.load_state(snap)
    k.update()
    np.testing.assert_allclose(one(k, exch("solp"))[0], 3e-6, rtol=1e-6)
    np.testing.assert_allclose(one(k, load("no3"))[0], 4e-5, rtol=1e-6)
    k.finalize()


@pytest.mark.filterwarnings("ignore:a negative point source")
def test_every_chemistry_set_stays_in_bounds(monkeypatch):
    """Gains with their masses, losses and loads every day, on the build with array bounds
    checked: a write outside SWAT+'s arrays would stop it with an error."""
    need(CHECK)
    monkeypatch.setenv("SWATPLUS_EXCHANGE", str(CHECK))

    def rule(m, d):
        m.set_value(GW, np.array([0.0004 if d % 2 else -0.0004]))
        for sp in NUTRIENTS:
            if d % 2:
                m.set_value(exch(sp), np.array([1e-6]))
            m.set_value(load(sp), np.array([2e-6]))

    run = osu("chem-bounds")
    step(run, rule)
    assert (run / "success.fin").exists()


# -- salt ions: the part of SWAT+ 62.0.1's salt module stock can run --------------------
# (salts.py: salts and a placeholder pesticide, no aquifer and no point source; the full
# module stops in stock 62.0.1, see test_the_full_salt_module_stops_in_stock_swatplus_62_0_1)

def osu_salts(dest):
    from salts import with_salts_channel
    return project("Osu_1hru", dest, daily=True, edit=with_salts_channel)


def salt_table(path):
    """channel_salt_day.txt (or the like) as {column: array}, for its one object."""
    lines = path.read_text().splitlines()
    at = next(i for i, l in enumerate(lines) if l.split()[:1] == ["jday"])
    head = lines[at].split()
    rows = [l.split() for l in lines[at + 1:] if len(l.split()) == len(head)]
    return {h: np.array([float(r[k]) for r in rows]) for k, h in enumerate(head)}


def salt_balance(t, ion):
    """Each day's residual: storage change - (in - out - seep), kg; day 1 left out, since
    the starting storage isn't printed."""
    return np.diff(t[f"{ion}_mass"]) - (t[f"{ion}_in"] - t[f"{ion}_out"] - t[f"{ion}_seep"])[1:]


def test_the_salt_ions_are_in_the_catalog():
    from salts import SALTS
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu_salts("salt-catalog")))
    names = m.get_output_var_names()
    sp = [n[len("channel_water_"):-len("__mass_concentration")] for n in names
          if n.endswith("__mass_concentration")]
    assert sp == list(SALTS) + list(NUTRIENTS)               # the salt module's order
    assert exch("cl") in m.get_input_var_names() and load("cl") not in names   # no point sources
    m.finalize()


def test_salt_concentrations_read_are_swats():
    from salts import SALTS
    need(EXCHANGE)
    run = osu_salts("salt-read")
    got = step(run, read=[conc(s) for s in SALTS])
    t = salt_table(run / "channel_salt_day.txt")
    for s in SALTS:
        np.testing.assert_allclose(got[conc(s)][:, 0], t[f"{s}_conc"] / 1000, rtol=1e-6, atol=1e-12)
    assert (got[conc("cl")] > 0).any()


def test_nothing_set_is_stock_with_salts():
    """Stepped a day at a time, reading every variable every day: stock output."""
    need(STOCK, EXCHANGE)
    stock = osu_salts("salt-stock")
    run_stock(stock)
    stepped = osu_salts("salt-stepped")
    m = SwatPlusBmi()
    m.initialize(str(stepped))
    while m.get_current_time() < m.get_end_time():
        m.update()
        for v in m.get_output_var_names():
            one(m, v)
    m.finalize()
    assert differences(stock, stepped) == []


def test_a_gain_carries_its_salt_into_the_channel():
    """A gain with salt: the channel's printed salt inflow rises by exactly that mass, the
    exchange read back is the mass set, and the channel's salt balance still closes."""
    from salts import SALTS
    need(EXCHANGE)
    gain = 0.0005                                           # m3/s
    mass = {s: (k + 1) * 1e-6 for k, s in enumerate(SALTS)}  # kg/s

    def rule(m, d):
        m.set_value(GW, np.array([gain]))
        for s, v in mass.items():
            m.set_value(exch(s), np.array([v]))

    base = osu_salts("salt-gain-base")
    step(base)
    run = osu_salts("salt-gain")
    got = step(run, rule, read=[exch(s) for s in SALTS])
    b = salt_table(base / "channel_salt_day.txt")
    t = salt_table(run / "channel_salt_day.txt")
    for s, v in mass.items():
        np.testing.assert_allclose(got[exch(s)][:, 0], v, rtol=1e-6)
        np.testing.assert_allclose(t[f"{s}_in"] - b[f"{s}_in"], v * 86400, rtol=1e-5)
        res = salt_balance(t, s)
        assert (np.abs(res) <= 1e-6 * (t[f"{s}_in"] + t[f"{s}_out"] + t[f"{s}_mass"])[1:] + 1e-4).all()


def test_a_loss_takes_salt_at_the_outflows_concentration():
    """The share of the day's salt a loss takes is the share of the water; it's printed as
    seepage, and the channel's salt balance closes every day."""
    from salts import SALTS
    need(EXCHANGE)
    loss = 0.0008                                           # m3/s, all of the flow on some days

    def rule(m, d):
        m.set_value(GW, np.array([-loss]))

    run = osu_salts("salt-loss")
    got = step(run, rule, read=[GW] + [exch(s) for s in SALTS])
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    t = salt_table(run / "channel_salt_day.txt")
    w_taken = -got[GW][:, 0] * 86400
    w_share = w_taken / (w_taken + cha["flo_out"] * 86400)
    for s in SALTS:
        m_taken = -got[exch(s)][:, 0] * 86400
        assert (m_taken >= 0).all()
        ok = (w_taken > 0) & (m_taken + t[f"{s}_out"] > 1e-6)
        assert ok.sum() > 100
        np.testing.assert_allclose(m_taken[ok] / (m_taken[ok] + t[f"{s}_out"][ok]), w_share[ok],
                                   rtol=5e-3, atol=1e-4)
        np.testing.assert_allclose(t[f"{s}_seep"], m_taken, rtol=1e-5, atol=1e-6)
        res = salt_balance(t, s)
        assert (np.abs(res) <= 1e-6 * (t[f"{s}_in"] + t[f"{s}_out"] + t[f"{s}_seep"]
                                       + t[f"{s}_mass"])[1:] + 1e-4).all()
    # without the fix (0.4.0), the salt stayed behind: the loss's salt is real mass
    assert (-got[exch("cl")][:, 0] * 86400).sum() > 1.0


def test_a_salt_load_is_refused_where_swatplus_reads_none():
    """SWAT+ 62.0.1 never reads salt_recall.rec, so a salt load on a point source would not
    be used. (The run can't go on: with salts on, a point source stops stock 62.0.1 on its
    first day, so the test only sets before the first day.)"""
    need(EXCHANGE)

    def both(run):
        from salts import with_salts_channel
        with_salts_channel(run)
        with_nutrients(run)

    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "salt-load-refuse", daily=True, edit=both)))
    with pytest.raises(ExchangeError, match="never calls recall_read_salt"):
        m.set_value(load("cl"), np.array([1e-4]))
    m.set_value(load("no3"), np.array([1e-5]))             # nitrate is read from the recall file
    m.finalize()


def test_the_full_salt_module_stops_in_stock_swatplus_62_0_1():
    """Documents SWAT+ 62.0.1: with salts on, a project with an aquifer and a point source
    (salts.with_salts) stops before it finishes; without pesticides it fails in
    salt_hru_init (docs/upstream-issue-salt-module.md). This test fails when SWAT+ fixes it,
    and then the salt variables can be tested on aquifers and point sources too."""
    import subprocess
    from salts import with_salts
    need(STOCK)
    run = project("Osu_1hru", "salt-full-stock", daily=True, edit=with_salts)
    with pytest.raises(subprocess.CalledProcessError):
        run_stock(run)
    assert not (run / "success.fin").exists()
