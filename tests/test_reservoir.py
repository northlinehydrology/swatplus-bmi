"""Reservoirs: their water read each day, and their groundwater exchange set (docs/design.md,
"The groundwater exchange with reservoirs").

What a groundwater model's lake needs from SWAT+'s reservoir each day: its volume and area,
and the day's inflow, outflow, precipitation, evaporation and SWAT+'s own seepage; and a way to
put the lake's exchange with the aquifer into, or take it out of, the reservoir. Test project:
Osu_1hru with a reservoir below its channel (tests/reservoir.py), everything printed daily.

Each day the reservoir's balance closes on the values read: the change in volume is the
inflow, the gain and the precipitation, less the outflow, the evaporation, SWAT+'s seepage and
the loss taken.
"""

import numpy as np

from conftest import EXCHANGE, need, project
from reservoir import reservoir, with_reservoir
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

V = "reservoir_water__volume"
AREA = "reservoir_water_surface__area"
QIN = "reservoir_water__volume_inflow_rate"
QOUT = "reservoir_water__volume_outflow_rate"
PCP = "reservoir_water__precipitation_volume_flow_rate"
EVAP = "reservoir_water__evaporation_volume_flow_rate"
SEEP = "reservoir_water__seepage_volume_flow_rate"
GW = "reservoir_water__groundwater_volume_exchange_rate"
CHA = "channel_water__volume_outflow_rate"
RATES = [QIN, QOUT, PCP, EVAP, SEEP, GW, CHA]


def step(run, rate=None, years=None):
    """Step a run, setting the reservoir's groundwater exchange to ``rate`` (m3/s) every day if
    given; read its water after each day (rates as m3 on the day)."""
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(run))
    out = {v: [] for v in [V, AREA] + RATES}
    days = 0
    while m.get_current_time() < m.get_end_time() and (years is None or days < 365 * years):
        if rate is not None:
            m.set_value(GW, np.array([rate]))
        m.update()
        days += 1
        for v in out:
            x = m.get_value(v, np.empty(1))[0]
            out[v].append(x * 86400.0 if v in RATES else x)
    names = m.get_grid_object_names(m.get_var_grid(V))
    m.finalize()
    return names, {k: np.array(v) for k, v in out.items()}


def balance(r):
    """The day's change in volume, less what the values read say it should be."""
    before = np.concatenate([[0.0], r[V][:-1]])            # the reservoir starts empty
    return (r[V] - before) - (r[QIN] + r[PCP] - r[QOUT] - r[EVAP] - r[SEEP]
                              + np.minimum(r[GW], 0.0))


def close(r, what):
    # SWAT+ keeps 4-byte reals: a day's sum is good to a few units in the last place of the
    # largest term
    scale = np.maximum.reduce([r[V], r[QIN], r[QOUT], np.ones_like(r[V])])
    assert (np.abs(balance(r)) <= 5e-6 * scale).all(), what


def test_a_reservoir_read():
    names, r = step(project("Osu_1hru", "res-read", daily=True, edit=with_reservoir), years=3)
    assert list(names) == ["res01"]
    assert r[V].max() > 1000.0 and r[QOUT].max() > 0 and r[EVAP].max() > 0 and r[PCP].max() > 0
    np.testing.assert_allclose(r[QIN], r[CHA], rtol=1e-6, atol=1e-6)   # the channel flows in
    assert (r[GW] == 0).all() and (r[SEEP] == 0).all()                  # k = 0, nothing set
    assert ((r[AREA] > 0) == (r[V] > 0)).all()
    close(r, "no exchange")


def test_the_read_is_swats():
    """Every read as reservoir_day.txt prints it, with SWAT+'s own seepage on."""
    run = project("Osu_1hru", "res-printed", daily=True, edit=reservoir(k=0.05))
    _, r = step(run)
    day = daily_table(run / "reservoir_day.txt")["res01"]
    assert len(day["flo_stor"]) == len(r[V])
    for v, col, k in ((V, "flo_stor", 1.0), (AREA, "area", 1e4), (QIN, "flo_in", 1.0),
                      (QOUT, "flo_out", 1.0), (PCP, "precip", 1.0), (EVAP, "evap", 1.0),
                      (SEEP, "seep", 1.0)):
        np.testing.assert_allclose(r[v], day[col] * k, rtol=5e-4, atol=1e-3 * k, err_msg=v)
    assert r[SEEP].max() > 0
    close(r, "SWAT+'s own seepage")


def test_a_gain_joins_the_inflow_exactly():
    gain = 0.002                                                        # m3/s
    _, b = step(project("Osu_1hru", "res-base", daily=True, edit=with_reservoir), years=2)
    _, r = step(project("Osu_1hru", "res-gain", daily=True, edit=with_reservoir), gain, years=2)
    np.testing.assert_allclose(r[GW], gain * 86400.0, rtol=1e-6)
    np.testing.assert_allclose(r[QIN], r[CHA] + gain * 86400.0, rtol=1e-6)
    np.testing.assert_allclose(r[CHA], b[CHA], rtol=1e-6)              # upstream is the same
    assert r[V].sum() > b[V].sum()
    close(r, "a gain")


def test_a_loss_takes_no_more_than_the_reservoir_holds():
    loss = 0.05                                                         # m3/s, 4,320 m3 a day
    _, r = step(project("Osu_1hru", "res-loss", daily=True, edit=with_reservoir), -loss, years=2)
    assert (r[GW] <= 0).all() and (r[GW] >= -loss * 86400.0 * (1 + 1e-6)).all()
    assert (r[V] >= 0).all()
    short = r[GW] > -loss * 86400.0 * (1 - 1e-6)
    assert short.any() and (~short).any()                               # both happen
    assert (r[V][short] == 0).all()                                     # emptied when short
    close(r, "a loss")


def test_a_reservoir_not_yet_built_exchanges_nothing():
    _, r = step(project("Osu_1hru", "res-later", daily=True, edit=reservoir(yr_op=2011)),
                0.002, years=2)
    first = slice(0, 365)
    assert (r[GW][first] == 0).all() and (r[V][first] == 0).all() and (r[QIN][first] == 0).all()
    assert (r[GW][365:] > 0).all()
