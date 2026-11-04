"""Write a copy of SWAT+'s sources in which no local keeps its value between calls.

In Fortran a local variable with an initializer (``real :: rto = 0.``) is
implicitly SAVE: it is set once, at program start, and keeps whatever it
held at the end of the last call. SWAT+ initializes most locals this way,
so 5,379 locals in 537 procedures are technically state (62.0.1, see
state_inventory.py). This tool rewrites each such declaration into a plain
one and an assignment at the start of the procedure's executable part, so
the value is set on every call instead:

    real :: rto = 0.        ->      real :: rto
    ...                             ...
                                    rto = 0.          (first executable statement)

Built and run on the test projects, the copy shows whether any of these
locals carries state SWAT+ relies on: if its output is identical to stock,
none does on the code paths those projects run. Locals with an explicit
SAVE, pointers, parameters and module variables are left alone.

    python tools/reset_saved_locals.py <SWAT+ src/> <output src/>

Only for this experiment; the output is never meant to be committed.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from state_inventory import DECL, parse_decl, split_top, strip_comment  # noqa: E402

SPEC = re.compile(r"^(use|implicit|external|intrinsic|parameter|dimension|data|save|common|equivalence|"
                  r"include|namelist|import|format|procedure\s*\(|enum)\b", re.I)
DECL_WORD = re.compile(r"^\s*(integer|real|double\s*precision|logical|character|complex)\b"
                       r"(?=\s*[\*\(,:]|\s+\w)|^\s*(type|class)\s*\(", re.I)
UNIT = re.compile(r"^\s*(?:(?:pure|elemental|recursive|module)\s+)*(?:(?:integer|real|logical|character|"
                  r"double\s+precision)(?:\s*\([^)]*\))?\s+)?(subroutine|function|program)\s+(\w+)", re.I)
END_UNIT = re.compile(r"^\s*end\s*(subroutine|function|program)\b|^\s*end\s*$", re.I)


def statements(lines):
    """Group physical lines into statements: (first index, last index, joined text)."""
    i = 0
    while i < len(lines):
        start, buf = i, ""
        while True:
            line = strip_comment(lines[i]).rstrip()
            if buf and not line.strip() and i + 1 < len(lines):
                i += 1                     # a comment or blank line inside a continued statement
                continue
            if buf:
                line = line.lstrip()
                if line.startswith("&"):
                    line = line[1:]
            if line.endswith("&"):
                buf += line[:-1]
                i += 1
                continue
            buf += line
            break
        yield start, i, buf
        i += 1


def rewrite_decl(text):
    """A declaration without initializers, and the assignments that replace them; None to leave it."""
    if "::" not in text:
        return None
    parsed = parse_decl(text)
    if parsed is None:
        return None
    base, attrs, names = parsed
    if any(a in ("parameter", "save", "pointer") or a.startswith("intent(") for a in attrs):
        return None
    if not any(init is not None for _, _, init in names):
        return None
    head = text.split("::", 1)[0].strip()
    items, assigns = [], []
    for item in split_top(text.split("::", 1)[1]):
        m = re.match(r"\s*(\w+)\s*(\([^=]*\))?\s*(=>?)?\s*(.*)$", item)
        name, dims, op, init = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        if op == "=>":                                   # pointer initialization: leave the statement
            return None
        items.append(name + dims)
        if op == "=":
            assigns.append(f"{name} = {init.strip()}")
    return f"{head} :: {', '.join(items)}", assigns


def rewrite_file(src: Path, dst: Path):
    lines = src.read_text(errors="replace").splitlines()
    out = []
    stack = []            # open procedures: dict(spec=bool, pending=[assignments])
    in_module_spec = False
    block = 0             # inside an interface or a derived-type definition
    changed = 0
    for first, last, text in statements(lines):
        low = text.strip().lower()
        phys = lines[first:last + 1]
        if not low or low.startswith("#"):
            out.extend(phys)
            continue
        if re.match(r"(abstract\s+)?interface\b", low) or (
                re.match(r"type\b(?!\s*\()", low) and not re.match(r"type\s+is\b", low)):
            block += 1
            out.extend(phys)
            continue
        if re.match(r"end\s*(interface|type)\b", low):
            block -= 1
            out.extend(phys)
            continue
        if block:
            out.extend(phys)
            continue
        if re.match(r"module\s+(?!procedure)\w+\s*$", low):
            in_module_spec = True
            out.extend(phys)
            continue
        if re.match(r"end\s*module\b", low):
            in_module_spec = False
            out.extend(phys)
            continue
        if low.startswith("contains"):
            in_module_spec = False
            if stack and stack[-1]["spec"]:
                out.extend(stack[-1]["pending"])
                stack[-1]["spec"] = False
            out.extend(phys)
            continue
        if UNIT.match(text) and not low.startswith("end"):
            stack.append({"spec": True, "pending": []})
            out.extend(phys)
            continue
        if END_UNIT.match(text):
            if stack:
                top = stack.pop()
                if top["spec"]:
                    out.extend(top["pending"])
            out.extend(phys)
            continue
        if not stack or in_module_spec:
            out.extend(phys)
            continue
        top = stack[-1]
        if top["spec"]:
            if DECL_WORD.match(text) and DECL.match(text):
                r = rewrite_decl(text)
                if r is None:
                    out.extend(phys)
                else:
                    decl, assigns = r
                    indent = re.match(r"\s*", phys[0]).group(0)
                    out.append(indent + decl)
                    top["pending"].extend(indent + a for a in assigns)
                    changed += len(assigns)
                continue
            if SPEC.match(low):
                out.extend(phys)
                continue
            # the first executable statement: set the locals here
            out.extend(top["pending"])
            top["spec"] = False
        out.extend(phys)
    dst.write_text("\n".join(out) + "\n")
    return changed


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    total = files = 0
    for f in sorted(src.glob("*.f90")):
        n = rewrite_file(f, dst / f.name)
        if n:
            total += n
            files += 1
    print(f"reset {total} saved locals in {files} files")


if __name__ == "__main__":
    main()
