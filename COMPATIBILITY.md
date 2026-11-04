# SWAT+ versions

swatplus-bmi patches a specific SWAT+ release. A version is supported only if
it has a folder in `patches/` and passes the full test suite. `build.sh`
refuses any other version.

| SWAT+ | Released | Status | Tested on |
|---|---|---|---|
| 62.0.1 | 2026-10-19 | **Supported** | macOS 14 arm64, gfortran 16.1 (local); Ubuntu and macOS in the nightly build |
| main | rolling | Watched: built and tested nightly with the 62.0.1 patch, as an early warning. Not supported. | nightly build |
| 62.0.0 | 2026-06-18 | Not supported: the patch doesn't apply (`time_control.f90` differs) | — |
| 61.x and earlier | 2024–2026 | Not supported: no water allocation places of use, and the patch doesn't apply | — |

Checked Nov 4, 2026: the patch applies unchanged to SWAT+ main as of commit
6f51536 (Oct 16, 2026). `time_control.f90`, `command.f90` and `main.f90.in`
are identical to 62.0.1 there.

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
