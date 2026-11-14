"""Arrays cross the exchange as raw 8-byte reals (protocol 6).

``echo`` sends an array to SWAT+ and back untouched, so the channel itself can be
checked with any bit pattern: negative and positive zero, the smallest and largest
reals, infinities, NaN with its payload, and random bit patterns, in arrays larger
than a pipe holds. The commands that carry arrays are checked against each other on a
real project, and a refused command must leave the two ends in step.
"""

import numpy as np
import pytest

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi
from swatplus_bmi.exchange import ExchangeError

TS = "soil_layer__temperature"
PCP = "atmosphere_water__precipitation_leq-volume_flux"

SPECIAL = np.array([0.0, -0.0, 1.0, -1.0, 0.1, -0.1, np.pi, -np.e, 1e-300, -1e-300,
                    5e-324, -5e-324,                       # the smallest subnormals
                    2.2250738585072014e-308,               # the smallest normal
                    1.7976931348623157e308, -1.7976931348623157e308,
                    np.inf, -np.inf, np.nan, 86400.0, 1 / 86400, 3.4028234663852886e38,
                    1.401298464324817e-45])                # the smallest 4-byte subnormal


def bits(a):
    return np.asarray(a, dtype=np.float64).view(np.uint64)


def started(dest, days=0, name="Osu_1hru"):
    m = SwatPlusBmi()
    m.initialize(str(project(name, dest, daily=True)))
    m.update_until(days)
    return m


@pytest.fixture(scope="module")
def model():
    need(EXCHANGE)
    m = started("transport", 40)
    yield m
    m.finalize()


def test_echo_is_bit_for_bit(model):
    x = model._x
    assert (bits(x.echo(SPECIAL)) == bits(SPECIAL)).all()
    rng = np.random.default_rng(6)
    patterns = rng.integers(0, 2**64, size=300_000, dtype=np.uint64, endpoint=False)
    patterns[:4] = [0x7FF8000000000001, 0xFFF0000000000001,   # NaNs with payloads, one signaling
                    0x7FF4000000000000, 0x8000000000000000]
    back = x.echo(patterns.view(np.float64))                 # 2.4 MB: more than a pipe holds
    assert back.dtype == np.float64
    assert (bits(back) == patterns).all()
    assert x.echo([]).size == 0


def test_get_at_is_get_at_those_indices(model):
    n = model.get_grid_size(model.get_var_grid(TS))
    whole = model.get_value(TS, np.empty(n))
    inds = np.array([n - 1, 0, n // 2, 0, n - 1])            # any order, repeats allowed
    some = model.get_value_at_indices(TS, np.empty(len(inds)), inds)
    assert (bits(some) == bits(whole[inds])).all()
    last = model.get_value_at_indices(TS, np.empty(1), np.array([-1]))   # numpy's rule, as before
    assert bits(last)[0] == bits(whole)[-1]
    assert model.get_value_at_indices(TS, np.empty(0), np.array([], dtype=int)).size == 0
    with pytest.raises(IndexError):
        model.get_value_at_indices(TS, np.empty(1), np.array([n]))


def test_a_value_set_comes_back_to_the_bit():
    """Soil temperatures are 4-byte reals in SWAT+: any 4-byte value set, in bulk or at
    indices, reads back as that value exactly."""
    need(EXCHANGE)
    m = started("transport-set", 30)
    n = m.get_grid_size(m.get_var_grid(TS))
    rng = np.random.default_rng(7)
    want = rng.uniform(-40, 40, n).astype(np.float32).astype(np.float64)
    want[:3] = [0.0, -0.0, np.float32(1e-30)]
    m.set_value(TS, want)                                    # setv
    m.update()
    assert (bits(m.get_value(TS, np.empty(n))) == bits(want)).all()
    inds = np.arange(n)[::-2]
    m.set_value_at_indices(TS, inds, -want[inds])            # seti
    m.update()
    assert (bits(m.get_value_at_indices(TS, np.empty(len(inds)), inds)) == bits(-want[inds])).all()
    m.finalize()


def test_refusals_leave_the_channel_in_step(model):
    x = model._x
    n = model.get_grid_size(model.get_var_grid(TS))
    ng = model.get_grid_size(model.get_var_grid(PCP))

    def in_step():
        x._send("ping")
        x._expect("@ok")
        assert (bits(x.echo(SPECIAL)) == bits(SPECIAL)).all()

    # refused before anything is sent
    with pytest.raises(ExchangeError, match=f"setv needs all {n} values"):
        x.set_all(TS, np.zeros(n + 1))
    in_step()
    with pytest.raises(ExchangeError, match=f"each of the {ng} gages"):
        x.set_records(PCP, 2011, 300, np.zeros((2, ng + 1)))
    in_step()
    with pytest.raises(ExchangeError, match="unknown variable"):
        x.get_at("no_such_variable", [0])
    in_step()
    # refused after the array has been read
    with pytest.raises(ExchangeError, match=f"index 2 is {n + 1}, not 1 to {n}"):
        x.get_at(TS, [0, n])
    in_step()
    with pytest.raises(ExchangeError, match="value 1: a value must be a finite number"):
        x._send_arrays(f"setv {TS} {n}", np.full(n, np.nan))   # past Python's own check
        x._expect("@ok")
    in_step()
    with pytest.raises(ExchangeError, match="value 2: a temperature can't be below absolute zero"):
        x.set_at(TS, [0, 1], [5.0, -300.0])
    in_step()


def test_a_branch_has_its_own_array_channels(tmp_path):
    need(EXCHANGE)
    a = started("transport-branch-a", 20)
    b = a.branch(tmp_path / "b")
    for m in (a, b):
        assert (bits(m._x.echo(SPECIAL)) == bits(SPECIAL)).all()
    n = a.get_grid_size(a.get_var_grid(TS))
    assert (bits(a.get_value(TS, np.empty(n))) == bits(b.get_value(TS, np.empty(n)))).all()
    b.set_value(TS, np.full(n, 3.0))
    b.update()
    a.update()
    assert (b.get_value(TS, np.empty(n)) == 3.0).all()
    assert not (a.get_value(TS, np.empty(n)) == 3.0).all()
    a.finalize()                                             # the parent's replies end with it,
    b.update()                                               # and the branch goes on
    b.finalize()
