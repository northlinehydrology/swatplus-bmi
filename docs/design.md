# swatplus-bmi design

Nov 4, 2026. SWAT+ 62.0.1.

## Goal

Run SWAT+ one day at a time under outside control: a caller runs a day,
reads state, sets fluxes for the next day, and so on, with output identical
to stock SWAT+. This allows same-day feedback (curtail a withdrawal when
yesterday's flow was low, pass-by flows, drawdown limits) and coupling
through BMI 2.0 (CSDMS) to other models, NextGen and eWaterCycle.

Decisions behind the design:

- **Stepping, not a batch wrapper.** A wrapper that writes inputs, runs
  SWAT+ to the end and reads outputs was rejected. One-way use (another
  model's results feeding SWAT+) is better served by writing SWAT+ input
  files directly.
- **Daily steps.** SWAT+'s sub-daily options aren't exposed.
- **Fluxes only.** Point source flow and water allocation demand can be set.
  Storage, land use, soils and parameters can't; they are scenario inputs.
- **Snapshots.** A warm-up is run once and every scenario starts from it.

## Pieces

```
SWAT+ 62.0.1 (tag)  + patches/62.0.1/*.patch     four edited files, 20 lines
                    + fortran/bmi_exchange_module.f90   the exchange mode
                    + fortran/bmi_fork.c                fork for branching (Mac, Linux)
                    + bmi_state_module.f90              state serializer, generated per release
                                                        (tools/gen_serializer.py)
                    = bin/swatplus-62.0.1-exchange     (build.sh, CMake)

src/swatplus_bmi/exchange.py    the line protocol: one SWAT+ process
src/swatplus_bmi/bmi.py         BMI 2.0 (bmipy) on top of it
```

The Python side is a separate program. It starts SWAT+ as a child process
and talks to it over standard input and output; nothing is linked.

## Where SWAT+ pauses

The daily loop is in `time_control.f90`. The patch adds four calls there and one in `sd_channel_control3.f90` (the channel loss hook),
one in `main.f90.in` and one in `command.f90`. Each returns at once when
exchange mode is off.

| Call | Where | What it does in exchange mode |
|---|---|---|
| `exch_open` | first line of `main` | turns the mode on if SWAT+ was started with `--exchange`; moves the console to `exchange.log` |
| `exch_start` | `time_control`, before the year loop | sends the catalog (grids, objects, variables, dates) |
| `exch_pause` | each day, after the weather, the land use decision tables and the manure allocation, **before** `wallo_start_day`, as `if (exch_pause()) cycle` | sends `@paused`, then answers commands until told to run; while a saved state is loading, skips the days before the saved one |
| `exch_apply` | each day, **after** `wallo_start_day`, before `call command` | puts values set while paused in place for this day |
| `exch_record` | `command.f90`, after the command loop, before outputs are printed | keeps the day's water allocation demand and withdrawal, which printing zeroes |
| `exch_channel` | `sd_channel_control3.f90`, after routing and water allocation, before the outflow is set | takes a set groundwater loss from the day's outflow and records the day's exchange |
| `exch_end` | `time_control`, after the year loop | sends `@ended`, then answers commands until told to finish |

The wait and the apply are split around `wallo_start_day` for two reasons.
`wallo_start_day` zeroes the previous day's water allocation totals, so
reading must happen before it. It also sets each place of use's demand for
the day from its inputs and decision tables, so setting must happen after
it, or SWAT+ would overwrite the value.

So, at a pause before day *d*:

- values read are those at the end of day *d*-1, after SWAT+'s end-of-year
  processing if *d*-1 was December 31;
- day *d*'s weather has already been read or generated;
- values set apply to day *d* only. After day *d*, SWAT+ goes back to its
  own inputs (the recall file, the water allocation rules). The one
  exception is SWAT+'s own: it takes water from outside sources, water
  towers and unlimited sources before it works out the day's demand, so
  those sources get a set demand on day *d*+1, as they get SWAT+'s own
  (Setting fluxes).

With the mode off, nothing changes. The test suite checks every output file,
byte for byte, against the unpatched release, with the mode off, stepped one
day at a time, read every day, branched, and saved and loaded (What the tests
prove, below, says on which projects).

## Protocol

Text lines in both directions. Commands go to SWAT+'s standard input, one
per line. Replies come on standard output, one per line, each starting
with `@`. On Mac and Linux, SWAT+'s own console messages go to
`exchange.log`, so standard output carries only replies. Where SWAT+ can't
open `/dev/stdout` (Windows), replies and console messages share standard
output, and the `@` keeps them apart; the Python side writes any other line
to `exchange.log`. No sockets and no C are needed for stepping.

Startup (SWAT+ to caller):

```
@hello swatplus-exchange 3
@model SWAT+ 2026-10-16        MODULAR Rev 2026.62.0.1
@build 3f9c0e...                  hash of the swatplus-bmi sources it was built from
@start 2010 1                    first year, first day of year
@days 5113                       days in the simulation
@grid cha 1                      a grid: name, number of objects
@obj cha 1 cha01 1.27328949E+02 3.55263214E+01 1.04000006E+01    grid, index, name, lon, lat, area (ha)
@grid aqu 1
@obj aqu 1 aqu001 ...
@grid rec 0
@grid pou 0
@grid hru 1
@obj hru 1 hru0001 ...
@grid hly 5                      HRU soil layers; a seventh field, the layer's mid-depth (m)
@obj hly 1 hru0001:1 1.27328949E+02 3.55263214E+01 1.04000006E+01 5.00000000E-03
@var channel_water__volume_outflow_rate cha out m3 s-1
...
@ready
@paused 0 2010 1                 days done, year and day of year of the next day
```

Commands, while paused:

| Command | Reply | Meaning |
|---|---|---|
| `run [n]` | `@paused d y j` or `@ended d` | run *n* days (default 1) without pausing in between |
| `get <var>` | `@value n v1 ... vn` | one value per object on the variable's grid, seventeen significant digits (an 8-byte real exactly) |
| `set <var> <i> <v>` | `@ok` | object *i* (1-based) gets *v* for the next day; refused (`@error`) when SWAT+ would not use it as given (Setting fluxes) |
| `setv <var> <n>`, then *n* lines `<v>` | `@ok` | every object on the grid, in grid order, in one command |
| `seti <var> <n>`, then *n* lines `<i> <v>` | `@ok` | the objects given; for both, all or nothing: one refused value refuses them all, and the reply names it |
| `flush` | `@ok` | write out every open file's buffer |
| `branch "<new>" "<old>" "<in>" "<out>"` | `@branched pid` | fork; see Snapshots |
| `save "<file>" "<run dir>"` | `@ok` | write SWAT+'s module state at this pause, and the values set for the next day, to the file (see Snapshots); refused when an open file couldn't be carried |
| `load "<file>"` | `@restore`, later `@paused` | right after startup only: run forward to the saved day and load the state; the caller puts back the saved files and sends `restored` |
| `finish` | (process exits 0) | run to the end without pausing and complete the outputs |
| `stop` | `@bye` | exit now; outputs stay incomplete |
| `ping` | `@ok` | |

Errors come back as `@error <message>`, and SWAT+ keeps waiting. If
standard input closes, SWAT+ stops (`error stop 3`). Exchange mode refuses
to start with soft or hard calibration on (`error stop 2`), because those
run the daily loop more than once.

## Variables

SWAT+ keeps its values in 4-byte reals. They cross the protocol, and come out
of BMI, as 8-byte reals (`float64`) in SI units; flows that SWAT+ keeps in
m3/day are converted to m3/s in 8-byte arithmetic. So a value read and set
back is SWAT+'s own 4-byte value to the bit, which 4-byte conversion didn't
give (it changed about one value in seven by one step). Names follow CSDMS Standard Names (object, then quantity).
`channel_water__volume`, `channel_water__volume_inflow_rate`,
`channel_water__volume_outflow_rate` and `soil_water_table__recharge_rate`
are on the CSDMS list (TopoFlow names,
checked Nov 4, 2026). The others are new, built the same way:
`soil_water_table_surface__depth` follows CSDMS's
`soil_water_table_surface__elevation`, and the aquifer and place-of-use
names follow the channel ones. CSDMS lists no aquifer or withdrawal names.

| Variable | Grid | In/out | Units | SWAT+ source | Notes |
|---|---|---|---|---|---|
| `channel_water__volume_outflow_rate` | cha | out | m3 s-1 | `ch_out_d(i)%flo` | the `flo_out` column of `channel_sd_day.txt` |
| `channel_water__volume` | cha | out | m3 | `ch_stor(i)%flo` | `flo_stor` |
| `channel_water__volume_inflow_rate` | cha | out | m3 s-1 | `ch_in_d(i)%flo` | net inflow, `flo_in`; negative when a point source diversion is larger than the other inflow (see Found in SWAT+ 62.0.1, item 2) |
| `aquifer_water__volume` | aqu | out | m3 | `aqu_d(i)%stor` (mm) x 10 x area (ha) | `stor` in `aquifer_day.txt` |
| `soil_water_table_surface__depth` | aqu | out | m | `aqu_d(i)%dep_wt` | depth to the water table, `dep_wt` |
| `aquifer_water__volume_outflow_rate` | aqu | out | m3 s-1 | `ob(iob)%hd(1)%flo` | return flow, `flo` in `aquifer_day.txt` |
| `soil_water_table__recharge_rate` | aqu | out | m s-1 | `aqu_d(i)%rchrg` (mm/day) | `rchrg` in `aquifer_day.txt`; a CSDMS name; what MODFLOW 6's recharge package takes |
| `channel_water__depth` | cha | out | m | SWAT+'s rating curve (`rcurv_interp_flo`) at the day's mean outflow, `ch_out_d(i)%flo` | the working rating curve `rcurv` is put back after, so reading changes nothing |
| `channel_water__groundwater_volume_exchange_rate` | cha | in and out | m3 s-1 | gains into `ob(iob)%trans`; losses from `ht2` after routing, counted in `ch_wat_d(i)%seep` | positive into the channel; reading gives what was exchanged on the day just done |
| `soil_layer__temperature` | hly | in and out | degC | `soil(j)%phys(k)%tmp` | a state: setting replaces the day's value right after SWAT+ computes it (Setting states); degrees C, as SWAT+ keeps it, so a value read and set back is exact; layer 2 is `sol_tmp` in `hru_pw_day.txt` |
| `snowpack__liquid-equivalent_depth` | hru | out | m | `hru(j)%sno_mm` | `sno_final` in `hru_wb_day.txt` |
| `point_source_water__volume_flow_rate` | rec | in and out | m3 s-1 | `recall(i)%hd(day,yr)%flo` | settable on daily recall objects inside their records only; negative is a withdrawal |
| `place_of_use_water__volume_demand_rate` | pou | in and out | m3 s-1 | `pou(i)%demand` | reading gives the day's demand; setting replaces SWAT+'s for the next day |
| `place_of_use_water__volume_withdrawal_rate` | pou | out | m3 s-1 | `poud_met(i)%duty_tot%deliv` | what was actually taken |

The reading tests compare every day of every output variable with SWAT+'s
own daily files, to the precision SWAT+ prints them (seven or eight
significant digits); exact agreement with SWAT+'s memory is shown by setting
values back (What the tests prove).

### Grids and IDs

Each object type is one BMI grid of type `points`: one point per SWAT+
object, in the order of SWAT+'s input files, longitude as x and latitude as
y. BMI has no element names, so the wrapper adds
`get_grid_object_names(grid)` and `get_object_index(var, name)`, which use
SWAT+'s object names (`cha01`). Places of use have no location; their
coordinates are NaN. Grids with no objects in a project are left out, along
with their variables.

### The groundwater exchange with channels

This is for coupling a groundwater model, such as MODFLOW 6's river or stream package, that
computes each day's stream–aquifer exchange.
- **Gains** (positive) are added to the channel's transfer hydrograph (`ob(iob)%trans`) when set
  values are put in place. SWAT+ adds that to the channel's inflow before routing, as it does for
  water allocation returns, so a gain shows in `flo_in`.
- **Losses** (negative) are taken in a hook in `sd_channel_control3.f90`, after routing and water
  allocation and before the outflow is set: `exch_channel` takes up to the day's outflow (`ht2`),
  with its constituents in proportion, and adds it to the channel's seepage (`ch_wat_d%seep`). So
  SWAT+'s printed balance (inflow + precipitation − evaporation − seepage − outflow − change in
  storage) still closes.
- **Reading** after the day gives what was exchanged: the gain, less what was taken.
- **Not adjusted:** salts and other constituents (`cs_db`) on losses; for projects that simulate
  them, losses would need the same treatment there.
- **Daily routing only.** With sub-daily routing (a time step in `time.sim` above 1) gains
  wouldn't reach the sub-daily hydrograph SWAT+ routes and losses wouldn't come out of it, so a
  set is refused there.

The tests check that a gain raises the inflow by exactly that water, that a loss takes no more
than the outflow and the balance closes, that a set value lasts one day, and that the day's
exchange survives saving and loading.

### Setting fluxes

- A set value applies to the next day only. Anything SWAT+ would not use
  as given is refused with `@error` (an `ExchangeError` in Python), never
  accepted and dropped: a value that isn't a finite number, or is too large
  for a 4-byte real, and the cases below.
- **Point sources** (`rec`) must be daily recall objects (type 1). The
  value replaces the recall file's flow for that day; the other
  constituents stay as in the file. Refused: sub-daily, monthly, yearly and
  constant recalls, whose records have other shapes (setting one used to
  write into the wrong place, or past the end of the array), and a day
  outside the recall file's years, for which SWAT+ uses no flow at all.
- **Water allocation demand** (`pou`) replaces the demand `wallo_start_day`
  computed. As SWAT+ does, it is capped at the place of use's `rate_max`
  and split among its points of diversion by their fractions. It affects
  withdrawals from channels, aquifers and reservoirs, which SWAT+ makes
  during the command loop, on the day it is set for. SWAT+ takes water from
  outside sources, water towers and unlimited sources inside
  `wallo_start_day` before it recomputes the demand, using the previous
  day's; so those get a set demand on the following day, exactly as they
  get SWAT+'s own. That lag is SWAT+'s, and the wrapper keeps it.
- **The groundwater exchange**: daily routing only (above).
- Storage can't be set.

### Setting states

A state is set differently from a flux. A flux is an input SWAT+ takes for
the day, so a set flux goes in at the start of the day or where SWAT+ uses
it. A state is something SWAT+ computes from yesterday's state each day, so
a value set at the start of the day would be overwritten. Instead, each
settable state has a hook placed right after SWAT+ computes it and before
anything uses it. The hook replaces SWAT+'s value for that day with the
value set, and SWAT+ carries it into the next day as it would carry its
own. As with fluxes, a set lasts one day, so a held state is set every day.

Soil layer temperature is the first. `stmp_solt` computes it at the start
of each HRU's day in `hru_control.f90`. Snowmelt, the frozen-soil curve
number (`sq_dailycn`) and percolation (`swr_percmicro`, which stops at or
below 0 °C) come after. The hook, `exch_hru_soil`, is the line after
`stmp_solt`. Uses: a soil temperature from another model, or a rule in the
caller, such as holding a layer at 0 °C under lasting snow. SWAT+ 62.0.1
lets half the snow's insulation through (`stmp_solt.f90`), so soils under
snow can sit at −5 to −8 °C and stop percolating all winter.

**What a set state means.** It overrides SWAT+'s own model of that state,
and SWAT+'s soil temperature isn't an energy balance (it's an empirical
lag), so no heat is accounted for. A study that sets a state states the
rule it used and why. With gwflow's heat transport on, gwflow can change
layer temperatures later in the day (`gwflow_soil.f90`).

**Adding another state** takes, for each one:
1. the point in SWAT+'s day after it is computed and before anything uses
   it, and nothing later in the day that changes it again;
2. one call there in the patch, and the variable's read, set and grid in
   `bmi_exchange_module.f90`, with its set values saved in the state;
3. the tests every settable variable has: nothing set is stock; setting
   SWAT+'s own value back every day is stock; a set changes that day and
   then only through SWAT+'s own carry-over; it runs in bounds on the
   checked build; a set saved before its day applies after loading.


## Time

BMI time is days since the start of the simulation: `get_time_units()`
returns `days since 2010-01-01 00:00:00`, the form eWaterCycle expects.
Start time is 0, the step is 1, and current time is the number of days
done. `update_until(t)` needs a whole number of days and sends one
`run n`.

`get_value_ptr` is declined (NotImplementedError). The values live in the
SWAT+ process, so there is nothing to point to; grpc4bmi declines it for
the same reason.

## Snapshots

The required test for any snapshot method: snapshot at day N, restore or
branch, continue, and match an unbroken run bit for bit, every output file.

### Branching in memory (done; Mac and Linux)

`branch(run_dir)` duplicates the paused SWAT+ process with `fork`. Both
copies have the same state down to every saved local and the random number
generator. A warm-up is run once, and every scenario branches from it.

1. The caller sends `flush`; SWAT+ writes out every open file's buffer.
2. The caller copies the run folder to the branch's folder and makes two
   named pipes.
3. The caller sends `branch`. SWAT+ forks. The parent replies
   `@branched <pid>` and goes on as before.
4. The child closes the parent's reply channel, moves to the new folder,
   points standard input and output at `/dev/null` and standard error at
   its own `exchange.err`, and reopens every open file in the new folder at
   the same place (start or end). It then talks over the named pipes and
   replies with the same `@paused` line.

Before forking, SWAT+ checks its open files and refuses the branch when one
can't be carried: a file outside the run folder (an `out_path` in
`file.cio` that leads out of it), which the copy doesn't have and both
processes would write to; or a file open partway through, which can't be
reopened at the same place and would be shared, both processes reading from
one position. gwflow reads `chan_depth.gw` and `pond_div.gw` a line a day,
so a gwflow project with those files can't be branched or saved. Neither
test project has such a file at any pause. An `out_path` inside the run
folder is fine.

SIGCHLD is ignored in the parent, so finished branches are reaped and leave
no zombies. A branch outlives its parent. The C helper (`bmi_fork.c`) is
four functions: `fork`, `chdir`, rewiring the standard streams, and
`getpid`. It isn't compiled on Windows, where `branch` replies with an
error.

Tests: branch at day 0, 365, 1000 and the last day, with parent and child
stepped in turn; a branch of a branch; and three water allocation scenarios
from one warm-up, each identical to the same scenario run unbroken.

### Saving to a file (built; every platform)

`save_state(path)` writes a folder: `state.bin`, with all of SWAT+'s module
state and the values set for the next day, and `run/`, a copy of the run
folder (subfolders too, so an `out_path` inside it) with every output file
complete up to the pause. `load_state(path)` carries on from it in a new
process. `get_state()` and `set_state(bytes)` do the same with the folder as
bytes. A save is refused for the same open files as a branch.

**The serializer is generated, never written by hand.**
`tools/gen_serializer.py` reads the release's module declarations and writes
`bmi_state_module.f90`; `build.sh` runs it for every build. For 62.0.1 that's
66 modules, 2,049 variables and 524 derived types, about 60,000 lines.

- Intrinsic scalars and fixed-size arrays get one write.
- Allocatable arrays get a flag (allocated or not), their bounds, then
  their values.
- Allocatable scalars, including deferred-length strings, get a flag, a
  length and a value.
- Every derived type gets a recursive write routine and a read routine
  that handle its components the same way. Four type names are defined in
  two modules each, so types are keyed by their module.
- Every module variable is preceded by a tag naming it. The file header
  records a digest of the generated code, so a state is only loaded by
  the build that wrote it; any other build is refused (tested).

`tools/state_inventory.py` gives the scale:

| | |
|---|---|
| modules | 67 (65 with variables) |
| module variables | 2,055, of which 1,049 are allocatable and 1,142 are derived types |
| derived types | 521; 128 have allocatable components; none has pointer components |
| module pointers | 7: `d_tbl`, `wbody`, `h`, `pl_cp`, `pl_db`, `wbody_prm`, `wbody_wb` |
| implicitly saved locals | 5,379 in 537 procedures |

**What's left out, and why that's safe:**

- **The 7 module pointers.** SWAT+ points each at a table entry just
  before it uses it, so a value left from before a load is never read.
  Checked by reading every assignment and use in 62.0.1 (Nov 6, 2026):
  `d_tbl` is set just before every call to `conditions`, `actions` and
  `res_hydro`; `wbody`, `wbody_wb` and `wbody_prm` at the top of
  `res_control` and `wetland_control`, within the branch that uses them;
  `pl_db` is set but never read; `h` and `pl_cp` are never used. Nothing
  calls `associated` on them. The save/load tests pass with them left out.
- **Saved locals, except the few that carry state.** In Fortran a local
  with an initializer (`real :: rto = 0.`) keeps its value between calls,
  so 5,379 locals in 537 procedures are technically state. A local can't
  be reached from outside its procedure, so the serializer can't save it
  where it is. To find which ones matter, `tools/reset_saved_locals.py`
  rewrites the locals to be set on every call instead (5,372 of them; the
  rest have an explicit SAVE, or are pointers or parameters), and
  `build.sh --nosave` builds that variant.
  - On Osu_1hru, in five variants, its output is identical to stock.
  - On Ames_sub1 it isn't. `write_hdr` in `soil_nutcarb_write_legacy.f90`
    is a "header written" flag, and resetting it writes the soil carbon
    layer headers again. A save/load test on Ames showed the same two files
    differing, and nothing else.
  - Locals like that are listed per release in
    `patches/<tag>/carried-locals.toml`; for 62.0.1 the list is that one.
    `tools/carried_locals.py` moves each into a generated module
    (`bmi_locals_module.f90`) at build time, and the procedure uses it
    under its old name. A module variable keeps its value between calls
    just as a saved local does, so SWAT+ behaves the same; the tests that
    compare against stock still pass. The serializer then saves it.
  - The no-saved-locals build leaves listed locals alone, so its test
    checks that the list is complete. With the list, Ames_sub1 matches
    stock, and so does saving and loading on Ames
    (`tests/test_saved_locals.py` and `tests/test_state.py`, under
    `-m full`).

  The list is complete only for the procedures these projects run. A
  coverage build (stock compiled with
  `--coverage`, the five Osu_1hru variants run, `gcov -f` over the
  counters) shows how far that is. It ran 432 of SWAT+'s 1,394 procedures,
  including 325 of the 537 with saved locals: 2,906 of the 5,379 saved
  locals, about 54%. Those not run are mostly gwflow (24), salt and
  other constituents (39), reservoir (20), calibration (19) and pesticide
  (15) code, and parts of the wetland code. Ames_sub1 (12 HRUs, crop
  management) adds more (not yet counted), and a project with reservoirs
  and pesticides would widen it further. A new release, or a project that
  runs code the tests haven't, should be checked with `build.sh --nosave`
  and `pytest -m full` before relying on save and load. Saved locals don't
  affect branching: a fork copies them with everything else.
- **Random numbers.** SWAT+ doesn't use Fortran's generator. Its own
  (`aunif.f90`) keeps its seeds in module variables (`rndseed`,
  `rndseed_cond` in `climate_module`), so they are saved.

**The loop counters.** The year and day counters of `time_control`'s loops
are locals, not module state, so a new process can't just read the state
and carry on. Instead, `load` is given right after startup. The process
then runs forward to the saved day without computing it: the pause hook is
`if (exch_pause()) cycle`, and it returns true for each day before the
saved one. That skips the rest of the day: no command loop, no outputs. At
the saved day's pause, the process reads the saved state over its own,
closes its output files, and replies `@restore`. The caller then puts back
the saved run folder's files (SWAT+ rewrote them at startup) and answers
`restored`. SWAT+ reopens the files at their ends and pauses with the saved
`@paused` line. The year-end processing that runs during the skipped years
changes only state that the load replaces, and anything it writes to
output files is discarded when the saved files are put back.

**Tests** (`tests/test_state.py`):

- Save at day 0, 365, 1000 and the last day, load in a new process, and
  continue. Every output file matches stock, and the run that was saved
  also carries on unchanged.
- Every output variable read after loading equals the saved value exactly.
- A value set before saving applies after loading.
- Two water allocation scenarios from one saved warm-up each match the same
  scenario run unbroken.
- The `get_state`/`set_state` round trip works.
- A state from another build is refused.

On Osu_1hru, `state.bin` is 1.2 MB.

**Naming.** BMI 2.0 has no state functions. The nearest conventions are:

- **CSDMS bmi PR #112** (Rolf Hut, eWaterCycle; opened Sep 2022, still
  open). It adds `get_state` and `set_state` (with `_ptr` variants) to the
  spec: the whole model state in one request.
- **NGWPC's ngen fork** (merged Mar 2026). It passes serialized state
  through BMI variables (`serialization_create`, `serialization_state`,
  `serialization_size` (spelled `serializatoin_size` in the code),
  `serialization_free`, `reset_time`) and a `state_saving` block in the
  realization config (`direction` save or load, `path`, `type`
  `FilePerUnit`, `when` `StartOfRun` or `EndOfRun`).

The wrapper offers `get_state()` and `set_state(bytes)`, after the CSDMS
proposal, plus `save_state(path)` and `load_state(path)` as file versions.
The NGWPC variables could be added on top for ngen.

### Process freeze (container-only fallback)

On Linux, CRIU (Checkpoint/Restore In Userspace) can freeze the paused SWAT+
process to disk and restore it later, with no serializer. It needs Linux,
root or CAP_CHECKPOINT_RESTORE, and the same kernel and binary on restore,
so in practice it's for containers (Docker and Podman support
`checkpoint`). It's a fallback for long warm-ups on Linux servers, not a
general answer.

## What the tests prove

The claim is three sentences, and each has its own tests:

1. **With nothing set, every output file is stock SWAT+'s, however the run
   is driven.** `tests/test_faithful.py` runs each project stepped one day
   at a time while reading every variable every day, in uneven chunks,
   branched partway through a year (both copies run to the end), and saved
   and loaded there, and compares every file with a stock run, byte for
   byte. `tests/test_stepping.py`, `test_branch.py` and `test_state.py`
   add the mode switched off, branches and saves at the first and last
   days, and branches of branches.
2. **A value set changes results only through that input.** Setting
   SWAT+'s own point source flow or demand back every day, read from a
   first run, gives stock output, byte for byte, and so does a zero
   groundwater exchange every day. A set on one day changes only that day
   (`test_setting.py`, `test_groundwater.py`), by exactly the water set
   (balances close). Every kind of set runs on a build with array bounds
   checked (`build.sh --check`).
3. **Anything that can't be carried out faithfully is refused.** Each
   refusal above has a test that it raises an error and leaves the run
   able to go on.

And for the build: every SWAT+ file the patch doesn't touch compiles to
the same object, byte for byte, as in the stock build
(`tools/build_parity.py`, in CI), and an executable built from other
swatplus-bmi sources is refused. In CI a test whose executable or project
is missing fails rather than skips, and an `exchange.log` that says a file
was left behind counts as a difference.

**What isn't proven.** The checks run on SWAT+'s two example projects, a
few variants of them, and whatever is added with `$SWATPLUS_BMI_PROJECTS`
(COMPATIBILITY.md lists the projects checked). Code those don't run (gwflow,
salts and other constituents, pesticides, most reservoir and wetland code,
calibration) is covered by the reasoning in this note, not by tests. The
saved-locals list is complete only for the procedures the test projects
run. Saved states are checked on macOS and on Linux, each loaded on the
platform that wrote it. A state moved between platforms would be accepted
(its digest is of the generated serializer, the same on both) but hasn't
been tried.

## Code organization

- **swatplus-bmi** (this repository) has three layers, split by license and
  language:
  - `fortran/` holds new SWAT+ source files (LGPL 2.1, like SWAT+); these
    are what an upstream pull request would contribute.
  - `patches/<tag>/` holds the edits to existing SWAT+ files, one folder
    per supported release (COMPATIBILITY.md).
  - `src/swatplus_bmi/` is the Python wrapper. It's a separate program
    that talks to SWAT+ over pipes.
  If SWAT+ takes the exchange mode, `fortran/` and `patches/` go away from
  that release on, and this repository is the Python wrapper and tests
  against stock SWAT+.
- **The waterledger packages** don't depend on SWAT+ or on this
  repository. `CoolingWaterBmi` gets only the input it needs for coupling
  (a dry mode or a withdrawal limit).
- **Coupling** of the two lives where it's used: a demo (Plaster Creek)
  that depends on both, Northline's engine for client work, and an
  eWaterCycle plugin as its own small package.

## Found in SWAT+ 62.0.1

These are worth reporting upstream, separately from the exchange mode:

1. `recall_day.txt` labels the flow column `m^3/s`, but the values are
   m3/day. A daily recall of 86.4 raises channel inflow by 0.001 m3/s.
   (Shown by the point source tests.)
2. A recall used as a withdrawal (negative flow) isn't limited to the water
   in the channel. When it is larger than the channel's other inflow, the
   net inflow is negative, and `ch_rtmusk.f90` adds inflow to storage only
   `if (ht1%flo > 1.e-6)`. So nothing is taken and outflow is zero, but
   `recall_day.txt` records the full withdrawal, and the channel balance is
   off by the untaken part. `recall_nut.f90` limits the diversion's
   nutrients to what's in the channel, but nothing limits the water. In the
   curtailment test, 7,539 of 182,339 m3 recorded (4.1%) was never taken,
   on 198 low-flow days. (Shown by the curtailment test; draft issue in
   docs/upstream-issue-recall-diversion.md.) The wrapper warns when a
   negative point source is set, and `channel_water__volume_inflow_rate`
   shows the shortfall. Withdrawals that must be limited to the water
   available belong in water allocation.
3. In `wallo_withdraw.f90`, for an aquifer point of diversion, storage is
   reduced before the withdrawal is computed from it:
   `aqu_d(j)%stor = (1. - rto) * aqu_d(j)%stor`, then
   `outflo_om%flo = rto * aqu_d(j)%stor * ...`. The aquifer loses
   rto × S, but the place of use receives rto × (1 − rto) × S. Nitrate
   and phosphorus have the same problem. With a 0.002 m3/s well on
   Osu_1hru's aquifer, the aquifer loses 172.8 m3/day and the user receives
   171.3. The gap grows as the aquifer drains; at rto = 1 the aquifer is
   emptied and the user receives nothing. (Shown by
   `test_aquifer_withdrawal_delivers_less_than_it_removes_swatplus_62_0_1`,
   which will fail when SWAT+ fixes it; draft issue in
   docs/upstream-issue-aquifer-withdrawal.md.)
4. `water_allocation_read.f90` opens the place of use and point of
   diversion files even when they don't exist
   (`if (.not. i_exist .or. ... /= "null")`), creating empty files in the
   run folder. (Found by reading the code.)
5. A yearly recall (type 3) is used in every year, inside its record or
   not: `command.f90` tests `time%yrc >= start_yr .or. time%yrc <= end_yr`,
   which is always true; the daily and monthly cases use `.and.`. (Found by
   reading the code; draft issue in docs/upstream-issue-recall-yearly.md.)
6. In `hru_lte_control.f90`, the end-of-growth decision table is checked
   with `call conditions (isd, icmd)`, passing the command number where the
   table number `iend` belongs (the start-of-growth call two lines up
   passes `istart`). (Found by reading the code; draft issue in
   docs/upstream-issue-hru-lte-conditions.md.)
7. The reservoir `weir` release in `res_hydro.f90` takes the crest height
   from `wet_ob(jres)%weir_hgt`, the wetland array, when it runs for
   reservoir `jres`. (Draft issue in docs/upstream-issue-reservoir-weir.md.)
8. With br2 = 0.9, `res_initial.f90` sets `br1 = (psa / pvol)**0.9`, so a
   reservoir's area at the principal volume is psa^0.9 ha. (Draft issue in
   docs/upstream-issue-reservoir-area.md.)
9. A `chn` change in `calibration.cal` is applied after the rating curves
   are built and never reaches the routing. (Draft issue in
   docs/upstream-issue-calibration-chn.md; `check_calibration` warns.)

Items 7–9 were found by a study's model build (Nov 6, 2026) and confirmed
by reading the source.

## Limits

- Daily steps only; the groundwater exchange is refused with sub-daily
  routing.
- Only fluxes and listed states can be set: point source flow (daily
  recalls inside their records), water allocation demand, a channel's
  groundwater exchange, and soil layer temperature.
- A gwflow project that reads `chan_depth.gw` or `pond_div.gw` can't be
  branched or saved, and nor can a project whose `out_path` leads out of
  the run folder.
- Branching needs fork: Mac and Linux. Saving to a file and loading work
  everywhere, but the Windows build hasn't been tried.
- Loading a saved state starts a new process and runs forward through the
  saved days without computing them, so it takes a little time on long
  warm-ups; branching is instant.
- One process per SWAT+ run. NextGen can use SWAT+ only as a domain-wide
  model (one formulation covering the whole SWAT+ project), not split by
  catchment, because SWAT+ routes its own network.
- Soft and hard calibration runs are refused in exchange mode.
- Proven on the projects in COMPATIBILITY.md, not on every project; What
  the tests prove says what each check shows.
