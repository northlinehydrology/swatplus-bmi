# swatplus-bmi

Run [SWAT+](https://github.com/swat-model/swatplus) one day at a time under
outside control, through the Basic Model Interface
([BMI 2.0](https://bmi.readthedocs.io), CSDMS).

A small patch adds an exchange mode to SWAT+, off by default. In exchange
mode SWAT+ pauses before each day. While it is paused, a caller can read
state (channel flow, aquifer storage, depth to water) and set fluxes for
the next day (a point source's flow, a water allocation demand). A Python
BMI wrapper drives it. Stepped this way, SWAT+'s output is byte-for-byte
identical to a stock run, every output file, start to finish.

This allows same-day feedback, which a model run to the end can't do:
curtail a withdrawal when yesterday's flow was low, keep a pass-by flow,
stop pumping at a drawdown limit, or couple SWAT+ to another model that
answers back. On Mac and Linux a paused run can also be branched: warm up
once, then start every scenario from the same state.

Status: working for SWAT+ 62.0.1, tested on the Osu_1hru example that ships
with SWAT+. Not yet released. See [COMPATIBILITY.md](COMPATIBILITY.md) and
[docs/design.md](docs/design.md).

## Install and build

Needs git, make, gfortran (or Intel ifx) and [uv](https://docs.astral.sh/uv/).
`uv sync` installs Python, the package, pytest and CMake from the lock file.

```bash
git clone <this repository> swatplus-bmi && cd swatplus-bmi
uv sync
uv run ./build.sh --stock
```

`build.sh` fetches SWAT+ 62.0.1, copies in the exchange module, applies the
patch and builds `bin/swatplus-62.0.1-exchange`. `--stock` also builds the
unpatched release (`bin/swatplus-62.0.1-stock`), which the tests compare
against. The wrapper looks for the executable in `bin/`, in
`$SWATPLUS_EXCHANGE`, or wherever the config file says.

Run the tests (about two minutes on one core):

```bash
uv run pytest
```

Without uv: `pip install -e . --group dev` (pip 25.1 or later) in a virtual
environment of your own, then the same commands without `uv run`.

## Using it

A SWAT+ project folder (the one with `file.cio`) is the config:

```python
import numpy as np
from swatplus_bmi import SwatPlusBmi

m = SwatPlusBmi()
m.initialize("path/to/TxtInOut")       # SWAT+ starts, paused before day 1
print(m.get_time_units())               # days since 2010-01-01 00:00:00
print(m.get_output_var_names())

q = np.empty(m.get_grid_size(m.get_var_grid("channel_water__volume_outflow_rate")),
             dtype=np.float32)
while m.get_current_time() < m.get_end_time():
    m.update()                          # one day
    m.get_value("channel_water__volume_outflow_rate", q)   # m3/s, one per channel
m.finalize()                            # SWAT+ completes its output files
```

Or a TOML file, to run a copy of the project so the original stays clean:

```toml
project = "path/to/TxtInOut"
run_dir = "runs/scenario-a"            # copied from project, then run there
executable = "bin/swatplus-62.0.1-exchange"   # optional
```

### Same-day feedback

Cut a water user's demand to zero the day after a day whose flow was below
a threshold:

```python
DMD = "place_of_use_water__volume_demand_rate"     # m3/s
QOUT = "channel_water__volume_outflow_rate"

i = m.get_object_index(QOUT, "cha01")
q = np.empty(m.get_grid_size(m.get_var_grid(QOUT)), np.float32)
while m.get_current_time() < m.get_end_time():
    m.get_value(QOUT, q)                                # yesterday's flow
    if q[i] < 0.0012:
        m.set_value(DMD, np.array([0.0], np.float32))   # for the next day only
    m.update()
```

A value set between updates applies to the next day only; after that day
SWAT+ goes back to its own inputs. SWAT+ still applies its own limits: a
demand is capped at the place of use's `rate_max`, and a channel
withdrawal can't take more water than the channel has.

Use the water allocation demand for withdrawals, not a negative point source.
SWAT+ 62.0.1 doesn't limit a point source diversion to the water in the
channel: on a day when the diversion is larger than the channel's other
inflow, SWAT+ records it in full but takes none of the excess. The wrapper
warns when a negative point source is set (docs/design.md, "Found in SWAT+
62.0.1").

### Warm up once, branch every scenario (Mac and Linux)

```python
base = SwatPlusBmi()
base.initialize("runs/warmup.toml")
base.update_until(3 * 365)                  # warm-up
a = base.branch("runs/scenario-a")          # copies of the paused process,
b = base.branch("runs/scenario-b")          # each in its own folder
base.finalize()
# a and b now continue independently from the same state
```

Each branch matches, bit for bit, the same scenario run unbroken from the
start.

### Save a state, load it later (every platform)

```python
m.update_until(3 * 365)
m.save_state("states/warm")             # a folder: state.bin and a copy of the run folder
...
n = SwatPlusBmi()
n.initialize("runs/scenario-c.toml")
n.load_state("states/warm")             # carries on from day 1095, exactly as the saved run
state = n.get_state()                   # the same as bytes; set_state(state) restores it
```

The state serializer is generated from SWAT+'s own module declarations for
each release (`tools/gen_serializer.py`), and a state saved by one build is
refused by another.

### Objects and grids

Each SWAT+ object type is a BMI grid of type `points`: one point per object,
longitude as x and latitude as y. `get_grid_object_names(grid)` gives the
SWAT+ object names, and `get_object_index(var, name)` gives an object's
index. Grids with no objects in a project are left out.

## Variables

| Variable | Objects | Set? | Units |
|---|---|---|---|
| `channel_water__volume_outflow_rate` | channels | no | m3 s-1 |
| `channel_water__volume` | channels | no | m3 |
| `channel_water__volume_inflow_rate` (net inflow) | channels | no | m3 s-1 |
| `aquifer_water__volume` | aquifers | no | m3 |
| `soil_water_table_surface__depth` (depth to water table) | aquifers | no | m |
| `aquifer_water__volume_outflow_rate` | aquifers | no | m3 s-1 |
| `soil_water_table__recharge_rate` | aquifers | no | m s-1 |
| `point_source_water__volume_flow_rate` | daily recall objects | yes | m3 s-1 |
| `place_of_use_water__volume_demand_rate` | water allocation places of use | yes | m3 s-1 |
| `place_of_use_water__volume_withdrawal_rate` | water allocation places of use | no | m3 s-1 |

All values are 4-byte reals. Values read while paused are those at the end
of the day just completed. The design note gives the SWAT+ variable behind
each one.

## The protocol

The Python wrapper and SWAT+ talk in text lines over SWAT+'s standard input
and output, so nothing is linked and no sockets or C are needed for
stepping. Start SWAT+ with `--exchange` in its project folder. It sends a
catalog (`@grid`, `@obj`, `@var` lines, then `@ready`), then
`@paused <days done> <year> <day of year>`, and waits. Commands:

```
run [n]               run n days (default 1)    -> @paused ... or @ended <days>
get <variable>        -> @value <n> <v1> ... <vn>
set <variable> <i> <value>   object i (1-based), next day only   -> @ok
flush | ping          -> @ok
save "<file>"         write the state at this pause -> @ok
load "<file>"         right after startup: run forward to the saved day and load it
branch ...            fork the paused process (Mac, Linux)   -> @branched <pid>
finish                run to the end and complete the outputs
stop                  stop now   -> @bye
```

Any program that can write and read lines can drive SWAT+ this way. The
details are in [docs/design.md](docs/design.md).

## Limits

- Daily steps only.
- Only fluxes can be set (point source flow, water allocation demand), never
  storage, land use, soils or parameters; those are scenario inputs.
- Branching uses fork, so Mac and Linux only. Saving and loading state
  work on every platform, but the Windows build hasn't been tried.
- `get_value_ptr` is declined: the values live in the SWAT+ process.
- NextGen can use SWAT+ only as a domain-wide model (one formulation for
  the whole SWAT+ project), because SWAT+ routes its own channel network.
- Soft and hard calibration runs are refused in exchange mode.
- Supported SWAT+ versions: 62.0.1 ([COMPATIBILITY.md](COMPATIBILITY.md)).

## License

The exchange mode (`fortran/`, `patches/`) and any SWAT+ built with it are
under the GNU LGPL 2.1, the license of SWAT+. The Python wrapper is a
separate program that runs SWAT+ as its own process. It and everything
else here are under the MIT license. See [LICENSE](LICENSE).

SWAT+ is developed by USDA-ARS and Texas A&M AgriLife Research. This
project is independent of them.
