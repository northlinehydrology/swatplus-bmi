"""HRU irrigation (docs/design.md, "HRU irrigation"): read-only, one value per HRU on the
hru grid, the day just done.

``land_irrigation_water__volume_flow_rate`` is the water the day's irrigation put on each HRU,
from any source (a decision table's irrigate or irr_demand action, a scheduled irrm, water
allocation): what reached the soil and what ran off, as SWAT+'s water balance takes them in.
SWAT+ zeroes it at the start of the next day, before the pause, so the wrapper keeps it at the
end of the day. What an application loses (irr.ops' efficiency) never enters SWAT+'s balance
and isn't in it. A coupled model that pumps irrigation from groundwater wells pumps this.

Osu_1hru irrigates its rice paddy from an outside source (``pad_irr_r``, ponding) on about
700 of its days; the read is its irr column in hru_wb_day.txt times the HRU's area.
"""

import numpy as np
import pytest

from conftest import EXCHANGE, fresh, need, project
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

IRR = "land_irrigation_water__volume_flow_rate"


def test_irrigation_read_is_swats():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-irrigation", daily=True)))
    assert m.get_var_grid(IRR) == m.get_var_grid("soil_water__percolation_rate")
    assert m.get_var_units(IRR) == "m3 s-1"
    assert IRR in m.get_output_var_names() and IRR not in m.get_input_var_names()
    (h,) = m.get_grid_object_names(m.get_var_grid(IRR))
    area = 10.0 * 10000.0                                        # Osu_1hru's one HRU, 10 ha, in m2
    got = []
    while m.get_current_time() < m.get_end_time():
        m.update()
        got.append(m.get_value(IRR, np.empty(1))[0] * 86400.0)   # m3 on the day
    run = m.run_dir
    m.finalize()
    wb = daily_table(run / "hru_wb_day.txt")[h]["irr"]           # mm
    got = np.array(got)
    assert (got > 0).sum() > 500
    np.testing.assert_allclose(got / area * 1000.0, wb, rtol=5e-4, atol=5e-4)


# What SWAT+'s irrigate actions took from its channels and reservoirs (docs/design.md, "HRU
# irrigation"): read-only, the day just done, added up where actions.f90 takes the water
# (exch_irr_take), by source and by HRU. SWAT+ gives all or nothing: an action whose source holds
# no more than its demand takes nothing that day.
#
# Osu_1hru's paddy table (pad_irr_r) is switched to irrigate from cha01: 1 mm (100 m3 on the 10 ha
# HRU) with irr.ops' sprinkler_med (eff 0.85, no runoff), as a fixed amount, not ponding, so each
# action asks the same. Osu_1hru's channel is 3.4 km long and seeps 5 mm/hr through a 27 m bed,
# and its storage coefficient routes all it holds out each day, so it ends every day empty and
# any action from it would find nothing. Its seepage is turned off and the storage coefficient
# (scoef, parameters.bsn) halved, so it holds some water some days.
#
# In SWAT+ 62.0.1 the water an irrigate action takes from a channel never leaves the channel
# (test_channel_irrigation_takes_no_water_from_the_channel_swatplus_62_0_1); from a reservoir it does.

from datetime import date

from conftest import STOCK, differences, run_stock
from reservoir import with_reservoir

WCHA = "channel_water__irrigation_withdrawal_volume_flow_rate"
WRES = "reservoir_water__irrigation_withdrawal_volume_flow_rate"
WHRU = "land_irrigation_water__withdrawal_volume_flow_rate"
VOL = "channel_water__volume"
QOUT = "channel_water__volume_outflow_rate"
AMOUNT = 1.0                                  # mm per action
DEMAND = AMOUNT * 10.0 * 10.0                 # m3: mm x ha x 10
EFF = 0.85                                    # sprinkler_med


def from_source(amount=AMOUNT, src="cha 1"):
    """An edit: pad_irr_r irrigates ``amount`` mm from ``src``; cha01 keeps water from day to day."""
    def edit(run):
        p = run / "parameters.bsn"
        lines = p.read_text().splitlines()
        f = lines[2].split()
        f[21] = "0.50000"                                       # scoef
        lines[2] = "  ".join(f)
        p.write_text("\n".join(lines) + "\n")
        p = run / "hyd-sed-lte.cha"
        lines = p.read_text().splitlines()
        for i, line in enumerate(lines):
            f = line.split()
            if f[:1] == ["hydcha01"]:
                f[7] = "0.00000"                                # k, the bed's seepage (mm/hr)
                lines[i] = "  ".join(f)
        p.write_text("\n".join(lines) + "\n")
        p = run / "lum.dtl"
        lines = p.read_text().splitlines()
        i = next(k for k, line in enumerate(lines) if line.split()[:1] == ["pad_irr_r"])
        j = next(k for k in range(i, i + 10) if lines[k].split()[:2] == ["irrigate", "null"])
        lines[j] = f"irrigate  {src}  irr_src  sprinkler_med  {amount}  50.  null  y  n"
        p.write_text("\n".join(lines) + "\n")
    return edit


def actions_taken(run, start):
    """Days (from the start) of the HRU's irrigate actions in mgt_out.txt, and what each applied (mm)."""
    days = {}
    for line in (run / "mgt_out.txt").read_text().splitlines():
        f = line.split()
        if len(f) > 6 and f[5] == "IRRIGATE":
            d = (date(int(f[1]), int(f[2]), int(f[3])) - start).days
            days[d] = days.get(d, 0.0) + float(f[-1])
    return days


def step_irrigation(run, extra=()):
    """Step the whole run, reading the withdrawals, the irrigation and the channel each day; the
    channel's volume is also read before each day (what the day's actions find)."""
    m = SwatPlusBmi()
    m.initialize(str(run))
    start = date.fromisoformat(m.get_time_units().split()[2])
    names = [WCHA, WHRU, IRR, QOUT, VOL, *extra]
    out = {n: [] for n in names + ["before"]}
    while m.get_current_time() < m.get_end_time():
        out["before"].append(m.get_value(VOL, np.empty(1))[0])
        m.update()
        for n in names:
            out[n].append(m.get_value(n, np.empty(m.get_grid_size(m.get_var_grid(n)))).copy())
    run_dir = m.run_dir
    m.finalize()
    got = {n: np.array(v) for n, v in out.items()}
    for n in names:
        if got[n].ndim == 2 and got[n].shape[1] == 1:
            got[n] = got[n][:, 0]
    return got, run_dir, start


@pytest.fixture(scope="module")
def irrigated():
    need(EXCHANGE)
    run = project("Osu_1hru", "Osu_1hru-irr-cha", daily=True, edit=from_source())
    return step_irrigation(run)


def test_withdrawal_reads_are_in_the_catalog():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-irr-catalog", edit=with_reservoir)))
    for name, like in ((WCHA, QOUT), (WRES, "reservoir_water__volume"), (WHRU, IRR)):
        assert m.get_var_grid(name) == m.get_var_grid(like)
        assert m.get_var_units(name) == "m3 s-1"
        assert name in m.get_output_var_names() and name not in m.get_input_var_names()
    m.finalize()


def test_the_channel_gives_what_the_hru_takes(irrigated):
    got, _, _ = irrigated
    assert np.array_equal(got[WCHA], got[WHRU])
    assert (got[WHRU] > 0).sum() > 30


def test_an_action_takes_its_demand_or_nothing(irrigated):
    """On a day an action runs, the HRU takes its 100 m3 if cha01 holds more than that, and
    nothing otherwise (SWAT+'s all or nothing); on other days it takes nothing."""
    got, run, start = irrigated
    acts = actions_taken(run, start)
    days = np.array(sorted(acts))
    w = got[WHRU] * 86400.0                                     # m3 on the day
    enough = got["before"][days] > DEMAND
    clear = np.abs(got["before"][days] - DEMAND) > 1e-3          # leave out a tie at float precision
    full, short = days[enough & clear], days[~enough & clear]
    assert len(full) > 30 and len(short) > 300
    np.testing.assert_allclose(w[full], DEMAND, rtol=1e-5)
    # a short day reads 0, and the action applied nothing
    assert (w[short] == 0).all()
    assert all(acts[d] == 0 for d in short)
    others = np.setdiff1d(np.arange(len(w)), days)
    assert (w[others] == 0).all()


def test_the_hru_takes_what_reaches_it_and_its_loss(irrigated):
    """The withdrawal is at least what reached the soil and ran off; the difference is the
    application's loss, (1 - eff) of it with sprinkler_med."""
    got, _, _ = irrigated
    w, irr = got[WHRU], got[IRR]
    assert (irr <= w * (1 + 1e-6) + 1e-12).all()
    on = w > 0
    np.testing.assert_allclose(irr[on], EFF * w[on], rtol=1e-5)
    np.testing.assert_allclose((w - irr)[on], (1 - EFF) * w[on], rtol=1e-4)


def test_channel_irrigation_takes_no_water_from_the_channel_swatplus_62_0_1(irrigated):
    """Documents a SWAT+ 62.0.1 bug, so the test fails when it's fixed.

    actions.f90's irrigate takes the water from ch_stor, but the channel routes from tot_stor
    (ch_rtmusk.f90), which it carries from the day before and which the action doesn't touch, and
    then sets ch_stor from it again. So the channel never loses what its irrigate actions take,
    and the field gets water from nowhere. Against the same project with nothing irrigated, cha01's
    outflow and storage are the same to the bit until the field's own return reaches it, and the
    channel's printed daily balance closes with no room for the withdrawal.
    docs/upstream-issue-irrigate-channel.md."""
    got, run, _ = irrigated
    base, base_run, _ = step_irrigation(project("Osu_1hru", "Osu_1hru-irr-none", daily=True,
                                                edit=from_source(0.0)))
    assert (base[WHRU] == 0).all()
    cha = daily_table(run / "channel_sd_day.txt")["cha01"]
    returned = int(np.flatnonzero(cha["flo_in"] != daily_table(base_run / "channel_sd_day.txt")["cha01"]["flo_in"])[0])
    w = got[WCHA] * 86400.0                                     # m3 on the day
    assert w[:returned].sum() >= DEMAND                         # taken before the field's return
    assert np.array_equal(got[QOUT][:returned], base[QOUT][:returned])
    assert np.array_equal(got[VOL][:returned], base[VOL][:returned])

    q_in, q_out = cha["flo_in"] * 86400, cha["flo_out"] * 86400
    resid = q_in + cha["precip"] - cha["evap"] - cha["seep"] - q_out - np.diff(np.r_[0.0, cha["flo_stor"]])
    on = w > 0
    assert (np.abs(resid[on]) < 0.05 * w[on]).all()


def test_withdrawals_change_nothing_swat_does():
    """The irrigated project stepped, reading every withdrawal every day, writes stock SWAT+'s
    output, byte for byte."""
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "Osu_1hru-irr-stock", daily=True, edit=from_source())
    run_stock(stock)
    got, stepped, _ = step_irrigation(project("Osu_1hru", "Osu_1hru-irr-stepped", daily=True,
                                              edit=from_source()))
    assert (got[WHRU] > 0).sum() > 30
    assert differences(stock, stepped) == []


def test_a_reservoir_gives_what_the_hru_takes():
    """pad_irr_r irrigating from res01 (tests/reservoir.py, below cha01): the reservoir's read is
    the HRU's, each action takes 100 m3 or nothing, and the reservoir loses it."""
    need(EXCHANGE)

    def edit(run):
        with_reservoir(run)
        from_source(src="res 1")(run)

    RVOL = "reservoir_water__volume"
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-irr-res", edit=edit)))
    flows = ["reservoir_water__volume_inflow_rate", "reservoir_water__volume_outflow_rate",
             "reservoir_water__precipitation_volume_flow_rate", "reservoir_water__evaporation_volume_flow_rate",
             "reservoir_water__seepage_volume_flow_rate"]
    rows = []
    while m.get_current_time() < m.get_end_time():
        v0 = m.get_value(RVOL, np.empty(1))[0]
        m.update()
        rows.append([v0, *(m.get_value(n, np.empty(1))[0] for n in [WRES, WHRU, WCHA, RVOL, *flows])])
    m.finalize()
    v0, wres, whru, wcha, v1, q_in, q_out, pcp, evap, seep = np.array(rows).T
    assert np.array_equal(wres, whru)
    assert (wcha == 0).all()
    on = whru > 0
    assert on.sum() > 30
    np.testing.assert_allclose(whru[on] * 86400, DEMAND, rtol=1e-5)
    # the reservoir's day, the withdrawal included, closes to float32 (m3)
    resid = v0 - whru * 86400 + (q_in - q_out + pcp - evap - seep) * 86400 - v1
    assert (np.abs(resid) <= 1e-5 * np.maximum(v0, v1) + 1e-2).all()


def test_a_saved_state_carries_the_withdrawals():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-irr-save-a", edit=from_source())))
    while True:
        m.update()
        w = m.get_value(WHRU, np.empty(1))[0]
        if w > 0:
            break
    want = {n: m.get_value(n, np.empty(1)).copy() for n in (WCHA, WHRU)}
    snap = m.save_state(fresh("Osu_1hru-irr-save-snap"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-irr-save-b", edit=from_source())))
    n.load_state(snap)
    for name, v in want.items():
        assert np.array_equal(n.get_value(name, np.empty(1)), v), name
    n.finalize()
