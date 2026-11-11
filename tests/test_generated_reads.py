"""Every column of SWAT+'s daily output records, read-only (0.8.0).

A read named <file>:<column> is the column SWAT+ prints in <file>_day.txt,
for the day just done, in the units printed under it (tools/gen_reads.py
generates them from SWAT+'s source). The test is the printed file itself:
with every daily file on, each read after each day must be what SWAT+
printed for that day, object by object, to the digits printed.
Test projects: Osu_1hru and Ames_sub1 (12 HRUs).
"""

from collections import defaultdict
from decimal import Decimal

import numpy as np
import pytest

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi


def generated(m):
    return [n for n in m.get_output_var_names() if ":" in n]


def printed(path, cols):
    """{(year, jday): {object name: {column: printed text}}} from a daily file."""
    lines = path.read_text().splitlines()
    head = lines[1].split()
    where = {c: head.index(c) for c in cols}
    out = defaultdict(dict)
    for line in lines[3:]:
        f = line.split()
        if len(f) < len(head) - 3:                     # trailing text columns may be blank
            continue
        out[(int(f[head.index("yr")]), int(f[head.index("jday")]))][f[head.index("name")]] = \
            {c: f[i] for c, i in where.items()}
    return out


def close(got, text):
    """got matches a printed number to the digits printed (gfortran rounds to nearest)."""
    if "*" in text:                                    # too wide for its field: nothing to compare
        return True
    d = Decimal(text)
    half = Decimal(1).scaleb(d.as_tuple().exponent) / 2
    return abs(Decimal(got) - d) <= half * Decimal("1.0001") + abs(d) * Decimal("1e-6")


@pytest.mark.parametrize("name, years", [("Osu_1hru", (1990, 1991)), ("Ames_sub1", (1975, 1975))])
def test_every_read_is_what_swatplus_printed_that_day(name, years):
    need(EXCHANGE)
    run = project(name, f"reads-{name}", daily=True, years=years)
    m = SwatPlusBmi()
    m.initialize(str(run))
    names = generated(m)
    files = defaultdict(list)
    for n in names:
        files[n.split(":")[0]].append(n.split(":")[1])
    got = defaultdict(dict)                            # (file, day) -> {var: values}
    while m.get_current_time() < m.get_end_time():
        m.update()
        t = m.get_current_time()
        for n in names:
            g = m.get_var_grid(n)
            got[t][n] = (m.get_grid_object_names(g), m.get_value(n, np.empty(m.get_grid_size(g))))
    m.finalize()

    checked = defaultdict(int)
    wrong = []
    for file, cols in files.items():
        if not (run / f"{file}_day.txt").exists():         # Ames_sub1's print.prt lists HRU files only
            continue
        table = printed(run / f"{file}_day.txt", cols)
        days = sorted(table)
        assert len(days) == len(got), f"{file}: {len(days)} days printed, {len(got)} run"
        for t, key in zip(sorted(got), days):
            for col in cols:
                objs, vals = got[t][f"{file}:{col}"]
                assert sorted(objs) == sorted(table[key]), f"{file} on {key}: objects"
                for o, v in zip(objs, vals):
                    checked[file] += 1
                    if not close(v, table[key][o][col]):
                        wrong.append(f"{file}:{col} {o} {key}: read {v!r}, printed {table[key][o][col]}")
    assert not wrong, f"{len(wrong)} of {sum(checked.values())} differ, first: {wrong[:10]}"
    assert checked and all(checked.values())
    if name == "Osu_1hru":                             # every file, on the project that prints them all
        assert set(checked) == set(files)


def test_the_reads_are_read_only_and_named_by_their_files():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "reads-catalog", years=(1990, 1990))))
    names = generated(m)
    assert "hru_wb:perc" in names and "aquifer:rchrg" in names and "channel_sd:flo_out" in names
    assert not set(names) & set(m.get_input_var_names())
    assert m.get_var_units("hru_wb:perc") == "mm" and m.get_var_units("channel_sd:flo_out") == "m^3/s"
    assert m.get_var_grid("hru_wb:perc") == m.get_var_grid("soil_water__percolation_rate")
    assert np.isnan(m.get_value("hru_wb:perc", np.empty(1))).all()          # no day done yet
    m.update()
    with pytest.raises(ValueError, match="output only"):
        m.set_value("hru_wb:perc", np.zeros(1))
    m.finalize()


def test_a_skipped_year_has_no_records():
    """SWAT+ makes its daily records only in the years it summarizes: after a day in
    print.prt's nyskip years a read is NaN, and from the first summarized day it's a number."""
    need(EXCHANGE)

    def skip_one(run):
        prt = run / "print.prt"
        lines = prt.read_text().splitlines()
        f = lines[2].split()
        f[0] = "1"
        lines[2] = "  ".join(f)
        prt.write_text("\n".join(lines) + "\n")

    run = project("Osu_1hru", "reads-skip", daily=True, years=(1990, 1991), edit=skip_one)
    m = SwatPlusBmi()
    m.initialize(str(run))
    days = 0
    while m.get_current_time() < m.get_end_time():
        m.update()
        days += 1
        v = m.get_value("hru_wb:perc", np.empty(1))
        assert np.isnan(v).all() == (days <= 365)
        assert np.isfinite(m.get_value("soil_water__percolation_rate", np.empty(1))).all()
    m.finalize()
