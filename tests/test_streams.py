"""The RNG stream split, exercised through the public API.

The engine derives three independent substreams from one root seed —
market, economy, external — so that varying what one domain consumes
cannot shift any other domain's sequence. The derivation contract lives in
``docs/rng-streams.md`` and is pinned by the Rust suite; these tests assert
what the three consumers who asked for the split actually get:

- the TCA counterfactual: order flow consumes no market draws, so on a
  one-day analysis untouched names stay bit-identical (asserted in
  ``test_tca.py``; the guard-flip mechanics are demonstrated in
  ``rust/tests/stream_alignment.rs``). Over more days the draws are still
  identical but the 2026-08 VIX coupling opens a non-noise channel —
  trading moves the same-day VIX, VIX sets the factor's variance target
  two closes later — so the byte-identical guarantee is qualified: small
  next to direct impact, and restored exactly under a pinned VIX. The
  measurement lives in ``pretium.tca``'s ``moved()`` docstring,
- pinned-versus-baseline macro comparison: two macro paths, one market
  noise sequence,
- cutover: an embedder's own draws never move the market.
"""

import struct

import pytest

import pretium
from pretium.scenario import Scenario, run_scenario


UNIVERSE = pretium.Universe.random(12, seed=7)


def prices(engine):
    return list(struct.unpack("<%dd" % len(engine), engine.prices()))


def market_stream_state(engine):
    """The market stream's (state, increment, spare) from a snapshot.

    As raw bytes, because the spare slot is NaN when no Box-Muller spare is
    cached and NaN compares unequal to itself as a float.
    """
    return [struct.pack("<d", v) for v in engine.state_snapshot()["rng"][0:3]]


# --------------------------------------------------------------------------
# Pinned-versus-baseline macro: the market noise is shared
# --------------------------------------------------------------------------


def test_two_macro_scenarios_consume_identical_market_noise():
    """The guarantee `scenario.compare` could previously only measure.

    Before the split, a macro path that changed a price could flip a
    settlement guard and shift the whole shared stream -- `scenario.py`
    documented the mechanism, reported `draw_delta`, and called the result
    "a very good approximation" when it was non-zero. Now the market
    stream's schedule cannot depend on the trajectory, so two runs under
    DIFFERENT macro paths still consume -- and therefore see -- the same
    market noise sequence, draw for draw. The difference between the two
    worlds is the scenario, exactly.
    """
    shocked = run_scenario(Scenario.vix_shock(calm=15.0, peak=60.0, over=10),
                           seed=2026, universe=UNIVERSE, days=15)
    flat = run_scenario(Scenario(label="flat").hold(vix=15.0),
                        seed=2026, universe=UNIVERSE, days=15)

    # The scenario really moved the market...
    assert prices(shocked) != prices(flat)
    # ...but the market stream never noticed: same position, same count.
    assert market_stream_state(shocked) == market_stream_state(flat)
    assert shocked.draws_by_stream()["market"] == flat.draws_by_stream()["market"]


def test_a_pinned_run_matches_its_endogenous_twin_when_the_values_agree():
    """Pinning the values the chain would have produced is a no-op.

    The sharpest form of the alignment: world A evolves its macro chain,
    world B never runs a chain at all -- it pins each day to the values A
    happened to produce. Before the split, B's unconsumed macro draws
    shifted every market draw and the two worlds diverged everywhere. Now
    they are the same market to the bit, which is what makes a pinned
    HISTORICAL series comparable against an endogenous baseline: the
    difference is the macro values, never the plumbing.
    """
    days = 3
    a = pretium.Engine(seed=99, universe=UNIVERSE)
    pins = []
    for _ in range(days):
        a.open_market()
        a.run_session(9, 30, 3, 60)
        a.close_market()  # advances the macro chain into the next day
        m = a.macro_state
        pins.append(dict(vix=m.vix, federal_funds_rate=m.federal_funds_rate,
                         corporate_bond_yield=m.corporate_bond_yield,
                         inflation_rate=m.inflation_rate,
                         qe_pe_boost=m.qe_pe_boost,
                         fear_greed_index=m.fear_greed_index))

    b = pretium.Engine(seed=99, universe=UNIVERSE)
    for day in range(days):
        b.open_market()
        b.run_session(9, 30, 3, 60)
        # Overwrite whatever the close's own chain did with the recorded
        # values, the way a replayed historical series would.
        b.close_market()
        b.pin_macro(**pins[day])

    assert prices(a) == prices(b)
    assert market_stream_state(a) == market_stream_state(b)


# --------------------------------------------------------------------------
# Cutover: embedder draws are isolated and reproducible
# --------------------------------------------------------------------------


def test_embedder_draws_leave_the_market_bit_identical():
    def run(extra_draws):
        e = pretium.Engine(seed=42, universe=UNIVERSE)
        e.open_market()
        for _ in range(extra_draws):
            e.draw_uniform()
            e.draw_normal()
        e.run_session(9, 30, 3, 90)
        return e

    quiet, noisy = run(0), run(500)
    assert prices(quiet) == prices(noisy)
    assert market_stream_state(quiet) == market_stream_state(noisy)
    # And the isolation is visible in the accounting, not inferred.
    assert noisy.draws_by_stream()["external"] == 1000
    assert quiet.draws_by_stream()["external"] == 0


def test_embedder_draws_are_reproducible_from_the_seed():
    a = pretium.Engine(seed=5, universe=UNIVERSE)
    b = pretium.Engine(seed=5, universe=UNIVERSE)
    assert [a.draw_normal() for _ in range(8)] == [b.draw_normal() for _ in range(8)]


# --------------------------------------------------------------------------
# Accounting and checkpointing across the three streams
# --------------------------------------------------------------------------


def test_the_per_stream_counts_sum_to_the_total():
    e = pretium.Engine(seed=11, universe=UNIVERSE)
    e.draw_uniform()
    e.open_market()
    e.run_session(9, 30, 3, 30)
    e.close_market()
    by_stream = e.draws_by_stream()
    assert set(by_stream) == {"market", "economy", "external"}
    assert sum(by_stream.values()) == e.draws_consumed
    # Every stream did real work in that sequence.
    assert by_stream["market"] > 0
    assert by_stream["economy"] > 0
    assert by_stream["external"] == 1


def test_a_snapshot_carries_all_three_streams():
    # An odd number of normals per stream, so each has a Box-Muller spare in
    # flight -- the piece of position a lazy snapshot drops first.
    e = pretium.Engine(seed=3, universe=UNIVERSE)
    e.draw_normal()
    e.open_market()
    e.run_session(9, 30, 3, 17)
    e.close_market()
    snapshot = e.state_snapshot()
    assert len(snapshot["rng"]) == 9

    restored = pretium.Engine(seed=3, universe=UNIVERSE)
    restored.restore_state(snapshot)
    # Identical continuations on every stream: the market via prices, the
    # economy via the next day's chain, the external via the next draw.
    assert restored.draw_uniform() == e.draw_uniform()
    e.open_market()
    restored.open_market()
    e.run_session(10, 30, 3, 30)
    restored.run_session(10, 30, 3, 30)
    assert prices(restored) == prices(e)
    e.close_market()
    restored.close_market()
    assert restored.macro_state.vix == e.macro_state.vix


def test_a_pre_split_snapshot_is_refused_with_its_era_named():
    e = pretium.Engine(seed=1, universe=UNIVERSE)
    snapshot = e.state_snapshot()
    snapshot["rng"] = snapshot["rng"][0:3]  # the old single-stream format
    fresh = pretium.Engine(seed=1, universe=UNIVERSE)
    with pytest.raises(pretium.ValidationError, match="stream split"):
        fresh.restore_state(snapshot)
