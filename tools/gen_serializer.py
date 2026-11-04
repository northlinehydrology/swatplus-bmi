"""Generate bmi_state_module.f90: save and load all of SWAT+'s module state.

Rerun for each SWAT+ release (build.sh does); never edit the output by hand.
Reads every module in SWAT+'s src/ and writes, for every module variable
that isn't a parameter or a pointer, Fortran that writes it to an
unformatted stream and reads it back:

- intrinsic scalars and fixed-size arrays: one write;
- allocatable arrays: allocated or not, bounds, then the values;
- derived types: one write and one read routine per type, recursing into
  components the same way.

Each module variable is preceded by a tag naming it, so a file written by
a different build is caught at the first variable that doesn't match.
Pointers are left out (7 in 62.0.1); SWAT+ points them at a table entry
just before each use, and the save/load test checks that this holds.
Saved locals are left out on the evidence of tests/test_saved_locals.py.

    python tools/gen_serializer.py <SWAT+ src/> <output .f90>
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from state_inventory import DECL, logical_lines, parse_decl, rank_of  # noqa: E402

SKIP_MODULES = {"bmi_exchange_module", "bmi_state_module"}


@dataclass
class Item:
    name: str
    base: str            # "real", "type(hyd_output)", "character(len=16)", ...
    rank: int
    allocatable: bool
    pointer: bool
    parameter: bool

    @property
    def tname(self):
        m = re.match(r"type\((\w+)\)", self.base)
        return m.group(1) if m else None


@dataclass
class Module:
    name: str
    file: str
    uses: list = field(default_factory=list)       # (module, set of names or None)
    types: dict = field(default_factory=dict)      # type name -> [Item]
    vars: list = field(default_factory=list)


def parse(src: Path) -> dict[str, Module]:
    mods: dict[str, Module] = {}
    for path in sorted(src.glob("*.f90")):
        mod = None
        in_type = None
        type_contains = False
        interface = 0
        for stmt in logical_lines(path.read_text(errors="replace")):
            low = stmt.strip().lower()
            if re.match(r"(abstract\s+)?interface\b", low):
                interface += 1
                continue
            if re.match(r"end\s*interface\b", low):
                interface -= 1
                continue
            if interface:
                continue
            m = re.match(r"module\s+(\w+)\s*$", low)
            if m and not low.startswith("module procedure"):
                mod = Module(m.group(1), path.name)
                mods[mod.name] = mod
                continue
            if mod is None:
                continue
            if re.match(r"end\s*module\b", low):
                mod = None
                continue
            if low.startswith("contains") and in_type is None:
                mod = None          # module procedures follow: no more module state
                continue
            m = re.match(r"use\s*(?:,\s*\w+\s*::)?\s*(\w+)\s*(?:,\s*only\s*:\s*(.*))?$", low)
            if m and in_type is None:
                only = None
                if m.group(2) is not None:
                    only = {x.split("=>")[-1].strip() for x in m.group(2).split(",") if x.strip()}
                mod.uses.append((m.group(1), only))
                continue
            m = re.match(r"type\s*(?:,\s*[\w(), ]+)?(?:::)?\s*(\w+)\s*$", low)
            if m and not low.startswith("type(") and not low.startswith("type ("):
                in_type = m.group(1)
                type_contains = False
                mod.types[in_type] = []
                continue
            if in_type is not None:
                if re.match(r"end\s*type\b", low):
                    in_type = None
                elif low.startswith("contains"):
                    type_contains = True
                elif not type_contains and DECL.match(stmt):
                    mod.types[in_type].extend(items(stmt))
                continue
            if DECL.match(stmt) and not re.search(r"\bfunction\b", low):
                mod.vars.extend(items(stmt))
    return mods


def items(stmt):
    parsed = parse_decl(stmt)
    if parsed is None:
        raise ValueError(f"can't read: {stmt.strip()[:120]}")
    base, attrs, names = parsed
    return [Item(n, base, rank_of(dims, attrs), "allocatable" in attrs, "pointer" in attrs,
                 "parameter" in attrs) for n, dims, _ in names]


class Gen:
    def __init__(self, mods):
        self.mods = mods
        self.type_ids: dict[tuple[str, str], str] = {}
        for mname in sorted(mods):
            for tname in mods[mname].types:
                self.type_ids[(mname, tname)] = f"t{len(self.type_ids) + 1}"

    def resolve(self, mname, tname, seen=None):
        """The (module, type) a type name means inside module mname."""
        seen = seen or set()
        if mname in seen or mname not in self.mods:
            return None
        seen.add(mname)
        m = self.mods[mname]
        if tname in m.types:
            return (mname, tname)
        for used, only in m.uses:
            if only is not None and tname not in only:
                continue
            r = self.resolve(used, tname, seen)
            if r:
                return r
        return None

    def tid(self, mname, tname):
        r = self.resolve(mname, tname)
        if r is None:
            raise ValueError(f"type {tname} used in {mname} not found")
        return self.type_ids[r]

    # -- code for one item (a module variable or a component) ------------

    def save(self, ref, it, owner, ind):
        if it.allocatable and it.rank == 0:          # allocatable scalar, often character(len=:)
            body = ([f"{ind}  write (u) len({ref})"] if it.base.startswith("character") else [])
            body += ([f"{ind}  write (u) {ref}"] if it.tname is None
                     else [f"{ind}  call swx_w_{self.tid(owner, it.tname)} (u, {ref})"])
            return [f"{ind}write (u) allocated({ref})", f"{ind}if (allocated({ref})) then", *body,
                    f"{ind}end if"]
        if it.tname is None:
            if not it.allocatable:
                return [f"{ind}write (u) {ref}"]
            return [f"{ind}write (u) allocated({ref})",
                    f"{ind}if (allocated({ref})) then",
                    f"{ind}  write (u) lbound({ref}), ubound({ref})",
                    f"{ind}  write (u) {ref}",
                    f"{ind}end if"]
        t = self.tid(owner, it.tname)
        if it.rank == 0:
            return [f"{ind}call swx_w_{t} (u, {ref})"]
        loops = self.loops(ref, it.rank, ind + ("  " if it.allocatable else ""),
                           f"call swx_w_{t} (u, {ref}({self.idx(it.rank)}))")
        if not it.allocatable:
            return loops
        return [f"{ind}write (u) allocated({ref})",
                f"{ind}if (allocated({ref})) then",
                f"{ind}  write (u) lbound({ref}), ubound({ref})", *loops,
                f"{ind}end if"]

    def load(self, ref, it, owner, ind):
        r = it.rank
        if it.allocatable and r == 0:
            if it.base.startswith("character"):
                alloc = [f"{ind}  read (u) lb(1)", f"{ind}  allocate (character(len=lb(1)) :: {ref})"]
            else:
                alloc = [f"{ind}  allocate ({ref})"]
            body = ([f"{ind}  read (u) {ref}"] if it.tname is None
                    else [f"{ind}  call swx_r_{self.tid(owner, it.tname)} (u, {ref})"])
            return [f"{ind}read (u) al", f"{ind}if (allocated({ref})) deallocate ({ref})",
                    f"{ind}if (al) then", *alloc, *body, f"{ind}end if"]
        bounds = ", ".join(f"lb({k}):ub({k})" for k in range(1, r + 1))
        if it.tname is None:
            if not it.allocatable:
                return [f"{ind}read (u) {ref}"]
            return [f"{ind}read (u) al",
                    f"{ind}if (allocated({ref})) deallocate ({ref})",
                    f"{ind}if (al) then",
                    f"{ind}  read (u) lb(1:{r}), ub(1:{r})",
                    f"{ind}  allocate ({ref}({bounds}))",
                    f"{ind}  read (u) {ref}",
                    f"{ind}end if"]
        t = self.tid(owner, it.tname)
        if r == 0:
            return [f"{ind}call swx_r_{t} (u, {ref})"]
        loops = self.loops(ref, r, ind + ("  " if it.allocatable else ""),
                           f"call swx_r_{t} (u, {ref}({self.idx(r)}))")
        if not it.allocatable:
            return loops
        return [f"{ind}read (u) al",
                f"{ind}if (allocated({ref})) deallocate ({ref})",
                f"{ind}if (al) then",
                f"{ind}  read (u) lb(1:{r}), ub(1:{r})",
                f"{ind}  allocate ({ref}({bounds}))", *loops,
                f"{ind}end if"]

    @staticmethod
    def idx(r):
        return ", ".join(f"i{k}" for k in range(1, r + 1))

    @staticmethod
    def loops(ref, r, ind, body):
        out = []
        for k in range(r, 0, -1):            # last index outermost: column-major order
            out.append(f"{ind}{'  ' * (r - k)}do i{k} = lbound({ref},{k}), ubound({ref},{k})")
        out.append(f"{ind}{'  ' * r}{body}")
        for k in range(1, r + 1):
            out.append(f"{ind}{'  ' * (r - k)}end do")
        return out

    # -- whole file -----------------------------------------------------

    def module_routines(self, mname):
        m = self.mods[mname]
        vs = [v for v in m.vars if not v.parameter and not v.pointer]
        if not vs:
            return [], []
        uses = [f"      use {mname}, only : v{k} => {v.name}" for k, v in enumerate(vs, 1)]
        decl = ["      integer, intent(in) :: u",
                "      integer :: i1, i2, i3",
                "      integer :: lb(3), ub(3)",
                "      logical :: al",
                "      character(len=80) :: tag"]
        sv, ld = [], []
        for k, v in enumerate(vs, 1):
            tag = f"{mname}::{v.name}"
            sv += [f"      tag = \"{tag}\"", "      write (u) tag", *self.save(f"v{k}", v, mname, "      ")]
            ld += ["      read (u) tag", f"      if (tag /= \"{tag}\") call swx_mismatch (tag, \"{tag}\")",
                   *self.load(f"v{k}", v, mname, "      ")]
        n = self.n_mod[mname]
        s = [f"!!    {mname} ({m.file}): {len(vs)} variables",
             f"      subroutine swx_save_m{n} (u)", *uses, *decl, "      i1 = 0; i2 = 0; i3 = 0; lb = 0; ub = 0; al = .false.",
             *sv, "      end subroutine", ""]
        l = [f"      subroutine swx_load_m{n} (u)", *uses, *decl, "      i1 = 0; i2 = 0; i3 = 0; lb = 0; ub = 0; al = .false.",
             *ld, "      end subroutine", ""]
        return s, l

    def type_routines(self, mname, tname):
        t = self.type_ids[(mname, tname)]
        comps = [c for c in self.mods[mname].types[tname] if not c.pointer and not c.parameter]
        head = [f"      use {mname}, only : {t} => {tname}",
                "      integer, intent(in) :: u",
                "      integer :: i1, i2, i3",
                "      integer :: lb(3), ub(3)",
                "      logical :: al"]
        init = "      i1 = 0; i2 = 0; i3 = 0; lb = 0; ub = 0; al = .false."
        w = [f"!!    {mname}::{tname}", f"      recursive subroutine swx_w_{t} (u, x)", *head,
             f"      type ({t}), intent(in) :: x", init]
        r = [f"      recursive subroutine swx_r_{t} (u, x)", *head, f"      type ({t}), intent(inout) :: x", init]
        for c in comps:
            w += self.save(f"x%{c.name}", c, mname, "      ")
            r += self.load(f"x%{c.name}", c, mname, "      ")
        return w + ["      end subroutine", ""] + r + ["      end subroutine", ""]

    def generate(self, release_note):
        names = [m for m in sorted(self.mods) if m not in SKIP_MODULES]
        self.n_mod = {m: k for k, m in enumerate(names, 1)}
        body, saves, loads = [], [], []
        for m in names:
            s, l = self.module_routines(m)
            if s:
                body += s + l
                saves.append(f"      call swx_save_m{self.n_mod[m]} (u)")
                loads.append(f"      call swx_load_m{self.n_mod[m]} (u)")
        for (m, tname) in self.type_ids:
            if m in SKIP_MODULES:
                continue
            body += self.type_routines(m, tname)
        text = "\n".join(body)
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        head = f'''      module bmi_state_module

!!    GENERATED by tools/gen_serializer.py from {release_note}. Do not edit;
!!    rerun the generator for each SWAT+ release.
!!
!!    Saves and loads every SWAT+ module variable except parameters and
!!    pointers, as unformatted stream, for the exchange mode's save and
!!    load commands.

      implicit none

      private
      public :: swx_state_save, swx_state_load, swx_state_digest

      character(len=16), parameter :: swx_state_digest = "{digest}"

      contains

      subroutine swx_state_save (u)
      integer, intent(in) :: u
{chr(10).join(saves)}
      end subroutine swx_state_save

      subroutine swx_state_load (u)
      integer, intent(in) :: u
{chr(10).join(loads)}
      end subroutine swx_state_load

      subroutine swx_mismatch (got, want)
      character(len=*), intent(in) :: got, want
      write (*,*) "state file does not match this build: found ", trim(got), ", expected ", trim(want)
      error stop 5
      end subroutine swx_mismatch

'''
        return head + text + "\n      end module bmi_state_module\n", len(saves), len(self.type_ids)


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    mods = parse(src)
    g = Gen(mods)
    text, nmod, ntype = g.generate(f"SWAT+ {src.resolve().parent.name}/src")
    out.write_text(text)
    nvar = sum(len([v for v in m.vars if not v.parameter and not v.pointer])
               for k, m in mods.items() if k not in SKIP_MODULES)
    print(f"wrote {out}: {nmod} modules, {nvar} variables, {ntype} types, {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
