"""The run log and replay."""

import json
import struct

import pytest

import tradefloor

UNIVERSE = tradefloor.Universe.random(5, seed=3)


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def busy_run(seed=99):
    """A run that exercises every kind of log entry."""
    e = tradefloor.Engine(seed=seed, universe=UNIVERSE,
                       macro_state=tradefloor.Macro(federal_funds_rate=0.03))
    for day in range(3):
        e.open_market()
        e.run_session(9, 30, 3, 60,
                      news=[tradefloor.News(ticker=UNIVERSE[1].ticker, price_impact=0.03)],
                      order_flow={UNIVERSE[0].ticker: (2e6, 5e5)})
        e.tick(10, 45, 3)
        e.draw_uniform()
        e.draw_normal()
        e.pin_macro(federal_funds_rate=0.03 + day * 0.005)
        if day == 1:
            e.list_instrument(tradefloor.Instrument(
                "IPO", "energy", initial_price=25.0,
                shares_outstanding=5e7, eps=1.2))
        if day == 2:
            e.delist(0)
        e.close_market()
        e.record(day)
    return e


# --------------------------------------------------------------------------
# Replay fidelity
# --------------------------------------------------------------------------

def test_a_replayed_log_reproduces_the_run_exactly():
    """The case this exists for. A seed alone does not do this.

    The market an agent trades in depends on its own orders, so one seed with
    different flow is a different market. Reproducing a run means reproducing
    every input, and the log holds those.
    """
    original = busy_run()
    replayed = tradefloor.replay(original.order_log, seed=99, universe=UNIVERSE,
                              macro=tradefloor.Macro(federal_funds_rate=0.03))
    assert arr(replayed.prices()) == arr(original.prices())
    assert replayed.draws_consumed == original.draws_consumed
    assert replayed.tickers == original.tickers


def test_replay_reproduces_ground_truth_too():
    original = busy_run()
    replayed = tradefloor.replay(original.order_log, seed=99, universe=UNIVERSE,
                              macro=tradefloor.Macro(federal_funds_rate=0.03))
    for factor in tradefloor.Engine.FACTORS:
        assert arr(replayed.attribution(factor)) == arr(original.attribution(factor))
    assert arr(replayed.column("mispricing_s")) == arr(original.column("mispricing_s"))


def test_a_log_is_plain_data():
    # A script may not run next year; a list of dicts will. That is what makes
    # a published experiment archivable rather than merely described.
    log = busy_run().order_log
    text = json.dumps(log)
    assert tradefloor.replay(json.loads(text), seed=99, universe=UNIVERSE,
                          macro=tradefloor.Macro(federal_funds_rate=0.03))


def test_until_lets_a_divergence_be_bisected():
    original = busy_run()
    log = original.order_log
    partial = tradefloor.replay(log, seed=99, universe=UNIVERSE,
                             macro=tradefloor.Macro(federal_funds_rate=0.03),
                             until=6)
    full = tradefloor.replay(log, seed=99, universe=UNIVERSE,
                          macro=tradefloor.Macro(federal_funds_rate=0.03))
    assert partial.draws_consumed < full.draws_consumed


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------

def test_every_mutating_call_appears_in_the_log():
    """The defence against a silently incomplete log.

    This test exists because the log WAS incomplete: `open_market` was not
    recorded, and replay still matched -- because that call happens not to
    affect the price path. A fidelity test alone would have stayed green while
    the log was missing an operation, and the next operation to go missing
    might not be harmless.
    """
    log = busy_run().order_log
    ops = {entry["op"] for entry in log}
    assert ops == {
        "open_market", "close_market", "run_session", "tick", "pin_macro",
        "list_instrument", "delist", "draw_uniform", "draw_normal", "record",
    }


def test_the_log_records_inputs_not_outputs():
    # Prices and attribution are consequences of replaying the inputs.
    # Recording them too would create a second source of truth that could
    # disagree with the first.
    log = busy_run().order_log
    forbidden = {"price", "prices", "close", "attribution", "draws_consumed"}
    for entry in log:
        assert not (set(entry) & forbidden), entry


def test_embedder_draws_are_recorded_because_they_move_the_external_stream():
    # Before the stream split this test asserted the opposite market claim:
    # one embedder uniform shifted every later engine draw, so a replay that
    # dropped it produced a different market. The split removed exactly that
    # coupling — the market half here is the cutover consumer's guarantee.
    #
    # The draws stay in the log anyway, because the log reproduces a HISTORY
    # and the embedder's own randomness is part of it: a replay that skipped
    # a recorded draw would hand the embedder different values from the
    # EXTERNAL stream afterwards, and any decision built on them would
    # diverge from the run the log claims to reproduce.
    e = tradefloor.Engine(seed=1, universe=UNIVERSE)
    e.open_market()
    e.draw_uniform()
    e.run_session(9, 30, 3, 30)
    with_draw = arr(e.prices())
    next_external = e.draw_uniform()

    log = e.order_log
    assert any(x["op"] == "draw_uniform" for x in log)

    without = [x for x in log if x["op"] != "draw_uniform"]
    # log[:-1]: everything up to, not including, the probe draw taken above.
    replayed = tradefloor.replay(log[:-1], seed=1, universe=UNIVERSE)
    skipped = tradefloor.replay(without, seed=1, universe=UNIVERSE)

    # The market is bit-identical whether or not the draw is replayed: the
    # embedder's consumption lives on its own stream now.
    assert arr(replayed.prices()) == with_draw
    assert arr(skipped.prices()) == with_draw

    # But the external stream's position is not: the faithful replay hands
    # back the same next value as the original run, the skipping one does
    # not. That is why the log keeps recording draws.
    assert replayed.draw_uniform() == next_external
    assert skipped.draw_uniform() != next_external


def test_the_log_carries_tickers_not_internal_ids():
    # An id like "AAA-0" is an implementation detail whose embedded index
    # stops matching position after a delisting, so a log full of them would
    # be both opaque and, after a roster edit, misleading.
    e = tradefloor.Engine(seed=1, universe=UNIVERSE)
    e.open_market()
    e.run_session(9, 30, 3, 30,
                  news=[tradefloor.News(ticker=UNIVERSE[2].ticker, price_impact=0.02)])
    news = [x for x in e.order_log if x["op"] == "run_session"][0]["news"]
    assert news[0]["ticker"] == UNIVERSE[2].ticker
    assert "-" not in news[0]["ticker"]


def test_a_rejected_call_is_not_logged():
    # A log containing a call that never happened would replay into a
    # different market than the one it claims to describe.
    e = tradefloor.Engine(seed=1, universe=UNIVERSE)
    with pytest.raises(tradefloor.ValidationError):
        e.tick(99, 0, 3)
    assert e.order_log == []


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_an_unknown_operation_is_refused_not_skipped():
    # A replay that silently ignored an entry would produce a market the log
    # does not describe -- and it would look like a successful replay.
    with pytest.raises(tradefloor.ValidationError, match="unknown operation"):
        tradefloor.replay([{"op": "teleport"}], seed=1, universe=UNIVERSE)


def test_seed_and_universe_are_not_in_the_log_and_must_be_supplied():
    # They are the identity of the experiment. Burying them in a list of
    # operations would make replaying against the wrong starting conditions
    # easy to do without noticing.
    log = busy_run().order_log
    assert not any("seed" in entry for entry in log)
    with pytest.raises(TypeError):
        tradefloor.replay(log)


def test_the_log_round_trips_through_json_by_value():
    """An archived experiment must compare equal to the run that produced it.

    The log emitted order_flow as a tuple, and JSON has no tuples — it came
    back a list, so ``log == json.loads(json.dumps(log))`` was False. Replay
    worked either way, because PyO3 accepts any two-element sequence, which is
    precisely why this would have gone unnoticed until someone diffed two
    archived logs and found differences that were not there.

    The README calls the log "archivable as data". This is that claim, checked.
    """
    import json

    universe = tradefloor.Universe.random(6, seed=5)
    engine = tradefloor.Engine(seed=99, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30,
                       order_flow={engine.tickers[0]: (5000.0, 0.0)},
                       news=[tradefloor.News(ticker=engine.tickers[1],
                                          price_impact=0.03)])
    engine.tick(10, 0, 3, order_flow={engine.tickers[2]: (100.0, 200.0)})
    engine.close_market()

    log = engine.order_log
    assert json.loads(json.dumps(log)) == log


def test_a_log_that_has_been_through_json_replays_exactly():
    import json

    universe = tradefloor.Universe.random(6, seed=5)
    engine = tradefloor.Engine(seed=99, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30,
                       order_flow={engine.tickers[0]: (5000.0, 0.0)})
    engine.close_market()

    archived = json.loads(json.dumps(engine.order_log))
    replayed = tradefloor.replay(archived, seed=99, universe=universe)
    assert replayed.prices() == engine.prices()
    assert replayed.draws_consumed == engine.draws_consumed


def test_a_log_replays_against_a_universe_rebuilt_from_its_own_json():
    # The archival path in full: neither the log nor the universe is the
    # in-memory object that produced the run. That is what "replayable without
    # the code that produced it" has to mean.
    import json

    universe = tradefloor.Universe.random(8, seed=5)
    engine = tradefloor.Engine(seed=42, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    engine.close_market()

    rebuilt = tradefloor.Universe.from_json(universe.to_json())
    replayed = tradefloor.replay(json.loads(json.dumps(engine.order_log)),
                              seed=42, universe=rebuilt)
    assert replayed.prices() == engine.prices()


# --------------------------------------------------------------------------
# The log is a fixed point, and the convenience path is not a second path
# --------------------------------------------------------------------------


def test_replaying_a_log_produces_that_same_log():
    """The invariant that catches an out-of-order log, and it caught one.

    A log is the reproduction mechanism, so replaying it must be a fixed
    point: feed the log in, get the same log out. Comparing PRICES alone is
    not enough -- a log whose entries are in the wrong order can still
    reproduce prices in the case where the mis-ordering happens not to matter,
    and then fail silently in the case where it does.

    It failed here. `run_session` opens the day when the caller has not, and
    the auto-open was written into the log AFTER the session it preceded. The
    log said "run a session, then open the market". Replaying it opened the
    market in the middle of the day, and the prices came out different.
    """
    original = busy_run()
    replayed = tradefloor.replay(original.order_log, seed=99, universe=UNIVERSE,
                              macro=tradefloor.Macro(federal_funds_rate=0.03))
    assert replayed.order_log == original.order_log


def test_letting_run_session_open_the_day_replays_exactly():
    # The path every other test in this file skips, because they all open the
    # market by hand.
    engine = tradefloor.Engine(seed=1, universe=UNIVERSE)
    engine.run_session(9, 30, 3, 30)
    engine.run_session(10, 0, 3, 30)
    engine.close_market()

    assert [entry["op"] for entry in engine.order_log] == [
        "open_market", "run_session", "run_session", "close_market"
    ], "the auto-open is not logged before the session it opened"

    replayed = tradefloor.replay(engine.order_log, seed=1, universe=UNIVERSE)
    assert arr(replayed.prices()) == arr(engine.prices())
    assert replayed.order_log == engine.order_log


def test_opening_the_day_by_hand_is_the_same_as_letting_it_happen():
    """The convenience must be transparent, not a second code path.

    If these differed, a user who called `open_market` and one who did not
    would get different markets from the same seed -- and the log would not
    say which they had.
    """
    auto = tradefloor.Engine(seed=1, universe=UNIVERSE)
    auto.run_session(9, 30, 3, 30)
    auto.run_session(10, 0, 3, 30)
    auto.close_market()

    explicit = tradefloor.Engine(seed=1, universe=UNIVERSE)
    explicit.open_market()
    explicit.run_session(9, 30, 3, 30)
    explicit.run_session(10, 0, 3, 30)
    explicit.close_market()

    assert arr(auto.prices()) == arr(explicit.prices())
    assert auto.order_log == explicit.order_log
