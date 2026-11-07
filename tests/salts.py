"""Turn on SWAT+'s salt module in a copy of Osu_1hru, for tests of the salt variables.

``with_salts(run)`` is an ``edit`` for ``conftest.project``::

    project("Osu_1hru", "x", daily=True, edit=with_salts)

It writes the inputs SWAT+ 62.0.1 needs for the eight salt ions, in the order
its salt chemistry uses them (salt_chem_soil_single.f90, salt_chem_aqu.f90):
so4, ca, mg, na, k, cl, co3, hco3. The formats follow the readers:

constituents.cs (constit_db_read)
    Named in file.cio (the 5th file on the ``simulation`` line). Title, then a
    count line and a names line for pesticides, pathogens, metals, salts and
    other constituents. Every names line must be there, even for a count of 0:
    each list-directed read takes one line.
salt_hru.ini (salt_hru_read)
    Title and four header lines, then three lines a record: name; the soil
    water concentrations (8 ions, mg/L) and the five mineral fractions
    (CaCO3, MgCO3, CaSO4, MgSO4, NaCl); the same 13 values for plants (unused).
    It's found through soil_plant.ini, whose 6th column is read as the salt
    name although the editor's header calls it ``hmet``; the name goes in both.
salt_irrigation (salt_irr_read)
    Title, header, then name and 8 concentrations. Optional in the reader, but
    salt_hru_init reads it for every HRU, so SWAT+ needs it once salts are on.
salt_aqu.ini (salt_aqu_read)
    Title and three header lines, then name, 8 concentrations, 5 mineral
    fractions. Found through initial.aqu_cs (aqu_read_init_cs): name, pest,
    path, hmet, salt, cs, matched to aquifers by row number, not by name.
salt_channel.ini (salt_cha_read)
    Title and one header line, then name and 8 concentrations. Found through
    initial.cha_cs (ch_read_init_cs), whose rows pair with initial.cha's by
    row number.
salt_recall.rec (recall_read_salt) and its daily file
    The salt load on the point source that ``test_setting.add_point_source``
    turns on. Title, header, then num, name, type, file. The daily file: title
    (``Incoming`` would make it a source from outside the watershed), the
    number of years, a header, then jday, mo, day, year, object type, object
    name and the 8 loads in kg/day.

The point source also gets steady no3 and solp loads (columns 11 and 12 of
pnt01.rec, after jday mo day_mo yr ob_typ ob_name flo sed orgn sedp).

Channel water starts at a tenth of bankfull depth (a new om_water.ini row),
so the channel's starting salt is a mass, not just a concentration: SWAT+
sets the mass from the concentration and the starting volume, which is 0
with Osu_1hru's ``no_init``.

Daily salt output is turned on for the basin (which carries the point-source
load), the HRU, the aquifer and the channel, through ``set_print``.

Stock SWAT+ 62.0.1 can't run the result; four defects stop it, each
independently (found Nov 6, 2026):

- salt_hru_init writes cs_soil(ihru)%ly(ly)%salt, but cs_soil(ihru)%ly is
  allocated only in pesticide_init, which returns when there are no
  pesticides: a segmentation fault with salts and no pesticides.
- With a pesticide as well, aqu_initial allocates cs_aqu(iaq)%salt twice
  (line 125 in the pesticide block, again at line 129): "Attempting to
  allocate already allocated variable 'cs_aqu'".
- recall_read_salt (salt_recall.rec) is never called, so rec_salt is never
  allocated, but recall_salt reads it for every recall object once salts
  are on.
- recall_salt also writes div_conc_salt, which only gwflow_read allocates.
"""

from __future__ import annotations

from pathlib import Path

from swatplus_bmi.project import set_print
from test_setting import add_point_source

SALTS = ("so4", "ca", "mg", "na", "k", "cl", "co3", "hco3")

# mg/L, of the size seen in Midwest streams and shallow groundwater
SOIL = (30.0, 40.0, 15.0, 15.0, 3.0, 25.0, 1.0, 150.0)
AQUIFER = (40.0, 60.0, 20.0, 15.0, 2.0, 25.0, 1.0, 220.0)
CHANNEL = (25.0, 35.0, 12.0, 12.0, 3.0, 20.0, 1.0, 120.0)
IRRIGATION = (25.0, 35.0, 12.0, 12.0, 3.0, 20.0, 1.0, 120.0)
MINERALS = (0.0,) * 5                     # no salt minerals in soil or aquifer

LOAD_KG_DAY = 50.0                        # each ion, on the point source
NO3_KG_DAY = 1.0
SOLP_KG_DAY = 0.1
CHANNEL_DEPTH_FRAC = 0.1                  # starting channel water, fraction of bankfull depth
PLACEHOLDER_PEST = "245-tp"               # in pesticide.pes; Osu_1hru never applies it


def _nums(values) -> str:
    return "  ".join(f"{v:.4f}" for v in values)


def with_salts(run: Path) -> None:
    """The whole salt setup: soil, aquifer, channel and a point source. Stock 62.0.1 can't run it."""
    run = Path(run)
    add_point_source(run)
    _point_source_nutrients(run)
    _constituents(run, pests=())
    _soil(run)
    _aquifer(run)
    _channel(run)
    _salt_recall(run)
    _print(run, ["basin_salt", "hru_salt", "aqu_salt", "channel_salt"])


def with_salts_channel(run: Path) -> None:
    """The most of the salt module stock 62.0.1 runs: soil and channel salts, no aquifer.

    A placeholder pesticide makes pesticide_init allocate the soil layers
    salt_hru_init writes to; removing the aquifer keeps aqu_initial from
    allocating cs_aqu%salt twice; and there's no point source, since
    recall_salt reads rec_salt, which 62.0.1 never allocates. Without an
    aquifer, the routing unit's recharge (and the salt percolating with it)
    leaves the model. Soil water starts at field capacity.
    """
    run = Path(run)
    _constituents(run, pests=(PLACEHOLDER_PEST,))
    _pesticide_placeholder(run)
    _remove_aquifer(run)
    _soil(run)
    _start_soil_at_field_capacity(run)
    _channel(run)
    _print(run, ["basin_salt", "hru_salt", "channel_salt"])


def _start_soil_at_field_capacity(run: Path) -> None:
    """parameters.bsn sw_init = 1: soil water starts at field capacity.

    Soil salt starts as concentration times the soil water at the start, and
    Osu_1hru's soil starts nearly dry (0.2 mm), so with its own start almost no
    salt reaches the channel (0.003 kg of chloride in 14 years; 0.58 kg with this).
    """
    bsn = (run / "parameters.bsn").read_text().splitlines()
    names, values = bsn[1].split(), bsn[2].split()
    values[names.index("sw_init")] = "1.00000"
    bsn[2] = "  ".join(values)
    (run / "parameters.bsn").write_text("\n".join(bsn) + "\n")


def _set_cio(run: Path, section: str, index: int, value: str) -> None:
    """Set file ``index`` (1 = first file) on file.cio's ``section`` line."""
    cio = (run / "file.cio").read_text().splitlines()
    for i, line in enumerate(cio):
        f = line.split()
        if f[:1] == [section]:
            f[index] = value
            cio[i] = "  ".join(f)
    (run / "file.cio").write_text("\n".join(cio) + "\n")


def _constituents(run: Path, pests) -> None:
    _set_cio(run, "simulation", 5, "constituents.cs")
    (run / "constituents.cs").write_text(
        "constituents.cs: salt ions for swatplus-bmi tests\n"
        f"{len(pests)}\n{'  '.join(pests)}\n"
        "0\n\n"                       # pathogens
        "0\n\n"                       # metals
        f"{len(SALTS)}\n{'  '.join(SALTS)}\n"
        "0\n\n")                      # other constituents


def _pesticide_placeholder(run: Path) -> None:
    """Zero initial pesticide in soil and water, so pesticide_init and sd_hydsed_init have rows."""
    _set_cio(run, "init", 4, "pest_hru.ini")
    _set_cio(run, "init", 5, "pest_water.ini")
    # pest_hru_aqu_read: title, then per record a header, a name and a line per pesticide
    (run / "pest_hru.ini").write_text(
        "pest_hru.ini: no pesticide in soil or on plants\n"
        "name\n"
        "pest_none\n"
        f"{PLACEHOLDER_PEST}  0.0  0.0\n")
    # pest_cha_res_read counts a name and two lines per pesticide, but reads the name and
    # one line (name, water, benthic); the third line is only there to be counted
    (run / "pest_water.ini").write_text(
        "pest_water.ini: no pesticide in channel water or bed\n"
        "name\n"
        "pest_none\n"
        f"{PLACEHOLDER_PEST}  0.0  0.0\n"
        "benthic (counted, not read)\n")
    _set_column(run / "soil_plant.ini", 3, "pest_none")
    _set_column(run / "initial.cha", 2, "pest_none")


def _remove_aquifer(run: Path) -> None:
    """No aquifer object: the routing unit's recharge leaves the model."""
    cnt = (run / "object.cnt").read_text().splitlines()
    f = cnt[2].split()
    #  name ls_area tot_area obj hru lhru rtu mfl aqu cha res rec ...
    f[3] = str(int(f[3]) - int(f[8]))
    f[8] = "0"
    cnt[2] = "  ".join(f)
    (run / "object.cnt").write_text("\n".join(cnt) + "\n")
    _set_cio(run, "connect", 5, "null")        # aquifer.con
    _set_cio(run, "regions", 9, "null")        # aqu_catunit.ele
    # rout_unit.con: keep only the outflows that don't go to an aquifer
    con = (run / "rout_unit.con").read_text().splitlines()
    for i in range(2, len(con)):
        f = con[i].split()
        if len(f) < 13:
            continue
        outs = [f[13 + 4 * k: 17 + 4 * k] for k in range(int(f[12]))]
        keep = [o for o in outs if o[0] != "aqu"]
        f = f[:12] + [str(len(keep))] + [x for o in keep for x in o]
        con[i] = "  ".join(f)
    (run / "rout_unit.con").write_text("\n".join(con) + "\n")


def _soil(run: Path) -> None:
    (run / "salt_hru.ini").write_text(
        "salt_hru.ini: soil water salt ions (mg/L) and mineral fractions\n"
        "soil: " + " ".join(SALTS) + " caco3 mgco3 caso4 mgso4 nacl\n"
        "plant: the same 13 values (not used)\n"
        "name\n"
        "values\n"
        "salt_soil1\n"
        f"{_nums(SOIL + MINERALS)}\n"
        f"{_nums((0.0,) * (len(SALTS) + 5))}\n")
    # soil_plant_init reads the 6th column as the salt name (the editor's header says hmet)
    _set_column(run / "soil_plant.ini", 5, "salt_soil1")
    _set_column(run / "soil_plant.ini", 6, "salt_soil1")
    (run / "salt_irrigation").write_text(
        "salt_irrigation: outside irrigation water salt ions (mg/L)\n"
        "name  " + "  ".join(SALTS) + "\n"
        f"salt_soil1  {_nums(IRRIGATION)}\n")


def _aquifer(run: Path) -> None:
    (run / "salt_aqu.ini").write_text(
        "salt_aqu.ini: groundwater salt ions (mg/L) and mineral fractions\n"
        "concentrations: " + " ".join(SALTS) + "\n"
        "fractions: caco3 mgco3 caso4 mgso4 nacl\n"
        "name  " + "  ".join(SALTS) + "  caco3  mgco3  caso4  mgso4  nacl\n"
        f"salt_aqu1  {_nums(AQUIFER + MINERALS)}\n")
    names = [l.split()[0] for l in (run / "initial.aqu").read_text().splitlines()[2:] if l.split()]
    (run / "initial.aqu_cs").write_text(
        "initial.aqu_cs: aquifer constituent initial data (rows pair with aquifers)\n"
        "name  pest  path  hmet  salt  cs\n"
        + "".join(f"{n}  null  null  null  salt_aqu1  null\n" for n in names))


def _channel(run: Path) -> None:
    (run / "salt_channel.ini").write_text(
        "salt_channel.ini: channel water salt ions (mg/L)\n"
        "name  " + "  ".join(SALTS) + "\n"
        f"salt_cha1  {_nums(CHANNEL)}\n")
    names = [l.split()[0] for l in (run / "initial.cha").read_text().splitlines()[2:] if l.split()]
    (run / "initial.cha_cs").write_text(
        "initial.cha_cs: channel constituent initial data (rows pair with initial.cha)\n"
        "name  pest  path  hmet  salt  cs\n"
        + "".join(f"{n}  null  null  null  salt_cha1  null\n" for n in names))
    _starting_channel_water(run)


def _salt_recall(run: Path) -> None:
    """Point-source salt load, daily, over the years of pnt01.rec."""
    rows = [l.split() for l in (run / "pnt01.rec").read_text().splitlines()[3:] if l.split()]
    years = sorted({int(r[3]) for r in rows})
    (run / "salt_recall.rec").write_text(
        "salt_recall.rec: salt loads on point sources\n"
        "num  name  type  file\n"
        "1  pnt01  1  pnt01_salt.rec\n")
    load = _nums((LOAD_KG_DAY,) * len(SALTS))
    (run / "pnt01_salt.rec").write_text(
        "pnt01_salt.rec: steady daily salt load (kg/day)\n"
        f"{len(years)}\n"
        "jday mo day_mo yr ob_typ ob_name " + " ".join(SALTS) + "\n"
        + "".join(f"{' '.join(r[:6])} {load}\n" for r in rows))


def _print(run: Path, labels) -> None:
    _add_salt_rows(run / "print.prt")
    set_print(run, labels, intervals=["daily"])


def _set_column(path: Path, col: int, value: str) -> None:
    """Set column ``col`` (0 = name) on every data row (after title and header) of ``path``."""
    lines = path.read_text().splitlines()
    for i in range(2, len(lines)):
        f = lines[i].split()
        if len(f) > col:
            f[col] = value
            lines[i] = "  ".join(f)
    path.write_text("\n".join(lines) + "\n")


def _point_source_nutrients(run: Path) -> None:
    lines = (run / "pnt01.rec").read_text().splitlines()
    for i in range(3, len(lines)):
        f = lines[i].split()
        if len(f) > 11:
            f[10] = f"{NO3_KG_DAY:g}"           # no3, kg N/day
            f[11] = f"{SOLP_KG_DAY:g}"          # solp, kg P/day
            lines[i] = " ".join(f)
    (run / "pnt01.rec").write_text("\n".join(lines) + "\n")


def _starting_channel_water(run: Path) -> None:
    om = (run / "om_water.ini").read_text().splitlines()
    ncol = len(om[1].split()) - 1
    om.append(f"{'salt_start':<16}" + "".join(
        f"{v:>14.5f}" for v in (CHANNEL_DEPTH_FRAC,) + (0.0,) * (ncol - 1)))
    (run / "om_water.ini").write_text("\n".join(om) + "\n")
    _set_column(run / "initial.cha", 1, "salt_start")


def _add_salt_rows(prt: Path) -> None:
    """print.prt rows for salt output, after ``pest``, where SWAT+'s fixed order has them."""
    rows = ["basin_salt", "hru_salt", "ru_salt", "aqu_salt", "channel_salt", "res_salt",
            "wetland_salt"]
    lines = prt.read_text().splitlines()
    have = {l.split()[0] for l in lines if l.split()}
    new = [f"{r:<24}" + "".join(f"{'n':>14}" for _ in range(4)) for r in rows if r not in have]
    at = next((i + 1 for i, l in enumerate(lines) if l.split()[:1] == ["pest"]), len(lines))
    lines[at:at] = new
    prt.write_text("\n".join(lines) + "\n")
