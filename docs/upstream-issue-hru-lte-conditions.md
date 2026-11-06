# Draft issue for swat-model/swatplus: hru_lte end-of-growth decision table checked with the wrong index

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `hru_lte_control.f90`: `call conditions (isd, icmd)` where the table number `iend` belongs

**Version:** 62.0.1, `hru_lte_control.f90`, lines 199–203.

```fortran
            iend = hlt(isd)%end
            d_tbl => dtbl_lum(iend)
            call conditions (isd, icmd)
            call actions (isd, icmd, iend)
```

The second argument of `conditions` is the decision table's number. The start-of-growth block a few lines up passes `istart` (`call conditions (isd, istart)`); this one passes the command number, so the end-of-growth conditions are evaluated against another table's index. `conditions` uses it for the days since an action (`pcom(ob_num)%dtbl(idtbl)%days_act(iac)`) and passes it to `cond_real`, so those read another table's counters, or past the end of the array.

**Possible fix:** `call conditions (isd, iend)`.

Found by reading the source (Nov 2026).

Andrew Sommerlot
Northline Hydrology
