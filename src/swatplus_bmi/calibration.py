"""Parameter changes at initialize, through SWAT+'s own calibration.cal.

SWAT+ reads calibration.cal once, before its first day, and changes each
named parameter for the objects listed (``cal_parmchg_read``,
``cal_conditions``). That is the only way parameters change here: never as a
``set_value`` partway through a run, since SWAT+ derives values from its
parameters while it starts up (curve numbers, soil water, rating curves)
and a later change wouldn't reach them.

Not every change SWAT+ reads is one it makes. Some names have no code at
all, so SWAT+ reads them and does nothing; a channel's ``chn`` is applied
after the rating curves are built from it; plant changes index HRUs by
plant number. ``tools/audit_parameters.py`` tried every parameter in the
SWAT+ editor's ``cal_parms.cal`` on the test projects, one at a time, and
``calibration-62.0.1.json`` holds what it found. A change the evidence says
SWAT+ won't make as asked is refused; one no test project could check (its
objects are in neither) is allowed and listed by ``check_changes``.

    from swatplus_bmi.calibration import Change
    Change("cn2", -10)                                # every HRU's cn2 down 10%
    Change("awc", 0.15, "absval", layers=(1, 2))      # the top two layers' awc
    Change("cn2", 5, conditions=(("hsg", "D"),))      # HRUs on group D soils

or in an ``initialize`` TOML config, which needs ``run_dir`` so the
project's own calibration.cal isn't changed::

    [[parameter]]
    name = "cn2"
    value = -10
    change = "pctchg"          # absval, abschg, pctchg (percent) or relchg (fraction)
    objects = [1, 2, 3]        # SWAT+'s object numbers; all when left out
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

CHANGE_TYPES = ("absval", "abschg", "pctchg", "relchg")
HRU_TYPES = ("hru", "sol", "lyr")
# conditions cal_conditions checks against an HRU's own soil and land use, equality only
CONDITIONS = ("hsg", "texture", "landuse")
REFUSED = ("skipped", "refused")


@dataclass(frozen=True)
class Change:
    """One row of calibration.cal.

    ``objects`` are SWAT+'s 1-based numbers for the parameter's object type
    (HRUs for hru, sol and lyr; channels for rte, sdc and swq; aquifers for
    aqu), all of them when empty. ``layers`` (first, last) limits a soil
    parameter to those layers; 0 means the first or the last. ``conditions``
    are (variable, value) pairs, an HRU's hydrologic soil group, soil texture
    or land use.
    """

    name: str
    value: float
    change: str = "pctchg"
    objects: tuple[int, ...] = ()
    layers: tuple[int, int] = (0, 0)
    conditions: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> Change:
        extra = set(d) - {"name", "value", "change", "objects", "layers", "conditions"}
        if extra:
            raise ValueError(f"[[parameter]] {d.get('name', '?')}: unknown keys {sorted(extra)}")
        return cls(d["name"], float(d["value"]), d.get("change", "pctchg"),
                   tuple(int(i) for i in d.get("objects", ())),
                   tuple(int(i) for i in d.get("layers", (0, 0))),
                   tuple((str(k), str(v)) for k, v in dict(d.get("conditions", {})).items()))


@cache
def evidence() -> dict:
    """What tools/audit_parameters.py found, by parameter: obj_typ, verdict, why."""
    return json.loads((Path(__file__).parent / "calibration-62.0.1.json").read_text())["params"]


def _cio_chg(project: Path) -> list[str]:
    for line in (project / "file.cio").read_text().splitlines():
        f = line.split()
        if f[:1] == ["chg"]:
            return f
    raise ValueError(f"{project / 'file.cio'} has no chg line")


def cal_parms(project: str | Path) -> dict[str, tuple[str, float, float]]:
    """{name: (obj_typ, abs_min, abs_max)} from the project's cal_parms.cal; {} without one."""
    project = Path(project)
    f = _cio_chg(project)
    path = project / f[1]
    if f[1] == "null" or not path.exists():
        return {}
    rows = [r.split() for r in path.read_text().splitlines()[3:]]
    return {r[0]: (r[1], float(r[2]), float(r[3])) for r in rows if len(r) >= 4}


def object_counts(project: str | Path) -> dict[str, int]:
    """How many objects each calibration object type has in the project (object.cnt)."""
    f = (Path(project) / "object.cnt").read_text().splitlines()[2].split()
    cnt = dict(zip(["hru", "lhru", "rtu", "mfl", "aqu", "cha", "res", "rec", "exco", "dlr", "can",
                    "pmp", "out", "lcha", "aqu2d", "hrd", "wro"], map(int, f[4:])))
    return {"hru": cnt["hru"], "sol": cnt["hru"], "lyr": cnt["hru"], "hlt": cnt["lhru"],
            "ru": cnt["rtu"], "aqu": cnt["aqu"], "cha": cnt["cha"], "sdc": cnt["lcha"],
            "rte": cnt["lcha"], "swq": cnt["lcha"], "res": cnt["res"], "bsn": 1}


def check_changes(project: str | Path, changes) -> tuple[list[str], list[str]]:
    """(problems, notes) for ``changes`` in ``project``: a problem refuses the change; a note
    says what no test could check. Reads only."""
    parms = cal_parms(project)
    counts = object_counts(project)
    ev = evidence()
    problems, notes = [], []
    if not parms:
        problems.append("the project has no cal_parms.cal (file.cio's chg line), which SWAT+ needs "
                        "to make any calibration.cal change; the SWAT+ editor writes one")
        return problems, notes
    for c in changes:
        where = f"{c.name}:"
        if c.name not in parms:
            problems.append(f"{where} not in the project's cal_parms.cal, so SWAT+ would skip it")
            continue
        typ, lo, hi = parms[c.name]
        e = ev.get(c.name)
        if e is None:
            problems.append(f"{where} not in the editor's cal_parms.cal the tests checked, so it "
                            "isn't known what SWAT+ 62.0.1 does with it")
            continue
        if e["obj_typ"] != typ:
            problems.append(f"{where} the project lists it for {typ}, the checked list for {e['obj_typ']}")
            continue
        if e["verdict"] in REFUSED:
            problems.append(f"{where} {e['why']}")
            continue
        if e["verdict"] == "not checked":
            notes.append(f"{where} {e['why']}")
        if c.change not in CHANGE_TYPES:
            problems.append(f"{where} change {c.change!r} isn't one SWAT+ knows {CHANGE_TYPES}; it "
                            "would set the parameter to whatever memory held")
        if c.change == "absval" and not lo <= c.value <= hi:
            problems.append(f"{where} {c.value} is outside cal_parms.cal's range [{lo}, {hi}], "
                            "and SWAT+ would quietly use the nearer end")
        n = counts.get(typ, 0)
        if typ == "bsn" and c.objects:
            problems.append(f"{where} a basin parameter has no objects to list")
        bad = [i for i in c.objects if not 1 <= i <= n]
        if bad:
            problems.append(f"{where} objects {bad} aren't {typ} numbers 1 to {n} in this project")
        if c.layers != (0, 0):
            if typ != "sol":
                problems.append(f"{where} layers apply to soil (sol) parameters only")
            elif len(c.layers) != 2 or min(c.layers) < 0 or (c.layers[1] and c.layers[1] < c.layers[0]):
                problems.append(f"{where} layers {c.layers} aren't (first, last), 0 meaning all")
        for var, _ in c.conditions:
            if var not in CONDITIONS:
                problems.append(f"{where} condition {var!r}: only {CONDITIONS} are checked as SWAT+ "
                                "documents them")
            elif typ not in HRU_TYPES:
                problems.append(f"{where} a condition on {var} reads an HRU's soil or land use, and "
                                f"SWAT+ would read it for the {typ} object's number")
    return problems, notes


def write_changes(run_dir: str | Path, changes) -> list[str]:
    """Add ``changes`` to the run's calibration.cal, after the project's own rows (SWAT+ makes
    them in order), and point file.cio at it. Raises ValueError listing every problem; returns
    the notes."""
    run_dir = Path(run_dir)
    changes = list(changes)
    problems, notes = check_changes(run_dir, changes)
    if problems:
        raise ValueError("parameter changes SWAT+ wouldn't make as asked:\n  " + "\n  ".join(problems))
    cio = run_dir / "file.cio"
    lines = cio.read_text().splitlines()
    for i, line in enumerate(lines):
        f = line.split()
        if f[:1] == ["chg"] and f[2] == "null":
            f[2] = "calibration.cal"
            lines[i] = "".join(f"{x:<18}" for x in f)
            cio.write_text("\n".join(lines) + "\n")
    cal = run_dir / _cio_chg(run_dir)[2]
    if cal.exists():
        rows = cal.read_text().splitlines()
    else:
        rows = ["calibration.cal: written by swatplus-bmi", "0",
                " NAME           CHG_TYP                  VAL   CONDS  LYR1  LYR2   YEAR1  YEAR2   "
                "DAY1   DAY2  OBJ_TOT"]
    rows[1] = str(int(rows[1].split()[0]) + len(changes))
    for c in changes:
        objs = "".join(f"{i:>8}" for i in c.objects)
        rows.append(f"{c.name:<12}{c.change:<12}{c.value:>16.8E}{len(c.conditions):>8}"
                    f"{c.layers[0]:>8}{c.layers[1]:>8}" + f"{0:>8}" * 4 + f"{len(c.objects):>8}" + objs)
        rows += [f"{var:<16}{'=':<8}{0:>8}  {val}" for var, val in c.conditions]
    cal.write_text("\n".join(rows) + "\n")
    return notes
