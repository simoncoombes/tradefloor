"""The run log and replay."""

import json
import struct

import pytest

import pretium

UNIVERSE = pretium.Universe.random(5, seed=3)


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def busy_run(seed=99):
    """A run that exercises every kind of log entry."""
    e = pretium.Engine(seed=seed, universe=UNIVERSE,
                       macro_state=pretium.Macro(federal_funds_rate=0.03))
    for day in range(3):
        e.open_market()
        e.run_session(9, 30, 3, 60,
                      news=[pretium.News(ticker=UNIVERSE[1].ticker, price_impact=0.03)],
                      order_flow={UNIVERSE[0].ticker: (2e6, 5e5)})
        e.tick(10, 45, 3)
        e.draw_uniform()
        e.draw_normal()
        e.pin_macro(federal_funds_rate=0.03 + day * 0.005)
        if day == 1:
            e.list_instrument(pretium.Instrument(
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
    """The whole point. A seed alone does not do this.

    The market an agent trades in depends on its own orders, so one seed with
    different flow is a different market. Reproducing a run means reproducing
    every input, and that is what the log holds.
    """
    original = busy_run()
    replayed = pretium.replay(original.order_log, seed=99, universe=UNIVERSE,
                              macro=pretium.Macro(federal_funds_rate=0.03))
    assert arr(replayed.prices()) == arr(original.prices())
    assert replayed.draws_consumed == original.draws_consumed
    assert replayed.tickers == original.tickers


def test_replay_reproduces_ground_truth_too():
    original = busy_run()
    replayed = pretium.replay(original.order_log, seed=99, universe=UNIVERSE,
                              macro=pretium.Macro(federal_funds_rate=0.03))
    for factor in pretium.Engine.FACTORS:
        assert arr(replayed.attribution(factor)) == arr(original.attribution(factor))
    assert arr(replayed.column("mispricing_s")) == arr(original.column("mispricing_s"))


def test_a_log_is_plain_data():
    # A script may not run next year; a list of dicts will. That is what makes
    # a published experiment archivable rather than merely described.
    log = busy_run().order_log
    text = json.dumps(log)
    assert pretium.replay(json.loads(text), seed=99, universe=UNIVERSE,
                          macro=pretium.Macro(federal_funds_rate=0.03))


def test_until_lets_a_divergence_be_bisected():
    original = busy_run()
    log = original.order_log
    partial = pretium.replay(log, seed=99, universe=UNIVERSE,
                             macro=pretium.Macro(federal_funds_rate=0.03),
                             until=6)
    full = pretium.replay(log, seed=99, universe=UNIVERSE,
                          macro=pretium.Macro(federal_funds_rate=0.03))
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


def test_embedder_draws_are_recorded_because_they_move_the_stream():
    # Easy to overlook: taking a uniform between two ticks shifts every later
    # draw, so a replay that skipped it would produce a different market from
    # the same log.
    e = pretium.Engine(seed=1, universe=UNIVERSE)
    e.open_market()
    e.draw_uniform()
    e.run_session(9, 30, 3, 30)
    with_draw = arr(e.prices())

    log = e.order_log
    assert any(x["op"] == "draw_uniform" for x in log)

    without = [x for x in log if x["op"] != "draw_uniform"]
    assert arr(pretium.replay(log, seed=1, universe=UNIVERSE).prices()) == with_draw
    assert arr(pretium.replay(without, seed=1, universe=UNIVERSE).prices()) != with_draw


def test_the_log_carries_tickers_not_internal_ids():
    # An id like "AAA-0" is an implementation detail whose embedded index
    # stops matching position after a delisting, so a log full of them would
    # be both opaque and, after a roster edit, misleading.
    e = pretium.Engine(seed=1, universe=UNIVERSE)
    e.open_market()
    e.run_session(9, 30, 3, 30,
                  news=[pretium.News(ticker=UNIVERSE[2].ticker, price_impact=0.02)])
    news = [x for x in e.order_log if x["op"] == "run_session"][0]["news"]
    assert news[0]["ticker"] == UNIVERSE[2].ticker
    assert "-" not in news[0]["ticker"]


def test_a_rejected_call_is_not_logged():
    # A log containing a call that never happened would replay into a
    # different market than the one it claims to describe.
    e = pretium.Engine(seed=1, universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError):
        e.tick(99, 0, 3)
    assert e.order_log == []


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_an_unknown_operation_is_refused_not_skipped():
    # A replay that silently ignored an entry would produce a market the log
    # does not describe -- and it would look like a successful replay.
    with pytest.raises(pretium.ValidationError, match="unknown operation"):
        pretium.replay([{"op": "teleport"}], seed=1, universe=UNIVERSE)


def test_seed_and_universe_are_not_in_the_log_and_must_be_supplied():
    # They are the identity of the experiment. Burying them in a list of
    # operations would make replaying against the wrong starting conditions
    # easy to do without noticing.
    log = busy_run().order_log
    assert not any("seed" in entry for entry in log)
    with pytest.raises(TypeError):
        pretium.replay(log)


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

    universe = pretium.Universe.random(6, seed=5)
    engine = pretium.Engine(seed=99, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30,
                       order_flow={engine.tickers[0]: (5000.0, 0.0)},
                       news=[pretium.News(ticker=engine.tickers[1],
                                          price_impact=0.03)])
    engine.tick(10, 0, 3, order_flow={engine.tickers[2]: (100.0, 200.0)})
    engine.close_market()

    log = engine.order_log
    assert json.loads(json.dumps(log)) == log


def test_a_log_that_has_been_through_json_replays_exactly():
    import json

    universe = pretium.Universe.random(6, seed=5)
    engine = pretium.Engine(seed=99, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30,
                       order_flow={engine.tickers[0]: (5000.0, 0.0)})
    engine.close_market()

    archived = json.loads(json.dumps(engine.order_log))
    replayed = pretium.replay(archived, seed=99, universe=universe)
    assert replayed.prices() == engine.prices()
    assert replayed.draws_consumed == engine.draws_consumed


def test_a_log_replays_against_a_universe_rebuilt_from_its_own_json():
    # The archival path in full: neither the log nor the universe is the
    # in-memory object that produced the run. That is what "replayable without
    # the code that produced it" has to mean.
    import json

    universe = pretium.Universe.random(8, seed=5)
    engine = pretium.Engine(seed=42, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    engine.close_market()

    rebuilt = pretium.Universe.from_json(universe.to_json())
    replayed = pretium.replay(json.loads(json.dumps(engine.order_log)),
                              seed=42, universe=rebuilt)
    assert replayed.prices() == engine.prices()
