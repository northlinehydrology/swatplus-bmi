# Changelog

Changes that matter to someone using swatplus-bmi. Versions follow semantic versioning; the
SWAT+ releases each version supports are in COMPATIBILITY.md.

## 0.3.0 (unreleased)

The wrapper's claim, made exact and tested as a whole: with nothing set, every output file is
stock SWAT+'s however the run is driven; a value set changes results only through that input;
anything SWAT+ would not use as given is refused, never accepted and dropped. Fixes from an
independent review of 0.2.0.

**Changed (breaking)**

- Values cross the protocol and come out of BMI as 8-byte reals: `get_var_type` is `float64`,
  `get_var_itemsize` 8. SWAT+ keeps 4-byte reals, and converting m3/day to m3/s in 4-byte
  arithmetic changed about one value in seven by one step, so a value read and set back wasn't
  SWAT+'s own. Now it is, to the bit (tested: setting SWAT+'s own point source flow or demand
  back every day gives stock output). Protocol version 2.
- Saved states have a new format (`SWXSTATE-2`); 0.2 states are refused.

**Fixed**

- Setting a point source that isn't a daily recall (sub-daily, monthly, yearly or constant)
  wrote into the wrong place in SWAT+'s recall array, or past its end, and replied `@ok`. It is
  now refused, and so is a day outside the recall file's years, for which SWAT+ uses no flow.
- A value set and then saved was lost on loading (kept on a branch). Values set for the next day
  are now part of the saved state.
- Loading put back only the run folder's top-level files, so outputs in an `out_path` folder
  lost their earlier days. Subfolders are restored too.
- A branch or a save is refused when SWAT+ has a file open that it couldn't carry: one outside
  the run folder (an `out_path` leading out of it, which both branches would write to), or one
  partway through reading (gwflow's `chan_depth.gw` and `pond_div.gw`, read a line a day, which
  a branch would share and a load would leave at the wrong line).
- The groundwater exchange is refused with sub-daily routing, where gains and losses wouldn't
  reach the hydrograph SWAT+ routes.
- A value that isn't a finite number, or too large for a 4-byte real, is refused; so is a
  `set_value_at_indices` with more or fewer values than indices (it was cut short silently), and
  an index outside the grid. A gain under 1e-6 m3/day was dropped; any gain now counts.
- `find_executable` picks the supported release's build, and refuses to guess between several.

**Added**

- The executable carries a hash of the swatplus-bmi sources it was built from (`@build` in the
  catalog), and the wrapper refuses one built from other sources, such as a stale `bin/`.
- `build.sh --check`: the exchange build with array bounds checked, for the tests that set
  values. Every build compiles from one folder, so a SWAT+ file the patch doesn't touch
  compiles to the same object in the stock and exchange builds; `tools/build_parity.py` checks
  that in CI.
- `tests/test_faithful.py`: every way of driving a run against stock (stepped and reading
  everything, chunks, branch, save and load) on four Osu_1hru variants, including an `out_path`
  folder, and any projects in `$SWATPLUS_BMI_PROJECTS`; setting SWAT+'s own values back; a
  value set before saving; every refusal.
- In CI a missing executable or project fails a test instead of skipping it, and an
  `exchange.log` that says a file was left behind counts as a difference from stock.
- `swatplus_bmi.project.check_calibration` and `check_soils`: read-only checks for inputs SWAT+
  62.0.1 ignores or misreads without a message (a `calibration.cal` change missing from
  `cal_parms.cal`, or to `chn`; a soil misread after a blank field in `soils.sol`).
- Drafts of five SWAT+ issues for upstream (`docs/upstream-issue-*.md`): the reservoir weir
  release, a reservoir's area at the principal spillway, `chn` in `calibration.cal`, yearly
  recalls outside their record, and hru_lte's end-of-growth decision table. The patch fixes none
  of them; it reproduces SWAT+ as it is.

**Documented**

- SWAT+ takes water from outside sources, water towers and unlimited sources before it works
  out the day's demand, so those get a set demand the following day, as they get SWAT+'s own.
  The docs had said "that day only".
- What the tests prove, and on which projects (COMPATIBILITY.md: Osu_1hru, Ames_sub1 and a
  2,371-HRU study build of the Clam River, Michigan), in place of claims that went further.
- What the patch never does: change SWAT+'s physics, inputs or outputs, even where SWAT+ looks
  wrong.

## 0.2.0 (2026-11-05)

For coupling a groundwater model (Basin Ledger's SWAT+–MODFLOW 6 link).

- `channel_water__depth` (m): each channel's water depth on SWAT+'s own rating curve at the
  day's mean outflow, for a river stage.
- `channel_water__groundwater_volume_exchange_rate` (m3/s, settable, positive into the
  channel): gains join the channel's inflow, losses come out of the day's outflow (no more than
  is there) and count as seepage, so SWAT+'s channel balance still closes. Reading it gives what
  was exchanged.
- One more call in the patch, in `sd_channel_control3.f90`, for the loss.

## 0.1.1 (2026-11-04)

- `swatplus_bmi.project.set_print` turns SWAT+'s output on by object name. Unless print.prt's
  `use_obj_labels` switch is on, SWAT+ reads the object rows by position and ignores their names,
  so an edit by hand can switch on the wrong object; `set_print` turns labels on.
- Documented that a run stopped before its last day keeps its daily output up to that day (and
  writes no yearly or average-annual summaries), with a test.

## 0.1.0 (2026-11-04)

First release, for SWAT+ 62.0.1.

- Exchange mode for SWAT+, off unless started with `--exchange`: SWAT+ pauses before each day
  and takes commands on standard input. Six calls in three SWAT+ files, one Fortran module, and
  a C helper for branching (Mac, Linux).
- Python BMI 2.0 wrapper (`SwatPlusBmi`): one-day steps, time in days since the start date, one
  grid of points per object type, CSDMS-style names.
- Reading: channel outflow, inflow and storage; aquifer storage, depth to water, outflow and
  recharge; point source flow; water allocation demand and withdrawal.
- Setting, for the next day: point source flow, water allocation demand.
- Snapshots:
  - `branch()` forks the paused process (Mac, Linux);
  - `save_state`/`load_state` and `get_state`/`set_state` work on every platform, with the
    serializer generated from SWAT+'s module declarations;
  - saved locals that carry state are listed per release and saved too.
- `swatplus-bmi build` fetches, patches and builds SWAT+ from an installed package.
- Tests against unpatched SWAT+: every output file byte for byte, every variable against SWAT+'s
  daily output, water balances, branching and saving against unbroken runs.
- Example marimo notebook (`examples/tour.py`).
