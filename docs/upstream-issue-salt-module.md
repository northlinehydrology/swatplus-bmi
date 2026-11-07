# Draft issue for swat-model/swatplus: the salt module can't run with aquifers or point sources

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** Salt module (62.0.1): four allocation and call gaps stop any run with aquifers or point sources

**Version:** 62.0.1. Found trying to run the `Osu_1hru` example with the eight salt ions on (`constituents.cs`, `salt_hru.ini`, `salt_aqu.ini`, `salt_channel.ini`, `salt_irrigation`).

1. **Salts without pesticides: segmentation fault.** `salt_hru_init.f90` allocates `cs_soil(ihru)%ly(ly)%salt`, but `cs_soil(ihru)%ly` itself is allocated only in `pesticide_init.f90` (`allocate (cs_soil(ihru)%ly(nly))`), which returns at once when there are no pesticides (`if (npmx == 0) return`). The allocation in `soils_init.f90` is commented out.
2. **Salts with pesticides and an aquifer: "Attempting to allocate already allocated variable 'cs_aqu'".** `aqu_initial.f90` allocates `cs_aqu(iaq)%salt` in the pesticide block and again in the salt block just below it.
3. **Point source salt is never read.** `recall_read_salt` (`salt_recall.rec`) is declared `external` in `hyd_connect` but never called, so `rec_salt` and `recsaltb_d` are never allocated. With salts on, `recall_salt` and `salt_balance` read them for every recall object.
4. `recall_salt.f90` writes `div_conc_salt`, which only `gwflow_read.f90` allocates, so a point source without gwflow fails there too.

Two more, which don't stop a run:

5. **Salt and water leave a channel on different schedules.** Water is routed by `ch_rtmusk`, but the salt split is `hcs2 = scoef * hcs3`. With a channel that starts with water, the first day's routing can empty the water while a third of the salt stays, and that salt then leaves with near-zero flows: `channel_salt_day.txt` prints a chloride concentration of 3.8e7 g/m3 on day 3. Mass balances, but the concentration doesn't follow the water.
6. **`hru_salt_day.txt`'s header has eight `gwup_<ion>` names the rows don't have** (159 names, 151 values), so every column from there on is labelled one block off; read by name, `uptk` looks like 1e8 kg/ha.

7. **The salt split uses a stale storage coefficient.** In `sd_channel_control3.f90`, `scoef` is computed only in the branch where the travel time is longer than the routing step (`if (rttime > time%dtm / 60.)`). In the other branch, and in the salt split after it (`hcs2%salt(isalt) = scoef * hcs3%salt(isalt)`), it keeps the value from the last call that computed it, for whichever channel and day that was (0 on the first call). Resetting it on every call changes `channel_salt_day.txt` from day 56 of the Osu_1hru salt run.

**Possible fixes:** allocate `cs_soil(ihru)%ly(nly)` in `salt_hru_init` (and `cs_hru_init`) when it isn't allocated; drop the salt allocation from the pesticide block of `aqu_initial`; call `recall_read_salt` after `recall_read` in `main.f90.in`; allocate `div_conc_salt` when salts are on, whether or not gwflow is; and compute `scoef` in both branches.

A run with salts, a pesticide, no aquifer and no point source does work.

Found by swatplus-bmi's tests (Nov 2026), and confirmed with a bounds-checked build and by reading the source.

Andrew Sommerlot
Northline Hydrology
