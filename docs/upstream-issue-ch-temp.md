# Draft issue for swat-model/swatplus: stream temperature's groundwater term and units

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `ch_temp.f90`: the groundwater inflow is never filled for aquifer projects, and the mixing divides flows in mixed units

**Version:** 62.0.1, `ch_temp.f90` (component mixing model, last updated 03.11.2025).

1. **The groundwater term is always zero for aquifer projects.** The groundwater inflow is `q_gw = hdsep1%flo_gwsw / 86400`, from the channel's hydrograph separation (`ob(icmd)%hdsep_in`). In 62.0.1 `flo_gwsw` is set only in `command.f90` (passed from object to object, and zeroed), `sd_channel_control3.f90` (carried through channel storage) and `ch_temp.f90` (read). No aquifer routine fills it; the only `hdsep` writers are `hru_hyds.f90` (surface runoff, saturation excess, lateral flow) and `ru_control.f90`. So with `aqu_1d_control` aquifers, `gw_contr` is zero and aquifer return flow mixes in at the upstream temperature. (With gwflow on, `ch_temp` skips the term and gwflow's heat routines handle it.)

2. **The mixing divides flows in different units.** `tw_local = (sno_contr + gw_contr + lat_contr + surf_contr) / q_ru_wyld`, where `q_ru_wyld` is a sum of HRU water yields in mm (`hwb_d%wateryld`, summed over the routing units' HRUs without area weights; only snowmelt is weighted by `frac`), and `gw_contr` uses `q_gw` in m3/s. `tw_init` then mixes the upstream flow in m3/s (`ht1%flo/86400`) with `q_ru_wyld` in mm. So each source's share of the mix isn't its share of the water, and it changes with HRU size and count.

3. **For the docs:** in `channel_sd_day.txt`, `ch_temp`'s result is the unlabeled column before `water_temp` (header `null`, `ch_out_d%temp`). `water_temp` itself is the older regression, 5 + 0.75 × the air temperature (`wtemp`), which goes below 0 °C in winter.

**Possible fix:** fill `flo_gwsw` from the aquifer's return flow when an aquifer feeds a channel (`command.f90`, where `hdsep_in` is summed), and put every term of the mix in m3/s (area-weighted water yields).

Found by a study's basin model build (Nov 2026), confirmed by reading the source.

Andrew Sommerlot
Northline Hydrology
