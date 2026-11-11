# Draft issue for swat-model/swatplus: calibration.cal changes that SWAT+ reads but doesn't make

Draft for Andrew to read, edit and post himself. Not posted. It extends the earlier `chn` draft
(docs/upstream-issue-calibration-chn.md), which is one case of the first item.

---

**Title:** `calibration.cal`: many parameter changes are accepted without a message and change nothing

**Version:** 62.0.1.

Each parameter in the SWAT+ editor's `cal_parms.cal` (the one shipped with `refdata/Osu_1hru`) was tried alone: an `absval` change for every object, on `Osu_1hru` and `Ames_sub1`, compared with the run without it. Where a change moved no output, the source shows why. The full table is in swatplus-bmi's `docs/parameters-62.0.1.md`.

1. **Used only while starting up.** `main.f90.in` runs `proc_date_time` (line 41, which reads the weather generator), `proc_db` (42), `proc_hru` (71) and `proc_cha` (72) before `proc_cal` (121), which applies `calibration.cal`. These parameters are read only by routines those call, so a change reaches nothing:
   - `nperco`, `pperco`, `phoskd`, `psp`, `cmn`: `hrudb_init.f90` copies `bsn_prm` into each HRU (line 33 for `nperco`); the daily code reads the HRU's copy (`nut_nlch.f90`, line 93).
   - `usle_p` (`hydro_init`), `ovn` and `slope_len` (`time_conc_init`, `topohyd_init`), `dis_stream`, `field_len`, `field_ang` (`topohyd_init`), `ffcb` (`hydro_init`), `rsdco` (`plantparm_init`), `adj_pkr` and `dorm_hr` (`cli_initwgn`).
   - Channels: `chn`, `fps`, `fpn` (the rating curves, `sd_rating_curve`), `chk`, `hc_kh`, `hc_hgt`, `hc_ini`, `msk_co1`, `msk_co2`, `msk_x` (`sd_hydsed_init`).
2. **Never used:** `evlai` is read only by `basin_prm_default`. `init_slow` and `init_passive` are read only by `soil_nutcarb_init`, which nothing calls.
3. **Overwritten:** `sd_channel_sediment3.f90` line 80 sets `sd_ch(ich)%d50 = 1.0` on every call, so a `d50` change lasts until the first day's routing.
4. **The wrong variable:** `cal_parm_select`'s `tile_latk` case changes `hru(ielem)%sdr%lag`, the same as `tile_lag`. In `cal_conditions`, the plant `epco` case changes `lai_pot`, and the `temp` case indexes `tmp(ig)` with `ig` never set (it should be `ipg`, as in `precip`).
5. **No code:** 17 names in the editor's list have no `case` in `cal_parm_select`: `spcon`, `spexp`, `p_updis`, `rch_dox`, `rch_cbod`, `algae`, `organicn`, `ammonian`, `nitriten`, `organicp`, `disolvp`, `cherod`, `wd_rto`, `chseq`, `bedldcoef`, `chlar`, `seccir`. The plant (`plt`) rows `bio_e`, `hvsti`, `rdmx`, `t_opt` and `t_base` reach `cal_conditions`' plant branch, which handles only `phu_mat`, `epco`, `lai_pot` and `harv_idx`.
6. **Plant objects counted in plant-database rows:** for `plt` parameters, `cal_parmchg_read` sets the object count to `db_mx%plantparm` and `cal_conditions` then uses each number as an HRU (`pcom(ielem)`), so with more plants than HRUs it reads past the end of `pcom`.
7. **An unknown change type:** `chg_par` has no `case default`, so a misspelt type (`pctchange`) returns whatever the result variable held.

**Possible fixes:** apply `calibration.cal` before the start-up routines that derive values from parameters, or call them again for changed objects (as the `cn2` and `awc` cases already do with `curno` and `soil_awc_init`); make the `bsn_prm` copies in `hrudb_init` follow a change; remove the `d50` reset; fix the three wrong variables; warn on a name with no code, and on an unknown change type.

Found while adding parameter changes to swatplus-bmi (Nov 2026), by runs and by reading the source.

Andrew Sommerlot
Northline Hydrology
