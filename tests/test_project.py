"""The project helpers, and what an early stop leaves behind."""

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from swatplus_bmi.project import check_calibration, check_soils, set_print


def test_set_print_turns_on_exactly_the_named_object():
    """With labels off (as in the bundled examples), editing the aquifer row by
    hand doesn't turn on the aquifer's daily output; set_print does."""
    need(STOCK)
    by_hand = project("Osu_1hru", "prt-by-hand")
    lines = (by_hand / "print.prt").read_text().splitlines()
    lines = [l.replace("n", "y", 1) if l.split()[:1] == ["aquifer"] else l for l in lines]
    (by_hand / "print.prt").write_text("\n".join(lines) + "\n")
    run_stock(by_hand)
    assert not (by_hand / "aquifer_day.txt").exists()       # SWAT+ read that row as another object

    helped = project("Osu_1hru", "prt-helper")
    set_print(helped, ["aquifer"], intervals=["daily"], skip_years=0)
    run_stock(helped)
    assert len((helped / "aquifer_day.txt").read_text().splitlines()) == 5113 + 3
    assert not (helped / "channel_sd_day.txt").exists()


def test_set_print_rejects_unknown_rows():
    need(STOCK)
    run = project("Osu_1hru", "prt-unknown")
    with pytest.raises(ValueError, match="no rows named"):
        set_print(run, ["aquifr"])


def test_a_run_stopped_early_keeps_its_daily_output_to_that_day():
    need(EXCHANGE)
    run = project("Osu_1hru", "early-stop", daily=True)
    m = SwatPlusBmi()
    m.initialize(str(run))
    m.update_until(1000)
    q = m.get_value("channel_water__volume_outflow_rate", np.empty(1, np.float32))
    m.finalize()                                              # before SWAT+'s last day: stops it
    lines = (run / "channel_sd_day.txt").read_text().splitlines()
    assert len(lines) == 1000 + 3                             # three header lines, then each day run
    col = lines[1].split().index("flo_out")
    assert float(lines[-1].split()[col]) == pytest.approx(q[0], rel=5e-4, abs=1e-12)
    assert not (run / "success.fin").exists()


def test_check_calibration_names_the_changes_swat_wont_make():
    run = project("Osu_1hru", "check-cal")
    assert check_calibration(run) == []                      # the example's changes are all known
    rows = (run / "calibration.cal").read_text().splitlines()
    n = int(rows[1].split()[0])
    zeros = "  ".join(["0"] * 7)
    rows[1] = f"    {n + 2}"
    rows += [f"chn         pctchg    10.0  0  {zeros}", f"xyzzy       pctchg    10.0  0  {zeros}"]
    (run / "calibration.cal").write_text("\n".join(rows) + "\n")
    found = check_calibration(run)
    assert len(found) == 2
    assert "chn has no effect" in found[0]
    assert "xyzzy isn't in cal_parms.cal" in found[1]


def test_check_soils_compares_what_swat_read_with_soils_sol():
    """The comparison only: soils.sol is changed after the run, standing in for a soil
    SWAT+ misread."""
    need(STOCK)
    run = project("Osu_1hru", "check-soils")
    run_stock(run)
    assert check_soils(run) == []
    sol = (run / "soils.sol").read_text().replace(
        "PadHOEGOG                         5                 C",
        "PadHOEGOG                         5                 D")
    (run / "soils.sol").write_text(sol)
    found = check_soils(run)
    assert found == ["PadHOEGOG: SWAT+ read hydrologic group C, depth 750.0 mm; soils.sol gives D, "
                     "750.0 mm"]
