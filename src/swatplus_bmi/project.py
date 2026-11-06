"""Small edits to a SWAT+ project's input files, and checks that only read.

``set_print`` turns SWAT+'s output on for named objects. It exists because
``print.prt`` is easy to get wrong: unless its ``use_obj_labels`` switch is
``y`` (the second value on the line under ``csvout``, which some projects
label ``dbout``), SWAT+ reads the object rows by position, in its own order,
and ignores their names. Editing the row named ``aquifer`` can then switch
on a different object. ``set_print`` turns labels on and edits rows by name.

``check_calibration`` and ``check_soils`` look for inputs SWAT+ 62.0.1
misreads or ignores without a message. They change nothing: SWAT+ runs as it
would anyway, and each returns what it found, one line per problem.
"""

from __future__ import annotations

from pathlib import Path

INTERVALS = ("daily", "monthly", "yearly", "avann")


def set_print(project: str | Path, objects, intervals=("daily",), on: bool = True,
              skip_years: int | None = None) -> None:
    """Turn SWAT+ output on (or off) for ``objects`` at ``intervals``.

    ``objects`` are row names in print.prt (``"channel_sd"``, ``"aquifer"``,
    ``"water_allo"``, ...); ``intervals`` any of daily, monthly, yearly,
    avann. ``skip_years`` sets nyskip, the years printed outputs leave out.
    Other rows are left as they are. The file is edited in place.
    """
    prt = Path(project) / "print.prt"
    lines = prt.read_text().splitlines()
    names = {o.lower() for o in objects}
    bad = [i for i in intervals if i not in INTERVALS]
    if bad:
        raise ValueError(f"unknown intervals {bad}; use {INTERVALS}")
    cols = [INTERVALS.index(i) + 1 for i in intervals]

    head = next((i for i, l in enumerate(lines) if l.split()[:1] == ["csvout"]), None)
    rows = next((i for i, l in enumerate(lines) if l.split()[:1] == ["objects"]), None)
    if head is None or rows is None:
        raise ValueError(f"{prt} doesn't look like a SWAT+ print.prt")
    f = lines[head + 1].split()
    f[1] = "y"                                            # use_obj_labels: rows are read by name
    lines[head + 1] = "  ".join(f)
    if skip_years is not None:
        f = lines[2].split()
        f[0] = str(int(skip_years))
        lines[2] = "  ".join(f)

    found = set()
    for i in range(rows + 1, len(lines)):
        f = lines[i].split()
        if len(f) != 5 or f[0].lower() not in names:
            continue
        for c in cols:
            f[c] = "y" if on else "n"
        lines[i] = f"{f[0]:<24}" + "".join(f"{v:>14}" for v in f[1:])
        found.add(f[0].lower())
    missing = names - found
    if missing:
        raise ValueError(f"no rows named {sorted(missing)} in {prt}")
    prt.write_text("\n".join(lines) + "\n")


def _cio(project: Path, key: str) -> list[str]:
    for line in (project / "file.cio").read_text().splitlines():
        f = line.split()
        if f[:1] == [key]:
            return f[1:]
    return []


def check_calibration(project: str | Path) -> list[str]:
    """Changes in calibration.cal that SWAT+ won't make, with no message.

    SWAT+ makes a change only if its name is a row of cal_parms.cal, and
    skips any other. It also builds the channel rating curves before it
    applies calibration.cal, so a change to channel n (``chn``) never reaches
    the routing.
    """
    project = Path(project)
    chg = _cio(project, "chg")
    parms = project / (chg[0] if chg and chg[0] != "null" else "cal_parms.cal")
    upd = project / (chg[1] if len(chg) > 1 and chg[1] != "null" else "calibration.cal")
    if not upd.exists():
        return []
    known = set()
    if parms.exists():
        rows = parms.read_text().splitlines()[3:]
        known = {r.split()[0] for r in rows if r.split()}
    out = []
    rows = upd.read_text().splitlines()
    i, n = 3, int(rows[1].split()[0])
    for _ in range(n):
        while i < len(rows) and not rows[i].split():
            i += 1
        if i >= len(rows):
            break
        f = rows[i].split()
        name, conds = f[0], int(float(f[3])) if len(f) > 3 else 0
        if name not in known:
            out.append(f"{upd.name}: {name} isn't in {parms.name}, so SWAT+ skips it")
        elif name == "chn":
            out.append(f"{upd.name}: chn has no effect on routing; SWAT+ builds the channel rating "
                       "curves before it applies calibration.cal")
        i += 1 + conds                                    # condition lines follow the change
    return out


def check_soils(run_dir: str | Path) -> list[str]:
    """After a run: each soil an HRU uses, as SWAT+ read it, against soils.sol.

    SWAT+ reads soils.sol list-directed, so a field left blank (a texture,
    say) takes the next value and every soil after it is misread, with no
    message. checker.out lists the soils as SWAT+ read them: name,
    hydrologic group and depth of the deepest layer. Every soil named in
    hru-data.hru should be there with the values in soils.sol.
    """
    run = Path(run_dir)
    sol = run / (_cio(run, "soils") or ["soils.sol"])[0]
    hru = run / (_cio(run, "hru") or ["hru-data.hru"])[0]
    checker = run / "checker.out"
    if not checker.exists():
        return ["checker.out is missing: run SWAT+ first"]

    given, lines = {}, sol.read_text().splitlines()[2:]
    i = 0
    while i < len(lines):
        f = lines[i].split()
        if len(f) >= 3 and not _number(f[0]) and f[1].isdigit():
            nly = int(f[1])
            layers = [l.split() for l in lines[i + 1:i + 1 + nly]]
            given[f[0]] = (f[2], float(layers[-1][0]) if layers and layers[-1] else float("nan"))
            i += 1 + nly
        else:
            i += 1
    read = {}
    for line in checker.read_text().splitlines()[3:]:
        f = line.split()
        if len(f) >= 3 and _number(f[2]):
            read.setdefault(f[0], (f[1], float(f[2])))
    used = []
    rows = hru.read_text().splitlines()
    col = rows[1].split().index("soil")
    for r in rows[2:]:
        f = r.split()
        if len(f) > col and f[col] not in used:
            used.append(f[col])

    out = []
    for name in used:
        if name not in given:
            out.append(f"{name}: in {hru.name} but not in {sol.name}")
        elif name not in read:
            out.append(f"{name}: not in checker.out as SWAT+ read it; {sol.name} may be misread "
                       "before it (a blank field?)")
        elif read[name][0] != given[name][0] or abs(read[name][1] - given[name][1]) > 1e-3:
            out.append(f"{name}: SWAT+ read hydrologic group {read[name][0]}, depth {read[name][1]} "
                       f"mm; {sol.name} gives {given[name][0]}, {given[name][1]} mm")
    return out


def _number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
