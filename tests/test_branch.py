"""Milestone 4a: branching a paused run in memory (fork), Mac and Linux.

The snapshot test: branch at day N, continue every copy to the end, and
match an unbroken stock run bit for bit, every output file. Run on Osu_1hru
with daily printing, and on the water allocation variant so that branches
taking different decisions can be checked against unbroken runs taking the
same decisions.
"""

import sys

import numpy as np
import pytest

from conftest import EXCHANGE, RUNS, STOCK, differences, need, project, run_stock
from swatplus_bmi import SwatPlusBmi
from test_allocation import DMD, osu_with_allocation

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="branching needs fork")


def fresh(dest):
    d = RUNS / dest
    if d.exists():
        import shutil
        shutil.rmtree(d)
    return d


@pytest.mark.parametrize("day", [0, 365, 1000, 5112])
def test_branch_then_both_continue_is_stock(day):
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "Osu_1hru-daily-stock", daily=True)
    run_stock(stock)

    parent = SwatPlusBmi()
    parent.initialize(str(project("Osu_1hru", f"Osu_1hru-br{day}-parent", daily=True)))
    parent.update_until(day)
    child = parent.branch(fresh(f"Osu_1hru-br{day}-child"))
    assert child.get_current_time() == parent.get_current_time() == day
    # they run in turn, so a shared file would show up as a difference
    while parent.get_current_time() < parent.get_end_time():
        parent.update()
        child.update()
    parent.finalize()
    child.finalize()

    assert differences(stock, parent.run_dir if hasattr(parent, "_x") and parent._x else
                       RUNS / f"Osu_1hru-br{day}-parent") == []
    assert differences(stock, RUNS / f"Osu_1hru-br{day}-child") == []


def test_warm_up_once_then_scenarios_match_unbroken_runs():
    """Three scenarios branched from one warm-up each match the same scenario run unbroken."""
    need(EXCHANGE)
    warm_up = 2000
    scenarios = {"none": None, "half": 0.00025, "cut": 0.0}   # demand set every day after the warm-up

    def drive(m, rate):
        while m.get_current_time() < m.get_end_time():
            if rate is not None and m.get_current_time() >= warm_up:
                m.set_value(DMD, np.array([rate], np.float32))
            m.update()
        m.finalize()

    for name, rate in scenarios.items():                         # unbroken reference runs
        m = SwatPlusBmi()
        m.initialize(str(osu_with_allocation(f"Osu_1hru-wa-{name}-unbroken")))
        drive(m, rate)

    base = SwatPlusBmi()
    base.initialize(str(osu_with_allocation("Osu_1hru-wa-warmup")))
    base.update_until(warm_up)
    branches = {name: base.branch(fresh(f"Osu_1hru-wa-{name}-branch")) for name in scenarios}
    base.finalize()                                              # the warm-up itself can go
    for name, rate in scenarios.items():
        drive(branches[name], rate)

    for name in scenarios:
        assert differences(RUNS / f"Osu_1hru-wa-{name}-unbroken",
                           RUNS / f"Osu_1hru-wa-{name}-branch") == [], name
    # and the scenarios really differ
    assert differences(RUNS / "Osu_1hru-wa-none-branch", RUNS / "Osu_1hru-wa-cut-branch")


def test_branch_of_a_branch():
    need(STOCK, EXCHANGE)
    stock = project("Osu_1hru", "Osu_1hru-daily-stock", daily=True)
    run_stock(stock)
    a = SwatPlusBmi()
    a.initialize(str(project("Osu_1hru", "Osu_1hru-brbr-a", daily=True)))
    a.update_until(400)
    b = a.branch(fresh("Osu_1hru-brbr-b"))
    a.finalize()
    b.update_until(3000)
    c = b.branch(fresh("Osu_1hru-brbr-c"))
    b.update_until(b.get_end_time())
    c.update_until(c.get_end_time())
    b.finalize()
    c.finalize()
    assert differences(stock, RUNS / "Osu_1hru-brbr-b") == []
    assert differences(stock, RUNS / "Osu_1hru-brbr-c") == []
