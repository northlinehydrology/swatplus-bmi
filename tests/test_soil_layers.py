"""Each HRU soil layer as SWAT+ keeps it (read only, 0.7.0).

What a groundwater model needs to size and place the water it gives back to
the soil (soil_layer_water__groundwater_inflow_rate): where each layer ends,
the water in it and how much it holds, all as SWAT+ keeps them, with the
water depths above the wilting point (st, fc, ul). SWAT+ adds a 10 mm top
layer to the soil's file layers, with the first file layer's properties.
Test projects: Osu_1hru and Ames_sub1 (12 HRUs).
"""

import numpy as np
import pytest

from conftest import EXCHANGE, need, project
from swatplus_bmi import SwatPlusBmi

BOTTOM = "soil_layer_bottom__depth"
THICK = "soil_layer__thickness"
WATER = "soil_layer_water__depth"
WP = "soil_layer_water__wilting_point_depth"
FC = "soil_layer_water__field_capacity_depth"
SAT = "soil_layer_water__saturation_depth"
K = "soil_layer__saturated_hydraulic_conductivity"
LAYERS = (BOTTOM, THICK, WATER, WP, FC, SAT, K)
SW = "soil_water__depth"


def values(m, name):
    return m.get_value(name, np.empty(m.get_grid_size(m.get_var_grid(name))))


def soil_file_layers(run, soil):
    """{column: [values by file layer]} for a soil in soils.sol."""
    lines = (run / "soils.sol").read_text().splitlines()
    head = [h.lower() for h in lines[1].split()]          # editor files: dp, soil_k; older: DEPTH, K
    head = [{"depth": "dp", "k": "soil_k"}.get(h, h) for h in head]
    cols = head[head.index("dp"):]
    for i, line in enumerate(lines):
        f = line.split()
        if f[:1] == [soil]:
            rows = [l.split() for l in lines[i + 1:i + 1 + int(f[1])]]
            return {c: np.array([float(r[j]) for r in rows]) for j, c in enumerate(cols)}
    raise ValueError(soil)


def hru_soils(run):
    """{HRU name: its soil's name} from hru-data.hru."""
    rows = [l.split() for l in (run / "hru-data.hru").read_text().splitlines()[2:]]
    return {r[1]: r[4] for r in rows if len(r) > 4}


def awc_factor(run):
    """The change calibration.cal makes to every layer's awc (Osu_1hru lowers it by 62.6%);
    1 with none. Only a change with no conditions, for every object, is read."""
    cal = run / "calibration.cal"
    if not cal.exists():
        return 1.0
    for line in cal.read_text().splitlines()[3:]:
        f = line.split()
        if f[:2] == ["awc", "pctchg"] and all(x == "0" for x in f[3:]):
            return 1 + float(f[2]) / 100
    return 1.0


def by_hru(m, name):
    g = m.get_var_grid(name)
    v = values(m, name)
    out = {}
    for n, x in zip(m.get_grid_object_names(g), v):
        out.setdefault(n.split(":")[0], []).append(x)
    return {h: np.array(x) for h, x in out.items()}


def mm_average(file_dp, file_values, top, bottom):
    """A property over (top, bottom] mm as SWAT+ builds a layer from the file's: the average
    of its millimetre-by-millimetre values (soils_init)."""
    mm = np.arange(int(round(top)) + 1, int(round(bottom)) + 1)
    return file_values[np.searchsorted(file_dp, mm)].mean()


def test_the_layers_are_the_soil_files():
    """Bottoms, thicknesses, conductivity and field capacity (awc x thickness, after the
    project's calibration) from soils.sol, HRU by HRU. SWAT+ adds a 10 mm top layer, and with
    soil_lyr_depths.sol (Ames_sub1) puts the soil on those depths, each layer the millimetre
    average of the file's (5 file layers become 16); without it each layer is a file layer."""
    need(EXCHANGE)
    for name, years in (("Osu_1hru", None), ("Ames_sub1", (1975, 1975))):
        m = SwatPlusBmi()
        m.initialize(str(project(name, f"layers-{name}", years=years)))
        m.update()
        for var in LAYERS:
            assert m.get_var_grid(var) == m.get_var_grid("soil_layer__temperature")
            assert var in m.get_output_var_names() and var not in m.get_input_var_names()
        got = {v: by_hru(m, v) for v in LAYERS}
        soils = hru_soils(m.run_dir)
        assert len(got[BOTTOM]) == m.get_grid_size(m.get_var_grid(SW))
        relayered = (m.run_dir / "soil_lyr_depths.sol").exists()
        for hru, soil in soils.items():
            f = soil_file_layers(m.run_dir, soil)
            b = got[BOTTOM][hru] * 1000                        # mm
            assert b[0] == pytest.approx(10) and b[-1] == pytest.approx(f["dp"][-1])
            assert (np.diff(b) > 0).all()
            if not relayered:
                np.testing.assert_allclose(b[1:], f["dp"], rtol=1e-6)
            thick = np.diff(np.concatenate([[0], b]))
            np.testing.assert_allclose(got[THICK][hru] * 1000, thick, rtol=1e-4)
            tops = b - thick
            k = [mm_average(f["dp"], f["soil_k"], t, z) for t, z in zip(tops, b)]
            np.testing.assert_allclose(got[K][hru] * 3_600_000, k, rtol=1e-4)
            awc = np.array([mm_average(f["dp"], f["awc"], t, z) for t, z in zip(tops, b)])
            np.testing.assert_allclose(got[FC][hru] * 1000, awc * awc_factor(m.run_dir) * thick,
                                       rtol=1e-4)
            assert (got[WP][hru] > 0).all() and (got[FC][hru] < got[SAT][hru]).all()
        m.finalize()
    assert relayered                                          # Ames_sub1 checked the other way


def test_layer_water_adds_up_to_the_hrus_soil_water_every_day():
    need(EXCHANGE)
    m = SwatPlusBmi()
    m.initialize(str(project("Ames_sub1", "layers-sum", years=(1975, 1975))))
    hrus = m.get_grid_object_names(m.get_var_grid(SW))
    while m.get_current_time() < m.get_end_time():
        m.update()
        water = by_hru(m, WATER)
        sw = values(m, SW)
        total = np.array([water[h].sum() for h in hrus])
        np.testing.assert_allclose(total, sw, rtol=1e-5, atol=1e-7)
        sat = by_hru(m, SAT)
        assert all((water[h] <= sat[h] * (1 + 1e-6)).all() for h in hrus)
    m.finalize()
