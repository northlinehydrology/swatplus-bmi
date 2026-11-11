# Draft issue for swat-model/swatplus: calibration.cal changes that SWAT+ reads but doesn't make

Draft for Andrew to read, edit and post himself. Not posted. It extends the earlier `chn` draft
(docs/upstream-issue-calibration-chn.md), which is one case of the first item.

---

**Title:** `calibration.cal`: many parameter changes are accepted without a message and change nothing

**Version:** 62.0.1.

Each parameter in the SWAT+ editor's `cal_parms.cal`, as 2.2.0 wrote it (the one shipped with `refdata/Osu_1hru`) and as 3.1.0 has it, was tried alone: an `absval` change for every object, on `Osu_1hru` and `Ames_sub1`, compared with the run without it. Where a change moved no output, the source shows why. The full table is in swatplus-bmi's `docs/parameters-62.0.1.md`.

1. **Used only while starting up.** `main.f90.in` runs `proc_date_time` (line 41, which reads the weather generator), `proc_db` (42), `proc_hru` (71) and `proc_cha` (72) before `proc_cal` (121), which applies `calibration.cal`. These parameters are read only by routines those call, so a change reaches nothing:
   - `nperco`, `pperco`, `phoskd`, `psp`, `cmn`: `hrudb_init.f90` copies `bsn_prm` into each HRU (line 33 for `nperco`); the daily code reads the HRU's copy (`nut_nlch.f90`, line 93).
   - `usle_p` (`hydro_init`), `ovn` and `slope_len` (`time_conc_init`, `topohyd_init`), `dis_stream`, `field_len`, `field_ang` (`topohyd_init`), `ffcb` (`hydro_init`), `rsdco` (`plantparm_init`), `adj_pkr` and `dorm_hr` (`cli_initwgn`).
   - Channels: `chn`, `fps`, `fpn`, `chss` (the rating curves, `sd_rating_curve`), `chk`, `hc_kh`, `hc_hgt`, `hc_ini`, `msk_co1`, `msk_co2`, `msk_x`, `n_setl`, `p_setl`, `bank_exp` (`sd_hydsed_init`).
   - Aquifers: `dep_wt_init`, `flo_init_mm` and `cbn_init`, which `aqu_initial` copies into the aquifer's starting state; and `nperco_lchtile`, copied into each HRU by `hrudb_init`.
2. **Overwritten, or never read:** a soil's `lab_p`, `cbn`, `hum_c_n` and `hum_c_p` change the soil's pools in `proc_cal`, and `soil_nutcarb_init`, called right after (`main.f90.in` line 126), sets those pools again from the soil's inputs. (`hum_c_n` and `hum_c_p` are also ratios in the soil's file, while the case changes the pools.) `evlai` is read only by `basin_prm_default`; `rs6`, `rs7` and `rk6` only by `ch_read_nut`; `flo_dist` by nothing.
3. **Overwritten:** `sd_channel_sediment3.f90` line 80 sets `sd_ch(ich)%d50 = 1.0` on every call, so a `d50` change lasts until the first day's routing.
4. **The wrong variable:** `cal_parm_select`'s `tile_latk` case changes `hru(ielem)%sdr%lag`, the same as `tile_lag`. In `cal_conditions`, the plant `epco` case changes `lai_pot`, and the `temp` case indexes `tmp(ig)` with `ig` never set (it should be `ipg`, as in `precip`).
5. **No code:** 17 names in the editor's 2.2.0 list have no `case` in `cal_parm_select`: `spcon`, `spexp`, `p_updis`, `rch_dox`, `rch_cbod`, `algae`, `organicn`, `ammonian`, `nitriten`, `organicp`, `disolvp`, `cherod`, `wd_rto`, `chseq`, `bedldcoef`, `chlar`, `seccir`. In the editor's 3.1.0 list, the HRU `lai_pot`, `harv_idx` and `phu_mat` (moved from `plt` in 3.1.0), `flood_sedfrac` (3.0.0's new name for `chseq`) and `part_size` have none either, so a calibration made with the current editor changes nothing for them. The plant (`plt`) rows `bio_e`, `hvsti`, `rdmx`, `t_opt` and `t_base` reach `cal_conditions`' plant branch, which handles only `phu_mat`, `epco`, `lai_pot` and `harv_idx`.
6. **Plant objects counted in plant-database rows:** for `plt` parameters, `cal_parmchg_read` sets the object count to `db_mx%plantparm` and `cal_conditions` then uses each number as an HRU (`pcom(ielem)`), so with more plants than HRUs it reads past the end of `pcom`.
7. **An unknown change type:** `chg_par` has no `case default`, so a misspelt type (`pctchange`) returns whatever the result variable held.
8. **Not calibration, found on the way:** `aqu_initial.f90` line 172 computes the starting storage from `aqu_d(iaqdb)%dep_wt`, with `iaqdb` the database row (`ob(iob)%props`); the line before sets `aqu_d(iaq)%dep_wt`. An aquifer whose database row isn't its own number starts with another aquifer's water table, or zero.

**Possible fixes:** apply `calibration.cal` before the start-up routines that derive values from parameters, or call them again for changed objects (as the `cn2` and `awc` cases already do with `curno` and `soil_awc_init`); make the `bsn_prm` copies in `hrudb_init` follow a change; remove the `d50` reset; fix the three wrong variables; warn on a name with no code, and on an unknown change type.

The swatplus-calibration-test-suite (0.1.0, 2023) would find the first kind as a difference between a change in `calibration.cal` and the same change in the parameter's file; these are 62.0.1 results, from runs and the source.

Found while adding parameter changes to swatplus-bmi (Nov 2026), by runs and by reading the source.

Andrew Sommerlot
Northline Hydrology
