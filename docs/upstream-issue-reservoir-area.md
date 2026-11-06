# Draft issue for swat-model/swatplus: a reservoir's surface area at the principal spillway is psa^0.9

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `res_initial.f90`: with br2 = 0.9 the area at the principal volume is psa^0.9 ha, not psa

**Version:** 62.0.1, `res_initial.f90` lines 72–83, `res_control.f90` line 194.

The surface area is computed from the volume as `area_ha = br1 * vol ** br2`. When br2 is capped at 0.9, or the emergency area isn't usable, `br1` is set as

```fortran
            res_ob(ires)%br2 = 0.9
            res_ob(ires)%br1 = (res_ob(ires)%psa / res_ob(ires)%pvol) ** 0.9
```

so at the principal volume the area is `(psa / pvol)**0.9 * pvol**0.9 = psa**0.9`, not psa. For a lake entered with psa = 1,049 ha the model uses 523 ha at the principal spillway, which halves evaporation and precipitation on the lake.

**Possible fix:** `br1 = psa / pvol ** br2`, so the curve passes through (pvol, psa) for any br2.

Found while building a reservoir for a SWAT+ study (Nov 2026); confirmed by reading the source and with a run.

Andrew Sommerlot
Northline Hydrology
