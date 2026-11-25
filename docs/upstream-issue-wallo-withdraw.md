# Draft issue for swat-model/swatplus: a place of use's duty is read with the wrong indices in `wallo_withdraw.f90`

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** Water allocation: `wallo_withdraw` reads `pou(ipou)%pod(ipod)%duty` with a list position and a POD number, so places of use past the first point of diversion get nothing

**Version:** 62.0.1, `wallo_withdraw.f90`; the same on main as of 6f51536 (Oct 16, 2026).

`wallo_control` calls `wallo_withdraw (ipod, ipous)`, where `ipod` is the point of diversion's number and `ipous` is
the place of use's position in that point of diversion's list (`pod(ipod)%pou(ipous)`). `wallo_withdraw` then sizes
the withdrawal from:

```fortran
      if (wallo_comp == "y") then
        wdraw_max = pou(ipou)%pod(ipod)%duty - poud_met(ipou)%pod(ipod)%deliv
      else
        wdraw_max = pou(ipou)%pod(ipod)%duty
      end if
      ...
      pod_num = pod(ipod)%pou(ipou)%pod_num
      pou_num = pod(ipod)%pou(ipou)%num
```

So the duty is `pou(<position>)%pod(<POD number>)`. The entry meant is `pou(pou_num)%pod(pod_num)`, which the routine
computes a few lines later and uses for everything after (the minimum flow, `frac`, the delivery, `poud_met`). Only the
size of the withdrawal is wrong, and it's credited to the right place of use. As a result:

- **Several places of use on one point of diversion** each withdraw the duty of the place of use whose number equals
  their position in the list. Listed out of numbering order, they swap demands, and one takes more than its own.
- **A place of use on any point of diversion but the file's first** reads `pou(k)%pod(n)` with `n` past the end of
  `pou(k)%pod(:)`. Without bounds checking, that is whatever the heap holds there, mostly 0. Since a channel has one
  point of diversion (`sd_ch%wallo_pod`), a project with places of use on several channels gets nothing on all
  channels but the first.

**Example** (bundled `refdata/Osu_1hru`, one channel `cha01`, places of use of type `out` taking from it, 60 days,
demands of 0.0010 m3/s for `user01` and 0.0002 m3/s for `user02`):

| case | 62.0.1 | with the fix below |
|---|---|---|
| `user01` alone, on POD 1 | 1,687.8 of 5,184 m3 (flow-limited) | 1,687.8 |
| `user02` listed first on POD 1, then `user01` | `user01` 279.5; `user02` 1,687.8 of 1,036.8 asked, over its demand on 21 days | `user01` 1,587.9; `user02` 379.3; neither over |
| `user01` alone, its POD the file's second (after a POD with no places of use) | **0.0** | 1,687.8 |

In a 9-channel basin with one place of use per channel, the places of use on PODs 2 to 9 got nothing over 30 days,
except one that got 17,355 m3 of stray values from a dry channel, while their channels carried up to 2.8 m3/s. With the
fix, each got what its channel's flow allowed.

**Possible fix:** compute `pod_num` and `pou_num` first, and index the duty (and, on the compensation path, the
delivery) by them:

```fortran
      pod_num = pod(ipod)%pou(ipou)%pod_num
      pou_num = pod(ipod)%pou(ipou)%num

      if (wallo_comp == "y") then
        wdraw_max = pou(pou_num)%pod(pod_num)%duty - poud_met(pou_num)%pod(pod_num)%deliv
      else
        wdraw_max = pou(pou_num)%pod(pod_num)%duty
      end if
```

**Related, found while tracing this (not fixed by the change above):**

1. *Compensation calls `wallo_withdraw` with other arguments.* In `wallo_control`, the compensation loop calls
   `wallo_withdraw (ipodu, ipou)` with a POD's position in the place of use's list and a place of use number. The
   routine takes its arguments as a POD number and a position in that POD's list, so with compensation on, both the
   POD and the place of use can be the wrong ones.
2. *`fin` is set by the loop index.* In `wallo_control`,
   `do ipou = 1, pod(ipod)%pous; pou(ipou)%fin = ...` uses the loop index as a place of use number. It should be
   `pod(ipod)%pou(ipou)%num`, as in the loop above it.
3. *`poud_met`'s delivery is overwritten, not added to.* In `wallo_withdraw`,
   `poud_met(pou_num)%pod(pod_num)%deliv = pou(pou_num)%pod(pod_num)%deliv + outflo_om%flo` adds to `pou`'s `deliv`,
   not `poud_met`'s own.
4. *The `irr` place of use type doesn't run as written.* In `wallo_pou_deliv`, the `irr` loop (`do ird = ...`) tests
   `irrig(j)%demand` before it sets `j`, and then sets `j` and `id` from `pou(ipou)%irr%hru(ihru)` and `dtbl_num(ihru)`
   rather than `(ird)`. In `wallo_start_day`, the `irr` demand is summed as
   `irrig(j)%demand * hru(ihru)%area_ha * 10.`, with the loop index `ihru` where the HRU number `j` is meant (and
   `irrig(j)%demand` is already in m3 when an `irr_demand` action sets it).

Andrew Sommerlot
Northline Hydrology
