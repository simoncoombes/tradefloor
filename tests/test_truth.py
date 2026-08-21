"""The labelled dataset: every row says why the mispricing moved, and sums.

The claim these tests exist to defend is not "the simulator reports reasons" —
anything can report a reason. It is that the reported reasons **reconstruct the
outcome**, so a consumer can check the label rather than trust it. That is what
separates ground truth from commentary, and it is checkable, so it is checked.
"""

import math
import statistics

import pytest

pa = pytest.importorskip("pyarrow")

import pretium


# The seven, in schema order. Kept here as a literal rather than read from the
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
]

LEVELS = ["mispricing_s", "fundamental_value", "anchor_price"]


def run(n=6, seed=5, days=2):
    universe = pretium.Universe.random(n, seed=2)
    engine = pretium.Engine(seed=seed, universe=universe)
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
    universe = pretium.Universe.random(6, seed=2)
    engine = pretium.Engine(seed=5, universe=universe)
    engine.open_market()
    ticker = engine.tickers[0]
    engine.run_session(9, 30, 3, 60,
                       news=[pretium.News(ticker=ticker, price_impact=0.08)],
                       order_flow={ticker: (900_000.0, 0.0)})
    table = pa.table(engine.truth()).to_pydict()
    assert max(residuals(table, 6)) < 1e-15


def test_news_and_flow_land_on_the_traded_name_and_nowhere_else():
    universe = pretium.Universe.random(6, seed=2)
    engine = pretium.Engine(seed=5, universe=universe)
    engine.open_market()
    ticker = engine.tickers[0]
    engine.run_session(9, 30, 3, 60,
                       news=[pretium.News(ticker=ticker, price_impact=0.08)],
                       order_flow={ticker: (900_000.0, 0.0)})
    table = pa.table(engine.truth()).to_pydict()
    ids = table["instrument_id"]
    for column in ("company_news", "order_flow_impact"):
        values = table[column]
        assert all(values[k] != 0.0 for k in range(len(ids)) if ids[k] == 0)
        assert all(values[k] == 0.0 for k in range(len(ids)) if ids[k] != 0)


def test_a_quiet_run_leaves_the_shock_columns_at_zero():
    # Zero because nothing happened, not because the column is dead — the test
    # above proves the same columns light up when something does. Both halves
    # are needed: either alone is consistent with a broken column.
    _, table, _ = run()
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
    universe = pretium.Universe.random(4, seed=2)
    engine = pretium.Engine(seed=1, universe=universe)
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

    quiet = pretium.Engine(seed=7, universe=pretium.Universe.random(5, seed=1))
    quiet.run_days(2)
    watched = pretium.Engine(seed=7, universe=pretium.Universe.random(5, seed=1))
    watched.run_days(2, record=True)
    watched.truth()
    assert quiet.prices() == watched.prices()
    assert quiet.draws_consumed == watched.draws_consumed
    baseline = json.loads((here / "known_answer.json").read_text(encoding="utf-8"))
    assert known_answer.known_answer_digest() == baseline["sha256"]
