import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium", app_title="swatplus-bmi: a tour")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # swatplus-bmi: stepping SWAT+ one day at a time

    SWAT+ normally runs from its first day to its last without stopping. Everything it will do is
    fixed by its input files before it starts. That's fine for most studies, but not for the ones
    where something outside the model reacts to what the model is doing: a utility that cuts a
    withdrawal when the river is low, a pass-by flow that must be kept, a well field that stops
    pumping at a drawdown limit, or another model (groundwater, a water user, a reservoir operator)
    that answers back.

    **swatplus-bmi** makes SWAT+ pause before each day. While it's paused, a caller reads state
    (channel flow, aquifer storage, depth to water) and sets fluxes for the next day (a point
    source's flow, a water user's demand), through the
    [Basic Model Interface](https://bmi.readthedocs.io) (BMI 2.0), the interface CSDMS, NOAA's
    NextGen and eWaterCycle use for coupling models. Stepped this way, SWAT+'s output is
    byte-for-byte identical to a normal run.

    This notebook walks through:

    1. stepping SWAT+ and reading its state, and checking that against SWAT+'s own output;
    2. **same-day feedback**: a water user whose withdrawal is cut when yesterday's flow was low,
       and the trade-off between water delivered and low flow in the stream, which only a
       stepped model can show;
    3. warming up once and starting every scenario from the same state, by branching (Mac,
       Linux) or by saving and loading state (every platform);
    4. the things that catch people out.

    **The project.** It runs on `Osu_1hru`, the small example that ships with SWAT+: one HRU, one
    channel and one aquifer, 10 ha, 2010-2023. It's small so the notebook runs in about a minute.
    The flows are correspondingly small (litres per second). To use your own project, change
    `PROJECT`, `CHANNEL` and the water user below; nothing else depends on Osu_1hru.
    """)
    return


@app.cell
def _():
    import os
    import shutil
    import sys
    import time
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np

    from swatplus_bmi import SwatPlusBmi
    from swatplus_bmi.project import INTERVALS, set_print

    return INTERVALS, Path, SwatPlusBmi, mo, np, os, plt, set_print, shutil, sys, time


@app.cell
def _(Path, os):
    HERE = Path(__file__).resolve().parent
    ROOT = HERE.parent

    def _find_osu():
        for cand in (os.environ.get("SWATPLUS_SRC"), ROOT / "build" / "62.0.1" / "swatplus",
                     ROOT / ".cache" / "swatplus-62.0.1"):
            if cand and (Path(cand) / "refdata" / "Osu_1hru").is_dir():
                return Path(cand) / "refdata" / "Osu_1hru"
        raise FileNotFoundError("Osu_1hru not found: run ./build.sh first, or set PROJECT")

    # -- change these for your own project ----------------------------------
    PROJECT = _find_osu()          # a SWAT+ project folder (the one with file.cio)
    CHANNEL = "cha01"              # the channel the water user takes from
    RUNS = HERE / "runs"           # where the notebook's runs go
    FIGURES = ROOT / "docs" / "figures"
    # ------------------------------------------------------------------------
    RUNS.mkdir(exist_ok=True)
    return CHANNEL, FIGURES, PROJECT, RUNS


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Setting up runs

    Every run gets its own copy of the project, so the original stays clean and runs can go side
    by side. Two small edits are made to the copies:

    - **daily printing** of the channel and the water allocation, so the notebook can compare what
      it read through BMI with what SWAT+ wrote itself;
    - **a water user**: one SWAT+ water allocation *place of use* that takes water from `CHANNEL`
      and returns nothing (a consumptive use), at up to `rate` m³/s. SWAT+ limits a channel
      withdrawal to the water routed that day, so the user can't take water that isn't there.

    In your own project the water user would be whatever water allocation you have set up. The
    notebook only needs the name of the variable it sets.
    """)
    return


@app.cell
def _(CHANNEL, INTERVALS, PROJECT, RUNS, set_print, shutil):
    def new_run(name, rate=None):
        """A fresh copy of PROJECT in RUNS/name, with daily printing and, if rate (m3/s) is
        given, one consumptive water user on CHANNEL."""
        run = RUNS / name
        if run.exists():
            shutil.rmtree(run)
        shutil.copytree(PROJECT, run)
        # daily output for the channel, the water user and the aquifer, from the first day
        set_print(run, ["channel_sd", "water_allo", "aquifer"], intervals=INTERVALS, skip_years=0)
        if rate is not None:
            cio = [("water_rights      place_of_use.wro  point_of_diver.wro  null"
                    if l.startswith("water_rights") else l)
                   for l in (run / "file.cio").read_text().splitlines()]
            (run / "file.cio").write_text("\n".join(cio) + "\n")
            (run / "place_of_use.wro").write_text(
                "place_of_use.wro: one consumptive water user\n1\n"
                "num name typ typ_num pods pors dtbl_mx rate_max dtbl_pod_fr dtbl_por_fr\n"
                f"1 user01 out 1 1 0 null {rate} null null\n"
                f"1 1 {CHANNEL} cha 1 null 0 null 0.0 null 1000000. 1.0 n\n")
            (run / "point_of_diver.wro").write_text(
                f"point_of_diver.wro: {CHANNEL} supplies user01\n1\n"
                "num name typ typ_num pous pou_num pou_name pod_num typ typ_num right\n"
                f"1 {CHANNEL} cha 1 1 1 user01 1 out 1 senior\n")
        return run

    def daily_column(path, obj, col):
        """One column of a SWAT+ daily output file, for one object."""
        lines = path.read_text().splitlines()
        head = lines[1].split()
        i, k = head.index(col), head.index("name")
        return [float(l.split()[i]) for l in lines[3:]
                if len(l.split()) == len(head) and l.split()[k] == obj]

    return daily_column, new_run


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Stepping SWAT+ and reading its state

    `initialize` starts SWAT+ in its own process, paused before its first day. Each `update()`
    runs one day. While SWAT+ is paused, `get_value` reads what it holds at the end of the day
    just completed. Time is in days since the start date, the form eWaterCycle expects.

    Each SWAT+ object type is a BMI grid of points (one per channel, aquifer, point source or water
    user). `get_object_index` finds an object by its SWAT+ name.
    """)
    return


@app.cell
def _(CHANNEL, SwatPlusBmi, new_run, np, time):
    QOUT = "channel_water__volume_outflow_rate"        # m3/s
    VAQ = "aquifer_water__volume"                      # m3
    DWT = "soil_water_table_surface__depth"            # m below surface

    _m = SwatPlusBmi()
    _m.initialize(str(new_run("stepped")))
    print("time:", _m.get_time_units(), "| days:", _m.get_end_time())
    print("variables:", ", ".join(_m.get_output_var_names()))
    _ich = _m.get_object_index(QOUT, CHANNEL)
    _q, _v, _d = [], [], []
    _t0 = time.perf_counter()
    while _m.get_current_time() < _m.get_end_time():
        _m.update()
        _q.append(_m.get_value(QOUT, np.empty(1, np.float32))[_ich])
        _v.append(_m.get_value(VAQ, np.empty(1, np.float32))[0])
        _d.append(_m.get_value(DWT, np.empty(1, np.float32))[0])
    stepped_seconds = time.perf_counter() - _t0
    start_date = np.datetime64(_m.get_time_units().split()[2])
    _m.finalize()
    stepped = {"q": np.array(_q), "v": np.array(_v), "d": np.array(_d)}
    days = start_date + np.arange(len(_q)).astype("timedelta64[D]")
    print(f"{len(_q)} days stepped and read in {stepped_seconds:.1f} s")
    return DWT, QOUT, VAQ, days, stepped


@app.cell
def _(CHANNEL, RUNS, daily_column, days, np, plt, stepped):
    _swat = np.array(daily_column(RUNS / "stepped" / "channel_sd_day.txt", CHANNEL, "flo_out"))
    fig_state, _ax = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    _ax[0].plot(days, stepped["q"] * 1000, lw=0.6, color="#1f5f8b", label="read through BMI each day")
    _ax[0].plot(days, _swat * 1000, lw=0, marker=".", ms=1.2, color="#e07b39", alpha=0.6,
                label="SWAT+'s own channel_sd_day.txt")
    _ax[0].set_yscale("log")
    _ax[0].set_ylabel("channel outflow (L/s)")
    _ax[0].legend(loc="upper right", fontsize=8)
    _ax[1].plot(days, stepped["v"], lw=0.8, color="#3a7d44")
    _ax[1].set_ylabel("aquifer storage (m³)")
    _ax[2].plot(days, stepped["d"], lw=0.8, color="#6b4c9a")
    _ax[2].invert_yaxis()
    _ax[2].set_ylabel("depth to water (m)")
    fig_state.suptitle("Figure 1. State read through BMI after every day, against SWAT+'s own output")
    fig_state.tight_layout()
    _rel = np.abs(stepped["q"] - _swat) / np.maximum(np.abs(_swat), 1e-12)
    print(f"largest relative difference from SWAT+'s printed flow: {_rel.max():.1e} "
          "(SWAT+ prints four significant digits)")
    fig_state
    return (fig_state,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    The BMI values and SWAT+'s printed values agree to the four digits SWAT+ prints. The test
    suite goes further: it checks every output file of a stepped run against an unpatched SWAT+
    run, byte for byte.

    ## 2. Same-day feedback: a water user who backs off at low flow

    Here a water user takes water from the channel. It's sized at about half the channel's median
    flow, because in a 10 ha basin everything is small. Three ways to run it:

    - **no user**: the stream as it is;
    - **steady user**: takes its full rate every day it can;
    - **curtailed user**: before each day, looks at *yesterday's* flow, and takes nothing if that
      was below a threshold.

    The third needs the model to pause every day, report, and accept a new demand. A model run to
    the end can't do this: the curtailment depends on flows the model hasn't computed yet when it
    starts. The usual workaround is to iterate whole runs, or to curtail from observed flows,
    which no longer exist in a scenario.

    The rule is one line in the loop below. `set_value` applies to the next day only; on days the
    rule doesn't act, SWAT+ uses its own demand (`rate_max` in its water allocation input).
    """)
    return


@app.cell
def _(QOUT, SwatPlusBmi, new_run, np, stepped):
    DMD = "place_of_use_water__volume_demand_rate"        # m3/s, settable
    WDR = "place_of_use_water__volume_withdrawal_rate"    # m3/s, what was actually taken
    RATE = float(np.round(0.5 * np.median(stepped["q"]), 6))

    def run_user(name, rate=RATE, threshold=None):
        """Step a run with the water user; curtail on yesterday's flow if threshold (m3/s) is given."""
        m = SwatPlusBmi()
        m.initialize(str(new_run(name, rate=rate)))
        q_out, took, cut = [], [], []
        q_yesterday = np.inf
        while m.get_current_time() < m.get_end_time():
            curtail = threshold is not None and q_yesterday < threshold
            if curtail:
                m.set_value(DMD, np.array([0.0], np.float32))           # the rule
            m.update()
            q_yesterday = m.get_value(QOUT, np.empty(1, np.float32))[0]
            q_out.append(q_yesterday)
            took.append(m.get_value(WDR, np.empty(1, np.float32))[0])
            cut.append(curtail)
        m.finalize()
        return {"q": np.array(q_out), "w": np.array(took), "cut": np.array(cut)}

    print(f"user rate: {RATE * 1000:.3f} L/s ({RATE * 86400:.1f} m3/day)")
    return DMD, RATE, WDR, run_user


@app.cell
def _(np, run_user, stepped):
    THRESHOLD = float(np.percentile(stepped["q"], 30))       # the stream's 30th-percentile flow
    no_user = {"q": stepped["q"], "w": np.zeros_like(stepped["q"])}
    steady = run_user("steady")
    curtailed = run_user("curtailed", threshold=THRESHOLD)
    print(f"threshold: {THRESHOLD * 1000:.3f} L/s; curtailed on {curtailed['cut'].sum()} "
          f"of {len(curtailed['cut'])} days")
    return THRESHOLD, curtailed, no_user, steady


@app.cell
def _(RATE, THRESHOLD, curtailed, days, no_user, np, plt, steady):
    # a dry spell: the 90 days with the lowest mean flow without the user
    _k = 90
    _run = np.convolve(no_user["q"], np.ones(_k) / _k, mode="valid")
    _i0 = int(np.argmin(_run))
    _sl = slice(max(0, _i0 - 20), _i0 + _k + 20)
    fig_feedback, _ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True,
                                     gridspec_kw={"height_ratios": [2, 1]})
    for _r, _lab, _c in ((no_user, "no user", "#444444"), (steady, "steady user", "#c0392b"),
                         (curtailed, "curtailed user", "#1f5f8b")):
        _ax[0].plot(days[_sl], _r["q"][_sl] * 1000, lw=1.2, label=_lab, color=_c)
    _ax[0].axhline(THRESHOLD * 1000, ls="--", lw=0.8, color="#888888", label="curtailment threshold")
    _ax[0].set_ylabel("channel outflow (L/s)")
    _ax[0].legend(fontsize=8, ncol=2)
    _ax[1].step(days[_sl], steady["w"][_sl] * 1000, where="mid", color="#c0392b", lw=1, label="steady user takes")
    _ax[1].step(days[_sl], curtailed["w"][_sl] * 1000, where="mid", color="#1f5f8b", lw=1, label="curtailed user takes")
    _ax[1].axhline(RATE * 1000, ls=":", lw=0.8, color="#888888")
    _ax[1].set_ylabel("withdrawal (L/s)")
    _ax[1].legend(fontsize=8)
    fig_feedback.suptitle("Figure 2. The driest stretch of the run: the curtailed user backs off the day after low flow")
    fig_feedback.tight_layout()
    fig_feedback
    return (fig_feedback,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    During the dry stretch, the steady user keeps taking water and pushes the channel lower. The
    curtailed user stops the day after the flow drops below the threshold and starts again the day
    after it recovers. The same rule could watch the aquifer's depth to water instead, or a flow
    downstream, or come from another model.

    ### The trade-off

    Curtailing protects low flows, and the user gives up water for it. Where the threshold sits
    decides how much of each. Sweeping the threshold, one stepped run per value, draws the
    trade-off curve: water delivered against the stream's low flow. This is the kind of result a
    community, a utility or a regulator needs from a review of a large new water user, and it
    only exists if the model can respond to its own flows day by day.
    """)
    return


@app.cell
def _(RATE, no_user, np, run_user, stepped):
    def low_flow_7day(q):
        """Mean of each year's lowest 7-day mean flow (m3/s): a common low-flow statistic."""
        q7 = np.convolve(q, np.ones(7) / 7, mode="valid")
        years = np.array_split(q7, max(1, len(q) // 365))
        return float(np.mean([y.min() for y in years]))

    def days_below(q, level):
        return int((q < level).sum())

    LOW = float(np.percentile(stepped["q"], 10))             # a low-flow level to count days below
    sweep = []
    for _pct in (0, 10, 20, 30, 40, 50, 60, 70):
        _thr = float(np.percentile(stepped["q"], _pct)) if _pct else None
        _r = run_user(f"sweep-{_pct}", threshold=_thr)
        sweep.append({"pct": _pct, "delivered": _r["w"].sum() * 86400,
                      "low7": low_flow_7day(_r["q"]), "below": days_below(_r["q"], LOW)})
    base_low7 = low_flow_7day(no_user["q"])
    base_below = days_below(no_user["q"], LOW)
    full = RATE * 86400 * len(stepped["q"])
    return LOW, base_below, base_low7, full, sweep


@app.cell
def _(base_below, base_low7, full, plt, sweep):
    fig_tradeoff, _ax = plt.subplots(1, 2, figsize=(10, 4))
    _x = [s["delivered"] / full * 100 for s in sweep]
    _ax[0].plot(_x, [s["low7"] / base_low7 * 100 for s in sweep], "o-", color="#1f5f8b")
    _ax[1].plot(_x, [s["below"] - base_below for s in sweep], "o-", color="#c0392b")
    for _s, _xx in zip(sweep, _x):
        _lab = "steady" if _s["pct"] == 0 else f"P{_s['pct']}"
        _ax[0].annotate(_lab, (_xx, _s["low7"] / base_low7 * 100), fontsize=7,
                        textcoords="offset points", xytext=(4, 4))
    _ax[0].set_xlabel("water delivered (% of the user's full rate)")
    _ax[0].set_ylabel("7-day low flow (% of no user)")
    _ax[1].set_xlabel("water delivered (% of the user's full rate)")
    _ax[1].set_ylabel("extra days below the 10th-percentile flow")
    fig_tradeoff.suptitle("Figure 3. Curtailment thresholds from never (steady) to the 70th-percentile flow (P70)")
    fig_tradeoff.tight_layout()
    fig_tradeoff
    return (fig_tradeoff,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    Each point is a full 14-year run with the rule at a different threshold. Moving from no
    curtailment to curtailing at the median flow trades some delivered water for a large gain in
    the stream's low flow and fewer low-flow days. The numbers here belong to a 10 ha example; on
    a real project they'd answer questions like "how much of the user's demand can be met while
    keeping the stream's 7-day low flow within 10% of what it would be without the user?"

    ## 3. Warm up once, start every scenario from the same state

    A SWAT+ model usually needs a few years of warm-up before its soils and aquifers settle.
    Running the warm-up again for every scenario wastes most of the time when there are many
    scenarios, as in a sweep or a calibration ensemble. swatplus-bmi offers two ways to do it once:

    - **`branch(run_dir)`** duplicates the paused SWAT+ process (Unix `fork`). It's instant, and
      the copy carries on in its own folder. Mac and Linux.
    - **`save_state(path)` / `load_state(path)`** write SWAT+'s whole state to a folder and load it
      in a new process. Works everywhere. The serializer is generated from SWAT+'s own module
      declarations for each release.

    Either way the scenarios are identical, bit for bit, to running them from the start. The cell
    below checks that with save and load (and with branching where it's available), then counts
    what sharing the warm-up saves.
    """)
    return


@app.cell
def _(
    DMD,
    RATE,
    RUNS,
    SwatPlusBmi,
    new_run,
    np,
    shutil,
    stepped,
    sys,
    time,
):
    WARM_UP = 3653                                         # days, about ten years
    scenarios = {f"P{p}": float(np.percentile(stepped["q"], p)) for p in (20, 40, 60)}

    def drive(m, threshold):
        """Run the rest of the simulation with the curtailment rule (on yesterday's flow)."""
        q = np.empty(1, np.float32)
        m.get_value("channel_water__volume_outflow_rate", q)   # yesterday's flow, wherever we start
        while m.get_current_time() < m.get_end_time():
            if m.get_current_time() >= WARM_UP and q[0] < threshold:
                m.set_value(DMD, np.array([0.0], np.float32))
            m.update()
            m.get_value("channel_water__volume_outflow_rate", q)
        m.finalize()

    # every scenario from the start
    _t = time.perf_counter()
    for _name, _thr in scenarios.items():
        _m = SwatPlusBmi()
        _m.initialize(str(new_run(f"full-{_name}", rate=RATE)))
        drive(_m, _thr)
    t_full = time.perf_counter() - _t

    # one warm-up, saved, then every scenario loaded from it
    _t = time.perf_counter()
    _base = SwatPlusBmi()
    _base.initialize(str(new_run("warmup", rate=RATE)))
    _base.update_until(WARM_UP)
    _snap = RUNS / "warmup-state"
    shutil.rmtree(_snap, ignore_errors=True)
    _base.save_state(_snap)
    t_warm = time.perf_counter() - _t
    _t = time.perf_counter()
    for _name, _thr in scenarios.items():
        _m = SwatPlusBmi()
        _m.initialize(str(new_run(f"loaded-{_name}", rate=RATE)))
        _m.load_state(_snap)
        drive(_m, _thr)
    t_load = t_warm + time.perf_counter() - _t

    # the same with branching, where fork exists
    t_branch = None
    if sys.platform != "win32":
        _t = time.perf_counter()
        _kids = {}
        for _name in scenarios:
            _d = RUNS / f"branch-{_name}"
            shutil.rmtree(_d, ignore_errors=True)
            _kids[_name] = _base.branch(_d)
        for _name, _thr in scenarios.items():
            drive(_kids[_name], _thr)
        t_branch = t_warm + time.perf_counter() - _t
    _base.finalize()

    def same(a, b):
        files = ("channel_sd_day.txt", "water_allo_day.txt", "aquifer_day.txt", "basin_wb_yr.txt")
        return all((RUNS / a / f).read_bytes() == (RUNS / b / f).read_bytes() for f in files)

    for _name in scenarios:
        print(f"{_name}: loaded run identical to full run: {same(f'full-{_name}', f'loaded-{_name}')}"
              + (f", branched: {same(f'full-{_name}', f'branch-{_name}')}" if t_branch else ""))
    return WARM_UP, scenarios, t_branch, t_full, t_load


@app.cell
def _(WARM_UP, days, plt, scenarios, t_branch, t_full, t_load):
    _n, _d = len(scenarios), len(days)
    _from_start = _n * _d
    _shared = WARM_UP + _n * (_d - WARM_UP)
    fig_time, _ax = plt.subplots(figsize=(8, 2.8))
    _ax.barh(["each from the start", "one warm-up, then branched or loaded"], [_from_start, _shared],
             color=["#888888", "#1f5f8b"])
    _ax.barh(["one warm-up, then branched or loaded"], [WARM_UP], color="#9ab8cf",
             label="the warm-up, computed once")
    for _i, _v in enumerate([_from_start, _shared]):
        _ax.text(_v, _i, f" {_v:,} days", va="center", fontsize=8)
    _ax.set_xlabel("model days SWAT+ computes")
    _ax.set_xlim(0, _from_start * 1.18)
    _ax.invert_yaxis()
    _ax.legend(fontsize=8, loc="lower right")
    fig_time.suptitle(f"Figure 4. {_n} scenarios after a {WARM_UP:,}-day warm-up: "
                      f"{100 * (1 - _shared / _from_start):.0f}% fewer days to compute")
    fig_time.tight_layout()
    print(f"measured on this tiny project: each from the start {t_full:.1f} s, "
          f"loaded {t_load:.1f} s" + (f", branched {t_branch:.1f} s" if t_branch else ""))
    fig_time
    return (fig_time,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    On a real project, run time is close to proportional to the days SWAT+ computes, so Figure 4
    is the saving to expect. The more scenarios there are and the longer the warm-up, the bigger
    it gets: with 100 scenarios after the same warm-up, about 70% of the computing goes away.
    `Osu_1hru` is too small to show it on the clock. A whole stock run takes about 0.3 s, so the
    measured times above are mostly the cost of passing messages to SWAT+ every day, plus copying
    run folders. Loading also starts a new process and runs forward through the saved days
    without computing them. Branching copies the paused process in memory and is the quickest to
    start.

    ## 4. Things that catch people out

    - **A set value lasts one day.** `set_value` before `update()` applies to that day only, then
      SWAT+ goes back to its own inputs. Set it every day for a lasting change (as the rule above
      does on the days it acts).
    - **Values read are yesterday's.** At a pause, SWAT+ has finished the previous day and read the
      next day's weather, but hasn't routed it. That's what a same-day rule needs, but it's worth
      knowing when lining up times with observations.
    - **SWAT+'s own limits still apply.** A demand set above the water allocation's `rate_max` is
      capped at it. A channel withdrawal can't take more than the channel routes that day.
    - **Use water allocation for withdrawals, not negative point sources.** SWAT+ 62.0.1 doesn't
      limit a negative recall (a diversion) to the water in the channel. When it's larger than the
      other inflow, SWAT+ records the full diversion and takes none of the excess. The wrapper
      warns, and `channel_water__volume_inflow_rate` shows it.
    - **Aquifer withdrawals in SWAT+ 62.0.1 deliver less than they remove** (a bug in
      `wallo_withdraw.f90`; the test suite documents it). The aquifer side is right; don't route
      the delivered water back through SWAT+ and expect it to balance.
    - **Values are 4-byte reals,** as SWAT+ keeps them. `get_value_ptr` isn't available: the
      values live in the SWAT+ process.
    - **A saved state belongs to the build that wrote it.** Another build of SWAT+ refuses it. A new
      SWAT+ release, or a project using parts of SWAT+ the tests haven't covered (pesticides, salts,
      reservoirs, gwflow), should be checked with `build.sh --nosave` and `pytest -m full` before
      relying on save and load.

    ## Using your own project

    Set `PROJECT` to your SWAT+ project folder and `CHANNEL` to a channel name from its
    `chandeg.con`. Replace `new_run`'s water user with your own water allocation, or drop it and
    use `point_source_water__volume_flow_rate` for a discharge. Give the model's
    `get_output_var_names()` a look for what it can read.
    """)
    return


@app.cell
def _(FIGURES, fig_feedback, fig_state, fig_time, fig_tradeoff, mo, os):
    # Running this file as a script with SAVE_FIGURES=1 writes the figures the README uses.
    if os.environ.get("SAVE_FIGURES") == "1":
        FIGURES.mkdir(parents=True, exist_ok=True)
        for _name, _fig in (("state", fig_state), ("feedback", fig_feedback),
                            ("tradeoff", fig_tradeoff), ("warmup", fig_time)):
            _fig.savefig(FIGURES / f"tour-{_name}.png", dpi=150)
        _note = mo.md(f"Figures saved to `{FIGURES}`.")
    else:
        _note = mo.md("Set `SAVE_FIGURES=1` and run this file as a script to save the figures.")
    _note
    return


if __name__ == "__main__":
    app.run()
