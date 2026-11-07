# Draft issue for swat-model/swatplus: salt in a channel's transfer hydrograph is overwritten

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** `sd_channel_control3.f90`: a transfer into a channel fails when constituents are on, and its salt would be overwritten anyway

**Version:** 62.0.1, `sd_channel_control3.f90`.

Near the top, a transfer (the water allocation return, "wallo return here") is added to the incoming hydrograph, with its salt and other constituents:

```fortran
      if (ob(icmd)%trans%flo > 1.e-6) then
        ht1 = ht1 + ob(icmd)%trans
        ob(icmd)%trans = hz
        if (cs_db%num_tot > 0) then
          hcs1 = hcs1 + obcs(icmd)%trans
          obcs(icmd)%trans = 0. * obcs(icmd)%trans
        end if
      end if
```

About 70 lines later, before routing:

```fortran
      !set constituents (rtb salt) to incoming loads
      if (cs_db%num_tot > 0) then
        hcs1 = obcs(icmd)%hin(1)
      end if
```

Two problems:

1. **`obcs(icmd)%trans` is never allocated.** `hyd_read_connect.f90` allocates `obcs(i)%hd` and the `hin` hydrographs but not `trans`, and nothing else allocates its components. So with any constituent on (salts, or a pesticide), the first day a transfer brings water into a channel, `hcs1 + obcs(icmd)%trans` reads past unallocated arrays. With `-fcheck=bounds` it stops at `constituent_mass_module.f90` line 648 ("Index '1' of dimension 1 of array 'hydcs2%pest' above upper bound of 0"); without it, the run hung in our tests. Any water allocation return to a channel in a project with pesticides would meet this.
2. **The second assignment replaces `hcs1`**, so the transfer's salt and constituents would never reach the channel, and `obcs(icmd)%trans` has already been zeroed. The water and nutrients in `ht1` aren't reset, so they do arrive.

In 62.0.1 nothing puts salt into `obcs%trans` (`wallo_pou_deliv.f90` and `wallo_return.f90` add to `ob%trans` only), so today the effect is that water allocation returns carry no salt. Once returns do carry salt, it would be lost here.

**Possible fix:** allocate `obcs(i)%trans`'s components, zero, alongside `obcs(i)%hin` in `hyd_read_connect.f90`; and remove the second `hcs1 = obcs(icmd)%hin(1)`, or move the transfer block after it.

Found while adding salt to a groundwater gain in swatplus-bmi (Nov 2026): the first with a bounds-checked build, the second by reading the source.

Andrew Sommerlot
Northline Hydrology
