# swatplus-bmi design

Nov 4, 2026. SWAT+ 62.0.1.

## Goal

Run SWAT+ one day at a time under outside control: a caller runs a day,
reads state, sets fluxes (and a few states) for the next day, and so on,
with output identical to stock SWAT+ when nothing is set. This allows same-day feedback (curtail a withdrawal when
yesterday's flow was low, pass-by flows, drawdown limits) and coupling
through BMI 2.0 (CSDMS) to other models, NextGen and eWaterCycle.

Decisions behind the design:

- **Stepping, not a batch wrapper.** A wrapper that writes inputs, runs
  SWAT+ to the end and reads outputs was rejected. One-way use (another
  model's results feeding SWAT+) is better served by writing SWAT+ input
  files directly.
- **Daily steps.** SWAT+'s sub-daily options aren't exposed.
- **Fluxes, and states through hooks.** Point source flow and load, water
  allocation demand, and a channel's groundwater exchange and the mass it
  carries can be set, and soil layer temperature as the first state
  (Setting states). Storage, land
  use, soils and parameters can't; they are scenario inputs.
- **Snapshots.** A warm-up is run once and every scenario starts from it.

## Pieces

```
SWAT+ 62.0.1 (tag)  + patches/62.0.1/*.patch     five edited files and CMakeLists.txt, 20 lines
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

The daily loop is in `time_control.f90`. The patch adds four calls there,
three in `sd_channel_control3.f90` (the channel hooks), one in
`hru_control.f90` (the soil temperature hook), one in `swr_percmain.f90`
(groundwater into the soil), one in `main.f90.in` and one in `command.f90`. Each returns at once when
exchange mode is off.

| Call | Where | What it does in exchange mode |
|---|---|---|
| `exch_open` | first line of `main` | turns the mode on if SWAT+ was started with `--exchange`; moves the console to `exchange.log` |
| `exch_start` | `time_control`, before the year loop | sends the catalog (grids, objects, variables, dates) |
| `exch_pause` | each day, after the weather, the land use decision tables and the manure allocation, **before** `wallo_start_day`, as `if (exch_pause()) cycle` | sends `@paused`, then answers commands until told to run; while a saved state is loading, skips the days before the saved one |
| `exch_apply` | each day, **after** `wallo_start_day`, before `call command` | puts values set while paused in place for this day |
| `exch_record` | `command.f90`, after the command loop, before outputs are printed | keeps the day's water allocation demand and withdrawal, which printing zeroes |
| `exch_channel_in` | `sd_channel_control3.f90`, right after the channel's incoming salt load (`hcs1`) is set | adds the salt set with the day's groundwater gain |
| `exch_channel` | `sd_channel_control3.f90`, after routing and water allocation, before the outflow is set | takes a set groundwater loss from the day's outflow, with its sediment and nutrients, and records the day's exchange |
| `exch_channel_cs` | `sd_channel_control3.f90`, after SWAT+ splits salt and constituents between outflow and storage | takes the loss's share of the outflow's salt and constituents, as seepage |
| `exch_hru_soil` | `hru_control.f90`, right after `stmp_solt` | replaces the day's soil layer temperatures that were set |
| `exch_hru_gwsoil` | `swr_percmain.f90`, right after gwflow's `gwflow_soil` call, before the day's infiltration and percolation | adds the groundwater set for the day to the HRU's soil layers, counted in `gwsoilq` as gwflow's is |
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
| `setr <var> <yr> <day> <n>`, then *n* lines of one value per gage | `@ok` | weather records for *n* days from day *day* of *yr*, written now (Setting weather records); all or nothing |
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
| `soil_water__percolation_rate` | hru | out | m s-1 | `hwb_d(j)%perc` (`sepbtm`, mm/day) | water leaving the bottom of the soil profile on the day, `perc` in `hru_wb_day.txt`; what a groundwater model takes as recharge HRU by HRU |
| `land_surface__evapotranspiration_rate` | hru | out | m s-1 | `hwb_d(j)%et` (mm/day) | actual ET, `et`, with ponded water's evaporation |
| `land_surface__potential_evapotranspiration_rate` | hru | out | m s-1 | `hwb_d(j)%pet` (mm/day) | `pet` |
| `soil_water__depth` | hru | out | m | `soil(j)%sw` (mm) | the profile's water above the wilting point at the end of the day, `sw_final` |
| `soil_layer_water__groundwater_inflow_rate` | hly | in and out | m s-1 | added to `soil(j)%phys(k)%st` (mm) and `gwsoilq(j)` | a flux for the next day (Groundwater into the soil); reading gives what went in on the day just done |
| `soil_water__groundwater_inflow_rate` | hru | out | m s-1 | `hwb_d(j)%gwsoil` (`gwsoilq`, mm/day) | `gwsoilq` in `hru_wb_day.txt`: gwflow's and what was set |
| `atmosphere_water__precipitation_leq-volume_flux` | pcp | in and out | m s-1 | `pcp(i)%ts(day,col)` (mm/day) | a record, set ahead (Setting weather records); reading gives the day just done's record, NaN where there's none |
| `atmosphere_bottom_air__max_of_temperature` | tmp | in and out | degC | `tmp(i)%ts` | as above |
| `atmosphere_bottom_air__min_of_temperature` | tmp | in and out | degC | `tmp(i)%ts2` | as above |
| `land_surface_radiation~incoming~shortwave__energy_flux` | slr | in and out | W m-2 | `slr(i)%ts` (MJ/m2/day) | as above |
| `atmosphere_bottom_air_water~vapor__relative_saturation` | hmd | in and out | 1 | `hmd(i)%ts` (a fraction) | as above |
| `atmosphere_bottom_air_flow__speed` | wnd | in and out | m s-1 | `wnd(i)%ts` | as above |
| `point_source_water__volume_flow_rate` | rec | in and out | m3 s-1 | `recall(i)%hd(day,yr)%flo` | settable on daily recall objects inside their records only; negative is a withdrawal |
| `place_of_use_water__volume_demand_rate` | pou | in and out | m3 s-1 | `pou(i)%demand` | reading gives the day's demand; setting replaces SWAT+'s for the next day |
| `place_of_use_water__volume_withdrawal_rate` | pou | out | m3 s-1 | `poud_met(i)%duty_tot%deliv` | what was actually taken |
| `channel_water_<sp>__mass_concentration` | cha | out | kg m-3 | `obcs(iob)%hd(1)%salt(k)`, `ob(iob)%hd(1)%no3`, `%solp`, over `ob(iob)%hd(1)%flo` | the day's outflow concentration; 0 with no outflow |
| `channel_water_<sp>__groundwater_mass_exchange_rate` | cha | in and out | kg s-1 | gains: salt into `hcs1` (`exch_channel_in`), nitrate and P into `ob(iob)%trans`; losses: the loss's share of `ht2` and `hcs2` | positive into the channel; set only with a gain; reading gives what was exchanged on the day just done |
| `aquifer_water_<sp>__recharge_mass_rate` | aqu | out | kg s-1 | `obcs(iob)%hin(1)%salt(k)`, `ob(iob)%hin%no3`, `%solp` | what reaches the aquifer, as `aqu_1d_control` takes it; nitrate is `no3_rchg` × area in `aquifer_day.txt` |
| `point_source_water_<sp>__mass_flow_rate` | rec | in and out | kg s-1 | `rec_salt(i)%hd_salt(day,yr)%salt(k)`, `recall(i)%hd(day,yr)%no3`, `%solp` | as for the flow: daily recalls inside their records; salt needs a daily record in `salt_recall.rec`; 0 for a diversion |

`<sp>` is each salt ion as SWAT+ reads it from `constituents.cs`, in the
salt module's order (`so4`, `ca`, `mg`, `na`, `k`, `cl`, `co3`, `hco3`),
then `no3` (nitrate, as N) and `solp` (soluble phosphorus, as P). The
catalog is built when SWAT+ starts: without the salt module only `no3` and
`solp` are there. SWAT+'s other constituents (`cs`: selenium, boron) and
pesticides and pathogens aren't exposed.

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
- **Chemistry with a gain** (`channel_water_<sp>__groundwater_mass_exchange_rate`, set after the
  gain): nitrate and phosphorus go into the transfer hydrograph with the water. Salt can't, because
  SWAT+ 62.0.1 sets the channel's incoming salt load (`hcs1`) again after adding the transfer's
  (Found in SWAT+ 62.0.1, item 10). So a second hook, `exch_channel_in`, right after that line,
  adds the gain's salt to `hcs1`, which is how SWAT+ takes in an aquifer's salt. A transfer under
  1e-6 m3 waits in `ob%trans` for a later day (SWAT+ adds it only above that), and its salt
  waits with it.
- **Chemistry with a loss:** the loss takes the same fraction of the day's outflow for every
  constituent. Sediment and nutrients go with `ht2` in `exch_channel`. Salt and SWAT+'s other
  constituents are split between outflow and storage later, so a third hook, `exch_channel_cs`,
  after that split, takes the same fraction of the outflow's `hcs2` and adds it to the
  channel's salt and constituent seepage (`chsalt_d%seep`, `chcs_d%seep`), as SWAT+'s own
  seepage does. The loss's mass is read back as a negative exchange.
- **Channels of no length** (`len` under 0.001 km) are passed straight through in `command.f90`
  and never reach `sd_channel_control3`, so an exchange there is refused (0.4.0 and earlier
  accepted it and SWAT+ never used it).
- **Daily routing only.** With sub-daily routing (a time step in `time.sim` above 1) gains
  wouldn't reach the sub-daily hydrograph SWAT+ routes and losses wouldn't come out of it, so a
  set is refused there.

The tests check that a gain raises the inflow by exactly that water, that a loss takes no more
than the outflow and the balance closes, that a set value lasts one day, and that the day's
exchange survives saving and loading.

### Groundwater into the soil

The way back from a groundwater model to the land. SWAT+ has it already, for
its own groundwater model: with gwflow on, `gwflow_soil` runs at the top of
`swr_percmain`, before the day's infiltration and percolation. Where a
cell's water table is within an HRU's soil profile, it takes the cell's
specific yield times the height of the water table above the bottom of the
profile, over the part of the cell in the HRU, and adds it to the layers
below the water table in proportion to how much of each lies below. It
adds to `soil(j)%phys(k)%st` and counts the water in `gwsoilq(j)`, which
`hru_control` prints as `gwsoilq` in `hru_wb`; the cell loses it.

The hook, `exch_hru_gwsoil`, is the next line. It adds the water set for
each of the HRU's layers, the same way, to the same two places. Everything
after is SWAT+'s own: percolation moves water above field capacity down,
saturation excess sends what the profile can't hold to the surface, and
plants and lateral flow take from the layers as they would. So a coupled
groundwater model gives water back to the soil where SWAT+'s own would, and
SWAT+'s printed water balance includes it. How much goes into which layer
is the caller's to decide (gwflow's rule is above), and so is taking the
same water out of the groundwater model.

Only additions: water leaving the soil to the groundwater is percolation,
read with `soil_water__percolation_rate`, so a negative value is refused. A
project that runs gwflow is refused too, since gwflow would put in its own.
gwflow also brings solutes and heat with the water (`hru_soil`,
`gwflow_soil.f90`); the BMI brings water only, for now.

The tests set a different amount into each layer every third day for two
years and check that it goes in as set, is printed as `gwsoilq`, and closes:
over the run, the water added, less the irrigation the wetter soil no longer
needed, leaves as more runoff, lateral flow, percolation and ET, within
0.5 mm of 2,632 mm. On a 12-HRU basin, water set into one layer of one HRU
changes that HRU's output only.

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
- **The groundwater exchange**: daily routing only, and channels with
  length (above). A mass with it needs a gain set first for that channel
  and day, can't be negative, and makes a later loss for that day an
  error, since a loss takes the outflow's own concentration.
- **Point source loads** replace the day's load in the records SWAT+
  reads: `recall(i)%hd(day,yr)` for nitrate and phosphorus, and
  `rec_salt(i)%hd_salt(day,yr)` for salt, which also needs the point
  source's record in `salt_recall.rec` to be daily (type 1) and cover the
  year. A load on a diversion (a negative flow, set or SWAT+'s own) is
  refused, and so is a negative flow on a day a load is set: SWAT+ gives a
  diversion the channel's concentration (`recall_salt`, `recall_nut`).
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
caller, such as keeping a layer just above 0 °C under lasting snow (just
above: SWAT+ tests `tmp <= 0.`, so a layer held at exactly 0 °C still
counts as frozen). SWAT+ 62.0.1
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

### Setting weather records

Weather is neither a flux set for the day nor a state. SWAT+ reads each
day's weather in `climate_control`, which runs before the pause that
precedes the day, and precipitation one day earlier still
(`cli_precip_control` reads the next day's record into `precip_next`, which
decision tables test during the day). From the day's weather it builds the
mean temperature, PET, the 30-day precipitation and PET sums, heat units,
the stream temperature's air lag and the generator's wet and dry state. A
value put in after that would leave all of those built from the old one.

So a weather value is written into the gage's record (`pcp`, `tmp`, `slr`,
`hmd`, `wnd` in `climate_module`), where the file's value would be, for a day
SWAT+ hasn't read: from the day after the coming one, or for precipitation
the day after that. Nothing reads a record before its day, so the run is the
run whose file held the value, which the tests check byte for byte. A
record can be set again until it's read. Records are module state, so
branches and saved states carry them. Refused: a day already read, a day
outside the gage's records (SWAT+ would generate it), a day after the run, a
gage with sub-daily records, and values SWAT+ would read otherwise
(negative precipitation, solar radiation or wind, a temperature of -97 or
below, which SWAT+ reads as missing, a humidity outside 0 to 1).

A pause before the first day's weather would let those first days be set
too, but it moves where the first pause, branches and day-0 saves happen,
so it's left for later; the first days come from the files.


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
  - On Osu_1hru with salts (0.5.0), `scoef` in `sd_channel_control3.f90`
    carries state: SWAT+ sets it only on days whose travel time is longer
    than the routing step and uses the last value otherwise (Found in
    SWAT+ 62.0.1, item 15). Resetting it changes the channel's salt from
    day 56.
  - Locals like that are listed per release in
    `patches/<tag>/carried-locals.toml`; for 62.0.1 the list is those two.
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
`@paused` line. A state also lists the output files the saving process had
open; any of those the loading process hasn't opened yet is opened at its
end too. SWAT+ opens some files only on their first write (the salt
balance's units 5080–5086, as `fort.<unit>`); without this, the loaded
run's first write would start the restored file again from the top. The year-end processing that runs during the skipped years
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
   SWAT+'s own point source flow, loads or demand back every day, read
   from a first run, gives stock output, byte for byte, and so does a zero
   groundwater exchange every day. A set on one day changes only that day
   (`test_setting.py`, `test_groundwater.py`, `test_gw_soil.py`), by
   exactly the water set (balances close). Every kind of set runs on a build with array bounds
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
salts with aquifers or point sources, which stock 62.0.1 can't run, other
constituents, pesticide applications, most reservoir and wetland code,
calibration) is covered by the reasoning in this note, not by tests. Of
the chemistry variables, an aquifer's salt recharge and a point source's
salt load have no project to run on, and SWAT+ prints no soluble P
recharge to check against. The
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

10. In `sd_channel_control3.f90`, a transfer's salt and constituents
    (`obcs%trans`) are added to the incoming load `hcs1`, and then `hcs1`
    is set again from `obcs%hin(1)` before routing, so they never reach
    the channel. Nothing puts salt in a transfer in 62.0.1 (water
    allocation adds to `ob%trans` only), so water allocation returns carry
    no salt. (Draft issue in docs/upstream-issue-transfer-salt.md; the
    exchange's salt goes in through its own hook.)
11. An aquifer's soluble P outflow (`aqu_1d_control.f90`) is the day's
    recharge volume (`ob%hin%flo`) times `minp`, not the return flow, and
    the P arriving with recharge isn't kept anywhere. `aqu_d%minp` holds kg
    but is labeled kg/ha. (Draft issue in
    docs/upstream-issue-aquifer-phosphorus.md.)
12. `ch_temp.f90`'s groundwater term is always zero with `aqu_1d_control`
    aquifers: it reads `hdsep_in%flo_gwsw`, which no aquifer routine fills.
    The mixing also divides flows in mixed units (HRU water yields in mm,
    unweighted, and m3/s), and `channel_sd_day.txt`'s `water_temp` column
    is the old air-temperature regression, not `ch_temp`'s result (the
    column before it, headed `null`). (Draft issue in
    docs/upstream-issue-ch-temp.md.)

13. The salt module can't run with an aquifer or a point source: without
    pesticides `salt_hru_init` writes to `cs_soil%ly`, which only
    `pesticide_init` allocates; with them `aqu_initial` allocates
    `cs_aqu%salt` twice; `recall_read_salt` is never called, so `rec_salt`
    is never read; and `recall_salt` writes `div_conc_salt`, which only
    gwflow allocates. Salts with a placeholder pesticide, no aquifer and no
    point source do run. Salt also leaves a channel on its own schedule
    (`scoef`), not the water's, so the outflow concentration can reach
    10^7 g/m3 on near-zero flows; and `hru_salt_day.txt`'s header has eight
    names its rows don't. (Draft issue in
    docs/upstream-issue-salt-module.md; `tests/salts.py` builds both
    projects.)
14. `obcs%trans` (an object's transfer constituents) is never allocated,
    so with any constituent on, a transfer into a channel reads past
    unallocated arrays in `sd_channel_control3` (an error with bounds
    checked; a hang without). The wrapper allocates them, zero, before
    putting a gain in the transfer (`trans_cs_ready`), so a gain works
    with constituents on; no value changes, since SWAT+ sets the incoming
    load again before using it (item 10). SWAT+'s own transfers in a run
    with nothing set are untouched. (In docs/upstream-issue-transfer-salt.md.)

15. In `sd_channel_control3.f90`, the storage coefficient `scoef` is set
    only when the travel time is longer than the routing step. On other
    days the salt and constituent split (`hcs2 = scoef * hcs3`) and the
    hydrograph separation use the last value computed, from an earlier day
    or another channel (a saved local; 0 on the first call). It's in
    `carried-locals.toml`, so a saved state keeps it. (In
    docs/upstream-issue-salt-module.md.)

16. A soil layer's `bmix` (`soil_module.f90`) has no initial value, and
    `mgt_biomix` sets it only down to the mixing depth, so the `cons_bmix`
    column of `hru_carbvars.txt` prints whatever memory held for deeper
    layers: zero in a fresh process, as a rule, and old heap contents
    inside Python. It's the one output found to read unset memory
    (`tests/test_unset_memory.py`), and the comparisons with stock leave
    that column out (`UNSET` in `tests/conftest.py`). (Draft issue in
    docs/upstream-issue-bmix.md.)

Items 7–9 and 12 were found by a study's model build (Nov 6, 2026) and
confirmed by reading the source. Items 10, 11 and 13–15 were found while
adding chemistry to the exchange (0.5.0), and item 16 by the nightly
comparisons (Nov 7, 2026).

## Limits

- Daily steps only; the groundwater exchange is refused with sub-daily
  routing.
- Only fluxes and listed states can be set: point source flow and loads
  (daily recalls inside their records), water allocation demand, a
  channel's groundwater exchange and the mass a gain carries, and soil
  layer temperature.
- Chemistry is salt ions, nitrate and soluble phosphorus. SWAT+'s other
  constituents, pesticides and pathogens aren't exposed; a loss takes the
  other constituents with its water but leaves pesticides and pathogens,
  as SWAT+'s own seepage does.
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
