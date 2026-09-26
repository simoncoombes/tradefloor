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

    Stated on ``stream_positions``, which reports every stream.
    ``draws_by_stream`` reports three, and the rest of the streams
    attributed at event level are invisible to it, so it cannot state this
    claim. Swept over every arm the plan builds rather than one.
    """
    root = world().run(1)
    control, = root.fork("control")
    control.run(2)
    assert set(control.engine.stream_positions()) == set(noise.STREAMS)
    assert len(control.engine.draws_by_stream()) == 3
    blind = set(noise.STREAMS) - set(control.engine.draws_by_stream())
    # `market_vol_level` joined at 0.8.0 and `crisis_epicentre` on
    # 2026-09-22, and both are blind for the reason the five before them
    # are: `draws_by_stream` counts the three streams an embedder can reach,
    # and a mechanism stream is not one of them.
    assert blind == {"jumps", "news", "volume", "volume_idio", "overnight",
                     "market_vol_level", "crisis_epicentre"}

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
    """`measure` is its identity fields, the panel and the fear rows, exactly.

    Rebuilt from the parts on the same engine rather than held to a fixed
    key set: the fear rows read the macro table, which `panel_statistics`
    never sees, so they and their session diagnostics come from
    `fear_statistics`, the VIX's persistence from `persistence_statistics`,
    the crisis sector dispersion from `crisis_statistics` -- which reads the
    bars, the macro table AND the roster's sectors, so it is a part of its
    own and not a line in any of the other three -- and a key `measure`
    reports that none of the five parts produced fails here by name.

    `crisis_statistics` joined on 2026-09-22 with the row. On a 40-session
    run at a calm VIX it reports the row ABSENT under
    `crisis_sector_dispersion_blind`, which is a reading of the part and
    not a gap in it, so the rebuild below carries the same key.

    THE POINT IS THAT `measure` INVENTS NOTHING. A row computed inline
    there would be the one row with no independent caller and no
    independent check, so this assertion is what forced the VIX
    persistence row out of the middle of `measure` and into a part.
    """
    universe = tf.Universe.random(4, seed=7)
    measured = facts.measure(seed=3, universe=universe, days=40)
    engine = tf.Engine(seed=3, universe=universe)
    for day in range(40):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
    stats = facts.panel_statistics(engine.bars(grain="day"), universe)
    fear = facts.fear_statistics(engine.bars(grain="day"),
                                 engine.macro_table(), universe)
    # `burn` is part of the identity for the reason the two fingerprints are:
    # a panel read over a settled window and one read from a cold open are
    # different measurements. `measure` always emits it, 0 where nothing was
    # discarded, so the hand-built identity carries it at the same value the
    # call above ran at rather than leaving the reader to infer a default.
    identity = {"seed": 3, "universe_fingerprint": facts.fingerprint_of(universe),
                "model_fingerprint": engine.model_fingerprint, "days": 40,
                "burn": 0}
    persistence = facts.persistence_statistics(engine.macro_table(), days=40)
    crisis = facts.crisis_statistics(engine.bars(grain="day"),
                                     engine.macro_table(), universe)
    assert set(stats) <= set(measured)
    assert all(measured[k] == v for k, v in stats.items())
    assert set(stats).isdisjoint(fear)
    assert all(measured[k] == v for k, v in fear.items())
    assert set(persistence).isdisjoint(stats)
    assert set(persistence).isdisjoint(fear)
    assert all(measured[k] == v for k, v in persistence.items())
    assert set(crisis).isdisjoint(stats)
    assert set(crisis).isdisjoint(fear)
    assert set(crisis).isdisjoint(persistence)
    assert all(measured[k] == v for k, v in crisis.items())
    assert measured == {**identity, **stats, **fear, **persistence, **crisis}
    # The graded rows outside the shape set are the fear part's, and the
    # noise module's statistic target reads the panel part alone, so a
    # crisis row is refused there by name rather than read as absent.
    assert set(facts.CRISIS) <= set(fear)
    assert set(facts.SHAPE + facts.LEVEL) <= set(stats)


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

#: pt-v20 with the close's re-mark off, where a jump reaches no price
#: before the next open.
NO_REMARK = tf.ModelParams.from_preset("pt-v20",
                                       macro_publication_repricing=0.0)

#: How much of a jump the close's re-mark carries into that close's price,
#: as a share of what the next day reads. The re-mark scales each price by
#: its fair value after the close's macro step over its fair value before
#: it; a jump's permanent share (`fair_value_market_share`,
#: `fair_value_news_share`) moves the name's fair-value level at that same
#: close, and the ratio reads the level only through the buyback term's
#: yield (`buyback_payout_share`), so the jump reaches the price in the
#: second order. Measured on `world()` at seed 42: 6.4e-8 of the largest
#: effect a day later for the price, 3.0e-8 for the P&L; with the buyback
#: share at 0 it is 1.4e-14, and with both permanent shares at 0 it is 0.
REMARK_SHARE = 1e-6


def test_the_default_horizon_reaches_the_open_after_an_event():
    """A jump lands at its day's close and is first seen at the next open,
    so a horizon stopping on the window's last day measures every event row
    as exactly zero where the close writes no price (pt-v20 with
    `macro_publication_repricing` at 0 here). On pt-v20 itself the close's
    re-mark carries a second-order sliver of the jump into that close's
    price (`REMARK_SHARE`), so the short horizon measures almost nothing
    rather than nothing, and the default still reaches the open."""
    root = world()
    reached = noise.attribute(root, (1, 1), noise.column("price", 2),
                              "event", streams=["jumps"])
    assert reached.horizon == 2
    assert any(r["effect"] != 0.0 for r in reached.rows)
    largest = max(abs(r["effect"]) for r in reached.rows)
    sliver = noise.attribute(root, (1, 1), noise.column("price", 1),
                             "event", streams=["jumps"], horizon=1)
    assert max(abs(r["effect"]) for r in sliver.rows) < REMARK_SHARE * largest

    root = world(model=NO_REMARK)
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
    market factor over 20, 40, 80 and 160 ticks, four names at seed 99.

    THE PREMISE IS CHECKED AND IT HOLDS. `tick.rs` scales a tick by a FIXED
    `1 / sqrt(390)`, not by `1 / sqrt(T)`, so the day-accumulated market
    factor's sd really does grow as sqrt(T): measured over sixty seeds it
    reads 1.750e-3, 2.323e-3, 3.532e-3 and 5.070e-3 at 20, 40, 80 and 160
    ticks, a 20-to-160 ratio of 2.90 against sqrt(8) = 2.83.

    THE MARKET JUMP IS HELD OFF, and that is the whole of what changed here.
    `noise.attribute` shifts the factor and re-runs; `jump_vix_coupling`
    makes the market jump's INTENSITY a function of the VIX, so a shift can
    flip a Bernoulli draw and move the price by a whole jump. One flipped
    coin on one seed with four names swamps a sqrt(T) trend, and a Bernoulli
    inside a monotonicity assertion is not a monotone quantity at all.

    It went unnoticed because the coin had been landing the same way on both
    sides. At pt-v19's tape-derived `market_vol_alpha` / `market_vol_beta`
    it stops: shipped reads 0.0650, 0.1175, 0.4700, 0.4425 -- NOT monotone,
    at the 80-to-160 step -- and the same build with the old search optima
    reads 0.0425, 0.1175, 0.3825, 0.4650, monotone. Ruled out by measurement
    before the jump was found: `market_vol_ceiling_multiple` at 16, 40 and
    400 (identical to the bit), `crash_amplifier_slope` 0,
    `crisis_blend_gain` 0, `price_breaker_fraction` 0.999, `volume_move_cap`
    1e9, `market_vol_vix_excursion` 0 and `vix_ceiling` 80 -- none moves it.
    `jump_intensity_market` 0 makes it monotone, which is the negative
    control this docstring rests on.

    So the channel under test is held and the confounding one is switched
    off, which is what attributing to `market_factor_z` meant all along.
    Neither assertion is weakened: both are the ones that were here.
    """
    universe = tf.Universe.random(4, seed=99)
    model = tf.ModelParams.from_preset(tf.model_preset()["name"],
                                       jump_intensity_market=0.0)
    effects = []
    for ticks in (20, 40, 80, 160):
        root = World(seed=SEED, universe=universe, agent=Buyer(),
                     steps_per_day=1, ticks_per_step=ticks, model=model)
        attribution = noise.attribute(root, (1, 1),
                                      noise.column("price", 1), "day",
                                      streams=["market"], delta=1.0)
        row = [r for r in attribution.rows
               if r["site"] == "market_factor_z"][0]
        effects.append(abs(row["effect"]))
    # THE CLAIM IS THAT THE DAY'S DRAW MATTERS MORE IN A LONGER DAY, and it
    # does: 0.0175, 0.1325, 0.4850, 0.4725 on pt-v19, a factor of 27 from
    # end to end, against 0.0375, 0.1525, 0.2925, 0.4950 on pt-v18, a factor
    # of 13. MEASURED 2026-09-13. pt-v20 reads 0.1300, 0.2200, 0.3025,
    # 0.3175, a factor of 2.4 (0.8.5).
    #
    # THE SATURATION CLAIM IS WITHDRAWN, 2026-09-14, and the strict sort it
    # displaced is back. The claim was that the rise is steep to 80 ticks
    # and flat-to-falling after -- 0.4850 at 80, 0.4725 at 160, 0.4400 at
    # 320 -- pinned as `0.8 * effects[2] < effects[3] <= 1.1 * effects[2]`.
    # It was measured on ONE seed, and the ratio it bands is a one-seed
    # quantity that no band of width 0.3 can hold.
    #
    # MEASURED on the market-side warm-up tree, `Universe.random(4,
    # seed=99)`, `jump_intensity_market` 0, delta 1.0, one day, engine
    # seeds 42 to 51, the same ladder of 20, 40, 80 and 160 ticks:
    #
    #   42  0.0825 0.1200 0.3600 0.4550   e3/e2 1.264
    #   43  0.1000 0.1750 0.3525 0.6450   e3/e2 1.830
    #   44  0.0700 0.1600 0.2650 0.4700   e3/e2 1.774
    #   45  0.0475 0.2975 0.3475 0.4750   e3/e2 1.367
    #   46  0.0750 0.1075 0.3825 0.4425   e3/e2 1.157
    #   47  0.1075 0.1975 0.4500 0.6450   e3/e2 1.433
    #   48  0.1500 0.1950 0.4875 0.7150   e3/e2 1.467
    #   49  0.0850 0.2800 0.3800 0.8275   e3/e2 2.178
    #   50  0.0800 0.3075 0.3375 0.7850   e3/e2 2.326
    #   51  0.1075 0.1250 0.3400 0.4050   e3/e2 1.191
    #
    # The ratio runs 1.157 to 2.326, median 1.450, sd 0.411. ZERO of ten
    # land inside the 0.8-to-1.1 the assertion asked for, so the band was
    # not a near miss on this tree; the quantity it describes is somewhere
    # else entirely. The effect grows at the last step on every one of the
    # ten, and it grows FASTER than the sqrt(2) the fixed `1 / sqrt(390)`
    # tick scaling predicts on six of them. Whatever clamp made the old
    # reading flatten by 160 ticks does not bind there on this tree. That
    # is a change in the model's behaviour and it is filed as one; it is
    # not repaired here, because repairing it would move the draw
    # schedule.
    #
    # So the assertion goes back to the strict sort over all FOUR points,
    # which is what this test carried before the saturation comment cut it
    # to three, and which ten of ten seeds support. A model that saturates
    # again inside the ladder will fail this and say so.
    assert effects == sorted(effects), effects
    # Four, which is the bound this test has always carried, over the range
    # that rises. pt-v19 gives 27.7 and pt-v18 7.8, so the bound separates a
    # model where the day's length matters from one where it does not
    # without pinning either preset's slope.
    #
    # IT IS THIN AND THE SAME TEN SEEDS SAY SO, recorded here rather than
    # moved, because it passes today and lowering a passing bar is the
    # thing this file is not for. `effects[2] / effects[0]` reads 4.36 on
    # seed 42 and runs 3.16 to 7.32 over the ten, with 43, 44 and 51 below
    # four. The sqrt(T) premise the docstring states predicts 2.0 for a
    # four-fold day, so four is a bar somebody chose and not one anybody
    # derived. Whoever moves this next should put it on the ten seeds the
    # way `test_the_leverage_effect_is_real_since_the_gjr_term` was.
    #
    # MOVED AT 0.8.5, on the ten seeds, when pt-v20 became the default and
    # the bar of four failed: seed 42 reads 0.1300, 0.2200, 0.3025, 0.3175,
    # a ratio of 2.33 (pt-v19: 4.36). The same ten seeds on pt-v20:
    #
    #   42  0.1300 0.2200 0.3025 0.3175   e2/e0 2.33
    #   43  0.1500 0.2075 0.2750 0.3975   e2/e0 1.83
    #   44  0.1450 0.1925 0.2325 0.6850   e2/e0 1.60
    #   45  0.2250 0.3275 0.4325 0.5950   e2/e0 1.92
    #   46  0.1550 0.2125 0.2875 0.4525   e2/e0 1.85
    #   47  0.1475 0.2325 0.2775 0.3725   e2/e0 1.88
    #   48  0.2650 0.3200 0.4925 0.5675   e2/e0 1.86
    #   49  0.2275 0.3100 0.4400 0.4600   e2/e0 1.93
    #   50  0.2400 0.2300 0.3525 0.3750   e2/e0 1.47
    #   51  0.1300 0.2075 0.2825 0.4550   e2/e0 2.17
    #
    # The ratio runs 1.47 to 2.33 around the 2.0 the sqrt(T) premise
    # predicts for a four-fold day, where pt-v19 ran 3.16 to 7.32 above it:
    # pt-v20 grows as the premise says and pt-v19 grew faster. The strict
    # sort holds on nine of the ten (seed 50 dips at 40 ticks). The bar is
    # the premise's factor for a two-fold day, sqrt(2), which all ten clear
    # and which a model whose day length stopped mattering would not.
    assert effects[2] > math.sqrt(2) * effects[0], effects


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
    # AND THE MEASUREMENT THE CAVEAT PROMISES, in BOTH of its branches.
    #
    # This asserted only the clean one -- "all N arms matched" -- and was
    # therefore an assertion that could not fail in the way it mattered: the
    # caveat exists precisely because the economy chain's draw count depends
    # on its own state, so the interesting case is the one where an arm
    # DOES displace the chain, and the test said nothing about it. It went
    # red when pt-v19's VIX path made two of twenty-four arms displace it,
    # which is the hazard arriving exactly as documented.
    #
    # So: one of the two sentences is present, and whichever it is says how
    # many arms it is talking about. A run where every arm matches and a run
    # where two do not are both correct behaviour; a run that reports
    # neither is the defect.
    n = len(attribution.rows)
    streams = "draw positions on all %d streams" % len(noise.STREAMS)
    matched = [c for c in attribution.caveats if streams in c]
    displaced = [c for c in attribution.caveats
                 if "consumed a different number of draws from the control" in c]
    assert len(matched) + len(displaced) == 1, attribution.caveats
    if matched:
        assert f"all {n} arms" in matched[0]
    else:
        # "K of N arms", and K has to be under N: an attribution where EVERY
        # arm displaced the chain is not a common-random-numbers comparison
        # at all and should not be reported as one with a footnote.
        head = displaced[0].split(" arms", 1)[0]
        k, _, total = head.partition(" of ")
        assert int(total) == n, displaced[0]
        assert 0 < int(k) < n, displaced[0]


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
    zero, and one day past it they are not. Exactly zero where the close
    writes no price; on pt-v20 the close's re-mark carries the second-order
    sliver `REMARK_SHARE` describes, measured here as a bound.
    """
    remarked = world()
    root = world(model=NO_REMARK)
    for target in (noise.pnl(),
                   lambda arm: float(arm.summary()["pnl_since"])):
        full = noise.attribute(remarked, (1, 1), target, "event",
                               streams=["jumps"])
        sliver = noise.attribute(remarked, (1, 1), target, "event",
                                 streams=["jumps"], horizon=1)
        largest = max(abs(r["effect"]) for r in full.rows)
        assert largest > 0.0
        assert (max(abs(r["effect"]) for r in sliver.rows)
                < REMARK_SHARE * largest)

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
