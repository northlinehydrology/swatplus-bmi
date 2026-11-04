# Draft issue for swat-model/swatplus: recall diversions aren't limited to the water in the channel

Draft for Andrew to read, edit and post himself. Not posted. Separate from
the exchange mode issue (docs/upstream-issue.md).

---

**Title:** A recall diversion (negative flow) larger than a channel's inflow is recorded in full but not taken, so the channel water balance doesn't close

**Version:** 62.0.1 (SWAT-deg channel, `chandeg.con`). Not checked on the older channel type or on main.

**What happens.** A recall object with a negative daily flow is treated as a diversion (`command.f90`: "if negative flow (diversion), then remove nutrient mass"). Its flow is added to the receiving channel's inflow for the day. When the diversion is larger than the channel's other inflow, the channel's net inflow is negative. `ch_rtmusk.f90` adds inflow to storage only when it is positive:

```fortran
        !! add inflow to total storage
        if (ht1%flo > 1.e-6) then
          ...
          tot_stor(jrch) = tot_stor(jrch) + rto * ht1
        end if    ! ht1%flo > 1.e-6
```

So on those days the negative net inflow is skipped. Nothing is taken from channel storage, outflow is zero, and the excess part of the diversion disappears. `recall_day.txt` still reports the full diversion, and `channel_sd_day.txt` shows a negative `flo_in` with zero `flo_out`.

The nutrient part of a diversion is limited to what's in the channel (`recall_nut.f90`: "only take what is there"). The water isn't.

**Why it matters.** The days this happens are the low-flow days, which are when a withdrawal matters most. A model that represents a water user as a negative recall reports withdrawals the stream didn't supply, and overstates how much flow is left on those days.

**How to reproduce** (bundled `refdata/Osu_1hru`):

1. In `object.cnt`, set `rec` to 1 and add 1 to `obj`. The project's own `recall.con` already routes recall 1 ("inflow") to `sdc 1`.
2. Point `recall.rec` at a daily file (type 1) with a flow of -69.12 m3/day (0.0008 m3/s) every day, 2010–2023.
3. Turn on daily `channel_sd` and `recall` printing in `print.prt` and run.

In our test, with the diversion applied on 2,638 days, `recall_day.txt` recorded 182,339 m3 diverted. Of that, 7,539 m3 (4.1%) was never taken from the channel, on the 198 days when `flo_in` was negative. On those days the channel balance (inflow + precipitation − evaporation − seepage − outflow − change in storage) is off by the untaken part.

**A possible fix.** Limit the diversion to the water available (inflow plus channel storage), take that much, and record the amount actually taken in the recall output, the way `recall_nut` limits nutrient mass. Separately, the `flo` column header in `recall_day.txt` says `m^3/s`, but the values are m3/day (a recall of 86.4 raises channel inflow by 0.001 m3/s).

Water allocation already handles this correctly: a channel point of diversion takes at most the water routed that day (`wallo_withdraw.f90`, `rto = Min(1., rto)`), and the channel balance closes in our tests. So pointing users who need limited withdrawals to water allocation is an alternative to changing recall.

Andrew Sommerlot
Northline Hydrology
