# Draft issue for swat-model/swatplus: a yearly recall is used outside its record

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `command.f90`, yearly recall (type 3): the year test uses `.or.`, so it is always true

**Version:** 62.0.1, `command.f90`, `case ("recall")`.

```fortran
              case (3)    !yearly
                if (time%yrc >= recall(irec)%start_yr .or. time%yrc <= recall(irec)%end_yr) then
```

Any year is either after the start or before the end, so the `else` (no flow outside the record) never runs, and years outside the record read whatever `recall(irec)%hd(1,time%yrs)` holds. The daily and monthly cases just above use `.and.`.

**Possible fix:** `.and.`, as for the daily and monthly cases.

Found by reading the source (Nov 2026).

Andrew Sommerlot
Northline Hydrology
