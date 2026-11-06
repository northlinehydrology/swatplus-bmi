# Draft issue for swat-model/swatplus: the reservoir weir release reads the wetland's crest height

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `res_hydro.f90`, `weir` release: the crest height comes from `wet_ob(jres)` when the routine runs for reservoir `jres`

**Version:** 62.0.1, `res_hydro.f90`, `case ("weir")`.

`res_hydro` is called for reservoirs (`res_control.f90`, with `jres` the reservoir number) and for wetlands (`wetland_control.f90`, with `jres` the HRU number). The weir release takes the crest height from the wetland array in both cases:

```fortran
              res_h = vol / wsa1     !m
              hgt_above = max(0., res_h - wet_ob(jres)%weir_hgt)    !m
```

(and again at line 219). For a reservoir, `wet_ob(jres)` is the wetland of HRU number `jres`, unrelated to the reservoir, or out of range. Reservoirs have their own crest height, `res_ob(ires)%weir_hgt`, set in `res_initial.f90` from the principal volume and area.

The discharge is then `(wbody_wb%area_ha*0.45) * c * w * hgt_above ** k`. The comment above it describes a paddy weir ("configured for 5m width and 100mm weir height"), and the factor of 0.45 × the water area in ha applies to reservoirs too.

**Possible fix:** use `wbody`'s own crest height (pass it in, or select `res_ob` or `wet_ob` by the caller), and check whether the area factor is meant for reservoirs.

Found while building a reservoir for a SWAT+ study (Nov 2026); confirmed by reading the source and with a run.

Andrew Sommerlot
Northline Hydrology
