# Draft issue for swat-model/swatplus: a soil layer's bmix is never set below the mixing depth, so hru_carbvars prints whatever memory held

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `soil_module.f90`: `bmix` has no initial value, and layers below the biological mixing depth print unset memory in `hru_carbvars.txt`

**Version:** 62.0.1.

In `soil_module.f90` (line 21) the layer type declares `real :: bmix` without a value; the fields around it (`tillagef`, `tillagef_biomix`, `tillagef_tillmix`) are declared `= 0.`. `mgt_biomix.f90` sets `soil(jj)%ly(l)%bmix` layer by layer from the top and leaves the loop at the first layer below the mixing depth (line 177), so deeper layers never get a value. `soil_carbvar_write_legacy.f90` prints every layer's `bmix` in the `cons_bmix` column of `hru_carbvars.txt` and `.csv`.

A fresh process usually gets zeroed memory, so the column usually reads 0, but it isn't guaranteed. Running the Ames_sub1 example (daily printing, 1975–1979) with allocations filled with a pattern (`MallocPreScribble=1` on macOS) changes `cons_bmix` on 272,304 of 363,074 rows (0 becomes -3.03E-13) and nothing else in any output file. The same column differs when SWAT+ runs as a library inside another program, whose heap isn't fresh. Line 138 adds to a layer's `bmix`, so a layer that reaches it before an earlier call has set it would read the same memory; no other output changed in the run above.

**Possible fix:** declare `real :: bmix = 0.` (and `init_bmix = 0.`) in `soil_module.f90`.

Found Nov 7, 2026 by swatplus-bmi's nightly comparison of a stepped run against stock on a macOS runner; confirmed by reading the source and with the pattern-filled run above (`tests/test_unset_memory.py`).

Andrew Sommerlot
Northline Hydrology
