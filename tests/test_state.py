"""Milestone 4b: saving state to a file and loading it in a new process.

The snapshot test: save at day N, load in a new SWAT+ process, continue to
the end, and match an unbroken stock run bit for bit, every output file.
Works on every platform (no fork). The serializer is generated from
SWAT+'s module declarations by tools/gen_serializer.py at build time.
"""


import numpy as np
import pytest

from conftest import EXCHANGE, RUNS, STOCK, ames_light, differences, fresh, need, project, run_stock
from swatplus_bmi import ExchangeError, SwatPlusBmi
from test_allocation import DMD, osu_with_allocation



def stock_daily():
    stock = project("Osu_1hru", "Osu_1hru-daily-stock", daily=True)
    run_stock(stock)
    return stock


@pytest.mark.parametrize("day", [0, 365, 1000, 5112])
def test_save_load_continue_is_stock(day):
    need(STOCK, EXCHANGE)
    stock = stock_daily()
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", f"Osu_1hru-st{day}-saved", daily=True)))
    m.update_until(day)
    snap = m.save_state(fresh(f"Osu_1hru-st{day}-snapshot"))
    m.update_until(m.get_end_time())                   # the saved run carries on unchanged
    m.finalize()

    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", f"Osu_1hru-st{day}-loaded", daily=True)))
    n.load_state(snap)
    assert n.get_current_time() == day
    n.update_until(n.get_end_time())
    n.finalize()

    assert differences(stock, RUNS / f"Osu_1hru-st{day}-saved") == []
    assert differences(stock, RUNS / f"Osu_1hru-st{day}-loaded") == []


def test_values_read_after_loading_are_the_saved_ones():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(osu_with_allocation("Osu_1hru-st-values-a")))
    m.update_until(777)
    want = {v: m.get_value(v, np.empty(m.get_grid_size(m.get_var_grid(v)), np.float32)).copy()
            for v in m.get_output_var_names()}
    snap = m.save_state(fresh("Osu_1hru-st-values-snap"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(osu_with_allocation("Osu_1hru-st-values-b")))
    n.load_state(snap)
    for v, w in want.items():
        got = n.get_value(v, np.empty(n.get_grid_size(n.get_var_grid(v)), np.float32))
        assert np.array_equal(got, w), v
    n.finalize()


def test_scenarios_from_one_saved_warm_up_match_unbroken_runs():
    need(EXCHANGE)
    warm_up = 2000

    def drive(m, rate):
        while m.get_current_time() < m.get_end_time():
            if rate is not None and m.get_current_time() >= warm_up:
                m.set_value(DMD, np.array([rate], np.float32))
            m.update()
        m.finalize()

    base = SwatPlusBmi()
    base.initialize(str(osu_with_allocation("Osu_1hru-st-warmup")))
    base.update_until(warm_up)
    snap = base.save_state(fresh("Osu_1hru-st-warmup-snap"))
    base.finalize()
    for name, rate in {"none": None, "cut": 0.0}.items():
        u = SwatPlusBmi()
        u.initialize(str(osu_with_allocation(f"Osu_1hru-st-{name}-unbroken")))
        drive(u, rate)
        s = SwatPlusBmi()
        s.initialize(str(osu_with_allocation(f"Osu_1hru-st-{name}-loaded")))
        s.load_state(snap)
        drive(s, rate)
        assert differences(RUNS / f"Osu_1hru-st-{name}-unbroken",
                           RUNS / f"Osu_1hru-st-{name}-loaded") == [], name


def test_get_state_set_state_round_trip():
    need(STOCK, EXCHANGE)
    stock = stock_daily()
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-bytes-a", daily=True)))
    m.update_until(1500)
    state = m.get_state()
    assert isinstance(state, bytes) and len(state) > 1000
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-st-bytes-b", daily=True)))
    n.set_state(state)
    assert n.get_current_time() == 1500
    n.update_until(n.get_end_time())
    n.finalize()
    assert differences(stock, RUNS / "Osu_1hru-st-bytes-b") == []


def test_a_state_from_another_build_is_refused(tmp_path):
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-bad-a")))
    m.update_until(10)
    snap = m.save_state(tmp_path / "snap")
    m.finalize()
    data = bytearray((snap / "state.bin").read_bytes())
    data[16:32] = b"0000000000000000"                  # the build digest
    (snap / "state.bin").write_bytes(bytes(data))
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-st-bad-b")))
    with pytest.raises(ExchangeError, match="different build"):
        n.load_state(snap)


@pytest.mark.full
def test_save_load_continue_is_stock_ames():
    """Ames_sub1 prints the soil carbon layer files, whose header flag is a saved
    local that carries state (patches/<tag>/carried-locals.toml)."""
    need(STOCK, EXCHANGE)
    stock = project("Ames_sub1", "Ames_sub1-st-stock", edit=ames_light)
    run_stock(stock)
    m = SwatPlusBmi()
    m.initialize(str(project("Ames_sub1", "Ames_sub1-st-saved", edit=ames_light)))
    m.update_until(3000)
    snap = m.save_state(fresh("Ames_sub1-st-snapshot"))
    m.finalize()
    n = SwatPlusBmi()
    n.initialize(str(project("Ames_sub1", "Ames_sub1-st-loaded", edit=ames_light)))
    n.load_state(snap)
    n.update_until(n.get_end_time())
    n.finalize()
    assert differences(stock, RUNS / "Ames_sub1-st-loaded") == []


def _after_hru_irrigation(data):
    """Where the irrigation withdrawals' record starts: after the places of use and the HRU
    irrigation; and the number of HRUs."""
    import struct
    off = 16 + 16 + 4 + 80                                   # magic, digest, days_done, paused_line
    (n,) = struct.unpack_from("<i", data, off)
    off += 4 + 2 * 4 * n                                     # pou_dmd_day, pou_wdr_day
    (nh,) = struct.unpack_from("<i", data, off)
    return off + 4 + 4 * nh, nh                              # hru_irr_day


def _after_withdrawals(data, off, nh):
    """Where the irrigation applied from outside starts, given where the withdrawals do."""
    import struct
    nc, nr = struct.unpack_from("<ii", data, off)
    return off + 8 + 8 * 2 * (nc + nr + nh)


def as_0_16(snap):
    """Rewrite a saved state as swatplus-bmi 0.16 wrote it (SWXSTATE-9): the same layout without
    the water table (the held layers, the held_lateral option, the day's saturation excess and
    groundwater not taken), which follows the soil cover."""
    import struct
    p = snap / "state.bin"
    data = bytearray(p.read_bytes())
    assert data[:16] == b"SWXSTATE-10".ljust(16) and data[-16:] == b"SWXSTATE-10".ljust(16)
    off, nh = _after_hru_irrigation(data)
    off = _after_withdrawals(data, off, nh)
    off += 4 + 4 * nh * (8 + 4 + 8)                          # ira_set, ira_on, ira_day
    off += 4 + nh * (4 + 4 + 4)                              # cov_set, cov_on, cov_day
    nl, n = struct.unpack_from("<ii", data, off)
    assert n == nh
    del data[off:off + 8 + 4 * nl + 4 + 8 * nh]              # hly_held, held_lat, sat_day, nt_day
    data[:16] = data[-16:] = b"SWXSTATE-9".ljust(16)
    p.write_bytes(bytes(data))


def as_0_15(snap):
    """Rewrite a saved state as swatplus-bmi 0.15 wrote it (SWXSTATE-8): the 0.16 layout without
    the soil cover, which follows the irrigation applied from outside."""
    import struct
    as_0_16(snap)
    p = snap / "state.bin"
    data = bytearray(p.read_bytes())
    assert data[:16] == b"SWXSTATE-9".ljust(16) and data[-16:] == b"SWXSTATE-9".ljust(16)
    off, nh = _after_hru_irrigation(data)
    off = _after_withdrawals(data, off, nh)
    off += 4 + 4 * nh * (8 + 4 + 8)                          # ira_set, ira_on, ira_day: 4 each by HRU
    (n,) = struct.unpack_from("<i", data, off)
    assert n == nh
    del data[off:off + 4 + nh * (4 + 4 + 4)]                 # cov_set, cov_on, cov_day by HRU
    data[:16] = data[-16:] = b"SWXSTATE-8".ljust(16)
    p.write_bytes(bytes(data))


def as_0_14(snap):
    """Rewrite a saved state as swatplus-bmi 0.14 wrote it (SWXSTATE-7): the 0.15 layout without
    the irrigation applied from outside, which follows the irrigation withdrawals."""
    import struct
    as_0_15(snap)
    p = snap / "state.bin"
    data = bytearray(p.read_bytes())
    off, nh = _after_hru_irrigation(data)
    nc, nr = struct.unpack_from("<ii", data, off)
    off += 8 + 8 * 2 * (nc + nr + nh)                        # the irrigation withdrawals
    (n,) = struct.unpack_from("<i", data, off)
    assert n == nh
    del data[off:off + 4 + 4 * nh * (8 + 4 + 8)]             # ira_set, ira_on, ira_day: 4 each by HRU
    data[:16] = data[-16:] = b"SWXSTATE-7".ljust(16)
    p.write_bytes(bytes(data))


def as_0_13(snap):
    """Rewrite a saved state as swatplus-bmi 0.13 wrote it (SWXSTATE-6): the 0.14 layout without
    the irrigation withdrawals' record."""
    import struct
    as_0_14(snap)
    p = snap / "state.bin"
    data = bytearray(p.read_bytes())
    off, nh = _after_hru_irrigation(data)
    nc, nr = struct.unpack_from("<ii", data, off)
    del data[off:off + 8 + 8 * 2 * (nc + nr + nh)]           # the irrigation withdrawals
    data[:16] = data[-16:] = b"SWXSTATE-6".ljust(16)
    p.write_bytes(bytes(data))


def test_a_state_saved_by_0_13_loads_with_the_withdrawals_zero(tmp_path):
    """0.14 adds the day's irrigation withdrawals to a saved state (SWXSTATE-7) and changes
    nothing else in it, so a 0.13 state (SWXSTATE-6) loads with them zero: the run carries on as
    from the same state saved by 0.17, every output file byte for byte, and the withdrawals read
    0 for the day before the load and the same as the 0.14 state's from the first day on (when,
    as here, the saved day's decision tables took nothing before the pause)."""
    import shutil
    from test_irrigation import WCHA, WHRU, from_source
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-013-a", daily=True, edit=from_source())))
    m.update_until(2000)
    snap = m.save_state(tmp_path / "v10")
    m.finalize()
    old = tmp_path / "v6"
    shutil.copytree(snap, old)
    as_0_13(old)

    reads = {}
    for name, state in (("v10", snap), ("v6", old)):
        n = SwatPlusBmi()
        n.initialize(str(project("Osu_1hru", f"Osu_1hru-st-013-{name}", daily=True, edit=from_source())))
        n.load_state(state)
        assert n.get_current_time() == 2000
        first = n.get_value(WCHA, np.empty(1))[0]
        got = []
        while n.get_current_time() < n.get_end_time():
            n.update()
            got.append([n.get_value(WCHA, np.empty(1))[0], n.get_value(WHRU, np.empty(1))[0]])
        n.finalize()
        reads[name] = (first, np.array(got))
    assert reads["v6"][0] == 0
    assert np.array_equal(reads["v6"][1], reads["v10"][1])
    assert (reads["v6"][1][:, 0] > 0).sum() > 10
    assert differences(RUNS / "Osu_1hru-st-013-v10", RUNS / "Osu_1hru-st-013-v6") == []


def test_a_state_saved_by_0_14_loads_with_nothing_applied(tmp_path):
    """0.15 adds the irrigation applied from outside to a saved state (SWXSTATE-8) and changes
    nothing else in it, so a 0.14 state (SWXSTATE-7) loads with none set: the run carries on as
    from the same state saved by 0.17, every output file byte for byte."""
    import shutil
    from test_irrigation import from_source
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-014-a", daily=True, edit=from_source())))
    m.update_until(2000)
    snap = m.save_state(tmp_path / "v10")
    m.finalize()
    old = tmp_path / "v7"
    shutil.copytree(snap, old)
    as_0_14(old)
    for name, state in (("v10", snap), ("v7", old)):
        n = SwatPlusBmi()
        n.initialize(str(project("Osu_1hru", f"Osu_1hru-st-014-{name}", daily=True, edit=from_source())))
        n.load_state(state)
        assert n.get_current_time() == 2000
        n.update_until(n.get_end_time())
        n.finalize()
    assert differences(RUNS / "Osu_1hru-st-014-v10", RUNS / "Osu_1hru-st-014-v7") == []


def test_a_state_saved_by_0_15_loads_with_no_cover_set(tmp_path):
    """0.16 adds the soil cover to a saved state (SWXSTATE-9) and changes nothing else in it, so a
    0.15 state (SWXSTATE-8) loads with none set: the run carries on as from the same state saved
    by 0.17, every output file byte for byte."""
    import shutil
    from test_irrigation import from_source
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-015-a", daily=True, edit=from_source())))
    m.update_until(2000)
    snap = m.save_state(tmp_path / "v10")
    m.finalize()
    old = tmp_path / "v8"
    shutil.copytree(snap, old)
    as_0_15(old)
    for name, state in (("v10", snap), ("v8", old)):
        n = SwatPlusBmi()
        n.initialize(str(project("Osu_1hru", f"Osu_1hru-st-015-{name}", daily=True, edit=from_source())))
        n.load_state(state)
        assert n.get_current_time() == 2000
        n.update_until(n.get_end_time())
        n.finalize()
    assert differences(RUNS / "Osu_1hru-st-015-v10", RUNS / "Osu_1hru-st-015-v8") == []


def test_a_state_saved_by_0_16_loads_with_nothing_held(tmp_path):
    """0.17 adds the water table to a saved state (SWXSTATE-10) and changes nothing else in it, so
    a 0.16 state (SWXSTATE-9) loads with no layer held: the run carries on as from the same state
    saved by 0.17, every output file byte for byte."""
    import shutil
    from test_irrigation import from_source
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-016-a", daily=True, edit=from_source())))
    m.update_until(2000)
    snap = m.save_state(tmp_path / "v10")
    m.finalize()
    old = tmp_path / "v9"
    shutil.copytree(snap, old)
    as_0_16(old)
    for name, state in (("v10", snap), ("v9", old)):
        n = SwatPlusBmi()
        n.initialize(str(project("Osu_1hru", f"Osu_1hru-st-016-{name}", daily=True, edit=from_source())))
        n.load_state(state)
        assert n.get_current_time() == 2000
        n.update_until(n.get_end_time())
        n.finalize()
    assert differences(RUNS / "Osu_1hru-st-016-v10", RUNS / "Osu_1hru-st-016-v9") == []


def test_a_state_of_an_unknown_version_is_refused(tmp_path):
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-st-ver-a")))
    m.update_until(10)
    snap = m.save_state(tmp_path / "snap")
    m.finalize()
    data = bytearray((snap / "state.bin").read_bytes())
    data[:16] = b"SWXSTATE-5".ljust(16)
    (snap / "state.bin").write_bytes(bytes(data))
    n = SwatPlusBmi()
    n.initialize(str(project("Osu_1hru", "Osu_1hru-st-ver-b")))
    with pytest.raises(ExchangeError, match="not a saved SWAT\\+ state"):
        n.load_state(snap)
