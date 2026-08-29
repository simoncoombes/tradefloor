"""The labelled dataset: every row says why the mispricing moved, and sums.

The claim these tests exist to defend is not "the simulator reports reasons" —
anything can report a reason. It is that the reported reasons **reconstruct the
outcome**, so a consumer can check the label rather than trust it. That is what
separates ground truth from commentary, and it is checkable, so it is checked.
"""

import math
import statistics
import struct

import pytest

pa = pytest.importorskip("pyarrow")

import tradefloor


def _f64(buf):
    """Unpack a little-endian f64 buffer, the only shape the engine emits."""
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


# The nine, in schema order. Kept here as a literal rather than read from the
# table, so a column silently disappearing from the schema fails a test instead
# of shrinking the thing under test.
COMPONENTS = [
    "reversion",
    "momentum",
    "crowd_lean",
    "company_news",
    "order_flow_impact",
    "short_squeeze_effect",
    "random_noise",
    "circuit_breaker",
    "jump",
]

LEVELS = ["mispricing_s", "fundamental_value", "anchor_price"]


def run(n=6, seed=5, days=2, model=None):
    universe = tradefloor.Universe.random(n, seed=2)
    engine = tradefloor.Engine(seed=seed, universe=universe, model=model)
    engine.run_days(days, record=True)
    return engine, pa.table(engine.truth()).to_pydict(), n


def residuals(table, n):
    """|Δs − Σ components| for every row that has a previous tick."""
    s = table["mispricing_s"]
    out = []
    for k in range(n, len(s)):
        delta = s[k] - s[k - n]
        total = sum(table[key][k] for key in COMPONENTS)
        out.append(abs(delta - total))
    return out


# --------------------------------------------------------------------------
# The decomposition reconstructs the outcome
# --------------------------------------------------------------------------


def test_the_components_sum_to_the_change_in_mispricing():
    """The property that makes this a dataset rather than a commentary.

    Measured across 4,674 rows: median residual 1.7e-17, max 1.0e-16. That is
    float rounding and nothing else — the components are recorded in the same
    spelling the update applies, so the only gap is the order of additions.
    """
    _, table, n = run()
    res = residuals(table, n)
    assert max(res) < 1e-15
    assert statistics.median(res) < 1e-16


def test_the_reconstruction_still_holds_with_news_and_order_flow():
    # The two columns a quiet run leaves at zero. A decomposition that only
    # balanced when nothing happened would be worthless.
    universe = tradefloor.Universe.random(6, seed=2)
    engine = tradefloor.Engine(seed=5, universe=universe)
    engine.open_market()
    ticker = engine.tickers[0]
    engine.run_session(9, 30, 3, 60,
                       news=[tradefloor.News(ticker=ticker, price_impact=0.08)],
                       order_flow={ticker: (900_000.0, 0.0)})
    table = pa.table(engine.truth()).to_pydict()
    assert max(residuals(table, 6)) < 1e-15


def test_news_and_flow_land_on_the_traded_name_and_nowhere_else():
    universe = tradefloor.Universe.random(6, seed=2)
    engine = tradefloor.Engine(seed=5, universe=universe)
    engine.open_market()
    ticker = engine.tickers[0]
    engine.run_session(9, 30, 3, 60,
                       news=[tradefloor.News(ticker=ticker, price_impact=0.08)],
                       order_flow={ticker: (900_000.0, 0.0)})
    table = pa.table(engine.truth()).to_pydict()
    ids = table["instrument_id"]
    for column in ("company_news", "order_flow_impact"):
        values = table[column]
        assert all(values[k] != 0.0 for k in range(len(ids)) if ids[k] == 0)
        assert all(values[k] == 0.0 for k in range(len(ids)) if ids[k] != 0)


def test_a_quiet_run_leaves_the_shock_columns_at_zero():
    # Zero because nothing happened, not because the column is dead -- the test
    # above proves the same columns light up when something does. Both halves
    # are needed: either alone is consistent with a broken column.
    #
    # "Quiet" has to be asked for explicitly since pt-v12 became the default.
    # Earlier defaults generated no news of their own, so an engine with no
    # news argument WAS a quiet market; pt-v11 added endogenous news and
    # pt-v12 ships it on, so a default run now has company_news in it by
    # design. Turning the mechanism off is what this test means by quiet.
    quiet_model = tradefloor.ModelParams.from_preset(
        endogenous_news_intensity=0.0, endogenous_news_sigma=0.0)
    _, table, _ = run(model=quiet_model)
    assert all(x == 0.0 for x in table["company_news"])
    assert all(x == 0.0 for x in table["order_flow_impact"])
    assert any(x != 0.0 for x in table["random_noise"])
    assert any(x != 0.0 for x in table["reversion"])


# --------------------------------------------------------------------------
# The three levels are three different things
# --------------------------------------------------------------------------


def test_the_anchor_is_the_valuation_carrying_the_mispricing():
    # anchor_price = fundamental_value * exp(mispricing_s), exactly. These are
    # easy to conflate and the whole table is useless if they are: dividing a
    # print by the ANCHOR gives the microstructure residual, dividing by the
    # VALUATION gives the mispricing, and they are not the same number.
    _, table, _ = run()
    for k in range(6, 300):
        expected = table["fundamental_value"][k] * math.exp(table["mispricing_s"][k])
        assert table["anchor_price"][k] == pytest.approx(expected, rel=1e-12)


def test_the_anchor_is_not_the_printed_price():
    # If it were, the order book would be doing nothing, and "market impact is
    # emergent" would be false.
    engine, table, n = run()
    bars = pa.table(engine.bars()).to_pydict()
    differ = sum(1 for k in range(len(bars["close"]))
                 if bars["close"][k] != table["anchor_price"][k])
    assert differ > 0.5 * len(bars["close"])


def test_every_level_column_is_finite_once_a_company_has_ticked():
    _, table, _ = run()
    for column in LEVELS:
        assert all(math.isfinite(x) for x in table[column]), column


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_the_schema_is_the_full_labelled_row():
    engine, _, _ = run()
    schema = pa.table(engine.truth()).schema
    assert schema.names == (
        ["day", "tick", "instrument_id"] + LEVELS + COMPONENTS
    )


def test_every_numeric_column_is_f64():
    # No f32 option, here or anywhere. A half-precision copy would be a
    # different market that happens to plot the same.
    engine, _, _ = run()
    schema = pa.table(engine.truth()).schema
    for name in LEVELS + COMPONENTS:
        assert schema.field(name).type == pa.float64(), name


def test_truth_streams_one_batch_per_day():
    universe = tradefloor.Universe.random(4, seed=2)
    engine = tradefloor.Engine(seed=1, universe=universe)
    engine.run_days(3, record=True)
    assert engine.truth().num_batches == 3


def test_truth_joins_to_bars_on_the_same_keys():
    engine, table, _ = run()
    bars = pa.table(engine.bars()).to_pydict()
    for key in ("day", "tick", "instrument_id"):
        assert bars[key] == table[key], key


# --------------------------------------------------------------------------
# Observing does not disturb
# --------------------------------------------------------------------------


def test_recording_the_decomposition_does_not_change_the_market():
    """Ground truth is observational.

    The components are read out of the same values the tick already computed,
    never fed back into it — the update is written in the exact spelling the
    benched trajectories were produced by, and summing the reported components
    to advance `s` instead would round differently and be a different market.

    Checked against the cross-platform known-answer digest, which did not move
    when these columns were added.
    """
    import json
    import sys
    from pathlib import Path

    here = Path(__file__).parent
    sys.path.insert(0, str(here))
    import known_answer

    quiet = tradefloor.Engine(seed=7, universe=tradefloor.Universe.random(5, seed=1))
    quiet.run_days(2)
    watched = tradefloor.Engine(seed=7, universe=tradefloor.Universe.random(5, seed=1))
    watched.run_days(2, record=True)
    watched.truth()
    assert quiet.prices() == watched.prices()
    assert quiet.draws_consumed == watched.draws_consumed
    baseline = json.loads((here / "known_answer.json").read_text(encoding="utf-8"))
    assert known_answer.known_answer_digest() == baseline["sha256"]


# --------------------------------------------------------------------------
# The two grains agree
# --------------------------------------------------------------------------


def test_the_day_attribution_is_the_truth_column_summed_over_the_day():
    """`attribution()` and `truth` are one quantity at two grains.

    They used to be two quantities with the same names. `attribution()` summed
    the RAW factors, but the three drift factors are divided by 390 on their
    way into `s` while noise is multiplied by the intraday volatility curve —
    so raw sums overstated news, flow and squeeze by around 390x against
    noise.

    That was not cosmetic. `_dominant_factor` ranks these magnitudes to score
    whether an agent was right for the right REASON, and on one measured
    session raw called it `company_news` (6.0e0 against noise at 6.0e-2) where
    applied calls it `random_noise` (7.8e-1 against news at 1.5e-2). The
    scorer was systematically answering with a drift factor on days that were
    almost entirely noise.
    """
    import struct

    universe = tradefloor.Universe.random(6, seed=4)
    engine = tradefloor.Engine(seed=11, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 90)
    engine.record(0)
    table = pa.table(engine.truth()).to_pydict()

    ids = table["instrument_id"]
    for factor in tradefloor.Engine.FACTORS:
        day_total = struct.unpack(
            "<%dd" % len(universe), engine.attribution(factor)
        )
        for i in range(len(universe)):
            column = sum(table[factor][k] for k in range(len(ids)) if ids[k] == i)
            assert column == pytest.approx(day_total[i], abs=1e-15), (factor, i)


def test_the_dominant_factor_is_the_one_that_actually_moved_the_price():
    # A quiet session with no news and no order flow IS noise, and the scorer
    # must say so. The old raw ranking named company_news here.
    from tradefloor.harness import _dominant_factor

    engine = tradefloor.Engine(seed=11, universe=tradefloor.Universe.random(6, seed=4))
    engine.open_market()
    engine.run_session(9, 30, 3, 90)
    assert _dominant_factor(engine) == "random_noise"


# --------------------------------------------------------------------------
# A day of many sessions is one day
# --------------------------------------------------------------------------


def test_attribution_covers_the_whole_day_not_the_last_step():
    """`Engine::run_session` used to open the market every time it ran.

    For the reference, which runs one session per day, opening inside the
    session and opening the day are the same act. They stop being the same
    when a day is made of several sessions -- which is exactly what agent
    stepping does. `open_market` resets the attribution accumulator, so
    attribution documented itself as per-DAY and was per-session.

    The visible consequence: a large buy in step 0 of a six-step day moves the
    market, the tape records it, and `attribution("order_flow_impact")` read
    exactly zero at the close. The agent's own impact was erased from the
    ground truth that scores it.
    """
    universe = tradefloor.Universe.random(6, seed=5)
    engine = tradefloor.Engine(seed=3, universe=universe)
    ticker = universe[0].ticker
    engine.open_market()
    for step in range(6):
        hour, minute = divmod(9 * 60 + 30 + step * 60, 60)
        flow = ({ticker: (universe[0].avg_volume * 0.4, 0.0)}
                if step == 0 else None)
        engine.run_session(hour, minute, 3, 60, order_flow=flow)
    engine.close_market()
    engine.record(0)

    total = sum(abs(x) for x in _f64(engine.attribution("order_flow_impact")))
    assert total > 0, (
        "the day traded and attribution reports no order-flow impact at all"
    )


def test_attribution_equals_the_tape_for_every_factor():
    """The two ground-truth surfaces must describe the same window.

    `truth` is per-tick and `attribution` is per-day, so summing one over a
    day must give the other. This is the invariant that catches a day and a
    session being confused for each other, in either direction, and it failed
    in BOTH directions before: the tape held only the last session's ticks and
    attribution held only the last session's total.
    """
    pa = pytest.importorskip("pyarrow")
    pc = pytest.importorskip("pyarrow.compute")

    universe = tradefloor.Universe.random(4, seed=5)
    engine = tradefloor.Engine(seed=1, universe=universe)
    engine.open_market()
    for step in range(4):
        hour, minute = divmod(9 * 60 + 30 + step * 60, 60)
        engine.run_session(hour, minute, 3, 60)
    engine.close_market()
    engine.record(0)

    truth = pa.table(engine.truth())
    rows = truth.filter(pc.equal(truth.column("instrument_id"), 0))
    checked = 0
    for factor in tradefloor.Engine.FACTORS:
        if factor not in truth.column_names:
            continue
        recorded = _f64(engine.attribution(factor))[0]
        tape = sum(v for v in rows.column(factor).to_pylist() if v is not None)
        assert recorded == pytest.approx(tape, abs=1e-15), (
            f"{factor}: attribution {recorded:+.6e} against a tape sum of "
            f"{tape:+.6e}"
        )
        checked += 1
    assert checked == 9, f"only {checked} factors compared"
    # And at least one of them must be non-zero, or this compared zeros.
    assert any(_f64(engine.attribution(f))[0] != 0.0
               for f in tradefloor.Engine.FACTORS if f in truth.column_names)


def test_a_single_session_day_is_unaffected():
    """The parity guarantee. One session per day is what the reference does,
    and opening once versus opening twice with no ticks between is the same
    state -- so no golden vector moves. Asserted rather than reasoned."""
    universe = tradefloor.Universe.random(4, seed=5)

    explicit = tradefloor.Engine(seed=1, universe=universe)
    explicit.open_market()
    explicit.run_session(9, 30, 3, 120)
    explicit.close_market()

    implicit = tradefloor.Engine(seed=1, universe=universe)
    implicit.run_session(9, 30, 3, 120)
    implicit.close_market()

    assert explicit.prices() == implicit.prices()
