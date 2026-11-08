# Changelog

Changes that matter to someone using swatplus-bmi. Versions follow semantic versioning; the
SWAT+ releases each version supports are in COMPATIBILITY.md.

## 0.6.0 (2026-11-08)

- Weather, set ahead into the gages' records: precipitation, maximum and minimum temperature,
  solar radiation, relative humidity and wind speed, each on a grid of its gages (`pcp`, `tmp`,
  `slr`, `hmd`, `wnd`). `set_weather(name, start, values)` writes a series of days; `set_value`
  writes the first day SWAT+ hasn't read (`first_weather_day`: the day after the coming one, or
  the day after that for precipitation, which SWAT+ reads a day ahead). A run given a series is
  byte for byte the run whose files hold it (tested on Osu_1hru, also across branches, saves and
  day-by-day sets). Protocol 4 adds `setr`.
- HRU water, read-only on the `hru` grid, each the day just done: `soil_water__percolation_rate`
  (water leaving the bottom of the soil profile, what a groundwater model takes as recharge HRU by
  HRU, where `soil_water_table__recharge_rate` gives it only per aquifer),
  `land_surface__evapotranspiration_rate`, `land_surface__potential_evapotranspiration_rate` (m
  s-1) and `soil_water__depth` (m, the profile's water above the wilting point). Read against
  `hru_wb_day.txt` every day on Osu_1hru and on Ames_sub1's 12 HRUs.

## 0.5.0 (2026-11-07)

Chemistry that travels with the water through the coupling: for each salt ion SWAT+'s salt module
simulates, and for nitrate and soluble phosphorus, what a groundwater transport model needs from
SWAT+ and gives back. With nothing set, output is still stock.

- Four variables per species (`<sp>`: the salt ions from `constituents.cs`, then `no3` and
  `solp`): `channel_water_<sp>__mass_concentration` (the day's outflow concentration, kg m-3),
  `channel_water_<sp>__groundwater_mass_exchange_rate` (kg s-1, settable with a gain),
  `aquifer_water_<sp>__recharge_mass_rate` (kg s-1) and `point_source_water_<sp>__mass_flow_rate`
  (kg s-1, settable on daily point sources, as the flow is).
- A gain's nitrate and phosphorus go with its water through the transfer hydrograph. Its salt goes
  in through a new hook, `exch_channel_in`, because SWAT+ 62.0.1 drops a transfer's salt (docs,
  Found in SWAT+ 62.0.1, item 10).
- **Fixed:** a groundwater loss took a channel's water but left its salt and other constituents
  behind, so in a project with the salt module on, every losing reach gained concentration and the
  salt balance didn't close. A loss now takes the same share of the outflow's salt and constituents
  as of its water, counted as seepage as SWAT+ counts its own (a new hook, `exch_channel_cs`).
- **Fixed:** a groundwater exchange on a channel of no length (under 1 m) was accepted and never
  used, since SWAT+ passes such a channel's inflow straight on. It's now refused.
- **Fixed:** in a project with constituents on (salts or pesticides), a gain stopped the run (a
  hang in the release build), because SWAT+ 62.0.1 never allocates a channel's transfer
  constituents. The wrapper now allocates them, zero, before putting a gain in the transfer; no
  value changes.
- **Fixed:** a loaded state started SWAT+'s lazily opened output files (the salt balance's
  `fort.508x`) again from the top. A state now lists the files open at the save, and a load opens
  any not yet open at their ends.
- **Saved locals:** `scoef` in `sd_channel_control3.f90` carries state with salts on (SWAT+ uses
  it stale on some days), so it's in `carried-locals.toml` and saved with a state.
- SWAT+ 62.0.1's salt module runs only without aquifers and point sources, and only with a
  pesticide listed; the salt variables are tested on such a project (`tests/salts.py`), and a
  salt load on a point source is refused, since SWAT+ never reads `salt_recall.rec`.
- Refused: a mass with a loss or before the day's gain, a negative mass, a load on a diversion, a
  salt load without a daily record in `salt_recall.rec`.
- The read-only error from `set_value` now says the variable is an output only (it said "only
  fluxes", which stopped being true with soil temperature in 0.4.0).
- Found in SWAT+ 62.0.1 and drafted for upstream: the salt module's allocation and call gaps, its
  stale storage coefficient and its own routing schedule for salt; an aquifer's soluble P outflow
  is the day's recharge times `minp`, not its return flow, and recharge P isn't kept; `ch_temp`'s
  groundwater term is always zero with aquifers, and its mixing divides flows in mixed units.
- A soil layer's `bmix` is never set below the mixing depth in SWAT+ 62.0.1, so the `cons_bmix`
  column of `hru_carbvars.txt` prints whatever memory held: zero in a stock run as a rule, and
  sometimes not inside Python (a nightly Ames_sub1 run on macOS). The comparisons with stock leave
  that column out, a new test runs stock SWAT+ with its memory filled with a pattern to show it's
  the only output that reads unset memory on the test projects, and the issue is drafted for
  upstream.
- The patch adds ten calls to five SWAT+ files (two new, in `sd_channel_control3.f90`). Saved
  states have a new format (`SWXSTATE-4`) with masses and loads pending for the next day.

## 0.4.0 (2026-11-06)

Settable states, starting with soil layer temperature. A state can't be set at the start of the
day like a flux, because SWAT+ computes it from yesterday's. So each settable state gets a hook
right after SWAT+ computes it and before anything uses it, which replaces that day's value. The
first is in `hru_control.f90`, after `stmp_solt`. With nothing set, output is still stock.

- `soil_layer__temperature` (degC, settable) on a new grid, `hly`: one point per HRU soil layer,
  HRU by HRU, at the HRU's location, with `get_grid_z` the layer's mid-depth (m). It's in degrees
  C, as SWAT+ keeps it, so a value read and set back is exact (tested: setting SWAT+'s own
  temperatures back every day gives stock output).
- `snowpack__liquid-equivalent_depth` (m) on a new grid, `hru`, one point per HRU.
- Bulk sets, protocol 3: `setv` sets every object on a grid in one command and `seti` the objects
  given, all or nothing. `set_value` and `set_value_at_indices` use them, so a set of 12,000 soil
  layers is one exchange, not 12,000.
- The README's example keeps frozen layers under more than 10 mm of snow-water equivalent at
  0.1 °C (SWAT+ counts 0 °C as frozen). Tried on a study's 2,371-HRU basin build (11,383 layers): basin
  January–March soil temperature went from about −7 °C to about −1 °C, and percolation from 109
  to 122 mm a year.
- docs/design.md, Setting states: what a set state means (it overrides SWAT+'s own model; SWAT+'s
  soil temperature isn't an energy balance), and what adding another state takes.
- Saved states have a new format (`SWXSTATE-3`), with set soil temperatures pending for the next
  day.

## 0.3.0 (2026-11-06)

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
  2,371-HRU study build), in place of claims that went further.
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
