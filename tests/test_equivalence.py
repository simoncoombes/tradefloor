"""Two spellings of one operation must produce one market.

The library offers several ways to say the same thing — a loop of sessions or
`run_days`, a loop of ticks or `run_session`, one engine per seed or an
`EngineBatch`. Each pair is a place where a defect can live in exactly one
branch and stay invisible, because nothing compares them.

That is not speculative. Two of them had diverged: `run_session(close_at_end=
True)` rolled GARCH from the day's total return while the explicit close
rolled it from the noise component, and a threaded sweep had to be checked
against a serial one to know the workers shared nothing.

Every comparison here is NaN-aware. Several columns are NaN until the state
they describe exists, and `NaN != NaN` — comparing raw lists reported three
columns as differing when nothing did, which nearly had me file a bug against
the engine for my own comparator.
"""

import math
import struct

import pytest

import tradefloor

UNIVERSE = tradefloor.Universe.random(6, seed=2)

#: Everything an equivalence must hold across. Prices alone are not enough:
#: the close-path divergence matched on price and differed on variance, so a
#: price-only check would have passed throughout.
FIELDS = (
    "price", "previous_close", "volume", "market_cap", "mispricing_s",
    "mispricing_momentum", "mispricing_s_prev_close", "garch_variance",
    "last_daily_return", "maker_inventory",
)


def column(engine, field):
    raw = engine.column(field)
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def identical(a, b, field):
    left, right = column(a, field), column(b, field)
    if len(left) != len(right):
        return False
    return all(
        (math.isnan(x) and math.isnan(y)) or x == y
        for x, y in zip(left, right)
    )


def assert_same_market(a, b, label):
    differing = [f for f in FIELDS if not identical(a, b, f)]
    assert differing == [], (label, differing)
    assert a.draws_consumed == b.draws_consumed, label
    assert a.tickers == b.tickers, label


def test_run_days_matches_a_hand_written_loop():
    packed = tradefloor.Engine(seed=5, universe=UNIVERSE)
    packed.run_days(3, record=False)

    manual = tradefloor.Engine(seed=5, universe=UNIVERSE)
    for _ in range(3):
        manual.open_market()
        manual.run_session(9, 30, 3, 390)
        manual.close_market()

    assert_same_market(packed, manual, "run_days vs manual loop")


def test_run_session_matches_a_loop_of_ticks():
    # The chunked call exists to save boundary crossings, not to change the
    # market. Measured elsewhere: the crossing costs 0.357us against 249us of
    # engine work, so this is for ergonomics and columnar output.
    chunked = tradefloor.Engine(seed=5, universe=UNIVERSE)
    chunked.open_market()
    chunked.run_session(9, 30, 3, 5)

    stepped = tradefloor.Engine(seed=5, universe=UNIVERSE)
    stepped.open_market()
    hour, minute = 9, 30
    for _ in range(5):
        stepped.tick(hour, minute, 3)
        minute += 1
        if minute == 60:
            minute, hour = 0, hour + 1

    assert_same_market(chunked, stepped, "run_session vs tick loop")


def test_close_at_end_matches_an_explicit_close():
    # The pair that had actually diverged. `close_at_end` built its innovation
    # slice BEFORE the session ran, so it could only ever pass "absent" and
    # take the total-return fallback, while the explicit close used the noise
    # component. Prices matched; variance did not.
    inline = tradefloor.Engine(seed=5, universe=UNIVERSE)
    inline.open_market()
    inline.run_session(9, 30, 3, 200, close_at_end=True)

    explicit = tradefloor.Engine(seed=5, universe=UNIVERSE)
    explicit.open_market()
    explicit.run_session(9, 30, 3, 200)
    explicit.close_market()

    assert_same_market(inline, explicit, "close_at_end vs explicit close")


def test_run_until_matches_an_unclosed_session():
    # It is the interactive shape and does NOT close, so its twin is the
    # session without a close rather than the one with it.
    halted = tradefloor.Engine(seed=5, universe=UNIVERSE)
    halted.open_market()
    halted.run_until(ticker=halted.tickers[0], above=1e12, max_ticks=120)

    open_ended = tradefloor.Engine(seed=5, universe=UNIVERSE)
    open_ended.open_market()
    open_ended.run_session(9, 30, 3, 120)

    assert_same_market(halted, open_ended, "run_until vs unclosed session")


def test_a_batch_row_matches_a_standalone_engine():
    batch = tradefloor.EngineBatch(seeds=[5, 11], universe=UNIVERSE)
    batch.open_market()
    batch.run_session(9, 30, 3, 60)
    packed = list(struct.unpack("<%dd" % (2 * len(UNIVERSE)), batch.prices()))

    for row, seed in enumerate((5, 11)):
        solo = tradefloor.Engine(seed=seed, universe=UNIVERSE)
        solo.open_market()
        solo.run_session(9, 30, 3, 60)
        start = row * len(UNIVERSE)
        assert packed[start:start + len(UNIVERSE)] == column(solo, "price")


def test_a_replayed_log_matches_the_run_that_produced_it():
    import json

    engine = tradefloor.Engine(seed=42, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, 80,
                       order_flow={engine.tickers[0]: (5000.0, 0.0)})
    engine.close_market()

    replayed = tradefloor.replay(json.loads(json.dumps(engine.order_log)),
                              seed=42, universe=UNIVERSE)
    assert_same_market(engine, replayed, "replay vs original")


def test_a_serialised_universe_is_the_same_universe():
    rebuilt = tradefloor.Universe.from_json(UNIVERSE.to_json())
    a = tradefloor.Engine(seed=9, universe=UNIVERSE)
    b = tradefloor.Engine(seed=9, universe=rebuilt)
    a.run_days(2, record=False)
    b.run_days(2, record=False)
    assert_same_market(a, b, "universe via JSON")


def test_the_comparator_can_actually_fail():
    """Otherwise every test above is a tautology.

    A NaN-aware comparator that returned True for everything would satisfy
    all seven, and the whole module would be decoration. Two genuinely
    different markets must come back differing.
    """
    a = tradefloor.Engine(seed=5, universe=UNIVERSE)
    b = tradefloor.Engine(seed=6, universe=UNIVERSE)
    a.run_days(1, record=False)
    b.run_days(1, record=False)
    with pytest.raises(AssertionError):
        assert_same_market(a, b, "different seeds")

    # And a difference in ONE column is caught, not just wholesale divergence.
    closed = tradefloor.Engine(seed=5, universe=UNIVERSE)
    closed.open_market()
    closed.run_session(9, 30, 3, 100, close_at_end=True)
    unclosed = tradefloor.Engine(seed=5, universe=UNIVERSE)
    unclosed.open_market()
    unclosed.run_session(9, 30, 3, 100)
    assert identical(closed, unclosed, "price")
    assert not identical(closed, unclosed, "garch_variance")
