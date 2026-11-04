"""Inventory of SWAT+'s state, the first step toward a generated serializer.

Reads the Fortran sources of a SWAT+ release and lists what a save_state
would have to write: every module-level variable (with its type, rank and
whether it is allocatable, a pointer or of a derived type), every derived
type and its components, and every local variable that keeps its value
between calls (a local with an initializer is implicitly SAVE in Fortran).

This is a parser for the declarations SWAT+ actually uses, not for all of
Fortran; anything it can't read is listed under "unparsed" so nothing is
dropped silently. The serializer generator (docs/design.md, "Saving state")
is meant to be built on this and rerun for each SWAT+ release.

    python tools/state_inventory.py build/62.0.1/swatplus/src [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

DECL = re.compile(r"^\s*(integer|real|double\s+precision|logical|character|complex|type\s*\(|class\s*\()",
                  re.I)
UNIT_START = re.compile(r"^\s*(?:(?:pure|elemental|recursive|module)\s+)*"
                        r"(subroutine|function|program)\s+(\w+)", re.I)
TYPED_FUNCTION = re.compile(r"^\s*(?:integer|real|logical|character|double\s+precision)\b.*\bfunction\s+(\w+)",
                            re.I)


def logical_lines(text: str):
    """Free-form source as whole statements: comments dropped, & continuations joined."""
    buf = ""
    for raw in text.splitlines():
        line = strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if buf:
            line = line.lstrip()
            if line.startswith("&"):
                line = line[1:]
        if line.endswith("&"):
            buf += line[:-1]
            continue
        yield buf + line
        buf = ""
    if buf:
        yield buf


def strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "!":
            return line[:i]
    return line


def split_top(s: str, sep: str = ","):
    """Split on sep outside parentheses, brackets and quotes."""
    out, depth, quote, cur = [], 0, None, ""
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


def parse_decl(stmt: str):
    """'real, dimension(:), allocatable :: a, b = 0.' -> (base type, attributes, [(name, dims, init)])."""
    if "::" in stmt:
        head, names = stmt.split("::", 1)
    else:                                         # old style: 'integer i, j'
        m = re.match(r"\s*(\w+(?:\s*\*\s*\d+)?(?:\s*\([^)]*\))?)\s+(.*)", stmt)
        if not m:
            return None
        head, names = m.group(1), m.group(2)
    parts = split_top(head)
    base, attrs = parts[0].lower().replace(" ", ""), [a.lower().replace(" ", "") for a in parts[1:]]
    out = []
    for item in split_top(names):
        init = None
        if "=" in item and not item.lstrip().startswith("="):
            item, init = item.split("=", 1)
            item = item.rstrip("=>").strip()
        m = re.match(r"\s*(\w+)\s*(\(.*\))?", item)
        if m:
            out.append((m.group(1).lower(), m.group(2), init.strip() if init else None))
    return base, attrs, out


def rank_of(dims, attrs):
    d = dims
    for a in attrs:
        if a.startswith("dimension("):
            d = a[len("dimension"):]
    if not d:
        return 0
    return len(split_top(d.strip()[1:-1]))


def inventory(src: Path):
    modules, types, saved_locals, unparsed = {}, {}, [], []
    for path in sorted(src.glob("*.f90")):
        where = None                              # ("module", name) or ("unit", name)
        in_type = None
        in_interface = 0
        for stmt in logical_lines(path.read_text(errors="replace")):
            low = stmt.strip().lower()
            if re.match(r"(abstract\s+)?interface\b", low):
                in_interface += 1
                continue
            if re.match(r"end\s*interface\b", low):
                in_interface -= 1
                continue
            if in_interface:
                continue
            m = re.match(r"module\s+(\w+)\s*$", low)
            if m and not low.startswith("module procedure"):
                where = ("module", m.group(1))
                modules.setdefault(m.group(1), {"file": path.name, "vars": []})
                continue
            if re.match(r"contains\b", low) and where and where[0] == "module":
                where = ("contains", where[1])
                continue
            if re.match(r"end\s*module\b", low):
                where = None
                continue
            m = UNIT_START.match(stmt) or TYPED_FUNCTION.match(stmt)
            if m and not re.match(r"\s*end\b", low):
                where = ("unit", m.group(m.lastindex), path.name)
                continue
            m = re.match(r"type\s*(?:,\s*[\w(), ]+)?(?:::)?\s*(\w+)\s*$", low)
            if m and not low.startswith("type("):
                in_type = m.group(1)
                types[in_type] = {"file": path.name, "components": []}
                continue
            if in_type and re.match(r"end\s*type\b", low):
                in_type = None
                continue
            if not DECL.match(stmt):
                continue
            if re.match(r"\s*type\s*\(", stmt, re.I) is None and re.search(r"\bfunction\b", low):
                continue
            parsed = parse_decl(stmt)
            if parsed is None:
                unparsed.append(f"{path.name}: {stmt.strip()[:100]}")
                continue
            base, attrs, names = parsed
            for name, dims, init in names:
                rec = {"name": name, "type": base, "rank": rank_of(dims, attrs),
                       "allocatable": "allocatable" in attrs, "pointer": "pointer" in attrs,
                       "parameter": "parameter" in attrs, "derived": base.startswith("type("),
                       "init": init is not None}
                if in_type:
                    types[in_type]["components"].append(rec)
                elif where and where[0] == "module":
                    if not rec["parameter"]:
                        modules[where[1]]["vars"].append(rec)
                elif where and where[0] == "unit":
                    if rec["parameter"] or "intent(" in " ".join(attrs):
                        continue
                    if init is not None or "save" in attrs:
                        saved_locals.append({"unit": where[1], "file": where[2], **rec})
    return modules, types, saved_locals, unparsed


def summary(modules, types, saved_locals, unparsed):
    mvars = [v for m in modules.values() for v in m["vars"]]
    alloc_types = {t for t, d in types.items() if any(c["allocatable"] for c in d["components"])}
    ptr_types = {t for t, d in types.items() if any(c["pointer"] for c in d["components"])}
    return {
        "modules": len(modules),
        "modules_with_state": sum(1 for m in modules.values() if m["vars"]),
        "module_variables": len(mvars),
        "allocatable_module_variables": sum(v["allocatable"] for v in mvars),
        "pointer_module_variables": sum(v["pointer"] for v in mvars),
        "derived_type_module_variables": sum(v["derived"] for v in mvars),
        "derived_types": len(types),
        "types_with_allocatable_components": len(alloc_types),
        "types_with_pointer_components": len(ptr_types),
        "saved_locals": len(saved_locals),
        "units_with_saved_locals": len({(s["file"], s["unit"]) for s in saved_locals}),
        "unparsed_declarations": len(unparsed),
        "module_variable_types": Counter(v["type"].split("(")[0] for v in mvars).most_common(),
        "pointers": sorted(f"{m}::{v['name']}" for m, d in modules.items() for v in d["vars"] if v["pointer"]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path, help="SWAT+ src/ folder")
    ap.add_argument("--json", type=Path, help="write the full inventory here")
    a = ap.parse_args()
    modules, types, saved_locals, unparsed = inventory(a.src)
    s = summary(modules, types, saved_locals, unparsed)
    for k, v in s.items():
        if isinstance(v, list) and len(v) > 12:
            print(f"{k}: {len(v)} ({', '.join(map(str, v[:12]))}, ...)")
        else:
            print(f"{k}: {v}")
    if a.json:
        a.json.write_text(json.dumps({"summary": s, "modules": modules, "types": types,
                                      "saved_locals": saved_locals, "unparsed": unparsed}, indent=1))


if __name__ == "__main__":
    main()
