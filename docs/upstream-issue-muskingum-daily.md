# Draft issue for swat-model/swatplus: Muskingum routing on a daily step runs one step with substep coefficients

Draft for Andrew to read, edit and post himself. Not posted. Once it is, its number goes in
README.md, docs/design.md and patches/62.0.1/0004-muskingum-daily-opt-in.patch, and the patch is
dropped from the first SWAT+ release that fixes it.

---

**Title:** Muskingum routing (`rte_cha` 1) on a daily step resets the stability substeps but keeps their coefficients, so every reach delays ~0.63 day whatever its K

**Version:** 62.0.1: `sd_hydsed_init.f90`, `ch_rtmusk.f90`, `sd_channel_control3.f90`, `command.f90`. The same
on main as of 6f51536 (Oct 16, 2026).

## 1. The substeps are computed, then thrown away, but the coefficients are kept

`sd_hydsed_init.f90` splits the routing step into the substeps the Muskingum stability limit needs, and computes the
coefficients for one substep:

```fortran
      detmax = 2.* xkm * (1.- bsn_prm%msk_x)
      det = time%dtm / 60.      !hours
      sd_ch(i)%msk%substeps = 1
      if (det > detmax) then
        sd_ch(i)%msk%substeps = Int(det / detmax) + 1
      end if
      if (bsn_cc%rte == 0 .and. time%step <= 1) then
        sd_ch(i)%msk%substeps = 1
      end if
      sd_ch(i)%msk%nsteps = time%step * sd_ch(i)%msk%substeps
      ...
        det = det / sd_ch(i)%msk%substeps
        denom = 2. * xkm * (1. - bsn_prm%msk_x) + det
        sd_ch(i)%msk%c1 = (det - 2. * xkm * bsn_prm%msk_x) / denom
```

`ch_rtmusk.f90` then resets the substeps on every call when the model runs daily:

```fortran
      !! set for daily time step
      if (time%step == 1) then
        sd_ch(jrch)%msk%nsteps = 1
        sd_ch(jrch)%msk%substeps = 1
      end if
```

So each day runs a single Muskingum step with the whole day's inflow, using coefficients for a step of 24/N hours.
Because N is chosen so that 24/N sits just under `detmax = 2K(1 - X)`, the coefficients go to a fixed point that
does not depend on K: c1 = (1 - 2X) / (2(1 - X)), c2 = 1 / (2(1 - X)), c3 near 0; with `msk_x` 0.20, c1 0.375,
c2 0.625, c3 0. Every reach releases 37.5% of today's inflow today and 62.5% tomorrow, a mean delay of about
0.63 day, whatever its length, slope or roughness. `sd_hydsed_init.f90` itself keeps the substeps at 1 only for
`rte_cha` 0, so the reset in `ch_rtmusk.f90` looks meant for the variable storage method only.

## 2. With the substeps kept, lateral and tile flow come in twice

`command.f90` builds a channel's inflow hydrograph `tsin` from an HRU or routing unit as

```fortran
                case ("tot")   ! total flow
                  hyd_flo = ob(iob)%hyd_flo(iday,:) + (ob(iob)%hd(4)%flo + ob(iob)%hd(5)%flo) / time%step
```

On a sub-daily step `hyd_flo` holds surface runoff only (`flow_hyd_ru_hru.f90`), so this is right. On a daily step
`hru_hyds.f90` sets `hyd_flo(day_cur,1) = hd(1)%flo`, the total, so lateral and tile flow are counted twice. The day's
inflow is right today only because `sd_channel_control3.f90` overwrites it when there is one step:

```fortran
      if (sd_ch(ich)%msk%nsteps == 1) then
        ob(icmd)%tsin(1) = ht1%flo
      end if
```

With item 1 fixed (nsteps above 1 on a daily step) the overwrite stops and the double count reaches the channel. As
shipped it already happens on the first day of an `rte_cha` 1 run, before `ch_rtmusk.f90` has reset nsteps, where
`rto = Min(1., rto)` hides it.

## 3. Two smaller points in the same loop of `ch_rtmusk.f90`

- `inflo` is a saved local set only when `ht1%flo > 1.e-6`, so on a day with no inflow the Muskingum step routes the
  inflow of the last channel routed (`c1 * inflo`, capped by the channel's own storage), and keeps it as `in1_vol`
  for the next step.
- `inflo_rate = inflo / 86400.` gives the inflow rating curve (`ch_rcurv%in2`, used for the travel time in
  `sd_channel_control3.f90`) a day's rate from a substep's volume; it should divide by the substep's length, `dts`.

## Effect

Rappahannock River above Fredericksburg, VA (1,183 channels; `msk_co1` 0.75, `msk_co2` 0.25, `msk_x` 0.20, Manning's n
0.05): K median 0.62 h (0.13-1.7 h, p10-p90), N median 25 (9-118). Headwater to the outlet is 50 reaches (median, up
to 77), so the mean delay as run is about 32 days, against a summed K of 1.8 days. Water year 2014, stock 62.0.1,
`rte_cha` 1 on a daily step: the outlet hydrograph lags the `rte_cha` 0 run by 31-36 days, the peak falls from 306 to
70 m3/s (observed 1,419), and daily NSE from 0.18 to -0.02. Doubling Manning's n moves the lag by one day.

## Proposed fix

```fortran
!! ch_rtmusk.f90: keep the substeps for Muskingum
      if (time%step == 1 .and. bsn_cc%rte == 0) then
        sd_ch(jrch)%msk%nsteps = 1
        sd_ch(jrch)%msk%substeps = 1
      end if
      ...
        if (ht1%flo <= 1.e-6) inflo = 0.          ! or set inflo = 0. before the ht1 test
      ...
        inflo_rate = inflo / dts

!! sd_channel_control3.f90: the day's inflow on any daily step
      if (time%step == 1) then
        ob(icmd)%tsin(1) = ht1%flo
      end if
```

With these, the same project and year: the outlet is 1 day behind the `rte_cha` 0 run (on the observed day), peak 156
m3/s, NSE 0.18; doubling n adds a day (peak 130); the channel water balance closes (-0.1% of inflow, as before). Stock
62.0.1 on an hourly step (`time.sim` step 24, `rte_cha` 1, where the reset doesn't apply) gives the same 1-day lag
(peak 193 m3/s, NSE 0.23). On the bundled `refdata/Osu_1hru` (one 3.4 km channel, `rte_cha` set to 1, a one-day
pulse of 1e6 m3 into the channel on June 30, 2010), the mean delay of what the pulse adds to the outflow is 0.64 day at
Manning's n 0.05 and 0.69 at 0.10 as shipped (36% leaves on the pulse's day), and 0.050 and 0.111 day with the fix:
the reach's K, doubling with n.

## Not in the fix, noted

- `sd_hydsed_init.f90`: `in1_vol = rcurv%flo_rate / (86400. / nsteps)` divides a rate by a time (a volume per step is
  `flo_rate * 86400. / nsteps`), but `rcurv` hasn't been set for the channel at that point, so it is 0 either way.
- `sd_rating_curve.f90` and `sd_hydsed_init.f90`: `a = b * dep + dep / chss` is not an area (dep, not dep**2), and the
  bottom width (`b = chw - 2 * chd * chss`) and the wetted perimeter (`sqrt(dep**2 * (1 + 1/chss**2))`) read the side
  slope two different ways. For channels 2.2-4.8 m wide with `side_slp` 0.5, the hydraulic radius at bankfull is
  1.2-1.6 times that of the trapezoid the bottom width implies, so K is shorter by 1.15-1.4 times. A geometry choice
  for the developers; it changes K on every step.

Related: #360 (the gwflow exchange isn't delivered to Muskingum routing), a different path through the same routine.
