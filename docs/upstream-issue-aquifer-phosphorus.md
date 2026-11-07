# Draft issue for swat-model/swatplus: an aquifer's soluble P outflow follows recharge, not return flow

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `aqu_1d_control.f90`: an aquifer's mineral P outflow is the day's recharge volume times `minp`, and recharge P isn't kept

**Version:** 62.0.1, `aqu_1d_control.f90`.

```fortran
      !! compute mineral p flow (constant concentration) from aquifer - m^3 * ppm * 1000 kg/m^3 = 1/1000
      aqu_d(iaq)%minp = ob(icmd)%hin%flo * aqu_dat(iaq)%minp / 1000.
      ob(icmd)%hd(1)%solp = aqu_d(iaq)%minp
```

The comment describes a constant concentration in the aquifer's outflow, but the volume used is `ob(icmd)%hin%flo`, the water reaching the aquifer that day (recharge), not `ob(icmd)%hd(1)%flo`, the return flow computed a few lines above. So:

- the P reaching the stream is the day's recharge times `minp`: none on a day with no recharge, however much the aquifer discharges, and the most on the wettest days, whatever the return flow;
- the P arriving with recharge (`ob(icmd)%hin%solp`) is never added to anything, so P doesn't balance across the aquifer;
- `aqu_d(iaq)%minp` holds kg, but the output header and `aquifer_module.f90` label it kg/ha P.

Nitrate a few lines above is tracked as a mass in storage (`no3_st`) with recharge, return flow, seepage and loss.

**Possible fix:** use the return flow, `ob(icmd)%hd(1)%flo * aqu_dat(iaq)%minp / 1000.`, if a constant concentration is meant; or track P in storage the way nitrate is. Either way, divide by the area for the kg/ha output.

Found by a study coupling SWAT+ to a groundwater transport model (Nov 2026), confirmed by reading the source.

Andrew Sommerlot
Northline Hydrology
