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
                    = bin/swatplus-62.0.1-exchange     (build.sh, CMake)

src/swatplus_bmi/exchange.py    the line protocol: one SWAT+ process
src/swatplus_bmi/bmi.py         BMI 2.0 (bmipy) on top of it
```

The Python side is a separate program. It starts SWAT+ as a child process
and talks to it over standard input and output; nothing is linked.

## Where SWAT+ pauses

The daily loop is in `time_control.f90`. The patch adds three calls there,
one in `main.f90.in` and one in `command.f90`. Each returns at once when
exchange mode is off.

| Call | Where | What it does in exchange mode |
|---|---|---|
| `exch_open` | first line of `main` | turns the mode on if SWAT+ was started with `--exchange`; moves the console to `exchange.log` |
| `exch_start` | `time_control`, before the year loop | sends the catalog (grids, objects, variables, dates) |
| `exch_pause` | each day, after the weather, the land use decision tables and the manure allocation, **before** `wallo_start_day` | sends `@paused`, then answers commands until told to run |
| `exch_apply` | each day, **after** `wallo_start_day`, before `call command` | puts values set while paused in place for this day |
| `exch_record` | `command.f90`, after the command loop, before outputs are printed | keeps the day's water allocation demand and withdrawal, which printing zeroes |
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
  own inputs (the recall file, the water allocation rules).

With the mode off, nothing changes. The test suite checks every output file
of the Osu_1hru example, byte for byte, against the unpatched release, both
with the mode off and stepped one day at a time.

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
@hello swatplus-exchange 1
@model SWAT+ 2026-10-16        MODULAR Rev 2026.62.0.1
@start 2010 1                    first year, first day of year
@days 5113                       days in the simulation
@grid cha 1                      a grid: name, number of objects
@obj cha 1 cha01 1.27328949E+02 3.55263214E+01 1.04000006E+01    grid, index, name, lon, lat, area (ha)
@grid aqu 1
@obj aqu 1 aqu001 ...
@grid rec 0
@grid pou 0
@var channel_water__volume_outflow_rate cha out m3 s-1
...
@ready
@paused 0 2010 1                 days done, year and day of year of the next day
```

Commands, while paused:

| Command | Reply | Meaning |
|---|---|---|
| `run [n]` | `@paused d y j` or `@ended d` | run *n* days (default 1) without pausing in between |
| `get <var>` | `@value n v1 ... vn` | one value per object on the variable's grid, nine significant digits (a 4-byte real exactly) |
| `set <var> <i> <v>` | `@ok` | object *i* (1-based) gets *v* for the next day |
| `flush` | `@ok` | write out every open file's buffer |
| `branch "<new>" "<old>" "<in>" "<out>"` | `@branched pid` | fork; see Snapshots |
| `finish` | (process exits 0) | run to the end without pausing and complete the outputs |
| `stop` | `@bye` | exit now; outputs stay incomplete |
| `ping` | `@ok` | |

Errors come back as `@error <message>`, and SWAT+ keeps waiting. If
standard input closes, SWAT+ stops (`error stop 3`). Exchange mode refuses
to start with soft or hard calibration on (`error stop 2`), because those
run the daily loop more than once.

## Variables

All values are 4-byte reals in SI units. Flows that SWAT+ keeps in m3/day
are converted to m3/s. Names follow the CSDMS Standard Names pattern
(object, then quantity). They haven't yet been checked one by one against
the CSDMS list; do that before a release.

| Variable | Grid | In/out | Units | SWAT+ source | Notes |
|---|---|---|---|---|---|
| `channel_water__volume_outflow_rate` | cha | out | m3 s-1 | `ch_out_d(i)%flo` | the `flo_out` column of `channel_sd_day.txt` |
| `channel_water__volume` | cha | out | m3 | `ch_stor(i)%flo` | `flo_stor` |
| `channel_water__volume_inflow_rate` | cha | out | m3 s-1 | `ch_in_d(i)%flo` | net inflow, `flo_in`; negative when a point source diversion is larger than the other inflow (see Found in SWAT+ 62.0.1, item 2) |
| `aquifer_water__volume` | aqu | out | m3 | `aqu_d(i)%stor` (mm) x 10 x area (ha) | `stor` in `aquifer_day.txt` |
| `soil_water_table_surface__depth` | aqu | out | m | `aqu_d(i)%dep_wt` | depth to the water table, `dep_wt` |
| `aquifer_water__volume_outflow_rate` | aqu | out | m3 s-1 | `ob(iob)%hd(1)%flo` | return flow, `flo` in `aquifer_day.txt` |
| `point_source_water__volume_flow_rate` | rec | in and out | m3 s-1 | `recall(i)%hd(day,yr)%flo` | daily recall objects only; negative is a withdrawal |
| `place_of_use_water__volume_demand_rate` | pou | in and out | m3 s-1 | `pou(i)%demand` | reading gives the day's demand; setting replaces SWAT+'s for the next day |
| `place_of_use_water__volume_withdrawal_rate` | pou | out | m3 s-1 | `poud_met(i)%duty_tot%deliv` | what was actually taken |

The reading tests compare every day of every output variable with SWAT+'s
own daily files, to the precision SWAT+ prints them.

### Grids and IDs

Each object type is one BMI grid of type `points`: one point per SWAT+
object, in the order of SWAT+'s input files, longitude as x and latitude as
y. BMI has no element names, so the wrapper adds
`get_grid_object_names(grid)` and `get_object_index(var, name)`, which use
SWAT+'s object names (`cha01`). Places of use have no location; their
coordinates are NaN. Grids with no objects in a project are left out, along
with their variables.

### Setting fluxes

- A set value applies to the next day only.
- **Point sources** (`rec`) must be daily recall objects. The value
  replaces the recall file's flow for that day; the other constituents
  stay as in the file. Inside the recall file's years only.
- **Water allocation demand** (`pou`) replaces the demand `wallo_start_day`
  computed. As SWAT+ does, it is capped at the place of use's `rate_max`
  and split among its points of diversion by their fractions. It affects
  withdrawals from channels, aquifers and reservoirs, which SWAT+ makes
  during the command loop. Withdrawals from outside sources, water towers
  and unlimited sources happen inside `wallo_start_day`, before the set
  takes effect.
- Storage can't be set.

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
   the same place (start or end). A file open partway through reading
   would be left shared, with a line in `exchange.log`; none is in the
   test project. It then talks over the named pipes and replies with the
   same `@paused` line.

SIGCHLD is ignored in the parent, so finished branches are reaped and leave
no zombies. A branch outlives its parent. The C helper (`bmi_fork.c`) is
four functions: `fork`, `chdir`, rewiring the standard streams, and
`getpid`. It isn't compiled on Windows, where `branch` replies with an
error.

Tests: branch at day 0, 365, 1000 and the last day, with parent and child
stepped in turn; a branch of a branch; and three water allocation scenarios
from one warm-up, each identical to the same scenario run unbroken.

### Saving to a file (designed, not built)

A cross-platform `save_state(path)` and `load_state(path)` needs a
serializer: Fortran that writes and reads all of SWAT+'s state. It must be
generated from the declarations by a script that's rerun for each SWAT+
release, never written by hand. `tools/state_inventory.py` is the first part
of that script. For 62.0.1 it finds:

| | |
|---|---|
| modules | 67 (65 with variables) |
| module variables | 2,055, of which 1,049 are allocatable and 1,142 are derived types |
| derived types | 521; 128 have allocatable components; none has pointer components |
| module pointers | 7: `d_tbl`, `wbody`, `h`, `pl_cp`, `pl_db`, `wbody_prm`, `wbody_wb` |
| implicitly saved locals | 5,379 in 537 procedures |

The generator would:

1. Walk the inventory and write, for each derived type, a `write_<type>`
   and `read_<type>` that handle each component: scalars and fixed arrays
   directly, and allocatables as an allocated flag, bounds, then data,
   recursing into derived-type components.
2. Write one `state_save(unit)` and `state_load(unit)` that call these for
   every module variable, as unformatted stream, with a header that
   records the SWAT+ version, the generator version and a hash of the
   inventory, so a state file is only loaded by the build that wrote it.
3. Store the 7 pointers as what they point to: an array name and index (for
   example, `d_tbl` points into `dtbl_lum`, `dtbl_flo` or `dtbl_scen`).
   Since there are only 7, these are listed by hand in the generator's
   configuration and checked against the inventory, so a new pointer in a
   new release stops the generator instead of being missed.
4. Save the output files' sizes. Loading copies the run folder, truncates
   each output file to its saved size and reopens it at its end. Fortran
   keeps no other state for an open sequential file.

The hard part is the saved locals. In Fortran a local with an initializer
(`real :: rto = 0.`) keeps its value between calls, and SWAT+ initializes
most locals that way, so 5,379 locals in 537 procedures are technically
state. Most are assigned before they are used on every call and carry
nothing, but that can't be proved from declarations. Two ways to settle it:

- **Test for it.** Build a variant that resets every saved local on entry
  to each procedure: a mechanical rewrite of `real :: x = 0.` into
  `real :: x` plus `x = 0.`. Compare its output with stock on the test
  projects. Any procedure whose locals change the output carries state, and
  its locals go into the serializer. The rest are left out, on the
  evidence of the test.
- **Save them all.** Generate module copies of every saved local, which
  means changing every procedure. That's too much patch for an upstream
  pull request, but it's possible as a build step.

The first is the plan; it also tells the SWAT+ developers which procedures
rely on saved locals, which is worth knowing for its own sake.

SWAT+ doesn't use Fortran's random number generator. It has its own
(`aunif.f90`), with seeds in module variables (`rndseed` and `rndseed_cond`
in `climate_module`), so the serializer covers them.

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

The wrapper will offer `get_state()` and `set_state(bytes)`, matching the
CSDMS proposal, plus `save_state(path)` and `load_state(path)` as file
versions. The NGWPC variables can be layered on top for ngen.

### Process freeze (container-only fallback)

On Linux, CRIU (Checkpoint/Restore In Userspace) can freeze the paused SWAT+
process to disk and restore it later, with no serializer. It needs Linux,
root or CAP_CHECKPOINT_RESTORE, and the same kernel and binary on restore,
so in practice it's for containers (Docker and Podman support
`checkpoint`). It's a fallback for long warm-ups on Linux servers, not a
general answer.

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

## Limits

- Daily steps only.
- Only fluxes can be set: point source flow and water allocation demand.
- Branching needs fork: Mac and Linux. Saving to a file waits for the
  generated serializer.
- One process per SWAT+ run. NextGen can use SWAT+ only as a domain-wide
  model (one formulation covering the whole SWAT+ project), not split by
  catchment, because SWAT+ routes its own network.
- Soft and hard calibration runs are refused in exchange mode.
- Tested on two variants of one small example (Osu_1hru: one HRU, one
  channel, one aquifer). The larger Ames_sub1 comparisons are run once the
  machine is free.
