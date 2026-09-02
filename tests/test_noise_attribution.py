"""Noise attribution (noise, phase 3).

The one case two instruments share is the case they must agree on: the
attribution of an unfired market jump is the compare() gap of the same
surgery. The rest states what a row is, what the day aggregate covers,
and what the statistics refactor kept.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import facts, noise
from tradefloor.counterfactual import World, compare

SEED = 42
UNIVERSE = tf.Universe.random(8, seed=99)
STEPS, TICKS = 2, 20
JUMPY = tf.ModelParams.from_preset("pt-v16", jump_intensity_market=1.0,
                                   jump_vix_coupling=0.0)


class Buyer:
    def act(self, obs):
        if obs.step % obs.steps_per_day == 0:
            return {obs.tickers[0]: 50}
        return {}


def world(model=None, universe=UNIVERSE):
    return World(seed=SEED, universe=universe, agent=Buyer(),
                 steps_per_day=STEPS, ticks_per_step=TICKS, model=model)


def test_the_attribution_of_one_unfired_jump_equals_the_compare_gap():
    root = world(model=JUMPY).run(3)
    control, unfired = root.fork("control", "unfired")
    unfired.unfire(4)
    control.run(4)
    unfired.run(4)
    report = compare(control, unfired)
    gap = report.treatment["pnl_since"] - report.control["pnl_since"]
    assert gap != 0.0

    attribution = noise.attribute(root, (4, 4), noise.pnl(), "event",
                                  streams=["jumps"], horizon=6)
    market = [r for r in attribution.rows if r["site"] == "jump_market_u"]
    assert [r["perturbation"] for r in market] == ["fire", "unfire"]
    fire, unfire = market
    assert unfire["effect"] == gap
    assert unfire["control"] == report.control["pnl_since"]
    assert unfire["treatment"] == report.treatment["pnl_since"]
    # the control fired (intensity one), so forcing a fire moves nothing
    assert fire["effect"] == 0.0
    assert unfire["index"] == unfired.surgeries[0]["address"][2]
    assert attribution.control == report.control["pnl_since"]
    assert any("fire, unfire" in c for c in attribution.caveats)
    assert any("day 6" in c for c in attribution.caveats)


def test_event_rows_cover_every_logged_draw_of_the_stream():
    root = world().run(1)
    n = len(UNIVERSE)
    attribution = noise.attribute(root, 1, noise.column("price", 1),
                                  "event", streams=["jumps", "volume_idio"])
    jumps = [r for r in attribution.rows if r["stream"] == "jumps"]
    # (1 + n) uniforms at two rows each, (1 + n) normals at one
    assert len(jumps) == 3 * (1 + n)
    assert sum(r["perturbation"] == "z+delta" for r in jumps) == 1 + n
    assert all(r["delta"] == 1.0 for r in jumps if r["kind"] == "normal")
    assert [r["ticker"] for r in jumps if r["site"] == "jump_company_z"] == list(root.engine.tickers)
    idio = [r for r in attribution.rows if r["stream"] == "volume_idio"]
    assert len(idio) == n and all(r["perturbation"] == "z+delta" for r in idio)
    assert all(math.isfinite(r["effect"]) for r in attribution.rows)
    assert all(r["control"] == attribution.control for r in attribution.rows)
    assert all(r["day"] == 1 for r in attribution.rows)


def test_day_aggregate_rows_cover_every_name_and_every_tick():
    root = world().run(1)
    n = len(UNIVERSE)
    sectors = root.engine.day_marks()[0]["sectors"]
    attribution = noise.attribute(root, (1, 1), noise.column("price", 1),
                                  "day", streams=["market"], delta=2.0)
    rows = attribution.rows
    assert len(rows) == n + 1 + sectors
    ticks = STEPS * TICKS
    assert all(r["count"] == ticks for r in rows)
    assert all(r["perturbation"] == "z+delta/sqrt(T)" for r in rows)
    assert all(r["delta"] == 2.0 / math.sqrt(ticks) for r in rows)
    assert all(r["granularity"] == "day" for r in rows)
    companies = [r for r in rows if r["site"] == "factor_idio_z"]
    assert [r["ticker"] for r in companies] == list(root.engine.tickers)
    assert sum(r["site"] == "market_factor_z" for r in rows) == 1
    assert sum(r["site"] == "sector_z" for r in rows) == sectors
    assert any(r["effect"] != 0.0 for r in rows)
    assert any("identified quantity" in c for c in attribution.caveats)
    assert any("settlement uniforms" in c for c in attribution.caveats)


def test_arms_share_every_other_draw():
    """Common random numbers: the arms and the control consume the same
    draws per stream, so an effect is the draw's and not a reshuffle.

    Stated on ``stream_positions``, which reports all seven streams.
    ``draws_by_stream`` reports three, and four of the five streams
    attributed at event level are invisible to it, so it cannot state this
    claim. Swept over every arm the plan builds rather than one.
    """
    root = world().run(1)
    control, = root.fork("control")
    control.run(2)
    assert set(control.engine.stream_positions()) == set(noise.STREAMS)
    assert len(control.engine.draws_by_stream()) == 3
    blind = set(noise.STREAMS) - set(control.engine.draws_by_stream())
    assert blind == {"jumps", "news", "volume", "volume_idio"}

    attribution = noise.attribute(root, (1, 1), noise.column("price", 2),
                                  "event", streams=["news", "jumps"])
    assert len(attribution.rows) > 1
    seen = set()
    checked = 0
    for row in attribution.rows:
        key = (row["stream"], row["kind"], row["index"], row["perturbation"])
        if key in seen:
            continue
        seen.add(key)
        arm, = root.fork("arm")
        noise.patch_draws(arm.engine, [noise.Patch(
            noise.DrawAddress(row["stream"], row["kind"], row["index"]),
            row["delta"])])
        arm.run(2)
        assert (arm.engine.stream_positions()
                == control.engine.stream_positions()), row
        checked += 1
    assert checked == len(attribution.rows)


def test_a_statistic_target_reads_facts_off_the_recorded_arms():
    root = world(universe=tf.Universe.random(4, seed=5)).run(1)
    attribution = noise.attribute(
        root, (32, 32), noise.statistic("annualised_vol_pct"), "event",
        streams=["volume"], horizon=32)
    assert len(attribution.rows) == 1
    control, = root.fork("control")
    control.run(32, record=True)
    stats = facts.panel_statistics(control.engine.bars(grain="day"),
                                   control.universe)
    assert attribution.control == stats["annualised_vol_pct"]
    assert math.isfinite(attribution.rows[0]["effect"])
    with pytest.raises(tf.ValidationError, match="not a panel statistic"):
        noise.attribute(root, 1, noise.statistic("mood"), "event",
                        streams=["volume"], horizon=32)


def test_facts_panel_statistics_is_what_measure_reports():
    universe = tf.Universe.random(4, seed=7)
    measured = facts.measure(seed=3, universe=universe, days=40)
    engine = tf.Engine(seed=3, universe=universe)
    for day in range(40):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
    stats = facts.panel_statistics(engine.bars(grain="day"), universe)
    assert set(stats) <= set(measured)
    assert all(measured[k] == v for k, v in stats.items())
    assert set(measured) - set(stats) == {
        "seed", "universe_fingerprint", "model_fingerprint", "days"}


def test_the_table_is_arrow_and_the_shard_is_a_slice():
    pa = pytest.importorskip("pyarrow")
    root = world().run(1)
    whole = noise.attribute(root, 1, noise.column("price", 1), "event",
                            streams=["jumps"])
    table = whole.table()
    assert isinstance(table, pa.Table)
    assert table.num_rows == len(whole.rows)
    assert table.column_names == [c for c, _ in noise.Attribution.COLUMNS]
    # Every field a row carries reaches the table. Compared against the
    # column list alone this assertion cannot fail, which is how the two
    # fields carrying the arms comparison stayed out of the table while
    # the rows held them.
    assert set(table.column_names) == set(whole.rows[0])
    got = table.to_pylist()
    assert all(r["positions_match"] is True for r in got)
    assert all(r["draws"] == whole.control_draws for r in got)
    part = noise.attribute(root, 1, noise.column("price", 1), "event",
                           streams=["jumps"], shard=(1, 3))
    assert part.rows == whole.rows[1::3]
    assert part.control == whole.control


def test_attribute_refuses_the_past_and_an_empty_plan():
    root = world().run(2)
    with pytest.raises(tf.ValidationError, match="has run"):
        noise.attribute(root, 1, noise.pnl(), "event")
    with pytest.raises(tf.ValidationError, match="nothing to attribute"):
        noise.attribute(root, 2, noise.pnl(), "day", streams=["jumps"])
    with pytest.raises(tf.ValidationError, match="horizon"):
        noise.attribute(root, (2, 3), noise.pnl(), "event", horizon=2)


def test_a_callable_target_and_run_record():
    root = world().run(1)

    def first_price(w):
        return w.trace[-1]["prices"][0]

    attribution = noise.attribute(root, 1, first_price, "event",
                                  streams=["volume"])
    assert attribution.target.label() == "first_price"
    assert len(attribution.rows) == 1
    recorded, plain = root.fork("recorded", "plain")
    recorded.run(1, record=True)
    plain.run(1)
    assert recorded.trace == plain.trace
    assert [e["op"] for e in recorded.order_log].count("record") == 1
    assert [e["op"] for e in plain.order_log].count("record") == 0


# -- the day a column target names -------------------------------------------

def test_a_column_target_is_read_at_its_own_day():
    """Read at the horizon, a target at day 1 with a window ending at day 3
    reported day 3's value under a label that read `at day 1`."""
    root = world()
    early = noise.attribute(root, (1, 3), noise.column("price", 1), "day",
                            streams=["market"])
    late = noise.attribute(root, (1, 3), noise.column("price", 3), "day",
                           streams=["market"])
    # two different days out of one window, and they differ
    assert early.control != late.control
    # each equals a world run straight to that day
    for attribution, day in ((early, 1), (late, 3)):
        probe = world()
        probe.run(day + 1)
        values = struct.unpack("<%dd" % len(probe.engine.tickers),
                               probe.engine.column("price"))
        assert attribution.control == sum(values) / len(values)
    # the label names the day the value came from
    assert "at day 1" in early.target.label()
    assert "at day 3" in late.target.label()


def test_a_column_target_before_the_window_is_refused():
    root = world().run(2)
    with pytest.raises(tf.ValidationError, match="which has run"):
        noise.attribute(root, (2, 2), noise.column("price", 1), "day",
                        streams=["market"])


# -- the horizon an event needs ----------------------------------------------

def test_the_default_horizon_reaches_the_open_after_an_event():
    """A jump lands at its day's close and is first seen at the next open,
    so a horizon stopping on the window's last day measures every event row
    as exactly zero."""
    root = world()
    reached = noise.attribute(root, (1, 1), noise.column("price", 2),
                              "event", streams=["jumps"])
    assert reached.horizon == 2
    assert any(r["effect"] != 0.0 for r in reached.rows)

    short = noise.attribute(root, (1, 1), noise.column("price", 1), "event",
                            streams=["jumps"], horizon=1)
    assert short.horizon == 1
    assert all(r["effect"] == 0.0 for r in short.rows)
    zero = [c for c in short.caveats if "measured exactly zero" in c]
    assert len(zero) == 1
    assert "first seen at the open after it" in zero[0]
    # and the fire/unfire caveat does not claim a zero row names the
    # control's state when both rows of every pair are zero
    forced = [c for c in short.caveats if "forced to each end" in c][0]
    assert "neither names the control's state" in forced
    assert "exactly one row is zero" not in forced


# -- the rows do not decompose the target -------------------------------------

def test_the_rows_carry_an_interaction_residual():
    """Single-draw finite differences through a market with feedback do not
    add up to the joint effect, and the gap is measured rather than left
    for the reader."""
    root = world(model=JUMPY)
    attribution = noise.attribute(root, (1, 1), noise.column("price", 3),
                                  "event", streams=["jumps"], horizon=3)
    changing = [r for r in attribution.rows if r["effect"] != 0.0]
    assert changing
    assert attribution.joint_rows == len(changing)
    summed = sum(r["effect"] for r in changing)
    assert attribution.interaction == attribution.joint - summed
    assert attribution.interaction != 0.0
    note = [c for c in attribution.caveats
            if "do not" in c and "decompose" in c]
    assert len(note) == 1
    assert "interaction residual" in note[0]
    assert "no total is claimed" in note[0]


# -- what delta means ---------------------------------------------------------

def test_the_day_step_is_delta_in_the_day_sums_own_sigma():
    """The published per-tick step times sqrt(count) is delta.

    One name's day is a sum over T tick normals whose sd is sqrt(T) tick
    sigmas, so a common shift of delta/sqrt(T) moves that sum by delta of
    its OWN sd. The derivation, not the constant: this holds for every
    tick count rather than pinning one.
    """
    for steps, ticks in ((1, 20), (2, 20), (1, 40)):
        root = World(seed=SEED, universe=UNIVERSE, agent=Buyer(),
                     steps_per_day=steps, ticks_per_step=ticks)
        attribution = noise.attribute(root, (1, 1), noise.column("price", 1),
                                      "day", streams=["market"], delta=2.0)
        for row in attribution.rows:
            assert row["count"] == steps * ticks
            assert row["delta"] * math.sqrt(row["count"]) == pytest.approx(2.0)


def test_the_day_effect_grows_with_the_tick_count():
    """A fixed delta is a fixed number of day sigmas, and a day with more
    ticks carries more noise, so the effect grows with T. Measured on the
    market factor over 20, 40, 80 and 160 ticks, four names at seed 99."""
    universe = tf.Universe.random(4, seed=99)
    effects = []
    for ticks in (20, 40, 80, 160):
        root = World(seed=SEED, universe=universe, agent=Buyer(),
                     steps_per_day=1, ticks_per_step=ticks)
        attribution = noise.attribute(root, (1, 1),
                                      noise.column("price", 1), "day",
                                      streams=["market"], delta=1.0)
        row = [r for r in attribution.rows
               if r["site"] == "market_factor_z"][0]
        effects.append(abs(row["effect"]))
    assert effects == sorted(effects)
    assert effects[-1] > 4 * effects[0]


# -- the counted caveats can be restated over merged rows ---------------------

def test_row_caveats_restate_the_counts_over_merged_rows():
    """A sharded plan merges its rows, and the caveats that count rows have
    to be taken over the merge. Read off a one-row probe they said 1 where
    the merged table held 18."""
    root = world(model=JUMPY)
    whole = noise.attribute(root, (1, 1), noise.column("price", 3), "event",
                            streams=["jumps"], horizon=3)
    probe = noise.attribute(root, (1, 1), noise.column("price", 3), "event",
                            streams=["jumps"], horizon=3,
                            shard=(0, 10 ** 9))
    assert len(probe.rows) == 1
    restated = noise.row_caveats(whole.rows, target=whole.target, last=1,
                                 horizon=3, event_streams=["jumps"])
    assert restated == [c for c in whole.plan_caveats
                        if "interaction residual" not in c]
    # the probe's own counted caveats are the ones a tool must drop
    assert probe.plan_caveats != restated
    assert any(c.startswith("1 event uniforms") for c in probe.plan_caveats)
    # the restated count is the whole plan's, derived from the rows
    fired = sum(1 for r in whole.rows if r["perturbation"] == "fire")
    assert fired > 1
    assert any(c.startswith(f"{fired} event uniforms") for c in restated)


# -- the arms are checked, not assumed ----------------------------------------

def test_an_economy_attribution_carries_its_caveats():
    """The one limitation the documentation names for this stream lived in
    the docstring and nowhere else: an economy call shipped an empty
    caveat list. The hazard is stated and the arms are measured against
    it."""
    root = world()
    attribution = noise.attribute(root, (1, 2), noise.column("price", 3),
                                  "event", streams=["economy"], horizon=3)
    assert attribution.rows
    assert attribution.caveats
    named = [c for c in attribution.caveats if "economy chain" in c]
    assert len(named) == 1
    assert "draw count depends on its own state" in named[0]
    # and the measurement the caveat promises
    measured = [c for c in attribution.caveats
                if "draw positions on all seven streams" in c]
    assert len(measured) == 1
    assert f"all {len(attribution.rows)} arms" in measured[0]


def test_the_arms_are_compared_on_all_seven_streams():
    """`draws_by_stream` reports three streams and cannot see four of the
    five attributed at event level, so the comparison is on the positions."""
    root = world()
    attribution = noise.attribute(root, (1, 1), noise.column("price", 2),
                                  "event", streams=["jumps", "news"])
    control, = root.fork("control")
    control.run(2)
    assert set(control.engine.stream_positions()) == set(noise.STREAMS)
    matched = [c for c in attribution.caveats if "matched the control" in c]
    assert len(matched) == 1


def test_the_default_horizon_holds_for_a_target_that_names_no_day():
    """The half of the horizon rule a column target hides.

    A column sets the horizon from its own day, so a test written on one
    passes whether or not the default reaches past the window. A target
    that names no day takes the default and nothing else, which is what
    states the rule: at the window's last day every event row is exactly
    zero, and one day past it they are not.
    """
    root = world()
    for target in (noise.pnl(),
                   lambda arm: float(arm.summary()["pnl_since"])):
        reached = noise.attribute(root, (1, 1), target, "event",
                                  streams=["jumps"])
        assert reached.horizon == 2
        assert any(r["effect"] != 0.0 for r in reached.rows)

        short = noise.attribute(root, (1, 1), target, "event",
                                streams=["jumps"], horizon=1)
        assert short.horizon == 1
        assert all(r["effect"] == 0.0 for r in short.rows)
        zero = [c for c in short.caveats if "measured exactly zero" in c]
        assert len(zero) == 1
        assert "the last day's events are never seen" in zero[0]


def test_the_arms_comparison_is_carried_on_the_rows():
    """A sharded plan merges its rows, so the comparison has to survive the
    merge. Counted where the arms run, it was a caveat a tool could not
    restate, and a one-row probe published a count of one above a table of
    ninety-six."""
    root = world()
    whole = noise.attribute(root, (1, 1), noise.pnl(), "event",
                            streams=["jumps"], horizon=3)
    # the flag is the comparison, checkable against the draws beside it
    assert all(r["positions_match"] is True for r in whole.rows)
    assert whole.control_draws > 0
    for row in whole.rows:
        assert row["positions_match"] == (row["draws"] == whole.control_draws)
    stated = [c for c in whole.plan_caveats if "arms matched" in c]
    assert len(stated) == 1
    assert f"all {len(whole.rows)} arms matched" in stated[0]

    probe = noise.attribute(root, (1, 1), noise.pnl(), "event",
                            streams=["jumps"], horizon=3, shard=(0, 10 ** 9))
    assert len(probe.rows) == 1
    # the probe's own count is one, and it is inside the set a tool drops
    assert any("all 1 arms matched" in c for c in probe.plan_caveats)
    # restated over the merged rows it is the whole plan's
    restated = noise.row_caveats(whole.rows, target=whole.target, last=1,
                                 horizon=3, event_streams=["jumps"])
    assert any(f"all {len(whole.rows)} arms matched" in c for c in restated)


def test_an_arm_that_moved_the_schedule_is_named():
    """The other side of the comparison, stated on a row the caller marks
    rather than on a market that will not misbehave to order."""
    rows = [{"stream": "economy", "kind": "uniform", "index": 4,
             "perturbation": "u=0", "effect": 0.1,
             "positions_match": False},
            {"stream": "economy", "kind": "uniform", "index": 5,
             "perturbation": "u=1", "effect": 0.0,
             "positions_match": True}]
    said = noise.row_caveats(rows, target=noise.pnl(), last=1, horizon=2,
                             event_streams=["economy"])
    named = [c for c in said if "consumed a different number" in c]
    assert len(named) == 1
    assert "1 of 2 arms" in named[0]
    assert "economy uniform 4 (u=0)" in named[0]


def test_the_caveats_meet_the_house_style(tmp_path):
    """The caveats are copied verbatim into the amplification report, which
    goes into a pull request body, so they are prose this repository
    governs. `prose.py` reads a path, so they are written out and handed to
    it the way every other surface is.
    """
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    said = []
    for streams, target, horizon in (
            (["jumps"], noise.column("price", 3), 3),
            (["economy"], noise.pnl(), 3),
            (["market"], noise.column("price", 2), None),
            (["jumps", "news"], noise.pnl(), 1)):
        attribution = noise.attribute(world(model=JUMPY), (1, 1), target,
                                      "both", streams=streams,
                                      horizon=horizon)
        said.extend(attribution.caveats)
    assert len(said) > 8
    page = tmp_path / "caveats.md"
    page.write_text(
        "## Caveats\n\n"
        + "\n".join(f"- {c}" for c in said) + "\n",
        encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "tools", "prose", "prose.py"),
         str(page)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 findings" in proc.stdout, proc.stdout
