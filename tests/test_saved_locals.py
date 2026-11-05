"""Whether any implicitly saved local carries state SWAT+ relies on.

``build.sh --nosave`` builds stock SWAT+ with every initialized local reset
on each call (tools/reset_saved_locals.py). If its output matches stock,
those locals carry nothing between calls on the code paths these projects
run, and a serializer for save_state can leave them out (docs/design.md,
"Saving to a file").
"""

import pytest

from conftest import BIN, STOCK, TAG, ames_light, differences, need, project, run_stock
from test_allocation import add_allocation
from test_setting import add_point_source

NOSAVE = BIN / f"swatplus-{TAG}-nosave"
CASES = {"plain": {}, "daily": {"daily": True},
         "point-source": {"daily": True, "edit": add_point_source},
         "allocation": {"daily": True, "edit": add_allocation}}


@pytest.mark.parametrize("case", CASES)
def test_no_saved_local_carries_state(case):
    need(STOCK, NOSAVE)
    stock = project("Osu_1hru", f"Osu_1hru-{case}-sl-stock", **CASES[case])
    run_stock(stock)
    nosave = project("Osu_1hru", f"Osu_1hru-{case}-sl-nosave", **CASES[case])
    run_stock(nosave, NOSAVE)
    assert differences(stock, nosave) == []


@pytest.mark.full
def test_no_saved_local_carries_state_ames():
    need(STOCK, NOSAVE)
    stock = project("Ames_sub1", "Ames_sub1-sl-stock", edit=ames_light)
    run_stock(stock)
    nosave = project("Ames_sub1", "Ames_sub1-sl-nosave", edit=ames_light)
    run_stock(nosave, NOSAVE)
    assert differences(stock, nosave) == []
