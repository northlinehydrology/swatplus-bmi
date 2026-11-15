"""A reservoir below Osu_1hru's channel, for tests of the reservoir variables.

``with_reservoir`` is an ``edit`` for ``conftest.project``::

    project("Osu_1hru", "x", daily=True, edit=with_reservoir)

Neither example project that ships with SWAT+ 62.0.1 has a reservoir. Osu_1hru's one channel
(``cha01``) flows out of the watershed; this sends it into a reservoir, ``res01``, which
flows out instead. The formats follow the readers:

object.cnt (basin_read_objs)
    One more object, and a reservoir in the ``res`` column.
reservoir.con (hyd_read_connect)
    As every connection file: id, name, gis_id, area, lat, lon, elev, the reservoir's
    number in reservoir.res, weather station, cst, ovfl, rule, out_tot (0: the outlet).
chandeg.con
    The channel's out_tot 1, to ``res 1``, all of it (``tot``).
reservoir.res (res_read)
    id, then name, init, hyd, rel, sed, nut, each matched by name: Osu_1hru's own
    initial.res (``initwet1``, which starts it empty), sediment.res and nutrients.res;
    hydrology.res below; and res_rel.dtl's ``drawdown_days`` release.
hydrology.res (res_read_hyd)
    name, yr_op and mon_op (the year, as a calendar year, and month it's built in; 0 and 1:
    from the start, since res_control compares yr_op with time%yrc), area_ps (ha), vol_ps (ha-m), area_es,
    vol_es, k (mm/hr, the bottom's conductivity: SWAT+'s own seepage), evap_co, shp_co1 and
    shp_co2 (SWAT+ 62.0.1 reads these as the release lags; 0 is none).
file.cio
    reservoir.con as the 8th connection file; reservoir.res and hydrology.res as the
    2nd and 3rd reservoir files.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path


def with_reservoir(run: Path, *, k: float = 0.0, yr_op: int = 0) -> None:
    """``k``: the bottom's conductivity (mm/hr), 0 so SWAT+ seeps nothing of its own;
    ``yr_op``: the calendar year it's built in, in January (0: from the start)."""
    run = Path(run)
    lines = (run / "object.cnt").read_text().splitlines()
    f = lines[2].split()
    f[3] = str(int(f[3]) + 1)                                   # obj
    f[10] = "1"                                                 # res
    lines[2] = "  ".join(f)
    (run / "object.cnt").write_text("\n".join(lines) + "\n")

    lines = (run / "chandeg.con").read_text().splitlines()
    f = lines[2].split()
    f[12] = "1"                                                 # out_tot
    lines[2] = "  ".join(f + ["res", "1", "tot", "1.00000"])
    (run / "chandeg.con").write_text("\n".join(lines) + "\n")

    (run / "reservoir.con").write_text(
        "reservoir.con: a reservoir below cha01 (swatplus-bmi tests/reservoir.py)\n"
        "id name gis_id area lat lon elev res wst cst ovfl rule out_tot\n"
        "1 res01 1 10.0 35.52 127.33 0.0 1 s35610n127290e 0 0 0 0\n")
    (run / "reservoir.res").write_text(
        "reservoir.res: swatplus-bmi tests/reservoir.py\n"
        "id name init hyd rel sed nut\n"
        "1 res01 initwet1 hydres01 drawdown_days sedwet1 nutwet1\n")
    (run / "hydrology.res").write_text(
        "hydrology.res: swatplus-bmi tests/reservoir.py\n"
        "name yr_op mon_op area_ps vol_ps area_es vol_es k evap_co shp_co1 shp_co2\n"
        f"hydres01 {yr_op} 1 1.0 1.0 1.5 2.0 {k} 0.6 0.0 0.0\n")

    lines = (run / "file.cio").read_text().splitlines()
    for i, line in enumerate(lines):
        f = line.split()
        if f and f[0] == "connect":
            f[8] = "reservoir.con"
        elif f and f[0] == "reservoir":
            f[2], f[3] = "reservoir.res", "hydrology.res"
        else:
            continue
        lines[i] = "  ".join(f)
    (run / "file.cio").write_text("\n".join(lines) + "\n")


def reservoir(**kw):
    """``with_reservoir`` with other inputs, as an ``edit``."""
    return partial(with_reservoir, **kw)
