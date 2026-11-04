# Draft issue for swat-model/swatplus: aquifer withdrawals deliver less water than they remove

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** Water allocation from an aquifer: the place of use receives rto × (1 − rto) × S while the aquifer loses rto × S

**Version:** 62.0.1, `wallo_withdraw.f90`, `case ("aqu")`.

Storage is reduced before the withdrawal is computed from it:

```fortran
          aqu_d(j)%stor = (1. - rto) * aqu_d(j)%stor
          aqu_d(j)%no3_st = (1. - rto) * aqu_d(j)%no3_st
          aqu_d(j)%minp = (1. - rto) * aqu_d(j)%minp
          outflo_om%flo = rto * aqu_d(j)%stor * 10. * aqu_prm(j)%area_ha
          outflo_om%no3 = rto * aqu_d(j)%no3_st * aqu_prm(j)%area_ha
          outflo_om%solp = rto * aqu_d(j)%minp * aqu_prm(j)%area_ha
```

So the aquifer loses rto × S, and the place of use receives rto × (1 − rto) × S. Nitrate and phosphorus are affected the same way. The difference is small when the withdrawal is a small share of storage, and grows as the aquifer drains. At rto = 1 the aquifer is emptied and the place of use receives nothing.

**Example** (bundled `refdata/Osu_1hru`, one place of use taking 0.002 m3/s from `aqu001`, compared with the same run without it): the aquifer loses 172.8 m3/day, and `water_allo_day.txt` shows 171.3 m3/day delivered.

**Possible fix:** compute the outflow before reducing storage (move the three `outflo_om` lines above the three storage lines).

Andrew Sommerlot
Northline Hydrology
