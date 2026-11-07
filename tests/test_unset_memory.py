"""Which outputs of stock SWAT+ depend on memory it never set.

A stock run is a fresh process, whose memory starts zeroed; inside Python the
heap holds whatever came before. Running stock SWAT+ with every allocation
filled with a pattern (MallocPreScribble on macOS, MALLOC_PERTURB_ on Linux)
shows which outputs read memory SWAT+ never set. On these projects the only
one is hru_carbvars' cons_bmix column (conftest.UNSET), which the comparisons
leave out; every other output must still match stock to the byte.
"""

import sys

import pytest

from conftest import STOCK, differences, need, project, run_stock

SCRAMBLED = ({"MallocPreScribble": "1", "MallocScribble": "1"} if sys.platform == "darwin"
             else {"MALLOC_PERTURB_": "85"})


def stock_and_scrambled(name: str, tag: str, **kw):
    need(STOCK)
    stock = project(name, f"{name}-{tag}-stock", **kw)
    run_stock(stock)
    scrambled = project(name, f"{name}-{tag}-scrambled", **kw)
    run_stock(scrambled, env=SCRAMBLED)
    return stock, scrambled


def test_osu_reads_no_unset_memory():
    stock, scrambled = stock_and_scrambled("Osu_1hru", "daily", daily=True)
    assert differences(stock, scrambled, unset=False) == []


@pytest.mark.full
def test_ames_reads_unset_memory_only_in_cons_bmix():
    stock, scrambled = stock_and_scrambled("Ames_sub1", "daily-5yr", daily=True, years=(1975, 1979))
    assert differences(stock, scrambled) == []
    assert set(differences(stock, scrambled, unset=False)) <= {
        "differs: hru_carbvars.txt", "differs: hru_carbvars.csv"}
