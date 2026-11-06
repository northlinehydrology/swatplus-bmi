"""Check that the exchange build compiles SWAT+ exactly as the stock build does.

Every SWAT+ source file the exchange build leaves unchanged must compile to
the same object file, byte for byte, in both builds; build.sh maps each
build's source folder to one name so the objects can be compared whole. The
files the exchange build changes (the patched ones and the ones whose saved
locals move to a module) are listed, and must be exactly the ones expected.

    python tools/build_parity.py build/62.0.1 [--expect a.f90,b.f90,...]
    python tools/build_parity.py --expected 62.0.1    # the files patches/62.0.1 changes

Exit status 1 on any difference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def expected(patch: str) -> list[str]:
    """The SWAT+ sources a release's patch changes: the patched ones and the ones whose
    saved locals tools/carried_locals.py moves into a module."""
    import re
    import tomllib
    d = ROOT / "patches" / patch
    files = set()
    for p in d.glob("*.patch"):
        files |= {m.removesuffix(".in") for m in
                  re.findall(r"^\+\+\+ b/src/(\S+\.f90(?:\.in)?)", p.read_text(), re.M)}
    locals_ = d / "carried-locals.toml"
    if locals_.exists():
        files |= {x["file"] for x in tomllib.loads(locals_.read_text()).get("local", [])}
    return sorted(files)


def objects(build: Path) -> dict[str, Path]:
    found = {}
    for o in build.glob("CMakeFiles/*.dir/src/*.f90.o"):
        found[o.name[:-2]] = o
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=Path, nargs="?", help="build.sh's work folder for the tag, e.g. build/62.0.1")
    ap.add_argument("--expect", default="", help="comma-separated sources the exchange build changes")
    ap.add_argument("--expected", metavar="PATCH", help="print the sources a release's patch changes")
    a = ap.parse_args(argv)
    if a.expected:
        print(",".join(expected(a.expected)))
        return 0
    if a.work is None:
        ap.error("give the work folder, or --expected")
    src_s, src_x = a.work / "stock-src" / "src", a.work / "exchange-src" / "src"
    obj_s, obj_x = objects(a.work / "stock-build"), objects(a.work / "exchange-build")
    if not obj_s or not obj_x:
        print(f"no objects under {a.work}: build with ./build.sh --stock first", file=sys.stderr)
        return 1

    def source(d: Path, f: str) -> Path | None:       # main.f90 is configured from main.f90.in
        for cand in (d / f, d / (f + ".in")):
            if cand.exists():
                return cand
        return None

    changed, bad = [], []
    for f in sorted(set(obj_s) | {p.name.removesuffix(".in") for p in src_s.glob("*.f90*")}):
        a_src, b_src = source(src_s, f), source(src_x, f)
        if a_src is None:
            continue                                    # not a SWAT+ source
        if b_src is None:
            bad.append(f"{f}: missing from the exchange build")
            continue
        if a_src.read_bytes() != b_src.read_bytes():
            changed.append(f)
            continue
        if f not in obj_s or f not in obj_x:
            bad.append(f"{f}: no object in one of the builds")
        elif obj_s[f].read_bytes() != obj_x[f].read_bytes():
            bad.append(f"{f}: unchanged source, different object")
    added = sorted(p.name for p in src_x.glob("*.f90") if source(src_s, p.name) is None)

    print(f"{len(obj_s)} stock objects; {len(changed)} sources changed: {', '.join(changed)}")
    print(f"added: {', '.join(added)}")
    if a.expect:
        want = sorted(x.strip() for x in a.expect.split(",") if x.strip())
        if changed != want:
            bad.append(f"changed sources {changed}, expected {want}")
    for b in bad:
        print("FAIL", b)
    if not bad:
        print("every unchanged source compiles to the same object in both builds")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
