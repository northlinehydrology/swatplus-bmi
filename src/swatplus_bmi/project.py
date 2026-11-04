"""Small edits to a SWAT+ project's input files.

``set_print`` turns SWAT+'s output on for named objects. It exists because
``print.prt`` is easy to get wrong: unless its ``use_obj_labels`` switch is
``y`` (the second value on the line under ``csvout``, which some projects
label ``dbout``), SWAT+ reads the object rows by position, in its own order,
and ignores their names. Editing the row named ``aquifer`` can then switch
on a different object. ``set_print`` turns labels on and edits rows by name.
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
