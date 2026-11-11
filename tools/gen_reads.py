"""Generate bmi_reads_module.f90: every column of SWAT+'s daily output records, read-only.

Rerun for each SWAT+ release (build.sh does); never edit the output by hand.
A read is named <file>:<column>, the daily file without _day.txt and the
column as printed in its header (hru_wb:perc, aquifer:rchrg,
channel_sd:flo_out), in the units printed under it. Its value is the record
SWAT+ holds for the day just done, which is what it prints for that day.

Each column is matched to the record component printed under it by
position, as SWAT+'s write statements do: a header type's leading columns
(day, mo, day_mo, yrc, isd, id, name) are the date and object, and the
rest follow the printed components in order. Columns headed "null" (the
hydrograph temperature), columns not printed from a record (channel_sd's
water temperature, a local; hru_wb's plant_cov and mgt_ops, text) and the
water balances' sw_init and sno_init, which SWAT+ moves on to the next
day's start right after printing, are left out. Header and component names that differ are SWAT+'s own (hru_wb's
sw_ave is the component sw); --list prints the table.

    python tools/gen_reads.py <SWAT+ src/> <output .f90>
    python tools/gen_reads.py <SWAT+ src/> --list
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from state_inventory import DECL, logical_lines, parse_decl  # noqa: E402

LEAD = ("day", "mo", "day_mo", "yrc", "isd", "id", "name")
# components SWAT+ moves on after printing, so the record no longer holds the printed value at
# the pause: hru_output sets sw_init and sno_init to the day's final values for the next day
# (the day's start is the day before's sw_final and sno_final)
CHANGED_AFTER_PRINT = {("output_waterbal", "sw_init"), ("output_waterbal", "sno_init")}


@dataclass
class Part:
    expr: str        # the printed object, with {n} for the object's number: "hwb_d({n})"
    vtype: str       # its derived type
    header: str      # the header type naming its columns
    units: str       # the header type giving their units


def landscape(prefix: str, arrays: tuple[str, str, str, str]):
    kinds = (("wb", "output_waterbal"), ("nb", "output_nutbal"), ("ls", "output_losses"),
             ("pw", "output_plantweather"))
    return {f"{prefix}_{k}": [Part(a, t, f"{t}_header", f"{t}_header_units")]
            for a, (k, t) in zip(arrays, kinds)}


# the daily files, the grid their objects are on, and what each prints
RECORDS: dict[str, tuple[str, list[Part]]] = {}
for f, parts in landscape("hru", ("hwb_d({n})", "hnb_d({n})", "hls_d({n})", "hpw_d({n})")).items():
    RECORDS[f] = ("hru", parts)
for f, parts in landscape("lsunit", ("ruwb_d({n})", "runb_d({n})", "ruls_d({n})", "rupw_d({n})")).items():
    RECORDS[f] = ("lsu", parts)
for f, parts in landscape("basin", ("bwb_d", "bnb_d", "bls_d", "bpw_d")).items():
    RECORDS[f] = ("bsn", parts)
RECORDS["aquifer"] = ("aqu", [Part("aqu_d({n})", "aquifer_dynamic", "aqu_header", "aqu_header_units")])
RECORDS["channel_sd"] = ("cha", [
    Part("ch_wat_d({n})", "water_body", "ch_watbod_header", "ch_watbod_header_units"),
    Part("ch_stor({n})", "hyd_output", "hyd_stor_header", "hyd_header_units3"),
    Part("ch_in_d({n})", "hyd_output", "hyd_in_header", "hyd_header_units1"),
    Part("ch_out_d({n})", "hyd_output", "hyd_out_header", "hyd_header_units1"),
])
# reservoir_day.txt comes with settable reservoirs, on a test project that has one


@dataclass
class Column:
    name: str        # file:column
    grid: str
    units: str
    expr: str        # Fortran, with {n}
    component: str   # the record component printed under the column


def parse(src: Path):
    """Derived types as [(component, base, init)], and which module declares each variable."""
    types: dict[str, list[tuple[str, str, str | None]]] = {}
    owner: dict[str, str] = {}
    for path in sorted(src.glob("*.f90")):
        module = None
        in_type = None
        for stmt in logical_lines(path.read_text(errors="replace")):
            low = stmt.strip().lower()
            m = re.match(r"module\s+(\w+)\s*$", low)
            if m:
                module = m.group(1)
                continue
            if re.match(r"(contains|end\s*module)\b", low):
                module = None
                continue
            m = re.match(r"type\s*(?:,\s*[\w(), ]+)?(?:::)?\s*(\w+)\s*$", low)
            if m and not low.startswith("type("):
                in_type = m.group(1)
                types[in_type] = []
                continue
            if in_type and re.match(r"end\s*type\b", low):
                in_type = None
                continue
            if not DECL.match(stmt) or (module is None and in_type is None):
                continue
            parsed = parse_decl(stmt)
            if parsed is None:
                continue
            base, attrs, names = parsed
            for name, _dims, init in names:
                if in_type:
                    types[in_type].append((name.lower(), base.lower(), init))
                elif "parameter" not in attrs:
                    owner.setdefault(name.lower(), module)
    return types, owner


def text(init: str | None) -> str:
    return (init or "").strip().strip("'\"").strip()


def columns(src: Path) -> tuple[list[Column], dict[str, str]]:
    types, owner = parse(src)
    out: list[Column] = []
    for file, (grid, parts) in RECORDS.items():
        seen = set()
        for p in parts:
            comps = types[p.vtype]
            bad = [c for c, b, _ in comps if b != "real"]
            if bad:
                raise SystemExit(f"{file}: {p.vtype} has components that aren't real: {bad}")
            head = [(c, text(i)) for c, _, i in types[p.header]]
            units = [(c, text(i)) for c, _, i in types[p.units]]
            head = head[sum(1 for c, _ in head[:len(LEAD)] if c in LEAD):]
            units = units[sum(1 for c, _ in units[:len(LEAD)] if c in LEAD):]
            if len(head) < len(comps):
                raise SystemExit(f"{file}: {p.header} names {len(head)} columns for {len(comps)} components")
            for k, (comp, _, _) in enumerate(comps):
                col = head[k][1]
                if col == "null" or (p.vtype, comp) in CHANGED_AFTER_PRINT:
                    continue
                if not re.fullmatch(r"[A-Za-z]\w*", col) or col in seen:
                    raise SystemExit(f"{file}: column {k + 1} of {p.header} is {col!r}")
                seen.add(col)
                unit = units[k][1] if k < len(units) else ""
                out.append(Column(f"{file}:{col}", grid, unit or "-", f"{p.expr}%{comp}", comp))
    arrays = {re.match(r"\w+", c.expr).group(0) for c in out}
    missing = sorted(a for a in arrays if a not in owner)
    if missing:
        raise SystemExit(f"no module declares {missing}")
    return out, {a: owner[a] for a in arrays}


def fortran(cols: list[Column], owners: dict[str, str]) -> str:
    uses = {}
    for a, m in sorted(owners.items()):
        uses.setdefault(m, []).append(a)
    L = ["!! generated by tools/gen_reads.py from this SWAT+ release's source; do not edit",
         "!! every column of SWAT+'s daily output records, as the record holds it for the day just done",
         "      module bmi_reads_module",
         "",
         "      implicit none",
         "",
         f"      integer, parameter :: n_gen = {len(cols)}",
         "",
         "      contains",
         "",
         "!!    --------------------------------------------------------------------",
         "      subroutine gen_catalog (name, grid, units)",
         "",
         "      character(len=*), dimension(:), intent(out) :: name",
         "      character(len=*), dimension(:), intent(out) :: grid",
         "      character(len=*), dimension(:), intent(out) :: units",
         ""]
    for i, c in enumerate(cols, 1):
        L.append(f'      name({i}) = "{c.name}"; grid({i}) = "{c.grid}"; units({i}) = "{c.units}"')
    L += ["",
          "      return",
          "      end subroutine gen_catalog",
          "",
          "!!    --------------------------------------------------------------------",
          "      real(8) function gen_value (ig, n)",
          "",
          "!!    column ig of object n's record (n is ignored for a basin record)",
          ""]
    for m, arrays in uses.items():
        for a in arrays:                           # one per line: free form stops at 132 columns
            L.append(f"      use {m}, only : {a}")
    L += ["",
          "      integer, intent(in) :: ig",
          "      integer, intent(in) :: n",
          "",
          "      select case (ig)"]
    for i, c in enumerate(cols, 1):
        L.append(f"      case ({i}); gen_value = {c.expr.format(n='n')}")
    L += ["      case default; gen_value = 0.d0",
          "      end select",
          "",
          "      return",
          "      end function gen_value",
          "",
          "      end module bmi_reads_module",
          ""]
    return "\n".join(L)


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    cols, owners = columns(Path(argv[0]))
    if argv[1] == "--list":
        for c in cols:
            note = "" if c.name.split(":")[1] in (c.component, c.component + "_in", c.component + "_out",
                                                  c.component + "_stor") else f"   (component {c.component})"
            print(f"{c.name:32} {c.grid:4} {c.units:12}{note}")
        print(f"{len(cols)} columns")
        return
    Path(argv[1]).write_text(fortran(cols, owners))


if __name__ == "__main__":
    main(sys.argv[1:])
