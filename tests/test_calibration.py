"""Parameter changes at initialize, through SWAT+'s own calibration.cal (0.8.0).

The wrapper only writes rows into the run copy's calibration.cal; SWAT+ makes
the changes. So the test of faithfulness is stock SWAT+ given the same file:
a run with changes must match it byte for byte. The rest is what the wrapper
refuses: every change the evidence (tools/audit_parameters.py) says SWAT+
62.0.1 won't make as asked.
Test projects: Osu_1hru (its own calibration.cal) and Ames_sub1 (12 HRUs,
given Osu_1hru's cal_parms.cal).
"""

import re
import shutil

import numpy as np
import pytest

from conftest import EXCHANGE, RUNS, STOCK, differences, made, need, project, refdata, run_stock
from swatplus_bmi import SwatPlusBmi
from swatplus_bmi.calibration import Change, check_changes, evidence, write_changes


def toml(v):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {toml(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml(x) for x in v) + "]"
    return f'"{v}"' if isinstance(v, str) else repr(v)


def config(tmp, run, changes, tag="copy"):
    """A TOML config running a copy of ``run`` with ``changes``."""
    rows = [f'project = "{run}"', f'run_dir = "{made(RUNS / f"{run.name}-{tag}")}"']
    for c in changes:
        rows += ["[[parameter]]"] + [f"{k} = {toml(v)}" for k, v in c.items()]
    path = tmp / f"{tag}.toml"
    path.write_text("\n".join(rows) + "\n")
    return path


def with_cal_parms(run):
    """Ames_sub1 has no cal_parms.cal: give it the editor's, as Osu_1hru has it."""
    shutil.copy(refdata() / "Osu_1hru" / "cal_parms.cal", run / "cal_parms.cal")
    cio = run / "file.cio"
    rows = cio.read_text().splitlines()
    for i, line in enumerate(rows):
        f = line.split()
        if f[:1] == ["chg"]:
            f[1] = "cal_parms.cal"
            rows[i] = "".join(f"{x:<18}" for x in f)
    cio.write_text("\n".join(rows) + "\n")


def run_all(cfg):
    m = SwatPlusBmi()
    m.initialize(str(cfg))
    m.update_until(m.get_end_time())
    run = m.run_dir
    m.finalize()
    return run


CHANGES = [{"name": "esco", "value": 20.0},
           {"name": "alpha", "value": 0.2, "change": "absval"},
           {"name": "awc", "value": -5.0, "layers": (2, 0)}]


def test_changes_are_the_run_stock_swatplus_makes_from_the_same_file(tmp_path):
    need(STOCK, EXCHANGE)
    run = project("Osu_1hru", "cal-osu", daily=True, years=(2010, 2012))
    got = run_all(config(tmp_path, run, CHANGES))
    ref = made(RUNS / "cal-osu-stock")
    shutil.copytree(run, ref, dirs_exist_ok=True)
    write_changes(ref, [Change.from_dict(c) for c in CHANGES])
    assert (ref / "calibration.cal").read_text() == (got / "calibration.cal").read_text()
    run_stock(ref)
    assert differences(ref, got) == []
    plain = made(RUNS / "cal-osu-none")
    shutil.copytree(run, plain, dirs_exist_ok=True)
    run_stock(plain)
    assert differences(plain, got)                            # and the changes changed it


def test_objects_and_conditions_pick_the_hrus_swatplus_changes(tmp_path):
    """cn2 on two HRUs by number, and on the HRUs with one land use: the first
    day's curve number (hru_wb:cn) moves for those HRUs only. Later days can't show it on
    Ames_sub1: its decision tables draw random numbers in turn from one generator, so a change in
    one HRU shifts the draws of the HRUs after it, in stock SWAT+ too."""
    need(EXCHANGE)
    base = project("Ames_sub1", "cal-ames", daily=True, years=(1975, 1975), edit=with_cal_parms)

    def first_day_cn(changes, tag):
        m = SwatPlusBmi()
        m.initialize(str(config(tmp_path, base, changes, tag)))
        m.update()
        names = m.get_grid_object_names(m.get_var_grid("hru_wb:cn"))
        cn = m.get_value("hru_wb:cn", np.empty(len(names)))
        m.finalize()
        return dict(zip(names, cn))

    ref = first_day_cn([], "none")
    moved = lambda got: sorted(h for h in ref if got[h] != ref[h])   # noqa: E731
    hrus = list(ref)
    got = first_day_cn([{"name": "cn2", "value": 15.0, "objects": (2, 5)}], "objs")
    assert moved(got) == [hrus[1], hrus[4]]

    lum = {f[1]: f[5] for f in (r.split() for r in (base / "hru-data.hru").read_text().splitlines()[2:])
           if len(f) > 5}
    target = lum[hrus[1]]
    want = sorted(h for h, u in lum.items() if u == target)
    assert want and len(want) < len(lum)
    got = first_day_cn([{"name": "cn2", "value": 15.0, "conditions": {"landuse": target}}], "lum")
    assert moved(got) == want


@pytest.mark.parametrize("change, says", [
    ({"name": "xyzzy", "value": 1.0}, "not in the project's cal_parms.cal"),
    ({"name": "spcon", "value": 10.0}, "has no code for it"),
    ({"name": "chn", "value": 10.0}, "rating curves"),
    ({"name": "bio_e", "value": 10.0}, "plant"),
    ({"name": "d50", "value": 10.0}, "to 1.0 each day"),
    ({"name": "nperco", "value": 10.0}, "only while starting up"),
    ({"name": "alpha", "value": 5.0, "change": "absval"}, "outside cal_parms.cal's range"),
    ({"name": "esco", "value": 10.0, "change": "pctchange"}, "isn't one SWAT+ knows"),
    ({"name": "alpha", "value": 10.0, "objects": (2,)}, "aren't aqu numbers 1 to 1"),
    ({"name": "esco", "value": 10.0, "layers": (1, 2)}, "soil (sol) parameters only"),
    ({"name": "alpha", "value": 10.0, "conditions": {"hsg": "B"}}, "for the aqu object"),
    ({"name": "cn2", "value": 10.0, "conditions": {"plant": "corn"}}, "only ('hsg'"),
])
def test_changes_swatplus_wouldnt_make_as_asked_are_refused(tmp_path, change, says):
    need(EXCHANGE)
    run = project("Osu_1hru", "cal-refuse", years=(2010, 2010))
    with pytest.raises(ValueError, match=re.escape(says)):
        SwatPlusBmi().initialize(str(config(tmp_path, run, [change])))


def test_a_change_needs_a_run_dir_and_a_cal_parms_cal(tmp_path):
    run = project("Osu_1hru", "cal-no-run-dir", years=(2010, 2010))
    cfg = tmp_path / "run.toml"
    cfg.write_text(f'project = "{run}"\n[[parameter]]\nname = "esco"\nvalue = 10\n')
    with pytest.raises(ValueError, match="give run_dir"):
        SwatPlusBmi().initialize(str(cfg))
    ames = project("Ames_sub1", "cal-no-parms", years=(1975, 1975))
    problems, _ = check_changes(ames, [Change("cn2", 10.0)])
    assert "no cal_parms.cal" in problems[0]


def test_the_evidence_covers_every_parameter_in_the_editors_list():
    rows = [r.split() for r in (refdata() / "Osu_1hru" / "cal_parms.cal").read_text().splitlines()[3:]]
    names = {r[0] for r in rows if r}
    ev = evidence()
    assert names == set(ev)
    assert {e["verdict"] for e in ev.values()} == {"made", "not checked", "skipped", "refused"}
    assert all(e["why"] for e in ev.values() if e["verdict"] != "made")
    assert sum(e["verdict"] == "made" for e in ev.values()) >= 80
