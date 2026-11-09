# SWAT+ versions

swatplus-bmi patches a specific SWAT+ release. A version is supported only if
it has a folder in `patches/` and passes the full test suite. `build.sh`
refuses any other version.

| SWAT+ | Released | Status | Tested on |
|---|---|---|---|
| 62.0.1 | 2026-10-19 | **Supported** | CI on every push: Ubuntu (gfortran from apt) and macOS 15 (Homebrew gcc). Locally: macOS 14 arm64, gfortran 16.1. |
| main | rolling | Watched: built and tested nightly with the 62.0.1 patch, as an early warning. Not supported. | nightly build |
| 62.0.0 | 2026-06-18 | Not supported: the patch doesn't apply (`time_control.f90` differs) | — |
| 61.x and earlier | 2024–2026 | Not supported: no water allocation places of use, and the patch doesn't apply | — |

Checked Nov 4, 2026: the patch applies unchanged to SWAT+ main as of commit
6f51536 (Oct 16, 2026). `time_control.f90`, `command.f90` and `main.f90.in`
are identical to 62.0.1 there. Checked again Nov 6 with the `hru_control.f90`
hook (0.4.0): it applies there too. Checked again Nov 7 with the two chemistry hooks (0.5.0), and Nov 9 with the
groundwater-into-soil hooks in `swr_percmain`, `nut_nlch` and `nut_solp` (0.7.0).

## How SWAT+ versions work

SWAT+ numbers its releases by model revision (the "Revision" line in
`simulation.out`), not semantic versioning: the 60.x series came before 61.x,
and 62 is the current series. Full releases are infrequent: 61.0.1 (Jul 2024),
61.0.2 (Aug 2024), 62.0.0 (Jun 2026), 62.0.1 (Oct 2026). Between them the
SWAT+ team tags prereleases (twelve 61.0.2.x tags, Nov 2024 to Jan 2026),
and the main branch takes merged pull requests continuously.

## Policy

- Each supported release gets its own `patches/<tag>/` folder. A new SWAT+
  release is added when the patch is moved onto it and the full suite
  passes, including the byte-for-byte comparisons with stock SWAT+.
- Only full releases are supported, not prereleases or main.
- The nightly build tests main with the newest supported patch. When it
  fails, a new SWAT+ release is likely to need a new patch.
- An older release stays supported as long as its patch still builds and
  passes. It isn't fixed for new features.
- If the exchange mode is merged into SWAT+ itself, the patch is no longer
  needed from that release on, and this table records which SWAT+ release
  has it built in.

## What has been proven, and on which projects

`tests/test_faithful.py` and the rest of the suite (docs/design.md, What the tests prove). With
nothing set, every output file must match a stock run byte for byte however the run is driven:
stepped while reading every variable every day, in uneven chunks, branched partway through a
year, and saved and loaded there. One column is left out: `cons_bmix` in `hru_carbvars.txt`,
which stock SWAT+ 62.0.1 prints from memory it never sets for layers below the mixing depth
(docs/design.md, Found in SWAT+ 62.0.1, item 16); `tests/test_unset_memory.py` shows it's the
only output on the test projects that does.

| Project | What it has | Checked (SWAT+ 62.0.1, macOS 14 arm64, Nov 6, 2026) |
|---|---|---|
| Osu_1hru (SWAT+ refdata) | 1 HRU, 1 channel, 1 aquifer, 2010–2023 | every mode, daily printing; also with a daily point source (with nitrate and phosphorus), a water allocation user, and outputs in an `out_path` folder; setting SWAT+'s own point source flow and loads, demand and soil layer temperatures back every day; weather records set as a series, day by day, again before they're read, and across a branch, a save and a load, each byte for byte the run on rewritten weather files; groundwater set into the soil layers, read back, printed as `gwsoilq` and closing over the run, and carried across a branch, a save and a load; every refusal; every kind of set on the bounds-checked build |
| Osu_1hru with salts (`tests/salts.py`) | the eight salt ions and a placeholder pesticide, no aquifer and no point source (what stock 62.0.1's salt module can run) | every mode; saved locals; salt reads against `channel_salt_day.txt`; a gain's salt and a loss's, with the channel's salt balance closing every day (Nov 7, 2026) |
| Ames_sub1 (SWAT+ refdata) | 12 HRUs, crop management, 1975–2020 | stepped and reading everything, save and load, saved locals (`-m full`); HRU water read against `hru_wb_day.txt` HRU by HRU, and groundwater set into one layer of one HRU changing only that HRU's output (1975) |
| A study's basin build (on the NextGen hydrofabric) | 2,371 HRUs (11,383 soil layers), 35 channels, 35 aquifers, 3 reservoirs, 2001–2005 | every mode, reading every variable every day (`$SWATPLUS_BMI_PROJECTS`), again with 0.5.0's chemistry variables on Nov 7; a soil-temperature hold, run by the study |

Not yet covered by any project: gwflow (refused for branching and saving when it reads a file a
day at a time), salts with aquifers or point sources (stock 62.0.1 can't run them), other
constituents, pesticide applications, sub-daily routing, and saved states
moved between platforms. Run a project of your own through the same checks with
`SWATPLUS_BMI_PROJECTS=/path/to/TxtInOut uv run pytest tests/test_faithful.py`.
