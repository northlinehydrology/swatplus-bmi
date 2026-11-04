"""The project helpers, and what an early stop leaves behind."""

import numpy as np
import pytest

from conftest import EXCHANGE, STOCK, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from swatplus_bmi.project import set_print


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
