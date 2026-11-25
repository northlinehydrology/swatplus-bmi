# Draft issue for swat-model/swatplus: irrigation from a channel takes no water from the channel

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `irrigate` from a channel (`cha`/`sdc`) reduces `ch_stor`, which the routing overwrites from `tot_stor`, so the channel never loses the water

**Version:** 62.0.1, `actions.f90` (`case ("irrigate")`, `case ("cha", "sdc")`) and `ch_rtmusk.f90`; the same on main as
of 6f51536 (Oct 16, 2026).

The decision table's `irrigate` action takes its water from the channel's storage:

```fortran
            case ("cha", "sdc")
              if (ch_stor(iob)%flo > irrig(j)%demand) then
                rto = irrig(j)%demand / ch_stor(iob)%flo
              ...
              irrig(j)%water = rto * ch_stor(iob)
              ch_stor(iob) = rto1 * ch_stor(iob)
```

But `ch_rtmusk.f90` routes from `tot_stor`, which it carries from the day before (`tot_stor = ch_stor + fp_stor` at
the end of each day) and which the action doesn't change:

```fortran
          tot_stor(jrch) = tot_stor(jrch) + rto * ht1      ! the day's inflow added to yesterday's tot_stor
          ...
            ch_stor(jrch) = tot_stor(jrch)                 ! ch_stor set again from it
```

So the reduced `ch_stor` is used only as `ch_stor_init` in the day's storage change, then overwritten. The water
reaches the field (`irrig(j)%applied`) but never leaves the channel: the channel's outflow and storage are what they
would be with no irrigation, and the basin gains the irrigation water from nowhere. `wet_irrp.f90` (paddy irrigation
from a channel) takes from `ch_stor` the same way. Irrigation from a reservoir is not affected: `res(iob)` is the
reservoir's own state, and `res_control` starts from it.

**Example** (bundled `refdata/Osu_1hru`, its paddy table `pad_irr_r` set to `irrigate cha 1` with 1 mm per action
(100 m3 on the 10 ha HRU); the channel's bed seepage `k` set to 0 and `scoef` to 0.5 in `parameters.bsn`, so the
channel keeps water from day to day): the first two actions take 100 m3 each from `cha01`. Compared with the same
project irrigating 0 mm, `cha01`'s outflow and storage in `channel_sd_day.txt` are identical to the last digit until
the field's return flow reaches the channel two days later. The channel's printed daily balance (`flo_in + precip -
evap - seep - flo_out - Δflo_stor`) closes to under 1 m3 on every day an action took 100 m3.

**Possible fix:** take the water from `tot_stor` too, for example after the two `ch_stor` lines:

```fortran
              tot_stor(iob) = ch_stor(iob) + fp_stor(iob)       ! the routing starts from tot_stor
```

and the same in `wet_irrp.f90`. (The day's channel water balance then shows the withdrawal as a fall in storage that
no column explains; a withdrawal column in `channel_sd_day` would close it.)

Andrew Sommerlot
Northline Hydrology
