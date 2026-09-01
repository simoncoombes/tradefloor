"""The pt-v17 recomposition dials: state that survives a snapshot, wires
that take no draws.

Three mechanisms joined the engine for the era: the crash-gated co-jump
family (`vix_selfex_*`, two state floats on the economy), the HAR anchor
for the VIX target (`vix_har_*`, two more), and the up-tick compensation
of the down-transmission wire (`market_beta_up_comp`, stateless).
Inertness at defaults is the known-answer test's job, and the
nothing-dormant forking guard exercises every settable at once; these
tests pin the specific new claims at REAL doses — a snapshot taken
mid-episode restores an engine that continues bit-for-bit, and the
stateless wire moves the market without moving the draw schedule.
"""

import struct

import tradefloor

UNIVERSE = tradefloor.Universe.random(8, seed=2)


def prices_and_draws(engine):
    n = len(engine.tickers)
    return (struct.unpack("<%dd" % n, engine.column("price")),
            engine.draws_consumed)


def continue_days(engine, days):
    """Prices after `days` more, plus the draws CONSUMED BY those days.

    The delta, not the counter: `draws_consumed` counts from an engine's
    construction, so a parent that ran fifteen days before the snapshot
    carries them on its counter and a freshly-restored engine does not.
    The snapshot contract is about the future — same continuation, same
    consumption — which the delta states and the counter does not."""
    before = engine.draws_consumed
    engine.run_days(days, record=False)
    prices, after = prices_and_draws(engine)
    return prices, after - before


def test_the_co_jump_state_survives_a_snapshot_mid_episode():
    """Fear and excitation are economy state: a snapshot taken while an
    episode is live must restore an engine that continues exactly. The
    dials are test doses chosen to make an event certain (threshold 0:
    any down day can fire at gain 1.5), not candidate values."""
    model = tradefloor.ModelParams.from_preset(
        vix_selfex_gain=1.5, vix_selfex_threshold=0.0)
    parent = tradefloor.Engine(seed=11, universe=UNIVERSE, model=model)
    parent.run_days(15, record=False)

    snap = parent.state_snapshot()
    assert snap["economy"]["vix_selfex_fear"] > 0.0, \
        "no event fired in 15 days at a fire-on-any-down-day dose; the " \
        "state test needs a live episode"

    restored = tradefloor.Engine(seed=11, universe=UNIVERSE, model=model)
    restored.restore_state(snap)
    assert continue_days(restored, 10) == continue_days(parent, 10), \
        "a restored snapshot diverged mid-episode: fear/excitation state " \
        "is not carried"


def test_the_har_anchor_state_survives_a_snapshot():
    model = tradefloor.ModelParams.from_preset(vix_har_weight=0.6)
    parent = tradefloor.Engine(seed=7, universe=UNIVERSE, model=model)
    parent.run_days(10, record=False)

    snap = parent.state_snapshot()
    assert snap["economy"]["vix_har_rv_week"] > 0.0, \
        "the anchor ran ten days with the weight on and accumulated nothing"

    restored = tradefloor.Engine(seed=7, universe=UNIVERSE, model=model)
    restored.restore_state(snap)
    assert continue_days(restored, 10) == continue_days(parent, 10), \
        "a restored snapshot diverged: the HAR EMAs are not carried"


def test_the_up_comp_wire_moves_the_market_and_not_the_schedule():
    base = tradefloor.Engine(seed=5, universe=UNIVERSE)
    base.run_days(3, record=False)
    wired = tradefloor.Engine(
        seed=5, universe=UNIVERSE,
        model=tradefloor.ModelParams.from_preset(market_beta_up_comp=0.05))
    wired.run_days(3, record=False)

    bp, bd = prices_and_draws(base)
    wp, wd = prices_and_draws(wired)
    assert wd == bd, "a pure transform of an existing draw consumed draws"
    assert wp != bp, "the up-tick compensation is not wired through"
