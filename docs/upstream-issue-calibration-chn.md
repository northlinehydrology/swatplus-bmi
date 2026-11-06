# Draft issue for swat-model/swatplus: a change to chn in calibration.cal has no effect on routing

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `calibration.cal`: `chn` is changed after the channel rating curves are built, so routing never sees it

**Version:** 62.0.1.

`main.f90.in` calls `proc_cha` (line 72), whose `sd_hydsed_init` builds each channel's rating curve from Manning's n (`sd_rating_curve.f90`, lines 53–54). Calibration changes are applied later, in `proc_cal` (line 121, `cal_conditions` → `cal_parm_select`, `case ("chn")`). Nothing rebuilds the rating curves afterwards, and routing uses only the curves, so a `chn` change in `calibration.cal` is accepted without a message and changes nothing.

**Possible fix:** rebuild the rating curves for changed channels after `cal_conditions`, or warn that `chn` must be set in `hyd-sed-lte.cha`.

Found while calibrating a SWAT+ study (Nov 2026); confirmed by reading the source and with a run. swatplus-bmi 0.3.0's `check_calibration` warns about it.

Andrew Sommerlot
Northline Hydrology
