"""Noise attribution (noise, phase 3).

The one case two instruments share is the case they must agree on: the
attribution of an unfired market jump is the compare() gap of the same
surgery. The rest states what a row is, what the day aggregate covers,
and what the statistics refactor kept.
"""

import math

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
    draws per stream, so an effect is the draw's and not a reshuffle."""
    root = world().run(1)
    control, = root.fork("control")
    control.run(1)
    attribution = noise.attribute(root, 1, noise.column("price", 1),
                                  "event", streams=["news"])
    assert attribution.rows
    # every arm ran the same schedule: an arm re-run here from the plan
    arm, = root.fork("arm")
    row = attribution.rows[0]
    noise.patch_draws(arm.engine, [noise.Patch(
        noise.DrawAddress(row["stream"], row["kind"], row["index"]), 0.0)])
    arm.run(1)
    assert arm.engine.draws_by_stream() == control.engine.draws_by_stream()


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
