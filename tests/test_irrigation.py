"""HRU irrigation (docs/design.md, "HRU irrigation"): read-only, one value per HRU on the
hru grid, the day just done.

``land_irrigation_water__volume_flow_rate`` is the water the day's irrigation put on each HRU,
from any source (a decision table's irrigate or irr_demand action, a scheduled irrm, water
allocation): what reached the soil and what ran off, as SWAT+'s water balance takes them in.
SWAT+ zeroes it at the start of the next day, before the pause, so the wrapper keeps it at the
end of the day. What an application loses (irr.ops' efficiency) never enters SWAT+'s balance
and isn't in it. A coupled model that pumps irrigation from groundwater wells pumps this.

Osu_1hru irrigates its rice paddy from an outside source (``pad_irr_r``, ponding) on about
700 of its days; the read is its irr column in hru_wb_day.txt times the HRU's area.
"""

import numpy as np

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi
from test_reading import daily_table

IRR = "land_irrigation_water__volume_flow_rate"


def test_irrigation_read_is_swats():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Osu_1hru", "Osu_1hru-irrigation", daily=True)))
    assert m.get_var_grid(IRR) == m.get_var_grid("soil_water__percolation_rate")
    assert m.get_var_units(IRR) == "m3 s-1"
    assert IRR in m.get_output_var_names() and IRR not in m.get_input_var_names()
    (h,) = m.get_grid_object_names(m.get_var_grid(IRR))
    area = 10.0 * 10000.0                                        # Osu_1hru's one HRU, 10 ha, in m2
    got = []
    while m.get_current_time() < m.get_end_time():
        m.update()
        got.append(m.get_value(IRR, np.empty(1))[0] * 86400.0)   # m3 on the day
    run = m.run_dir
    m.finalize()
    wb = daily_table(run / "hru_wb_day.txt")[h]["irr"]           # mm
    got = np.array(got)
    assert (got > 0).sum() > 500
    np.testing.assert_allclose(got / area * 1000.0, wb, rtol=5e-4, atol=5e-4)
