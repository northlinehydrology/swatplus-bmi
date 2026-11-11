"""Which calibration.cal changes SWAT+ actually makes: every parameter, tried one at a time.

For each row of a cal_parms.cal (Osu_1hru's, from the SWAT+ editor), two kinds
of evidence:

- the code: whether this release's cal_parm_select has a case for the name,
  or cal_conditions handles its object type itself (sol, plt, cli, rdt);
- a run: stock SWAT+ on each test project with only that change added to
  calibration.cal (absval at a point inside the parameter's range, for every
  object), printing everything daily, compared file by file with the run
  without it (leaving out files that differ between two identical runs). A
  change that moves no output where its objects exist doesn't reach the
  simulation there, or not in that project: Osu_1hru's one HRU is a rice
  paddy, whose runoff doesn't use the curve number.

A change that moves nothing has two possible reasons, and reading the code
tells them apart (CODE below): SWAT+ uses the value only while it starts
up, before it applies calibration.cal, or resets or misdirects it (refused);
or the test projects don't run the process that uses it (tiles, cracks,
lapse rates, pesticides; allowed, with a note).

    python tools/audit_parameters.py run <stock exe> <SWAT+ refdata/> <SWAT+ src/> <audit.json> [--jobs 2]
    python tools/audit_parameters.py summary <audit.json> src/swatplus_bmi/calibration-62.0.1.json \
        [docs/parameters-62.0.1.md]

``run`` writes {project: {"objects": {obj_typ: count}, "params": {name: {...}}}};
``summary`` writes what swatplus_bmi.calibration reads: by parameter, its
object type, a verdict (made, not checked, skipped, refused) and why.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# the projects and how long to run them: long enough for an aquifer change to show
PROJECTS = {"Osu_1hru": (2010, 2012), "Ames_sub1": (1975, 1977)}
POINT = 0.37          # where in [abs_min, abs_max] the tried value sits
NOT_OUTPUT = {"console.txt", "success.fin"}
CLOCK = [re.compile(rb"[ \d]\d:[ \d]\d:[ \d]\d"), re.compile(rb"[ \d]\d/[ \d]\d/\d{4}"),
         re.compile(rb"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")]
# the column stock SWAT+ prints without setting (cons_bmix, Found in SWAT+ 62.0.1 item 16), as
# tests/conftest.py masks it
UNSET = {"hru_carbvars.txt": re.compile(rb"(?m)^([ ]*(?:[^ \n]+[ ]+){11}[^ \n]+)[ ]+[^ \n]+[ ]+")}
# object types cal_conditions changes itself rather than through cal_parm_select
OWN_BRANCH = {"sol", "plt", "cli", "rdt"}


def cases(src: Path) -> set[str]:
    text = (src / "cal_parm_select.f90").read_text(errors="replace")
    return {n for c in re.findall(r"case\s*\(([^)]*)\)", text) for n in re.findall(r'"([^"]+)"', c)}


def parms(path: Path) -> list[tuple[str, str, float, float]]:
    rows = [r.split() for r in path.read_text().splitlines()[3:]]
    return [(r[0], r[1], float(r[2]), float(r[3])) for r in rows if len(r) >= 4]


OUTPUTS = re.compile(r".*(_day|_mon|_yr|_aa|_all)\.(txt|csv)$|.*\.out$")


def prepare(refdata: Path, name: str, first: int, last: int, cal_parms: Path, dest: Path) -> None:
    """The project, shortened, printing everything daily from the first day, reading cal_parms,
    without the outputs some example projects ship with (Ames_sub1 has an earlier run's)."""
    shutil.copytree(refdata / name, dest, ignore=lambda d, names: [n for n in names if OUTPUTS.match(n)])
    sim = dest / "time.sim"
    lines = sim.read_text().splitlines()
    f = lines[2].split()
    lines[2] = f"{0:>8}{first:>11}{0:>10}{last:>10}{f[4]:>10}"
    sim.write_text("\n".join(lines) + "\n")
    prt = dest / "print.prt"
    out = []
    for i, line in enumerate(prt.read_text().splitlines()):
        f = line.split()
        if i == 2:
            f[0] = "0"
            line = "  ".join(f)
        elif len(f) == 5 and all(x in ("y", "n") for x in f[1:]):
            on = "n" if f[0] in ("hru_cb", "hru_cb_vars") else "y"   # Ames: ~8.5 GB of soil carbon
            line = f"{f[0]:<24}" + "".join(f"{on:>14}" for _ in range(4))
        out.append(line)
    prt.write_text("\n".join(out) + "\n")
    if not (dest / "cal_parms.cal").exists():
        shutil.copy(cal_parms, dest / "cal_parms.cal")
    cio = dest / "file.cio"
    rows = cio.read_text().splitlines()
    for i, line in enumerate(rows):
        f = line.split()
        if f[:1] == ["chg"]:
            f[1] = "cal_parms.cal"
            f[2] = "calibration.cal"
            rows[i] = "".join(f"{x:<18}" for x in f)
    cio.write_text("\n".join(rows) + "\n")
    if not (dest / "calibration.cal").exists():
        (dest / "calibration.cal").write_text(
            "calibration.cal\n0\n NAME CHG_TYP VAL CONDS LYR1 LYR2 YEAR1 YEAR2 DAY1 DAY2 OBJ_TOT\n")


def add_change(run: Path, name: str, value: float) -> None:
    cal = run / "calibration.cal"
    lines = cal.read_text().splitlines()
    n = int(lines[1].split()[0])
    lines[1] = f"{n + 1}"
    lines.append(f"{name:<12}{'absval':<12}{value:>16.6E}" + "".join(f"{0:>8}" for _ in range(8)))
    cal.write_text("\n".join(lines) + "\n")


def run(exe: Path, d: Path) -> dict[str, str]:
    with open(d / "console.txt", "wb") as con:
        r = subprocess.run([str(exe)], cwd=d, stdout=con, stderr=subprocess.STDOUT, timeout=3600)
    if r.returncode != 0 or not (d / "success.fin").exists():
        return {"<failed>": str(r.returncode)}
    out = {}
    for p in sorted(d.iterdir()):
        if p.is_file() and p.name not in NOT_OUTPUT and p.suffix in (".txt", ".out", ".csv"):
            data = p.read_bytes()
            if p.name == "simulation.out":
                for rx in CLOCK:
                    data = rx.sub(b"<clock>", data)
            if p.name in UNSET:
                data = UNSET[p.name].sub(rb"\1 <unset> ", data)
            out[p.name] = hashlib.sha256(data).hexdigest()
    return out


def counts(proj: Path) -> dict[str, int]:
    """Objects of each calibration object type in a project (object.cnt and the data files)."""
    f = (proj / "object.cnt").read_text().splitlines()[2].split()
    cnt = dict(zip(["hru", "lhru", "rtu", "gwfl", "aqu", "cha", "res", "rec", "exco", "dlr", "can",
                    "pmp", "out", "lcha", "aqu2d", "hrd", "wro"], map(int, f[4:])))
    return {"hru": cnt["hru"], "sol": cnt["hru"], "lyr": cnt["hru"], "plt": cnt["hru"],
            "hlt": cnt["lhru"], "ru": cnt["rtu"], "aqu": cnt["aqu"], "cha": cnt["cha"],
            "sdc": cnt["lcha"], "rte": cnt["lcha"], "swq": cnt["lcha"], "res": cnt["res"],
            "rdt": cnt["res"], "bsn": 1, "pst": 0, "cli": 1}


STARTUP = ("SWAT+ uses it only while starting up ({}), before it applies calibration.cal, so the change "
           "reaches nothing")
# what reading SWAT+ 62.0.1's source found for changes the runs showed moving nothing, and for
# the plant rows, which cal_conditions handles itself
CODE = {
    "usle_p": STARTUP.format("hydro_init, the HRU's USLE factor"),
    "ovn": STARTUP.format("time_conc_init and plant_init, the time of concentration"),
    "slope_len": STARTUP.format("topohyd_init and time_conc_init"),
    "dis_stream": STARTUP.format("topohyd_init; its use in nut_nlch is commented out"),
    "field_len": STARTUP.format("topohyd_init and time_conc_init"),
    "field_ang": STARTUP.format("topohyd_init"),
    "adj_pkr": STARTUP.format("cli_initwgn"),
    "dorm_hr": STARTUP.format("cli_initwgn"),
    "ffcb": STARTUP.format("hydro_init, the initial soil water"),
    "cmn": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "nperco": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "pperco": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "phoskd": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "psp": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "rsdco": STARTUP.format("plantparm_init, which copies it into each plant"),
    "msk_co1": STARTUP.format("sd_hydsed_init, the channels' storage coefficients"),
    "msk_co2": STARTUP.format("sd_hydsed_init, the channels' storage coefficients"),
    "msk_x": STARTUP.format("sd_hydsed_init, the channels' storage coefficients"),
    "chn": STARTUP.format("sd_hydsed_init and sd_rating_curve: the rating curves are built from "
                          "it; docs/upstream-issue-calibration-chn.md"),
    "chk": STARTUP.format("sd_hydsed_init; ch_rtmusk and the other users route the old channels"),
    "fps": STARTUP.format("sd_hydsed_init and sd_rating_curve, the rating curves"),
    "fpn": STARTUP.format("sd_hydsed_init and sd_rating_curve, the rating curves"),
    "hc_kh": STARTUP.format("sd_hydsed_init"),
    "hc_hgt": STARTUP.format("sd_hydsed_init"),
    "hc_ini": STARTUP.format("sd_hydsed_init"),
    "evlai": "SWAT+ 62.0.1 never reads it after setting its default (basin_prm_default)",
    "init_slow": "only soil_nutcarb_init reads it, and SWAT+ 62.0.1 never calls soil_nutcarb_init",
    "init_passive": "only soil_nutcarb_init reads it, and SWAT+ 62.0.1 never calls soil_nutcarb_init",
    "d50": "SWAT+ sets every channel's d50 to 1.0 each day (sd_channel_sediment3.f90, line 80), so a "
           "change lasts until the first day's routing",
    "tile_latk": "cal_parm_select changes the tile's lag (hru%sdr%lag), not its lateral conductivity",
}
PLANT = ("a plant (plt) parameter: cal_conditions changes plants only for phu_mat, lai_pot, harv_idx "
         "and epco (epco changes lai_pot), through the HRUs, counting objects in plant-database rows; "
         "it has no code for {}")
NO_OBJECTS = "not checked: neither test project has {} objects"
QUIET = ("not checked: it moved no output on Osu_1hru or Ames_sub1, whose runs don't use it ({}); "
         "SWAT+ applies it where they would")


def summary(audit: dict) -> dict:
    out = {}
    first = next(iter(audit.values()))["params"]
    for name, row in first.items():
        typ = row["obj_typ"]
        runs = {p: a["params"][name] for p, a in audit.items()}
        has = [p for p, a in audit.items() if a["objects"].get(typ, 0) > 0]
        moved = sorted(p for p, r in runs.items() if r.get("files_changed"))
        if not row["case"]:
            v, why = "skipped", ("SWAT+ 62.0.1 reads it and has no code for it (no case in "
                                 "cal_parm_select), so nothing changes")
        elif typ == "plt":
            v, why = "refused", PLANT.format(name)
        elif name in CODE:
            v, why = "refused", CODE[name]
        elif not has:
            v, why = "not checked", NO_OBJECTS.format(typ)
        elif any(p in moved for p in has):
            v, why = "made", ""
        else:
            v, why = "not checked", QUIET.format(QUIET_WHY.get(name, "a process they don't run"))
        out[name] = {"obj_typ": typ, "verdict": v, "why": why, "moved_output_on": moved}
    return out


QUIET_WHY = {**{k: "no tile drains" for k in ("tile_dep", "tile_dtime", "tile_lag", "tile_rad", "tile_pump",
                                              "tile_dist", "tile_drain")},
             "crk": "no crack flow", "plaps": "no elevation bands", "tlaps": "no elevation bands",
             "field_wid": "no routing between HRUs", "evrch": "no old-style channels",
             "percop": "no pesticides", "tb_adj": "daily steps; the unit hydrographs are sub-daily",
             "manure_c_frac": "no manure", "bf_max": "a base flow "
             "that never reaches it", "carbon": "no channel carbon"}


def markdown(doc: dict) -> str:
    """The table for docs/parameters-62.0.1.md."""
    rows = sorted(doc["params"].items(), key=lambda kv: (["refused", "skipped", "not checked", "made"]
                                                          .index(kv[1]["verdict"]), kv[1]["obj_typ"], kv[0]))
    counts = {v: sum(1 for _, r in rows if r["verdict"] == v)
              for v in ("made", "not checked", "refused", "skipped")}
    out = ["# calibration.cal parameters in SWAT+ 62.0.1", "",
           "Generated by `tools/audit_parameters.py summary`; don't edit by hand. Every parameter in "
           "the SWAT+ editor's `cal_parms.cal`, tried alone on the test projects (stock SWAT+, absval "
           "at a point in its range, every object, everything printed daily), and SWAT+'s source read "
           "where a change moved nothing. swatplus-bmi refuses a `refused` or `skipped` change "
           "(`swatplus_bmi.calibration`) and notes a `not checked` one.", "",
           ", ".join(f"{n} {v}" for v, n in counts.items()) + f" of {len(rows)}.", "",
           "| Parameter | Objects | Verdict | Moved output on | Why |", "|---|---|---|---|---|"]
    for name, r in rows:
        out.append(f"| `{name}` | {r['obj_typ']} | {r['verdict']} | {', '.join(r['moved_output_on']) or '-'} "
                   f"| {r['why'] or '-'} |")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> None:
    if argv[:1] == ["summary"]:
        audit = json.loads(Path(argv[1]).read_text())
        doc = {"swat": "62.0.1",
               "about": "tools/audit_parameters.py: each parameter in the SWAT+ editor's cal_parms.cal "
                        "tried alone on the test projects, and SWAT+'s source read where it moved nothing",
               "projects": {p: a["objects"] for p, a in audit.items()},
               "params": summary(audit)}
        Path(argv[2]).write_text(json.dumps(doc, indent=1) + "\n")
        if len(argv) > 3:
            Path(argv[3]).write_text(markdown(doc))
        return
    if argv[:1] == ["run"]:
        argv = argv[1:]
    jobs = 2
    if "--jobs" in argv:
        i = argv.index("--jobs")
        jobs = int(argv[i + 1])
        del argv[i:i + 2]
    exe, refdata, src, out = (Path(a).resolve() for a in argv)
    cal_parms = refdata / "Osu_1hru" / "cal_parms.cal"
    known = cases(src)
    rows = parms(cal_parms)
    result = {}
    with tempfile.TemporaryDirectory(prefix="audit-") as tmp:
        for name, (first, last) in PROJECTS.items():
            clean = Path(tmp) / name / "_input"
            clean.parent.mkdir(parents=True)
            prepare(refdata, name, first, last, cal_parms, clean)
            base = clean.parent / "_base"
            shutil.copytree(clean, base)
            ref = run(exe, base)
            if "<failed>" in ref:
                raise SystemExit(f"{name}: the run without changes failed")
            # files that differ between two identical runs (unset memory in a project without
            # the objects they summarize) say nothing about a change: left out
            again = base.parent / "_again"
            shutil.copytree(clean, again)
            unstable = sorted(f for f, h in run(exe, again).items() if ref.get(f) != h)
            shutil.rmtree(again)
            for f in unstable:
                del ref[f]

            def one(row, clean=clean, ref=ref, unstable=unstable):
                p, typ, lo, hi = row
                d = clean.parent / p
                shutil.copytree(clean, d)
                v = lo + POINT * (hi - lo)
                add_change(d, p, v)
                got = run(exe, d)
                shutil.rmtree(d)
                if "<failed>" in got:
                    return p, {"obj_typ": typ, "value": v, "ran": False}
                moved = sorted(f for f in set(ref) | set(got)
                               if f not in unstable and ref.get(f) != got.get(f))
                return p, {"obj_typ": typ, "value": v, "ran": True, "files_changed": len(moved),
                           "example": moved[:3]}

            with ThreadPoolExecutor(jobs) as pool:
                params = dict(pool.map(one, rows))
            for p, typ, _, _ in rows:
                params[p]["case"] = p in known or typ in OWN_BRANCH
            result[name] = {"objects": counts(clean), "unstable": unstable, "params": params}
            print(f"{name}: {sum(1 for v in params.values() if v.get('files_changed'))} of "
                  f"{len(rows)} changes moved an output", flush=True)
    Path(out).write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
