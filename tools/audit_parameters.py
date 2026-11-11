"""Which calibration.cal changes SWAT+ actually makes: every parameter, tried one at a time.

For each parameter in the SWAT+ editor's cal_parms.cal, as 2.2.0 wrote it
(Osu_1hru's) and as 3.1.0 has it (unchanged through 4.0.0; editor_3_1), two
kinds of evidence:

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

``run`` writes {project: {"objects": {obj_typ: count}, "params": {"name/obj_typ": {...}}}};
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


def editor_3_1(rows: list[tuple[str, str, float, float]]) -> list[tuple[str, str, float, float]]:
    """The SWAT+ editor's cal_parms.cal as of 3.1.0 (unchanged through 4.0.0), from its 2.2.0 list
    (Osu_1hru's) and the changes its update code makes for 2.3.0, 3.0.0 and 3.1.0
    (swatplus-editor, src/api/actions/update_datasets.py)."""
    have = {r[0]: list(r) for r in rows}

    def drop(*names):
        for n in names:
            have.pop(n, None)

    def add(name, typ, lo, hi):
        have.setdefault(name, [name, typ, lo, hi])

    def rng(name, lo=None, hi=None):
        if name in have:
            have[name][2] = have[name][2] if lo is None else lo
            have[name][3] = have[name][3] if hi is None else hi

    def rename(old, new, lo=None, hi=None):
        if old in have:
            have[new] = [new, have[old][1], have[old][2], have[old][3]]
            del have[old]
            rng(new, lo, hi)

    # 2.3.0
    drop("wd_rto")
    rng("petco", 0.7, 1.3)
    for r in (("cbn_init", "aqu", 0, 20), ("dep_bot", "aqu", 1, 50), ("dep_wt_init", "aqu", 0, 50),
              ("flo_dist", "aqu", 0, 300), ("flo_init_mm", "aqu", 0, 5), ("hlife_n", "aqu", 0, 365),
              ("minp_init", "aqu", 0, 10), ("no3_init", "aqu", 0, 30), ("phu_mat", "plt", 50, 6000),
              ("lai_pot", "plt", 0.01, 12), ("harv_idx", "plt", 0.01, 0.95),
              ("drawdown_days", "rdt", 0.05, 5000), ("withdrawal_rate", "rdt", 0, 100000)):
        add(*r)
    # 3.0.0
    add("nperco_lchtile", "bsn", 0, 1)
    drop("spcon", "spexp")
    rename("bedldcoef", "bankfull_flo", 0.5, 1.5)
    rename("chseq", "flood_sedfrac", 0.1, 0.9)
    rng("d50", lo=0.00001)
    rng("petco", 0.8, 1.2)
    rng("phoskd", hi=400)
    rng("pperco", 5, 20)
    rng("pst_solub", hi=11000000)
    rng("rs2", hi=0.5)
    rng("rs3", hi=2)
    rename("withdrawal_rate", "withdraw_rate")
    for r in (("arc_len_fr", "rte", 0.5, 2), ("ch_n_conc", "rte", 0, 500), ("ch_p_bio", "rte", 0, 1),
              ("ch_p_conc", "rte", 0, 50.9), ("fp_inun_days", "rte", 0.05, 30), ("hum_c_n", "sol", 0, 20),
              ("hum_c_p", "sol", 0, 160), ("lab_p", "sol", 0, 30), ("n_dep_enr", "rte", 0.2, 1),
              ("n_setl", "rte", 0.05, 0.9), ("n_sol_part", "rte", 0.001, 0.1), ("p_dep_enr", "rte", 0.2, 1),
              ("p_setl", "rte", 0.05, 0.9), ("p_sol_part", "rte", 0.001, 0.1),
              ("part_size", "rte", 0.001, 0.01), ("pk_rto", "rte", 1, 3), ("sed_stlr", "res", 0.1, 2),
              ("velsetlr", "res", 0.1, 15), ("wash_bed_fr", "rte", 0, 0.8), ("aquifer_K", "gwf", 0.0001, 40),
              ("aquifer_Sy", "gwf", 0.001, 0.6), ("aquifer_delay", "hru", 0, 1000),
              ("aquifer_exdp", "gwf", 0, 4), ("stream_K", "gwf_riv", 0.0000001, 0.01),
              ("stream_thk", "gwf_riv", 0.01, 2), ("stream_bed", "gwf_sgl", 0, 20)):
        add(*r)
    # 3.1.0
    drop("rch_dox", "rch_cbod", "algae", "organicn", "ammonian", "nitriten", "organicp", "disolvp")
    for r in (("bank_exp", "rte", 1.5, 6), ("mumax", "swq", 1, 3), ("res_d50", "res", 0.1, 1000),
              ("rsd_covco", "bsn", 0.001, 1.25), ("usle_c", "plt", 0.001, 1.95), ("vcr_coef", "rte", 0.5, 2)):
        add(*r)
    for n, lo in (("cherod", 0.0001), ("chk", 0.00001), ("chl", 0.001), ("chn", 0.001), ("chs", 0.0001),
                  ("cov", 0.001), ("sp_yld", 0.0001), ("stream_K", 0.0000001)):
        rng(n, lo=lo)
    rng("prf", 250, 700)
    for n in ("harv_idx", "lai_pot", "phu_mat"):
        have[n][1] = "hru"
    return [tuple(r) for r in have.values()]


def write_parms(rows, path: Path) -> None:
    out = ["cal_parms.cal: written by swatplus-bmi's tools/audit_parameters.py", f"{len(rows)}",
           "name                       obj_typ       abs_min       abs_max             units"]
    out += [f"{n:<25}{t:>10}{lo:>14.7g}{hi:>14.7g}{'null':>18}" for n, t, lo, hi in rows]
    path.write_text("\n".join(out) + "\n")


# the editor's lists the audit covers; a parameter in both is tried with the newer one's range
LISTS = ("2.2.0", "3.1.0")


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
NEVER = "nothing in SWAT+ 62.0.1 reads it after its file is read ({})"
OVERWRITTEN = ("soil_nutcarb_init runs after calibration.cal (main.f90.in, line 126) and sets each layer's {} "
               "again from the soil's inputs, so the change is overwritten")
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
    "d50": "SWAT+ sets every channel's d50 to 1.0 each day (sd_channel_sediment3.f90, line 80), so a "
           "change lasts until the first day's routing",
    "tile_latk": "cal_parm_select changes the tile's lag (hru%sdr%lag), not its lateral conductivity",
    # editor 3.x's parameters
    "nperco_lchtile": STARTUP.format("hrudb_init, which copies it into each HRU"),
    "cbn_init": STARTUP.format("aqu_initial, which sets the aquifer's starting state from it"),
    "dep_wt_init": STARTUP.format("aqu_initial, which sets the aquifer's starting state from it"),
    "flo_init_mm": STARTUP.format("aqu_initial, which sets the aquifer's starting state from it"),
    "chss": STARTUP.format("sd_hydsed_init and sd_rating_curve, the rating curves"),
    "n_setl": STARTUP.format("sd_hydsed_init"),
    "p_setl": STARTUP.format("sd_hydsed_init"),
    "bank_exp": STARTUP.format("sd_hydsed_init"),
    "rs6": NEVER.format("ch_read_nut"),
    "rs7": NEVER.format("ch_read_nut"),
    "rk6": NEVER.format("ch_read_nut"),
    "flo_dist": NEVER.format("aqu_read"),
    "lab_p": OVERWRITTEN.format("labile P"),
    "hum_c_n": OVERWRITTEN.format("active humus N"),
    "hum_c_p": OVERWRITTEN.format("active humus P"),
    "cbn": OVERWRITTEN.format("total carbon"),
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
    for key, row in first.items():
        name, typ = row["name"], row["obj_typ"]
        runs = {p: a["params"][key] for p, a in audit.items()}
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
        out[key] = {"name": name, "obj_typ": typ, "verdict": v, "why": why, "moved_output_on": moved,
                    "in_lists": row["in_lists"]}
    return out


QUIET_WHY = {**{k: "no tile drains" for k in ("tile_dep", "tile_dtime", "tile_lag", "tile_rad", "tile_pump",
                                              "tile_dist", "tile_drain")},
             "crk": "no crack flow", "plaps": "no elevation bands", "tlaps": "no elevation bands",
             "field_wid": "no routing between HRUs", "evrch": "no old-style channels",
             "percop": "no pesticides", "tb_adj": "daily steps; the unit hydrographs are sub-daily",
             "manure_c_frac": "no manure", "aquifer_delay": "SWAT+'s gwflow, the only thing that uses it",
             "init_slow": "the slow humus pool it sets at start-up; nothing printed moved in three years",
             "init_passive": "the passive humus pool it sets at start-up; nothing printed moved in three years",
             "bf_max": "a base flow "
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
           "| Parameter | Objects | Editor lists | Verdict | Moved output on | Why |",
           "|---|---|---|---|---|---|"]
    for _, r in rows:
        out.append(f"| `{r['name']}` | {r['obj_typ']} | {', '.join(r['in_lists'])} | {r['verdict']} "
                   f"| {', '.join(r['moved_output_on']) or '-'} | {r['why'] or '-'} |")
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
    known = cases(src)
    older = parms(refdata / "Osu_1hru" / "cal_parms.cal")
    lists = {"2.2.0": older, "3.1.0": editor_3_1(older)}
    # every (name, object type) in either list, tried with the newest list that has it
    items = {}
    for ver in LISTS:
        for row in lists[ver]:
            items[(row[0], row[1])] = (ver, row)
    result = {}
    with tempfile.TemporaryDirectory(prefix="audit-") as tmp:
        for name, (first, last) in PROJECTS.items():
            clean = Path(tmp) / name / "_input"
            clean.parent.mkdir(parents=True)
            prepare(refdata, name, first, last, refdata / "Osu_1hru" / "cal_parms.cal", clean)
            refs, unstable = {}, set()
            for ver in LISTS:
                # a baseline for each list: the project's own calibration.cal rows are clamped
                # to that list's ranges (Osu_1hru's petco, for one)
                write_parms(lists[ver], clean / f"cal_parms-{ver}.cal")
                pair = []
                for k in ("a", "b"):
                    d = clean.parent / f"_base-{ver}-{k}"
                    shutil.copytree(clean, d)
                    shutil.copy(d / f"cal_parms-{ver}.cal", d / "cal_parms.cal")
                    pair.append(run(exe, d))
                    shutil.rmtree(d)
                if "<failed>" in pair[0]:
                    raise SystemExit(f"{name}: the run without changes failed ({ver} list)")
                # files that differ between two identical runs say nothing about a change
                unstable |= {f for f, h in pair[1].items() if pair[0].get(f) != h}
                refs[ver] = pair[0]

            def one(item, clean=clean, refs=refs, unstable=unstable):
                (p, typ), (ver, (_, _, lo, hi)) = item
                d = clean.parent / f"{p}-{typ}"
                shutil.copytree(clean, d)
                shutil.copy(d / f"cal_parms-{ver}.cal", d / "cal_parms.cal")
                v = lo + POINT * (hi - lo)
                add_change(d, p, v)
                got = run(exe, d)
                shutil.rmtree(d)
                key = f"{p}/{typ}"
                res = {"name": p, "obj_typ": typ, "list": ver, "value": v,
                       "in_lists": [w for w in LISTS if any(r[:2] == (p, typ) for r in lists[w])]}
                if "<failed>" in got:
                    return key, {**res, "ran": False}
                ref = refs[ver]
                moved = sorted(f for f in set(ref) | set(got)
                               if f not in unstable and ref.get(f) != got.get(f))
                return key, {**res, "ran": True, "files_changed": len(moved), "example": moved[:3]}

            with ThreadPoolExecutor(jobs) as pool:
                params = dict(pool.map(one, items.items()))
            for key, r in params.items():
                r["case"] = r["name"] in known or r["obj_typ"] in OWN_BRANCH
            result[name] = {"objects": counts(clean), "unstable": sorted(unstable), "params": params}
            print(f"{name}: {sum(1 for v in params.values() if v.get('files_changed'))} of "
                  f"{len(items)} changes moved an output", flush=True)
    Path(out).write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
