"""The opt-in fix for SWAT+ 62.0.1's daily Muskingum routing (routing_fix = "muskingum_daily",
patches/62.0.1/0004).

On a daily step with rte_cha 1, ch_rtmusk.f90 resets the stability substeps sd_hydsed_init.f90
computed, so each day is one Muskingum step with a substep's coefficients and every reach delays
about 0.63 day whatever its K. The fix keeps the substeps, takes the day's inflow as ht1 (tsin
counts lateral and tile flow twice on a daily step), routes no inflow on a substep with none, and
gives the rating curve the substep's inflow rate.

1. Off (the default) is stock, to the byte, with rte_cha 1: stepped, saved and loaded, branched.
2. On with rte_cha 0, or on an hourly step, is stock: the fix touches daily Muskingum only.
3. On with rte_cha 1: the channel's water balance closes, lateral flow comes in once, and the
   reach's delay is the Muskingum K's, doubling with Manning's n, where stock's is the same
   0.6 day for both.
4. A saved state and a branch keep the setting; a state saved without it (0.17) routes as stock.

Osu_1hru, whose one channel (cha01, 3.4 km) takes the HRU's water, run for 2010-2012.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from conftest import EXCHANGE, RUNS, STOCK, differences, fresh, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

YEARS = (2010, 2012)


def codes(rte: int, step: int = 0, mann: float | None = None):
    """An edit: rte_cha in codes.bsn, the time step in time.sim, and cha01's Manning's n."""
    def edit(run: Path) -> None:
        lines = (run / "codes.bsn").read_text().splitlines()
        head, vals = lines[1].split(), lines[2].split()
        vals[head.index("rte_cha")] = str(rte)
        lines[2] = "  ".join(vals)
        (run / "codes.bsn").write_text("\n".join(lines) + "\n")
        lines = (run / "time.sim").read_text().splitlines()
        f = lines[2].split()
        f[4] = str(step)
        lines[2] = "  ".join(f)
        (run / "time.sim").write_text("\n".join(lines) + "\n")
        if mann is not None:
            lines = (run / "hyd-sed-lte.cha").read_text().splitlines()
            head = lines[1].split()
            for i in range(2, len(lines)):
                f = lines[i].split()
                if f and f[0] == "hydcha01":
                    f[head.index("mann")] = f"{mann:.5f}"
                    lines[i] = "  ".join(f)
            (run / "hyd-sed-lte.cha").write_text("\n".join(lines) + "\n")
    return edit


def osu(dest: str, edit) -> Path:
    return project("Osu_1hru", dest, daily=True, years=YEARS, edit=edit)


def config(dest: str, edit, fix: str | None = "muskingum_daily") -> str:
    p = osu(dest, edit)
    if fix is None:
        return str(p)
    toml = p.parent / f"{dest}.toml"
    toml.write_text(f"project = '{p}'\nrouting_fix = \"{fix}\"\n")   # literal: D:\\ on Windows
    return str(toml)


def run(cfg: str) -> SwatPlusBmi:
    m = SwatPlusBmi()
    m.initialize(cfg)
    m.update_until(m.get_end_time())
    m.finalize()
    return m


def stock(dest: str, edit) -> Path:
    p = osu(dest, edit)
    run_stock(p)
    return p


def channel(path: Path) -> dict:
    return daily_table(path / "channel_sd_day.txt")["cha01"]


def delay(c: dict) -> float:
    """The reach's mean delay, days: the centroid of its outflow less that of its inflow."""
    t = np.arange(len(c["flo_in"]), dtype=float)
    return float((t * c["flo_out"]).sum() / c["flo_out"].sum() - (t * c["flo_in"]).sum() / c["flo_in"].sum())


def balance(c: dict) -> tuple[float, float]:
    """The channel's water balance over the run, m3: what's left over, and the inflow."""
    vin = (c["flo_in"] * 86400.).sum()
    vout = (c["flo_out"] * 86400.).sum()
    loss = c["evap"].sum() + c["seep"].sum() - c["precip"].sum()
    dstor = c["flo_stor"][-1] - c["flo_stor"][0]
    return float(vin - vout - loss - dstor), float(vin)


# -- 1. off is stock --------------------------------------------------------------------------

def test_off_is_stock_with_muskingum_stepped_saved_and_branched():
    need(STOCK, EXCHANGE)
    s = stock("rf-off-stock", codes(1))
    m = SwatPlusBmi()
    m.initialize(config("rf-off-stepped", codes(1), fix=None))
    for _ in range(400):
        m.update()
        for name in m.get_output_var_names():
            m.get_value(name, np.zeros(m.get_grid_size(m.get_var_grid(name))))
    snap = m.save_state(fresh("rf-off-snap"))
    b = m.branch(fresh("rf-off-branch"))
    m.update_until(m.get_end_time())
    m.finalize()
    b.update_until(b.get_end_time())
    b.finalize()
    n = SwatPlusBmi()
    n.initialize(config("rf-off-loaded", codes(1), fix=None))
    n.load_state(snap)
    n.update_until(n.get_end_time())
    n.finalize()
    for d in ("rf-off-stepped", "rf-off-branch", "rf-off-loaded"):
        assert differences(s, RUNS / d) == [], d


def test_none_is_the_default():
    need(STOCK, EXCHANGE)
    s = stock("rf-none-stock", codes(1))
    run(config("rf-none", codes(1), fix="none"))
    assert differences(s, RUNS / "rf-none") == []


# -- 2. on changes daily Muskingum only ---------------------------------------------------------

@pytest.mark.parametrize("rte,step", [(0, 0), (0, 24), (1, 24)])
def test_on_is_stock_without_daily_muskingum(rte, step):
    need(STOCK, EXCHANGE)
    s = stock(f"rf-{rte}-{step}-stock", codes(rte, step))
    run(config(f"rf-{rte}-{step}-on", codes(rte, step)))
    assert differences(s, RUNS / f"rf-{rte}-{step}-on") == []


# -- 3. on with daily Muskingum ---------------------------------------------------------------

GW = "channel_water__groundwater_volume_exchange_rate"
Q = "channel_water__volume_outflow_rate"
PULSE = 1e6                     # m3, a day's gain into cha01


def response(dest: str, edit, fix: str | None) -> np.ndarray:
    """What a one-day pulse into cha01 adds to its outflow on that day and the seven after, m3:
    two branches from day 180, one with the pulse set as a groundwater gain, one without."""
    m = SwatPlusBmi()
    m.initialize(config(dest, edit, fix))
    m.update_until(180)
    out = []
    for pulse in (0., PULSE):
        b = m.branch(fresh(f"{dest}-{int(pulse > 0)}"))
        if pulse:
            b.set_value(GW, np.array([pulse / 86400.]))
        q = []
        for _ in range(8):
            b.update()
            q.append(b.get_value(Q, np.zeros(1))[0] * 86400.)
        b.finalize()
        out.append(np.array(q))
    m.finalize()
    return out[1] - out[0]


def mean_delay(dq: np.ndarray) -> float:
    """Days after the pulse's day, weighted by what leaves the reach on each."""
    return float((np.arange(len(dq)) * dq).sum() / dq.sum())


@pytest.fixture(scope="module")
def pulses():
    """cha01's response to the pulse, stock and fixed, at Osu's Manning's n (0.05) and twice it."""
    need(EXCHANGE)
    return {(fix, n): response(f"rf-pulse-{fix}-{n}", codes(1, mann=n), fix)
            for fix in (None, "muskingum_daily") for n in (0.05, 0.10)}


def test_on_the_delay_is_the_reachs_own(pulses):
    d = {k: mean_delay(v) for k, v in pulses.items()}
    # stock: one step a day with a substep's coefficients, about 37% today and 63% tomorrow,
    # the same 0.6 day or so whatever n
    for n in (0.05, 0.10):
        assert 0.55 < d[None, n] < 0.8, d
    assert abs(d[None, 0.10] - d[None, 0.05]) < 0.1, d
    # fixed: the reach's K, about an hour here, and twice it with twice the roughness
    assert d["muskingum_daily", 0.05] < 0.1, d
    assert 1.6 < d["muskingum_daily", 0.10] / d["muskingum_daily", 0.05] < 3., d
    # all of the pulse comes out but what the channel seeps
    for k, v in pulses.items():
        assert 0.95 * PULSE < v.sum() <= PULSE, (k, v.sum())


def test_on_the_channel_balance_closes_and_lateral_flow_comes_in_once():
    """With the substeps kept and tsin left as command.f90 builds it, the HRU's lateral and tile
    flow would enter the reach twice: the channel would put out that much more than its inflow,
    its rain and its storage can account for."""
    need(STOCK, EXCHANGE)
    s = channel(stock("rf-wb-stock", codes(1)))
    run(config("rf-wb-on", codes(1)))
    c = channel(RUNS / "rf-wb-on")
    h = daily_table(RUNS / "rf-wb-on" / "hru_wb_day.txt")["hru0001"]
    lateral = float((h["latq"] + h["qtile"]).sum() * 10. * 10.)      # mm on 10 ha -> m3
    assert lateral > 200.
    assert np.allclose(c["flo_in"], s["flo_in"], rtol=1e-3, atol=1e-6)    # the same inflow
    left, vin = balance(c)
    left0, _ = balance(s)
    assert abs(left) < 0.1 * lateral and abs(left) <= abs(left0) + 1e-5 * vin, (left, left0, lateral)
    assert not np.allclose(c["flo_out"], s["flo_out"])                    # and routed otherwise


# -- 4. saved states and branches keep the setting ----------------------------------------------

def test_on_survives_save_load_and_branch():
    """A state saved with the fix on carries it: loaded by a process started without it, the
    run goes on fixed, as the unbroken run does."""
    need(EXCHANGE)
    straight = RUNS / "rf-keep-straight"
    run(config("rf-keep-straight", codes(1)))
    m = SwatPlusBmi()
    m.initialize(config("rf-keep-saved", codes(1)))
    m.update_until(400)
    snap = m.save_state(fresh("rf-keep-snap"))
    b = m.branch(fresh("rf-keep-branch"))
    m.finalize()
    b.update_until(b.get_end_time())
    b.finalize()
    n = SwatPlusBmi()
    n.initialize(config("rf-keep-loaded", codes(1), fix=None))
    n.load_state(snap)
    n.update_until(n.get_end_time())
    n.finalize()
    for d in ("rf-keep-branch", "rf-keep-loaded"):
        assert differences(straight, RUNS / d) == [], d


def test_an_unknown_fix_is_refused():
    with pytest.raises(ValueError, match="routing_fix"):
        SwatPlusBmi().initialize(config("rf-bad", codes(1), fix="all"))
