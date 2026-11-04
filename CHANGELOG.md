# Changelog

Changes that matter to someone using swatplus-bmi. Versions follow semantic versioning; the
SWAT+ releases each version supports are in COMPATIBILITY.md.

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
