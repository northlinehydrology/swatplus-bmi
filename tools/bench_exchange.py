"""Time a SWAT+ run through swatplus-bmi that reads and sets arrays every day.

Uses only the BMI calls every release has, so the same script times an older
release against this one: run it with the swatplus_bmi to time on the path and
SWATPLUS_EXCHANGE naming that release's executable (or let the package find its
own). The project is copied to a temporary folder first and left as it was.

    python tools/bench_exchange.py <project folder> [--days N] [--get VAR ...] [--set-back VAR]

By default each day reads the soil layer temperatures, snow, percolation and
channel outflow, and sets the layer temperatures back to SWAT+'s own values (so
the run is stock SWAT+'s), the kind of exchange a coupled groundwater model makes.
``--get`` with no names and no ``--set-back`` times stepping alone. Prints one
JSON line: wall time, SWAT+'s user and system (kernel) CPU time, and Python's.
Compare two builds back to back under the same load, as ratios.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

DEFAULT_GET = ["soil_layer__temperature", "snowpack__liquid-equivalent_depth",
               "soil_water__percolation_rate", "channel_water__volume_outflow_rate"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("--days", type=int, help="days to run (default: the whole simulation)")
    ap.add_argument("--get", nargs="*", default=DEFAULT_GET, metavar="VAR")
    ap.add_argument("--set-back", default="soil_layer__temperature", metavar="VAR",
                    help="read VAR and set it back every day ('' for none)")
    ap.add_argument("--keep", type=Path, help="run in this folder (made new) and keep it")
    a = ap.parse_args(argv)

    from swatplus_bmi import SwatPlusBmi, __version__
    from swatplus_bmi.exchange import PROTOCOL

    tmp = None
    if a.keep:
        run = a.keep
        if run.exists():
            raise SystemExit(f"{run} exists")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="bench-exchange-"))
        run = tmp / "run"
    shutil.copytree(a.project, run, ignore=shutil.ignore_patterns("exchange.log", "exchange.err"))
    try:
        self0, kids0 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        t0 = time.perf_counter()
        m = SwatPlusBmi()
        m.initialize(str(run))
        have = set(m.get_output_var_names())
        bufs = {v: np.empty(m.get_grid_size(m.get_var_grid(v))) for v in a.get if v in have}
        back = np.empty(m.get_grid_size(m.get_var_grid(a.set_back))) if a.set_back else None
        end = m.get_end_time() if a.days is None else min(m.get_end_time(), a.days)
        values = 0
        while m.get_current_time() < end:
            for v, buf in bufs.items():
                m.get_value(v, buf)
                values += buf.size
            if back is not None:
                m.get_value(a.set_back, back)
                m.set_value(a.set_back, back)
                values += 2 * back.size
            m.update()
        days = int(m.get_current_time())
        m.finalize()
        wall = time.perf_counter() - t0
        self1, kids1 = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({
        "swatplus_bmi": __version__, "protocol": PROTOCOL, "project": str(a.project), "days": days,
        "read": list(bufs), "not_in_project": [v for v in a.get if v not in bufs],
        "set_back": a.set_back or None, "values_exchanged": values, "wall_s": round(wall, 2),
        "swat_user_s": round(kids1.ru_utime - kids0.ru_utime, 2),
        "swat_sys_s": round(kids1.ru_stime - kids0.ru_stime, 2),
        "python_user_s": round(self1.ru_utime - self0.ru_utime, 2),
        "python_sys_s": round(self1.ru_stime - self0.ru_stime, 2),
        "load_1min": round(os.getloadavg()[0], 1),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
