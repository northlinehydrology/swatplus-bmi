# Draft issue for swat-model/swatplus

Draft for Andrew to read, edit and post himself. Not posted.

---

**Title:** Optional external stepping mode for coupling SWAT+ through BMI (and is a restart or BMI effort already under way?)

Hello, and thank you for keeping SWAT+ open and taking outside contributions.

I'd like to couple SWAT+ to other models one day at a time: a caller runs a day, reads state (channel flow, aquifer storage), and sets fluxes for the next day (a point source's flow, a water allocation demand) before SWAT+ routes it. Typical uses are same-day feedback rules such as curtailing a withdrawal when yesterday's flow fell below a threshold, pass-by flows, and drawdown limits, and coupling through the Basic Model Interface (BMI 2.0, CSDMS), which NOAA's NextGen framework and eWaterCycle use.

As far as I can tell SWAT+ has no way to do this today: weather and recall inputs are read in full at startup, the daily loop in `time_control.f90` runs to the end, and there is no restart file. The closest work I found is Harms et al. (2023, HESS 27:1683), who coupled SWAT+ through a socket protocol in a fork (joelz575/swatplus); that is Linux-only and not BMI.

**What I'm proposing, off by default:**

1. An exchange mode, turned on only by a command-line argument (`--exchange`). Without it, SWAT+ behaves exactly as now; I've checked that every output file of the bundled `Osu_1hru` example is byte-identical to the stock 62.0.1 build with the mode compiled in but off.
2. In exchange mode SWAT+ pauses once a day, after `wallo_start_day` and before `call command`, and reads simple line commands on standard input (`run`, `get`, `set`, `finish`, `stop`), replying on standard output. Console messages go to a log file. No sockets and no C, so it builds everywhere SWAT+ builds today.
3. The change to existing files is three calls in `time_control.f90`, one in `main.f90.in` and one in `command.f90` (keeping the day's water allocation totals before printing zeroes them), plus one new module (`bmi_exchange_module.f90`). Only fluxes can be set (point source flow, water allocation demand); storage and parameters can't.
4. A Python BMI 2.0 wrapper lives outside SWAT+ as a separate program. Stepping SWAT+ one day at a time through it gives output identical to a stock run, every file, start to finish.

Separately, and optionally: saving and restoring model state, so a warm-up can be run once and every scenario started from it. BMI 2.0 has no save or restore function. I generate the serializer from the module declarations with a script rerun for each release, rather than writing it by hand, because the state spans 65 modules, about 2,000 variables and 523 derived types. Saving at day N, loading in a new process and continuing gives output identical to an unbroken run. A loaded run needs to get back to the saved day, so the pause call becomes `if (exch_pause()) cycle`, which skips days while a state is loading. On Mac and Linux a fork of the paused process also gives in-memory branching.

While building this I checked whether any of SWAT+'s implicitly saved locals (locals with initializers, about 5,400 of them) carry state between calls: a build that resets them all on every call gives identical output on the test projects, so they don't, at least on those code paths. That may be useful to you independently.

**Questions:**

- Is anyone already working on a BMI, a restart or state-saving facility, or an external stepping mode? I'd rather join that than duplicate it.
- Would you consider a pull request for the exchange mode as described (off by default, five lines in three existing files, one new module), or would you prefer it stay an external patch? The state saving could be a second, separate pull request, or stay outside SWAT+.
- If you would take it, is `--exchange` an acceptable switch, or would you rather it be set in `file.cio` or `codes.bsn`?
- Are there places in the daily loop, besides the start of the run, where input files are read? Exchange mode and state saving both assume all inputs are read before the first day.

The patch, the Python wrapper and the tests are at [link to be added] for anyone who wants to look. The patch is LGPL 2.1 like SWAT+.

Thanks,
Andrew Sommerlot
Northline Hydrology
