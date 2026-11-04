# Contributing

swatplus-bmi is meant to be used in studies whose results feed public decisions about water,
so a change has to keep the one promise the project makes: **SWAT+ stepped, branched or loaded
through swatplus-bmi gives exactly the output SWAT+ gives on its own.** Every rule below follows
from that.

## Ways to contribute

- **Report a bug.** Open an issue with the SWAT+ version, your platform and compiler, what you
  ran, and what happened. A SWAT+ project that shows the problem is the most useful thing you
  can attach.
- **Share a test project.** The tests run on the two example projects that ship with SWAT+, and
  those exercise about half of SWAT+'s code. A project that uses reservoirs, pesticides, salts,
  gwflow or calibration, and can be made public, would widen the tests more than anything else.
- **Ask for a variable.** Say what you want to read or set, which SWAT+ variable holds it, and
  what you'd check it against.
- **Pull requests** are welcome for fixes, variables, tests and documentation. For a larger
  change (a new SWAT+ release, a protocol change), open an issue first.
- **Questions:** open an issue with the `question` label.

## Rules for changes

1. **Identical output.** The comparison tests against unpatched SWAT+ must pass. If a change
   can't keep SWAT+'s output identical, it doesn't go in.
2. **A test for every variable.** A new variable is checked against SWAT+'s own output files,
   every day, in `tests/test_reading.py` or `tests/test_setting.py`.
3. **Only fluxes can be set.** Storage, land use, soils and parameters stay scenario inputs.
4. **The patch stays small.** Changes to SWAT+'s own files go in `patches/<tag>/`, and as few as
   possible. Everything else goes in new files in `fortran/`.
5. **Generated code is generated.** Don't edit `bmi_state_module.f90` or `bmi_locals_module.f90`;
   change the generators in `tools/`.
6. **A new SWAT+ release** gets its own `patches/<tag>/` folder, a `carried-locals.toml` checked
   with `build.sh --nosave` and `pytest -m full`, and a line in COMPATIBILITY.md.
7. **Plain writing.** Documentation and comments say what the code does and why, plainly.

## Running the checks

```bash
uv sync
uv run ./build.sh --stock --nosave
uv run pytest            # about two minutes
uv run pytest -m full    # about fifteen minutes more
```

## Conduct

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## License of contributions

Contributions to `fortran/` and `patches/` are under the GNU LGPL 2.1, like SWAT+. Everything
else is under the MIT license. By opening a pull request you agree to license your
contribution that way.
