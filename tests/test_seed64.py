"""Seeds are 64-bit from 0.8.5.

Until 0.8.5 every seed was a `u32`, and anything from 2**32 up was refused
with pyo3's bare "out of range integral type conversion attempted". A hidden
seed drawn from 2**32 values can be found by simulating every one against a
market's first prices, which is what a sealed evaluation cannot afford. The
seed is now a `u64` on every surface, and the derivation keeps every seed
below 2**32 on the market it always gave: the known-answer digests in
`test_known_answer.py` are the proof of that half, and the high-seed row
there checks the other half on every wheel target.

This file covers what those digests cannot: the boundaries, the refusals,
that the high bits make different markets, and that a high seed survives
every path a seed travels (fork, replay, checkpoint, manifest, surgery).
"""

import json

import pytest

import tradefloor as tf
from tradefloor import _core

TOP = 2**64 - 1
HIGH = 2**63 + 12345
BOUNDARY = (2**32 - 1, 2**32, TOP)
REFUSED = (-1, 2**64, 2**70, 3.0, 3.5, "7", True, None, [1])

UNIVERSE = tf.Universe.random(4, seed=11)


def prices_after(seed, universe=UNIVERSE, ticks=20):
    engine = tf.Engine(seed=seed, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, ticks)
    engine.close_market()
    return engine.prices()


# -- the conversion at the edges ---------------------------------------------

@pytest.mark.parametrize("seed", (0, 1, *BOUNDARY, HIGH))
def test_a_python_int_reaches_the_engine_whole(seed):
    assert _core.check_seed(seed) == seed
    assert type(_core.check_seed(seed)) is int
    tf.GameRng(seed, 0).next_float()
    tf.Universe.random(3, seed=seed)
    assert len(prices_after(seed)) == 8 * len(UNIVERSE)


def test_an_integer_that_is_not_an_int_is_still_a_seed():
    np = pytest.importorskip("numpy")
    assert _core.check_seed(np.uint64(TOP)) == TOP
    assert _core.check_seed(np.int64(5)) == 5
    assert prices_after(np.uint64(HIGH)) == prices_after(HIGH)


@pytest.mark.parametrize("bad", REFUSED)
def test_what_is_not_a_seed_is_refused_naming_the_range(bad):
    with pytest.raises(tf.ValidationError, match=r"0 to 2\*\*64 - 1"):
        _core.check_seed(bad)
    with pytest.raises(tf.ValidationError, match=r"0 to 2\*\*64 - 1"):
        tf.Engine(seed=bad, universe=UNIVERSE)
    with pytest.raises(tf.ValidationError, match=r"0 to 2\*\*64 - 1"):
        tf.Universe.random(3, seed=bad)
    with pytest.raises(tf.ValidationError, match=r"0 to 2\*\*64 - 1"):
        tf.GameRng(bad, 0)


def test_the_refusal_names_the_argument_and_the_value():
    with pytest.raises(tf.ValidationError, match=r"^seed must .* got -1$"):
        tf.Engine(seed=-1, universe=UNIVERSE)
    with pytest.raises(tf.ValidationError, match=r"got 3\.5, a float"):
        tf.Engine(seed=3.5, universe=UNIVERSE)
    with pytest.raises(tf.ValidationError, match=r"got True, a bool"):
        tf.Engine(seed=True, universe=UNIVERSE)
    with pytest.raises(tf.ValidationError, match=r"^universe_seed must"):
        _core.fixed_simulation_digest(size=4, universe_seed=2**64, seed=1,
                                      days=1, ticks=5, preset="pt-v19")
    with pytest.raises(tf.ValidationError, match=r"^surgery_seed must"):
        tf.Engine.surgery_draws(1, "news", -1, ["uniform"])
    with pytest.raises(tf.ValidationError, match=r"^s_seed must"):
        tf.edgar.to_instruments(None, s_seed=-1)


def test_no_overflow_error_escapes():
    """The message 0.8.4 gave, and the exception type it came in."""
    for bad in (2**32 + 2**64, -(2**40)):
        try:
            tf.Engine(seed=bad, universe=UNIVERSE)
        except tf.ValidationError as exc:
            assert "out of range integral" not in str(exc)
        else:  # pragma: no cover
            pytest.fail("accepted a seed outside the range")


# -- the boundaries ------------------------------------------------------------

def test_the_first_wide_seed_is_not_the_first_narrow_one():
    """2**32 with its high bit dropped would be 0. A truncating build passes
    every test that uses small seeds and fails this one."""
    assert prices_after(2**32) != prices_after(0)
    assert prices_after(2**32 + 7) != prices_after(7)
    assert prices_after(TOP) != prices_after(2**32 - 1)
    u = lambda s: [i.initial_price for i in tf.Universe.random(6, seed=s)]
    assert u(2**32) != u(0)
    assert u(TOP) != u(2**32 - 1)


def test_seeds_differing_only_in_the_high_bits_are_different_markets():
    low = 20260820
    markets = [prices_after(low | (high << 32))
               for high in (0, 1, 2, 0x8000_0000, 0xFFFF_FFFF)]
    for i, a in enumerate(markets):
        for b in markets[i + 1:]:
            assert a != b


def test_no_two_seeds_on_a_sample_give_one_market():
    """A bijection by construction (see rng.rs); this is the tripwire.
    Half the sample differs only in the high bits, half only in the low."""
    seeds = ([5 | (h << 32) for h in range(1, 101)]
             + [(HIGH & ~0xFFFF_FFFF) | lo for lo in range(100)]
             + [TOP - k for k in range(50)] + list(range(50)))
    digests = {_core.fixed_simulation_digest(size=3, universe_seed=1, seed=s,
                                             days=1, ticks=4, preset="pt-v19")
               for s in seeds}
    assert len(digests) == len(set(seeds))
    rosters = {tuple(i.initial_price for i in tf.Universe.random(3, seed=s))
               for s in seeds}
    assert len(rosters) == len(set(seeds))


def test_a_narrow_seed_is_the_market_it_was():
    """The pins the rest of the suite carries, restated at the boundary.

    `Universe.random(40, seed=111)` and the seed-3 wasm probe were recorded
    before seeds widened; their values are asserted elsewhere, and this
    only checks that the widening did not route a narrow seed through the
    wide derivation.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_wasm_parity import CASE, EXPECTED
    assert _core.fixed_simulation_digest(**CASE) == EXPECTED
    assert (_core.fixed_simulation_digest(**dict(CASE, seed=2**32 - 1))
            != _core.fixed_simulation_digest(**dict(CASE, seed=2**32)))


# -- every surface -------------------------------------------------------------

def test_a_batch_member_is_the_engine_on_the_same_high_seed():
    seeds = [HIGH, TOP, 3]
    batch = tf.EngineBatch(seeds=seeds, universe=UNIVERSE)
    assert batch.seeds == seeds
    batch.run_session(9, 30, 3, 20)
    width = len(UNIVERSE) * 8
    for i, seed in enumerate(seeds):
        engine = tf.Engine(seed=seed, universe=UNIVERSE)
        engine.run_session(9, 30, 3, 20)
        assert batch.prices()[i * width:(i + 1) * width] == engine.prices()
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.EngineBatch(seeds=[1, -1], universe=UNIVERSE)


def test_evaluate_runs_on_a_high_seed_and_refuses_a_bad_one():
    agents = tf.baselines.reference_agents(seed=TOP)
    scores = tf.evaluate(agents, seed=HIGH, universe=UNIVERSE, days=1,
                         steps_per_day=2, ticks_per_step=10)
    assert set(scores) == set(agents)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.evaluate(tf.baselines.reference_agents(), seed=2**64,
                    universe=UNIVERSE, days=1)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.baselines.reference_agents(seed=-1)


def test_the_random_baseline_and_its_spec_take_a_high_seed():
    a = tf.baselines.RandomTrader(seed=HIGH)
    b = tf.baselines.RandomTrader(seed=HIGH & 0xFFFF_FFFF)
    assert a.rng.next_float() != b.rng.next_float()
    spec = tf.StrategySpec.random(seed=TOP)
    assert spec.seed == TOP
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.StrategySpec.random(seed=2**64)


def test_run_many_sweep_and_rank_take_high_seeds():
    results = tf.run_many([HIGH, TOP], universe=UNIVERSE, days=1, ticks=10,
                          workers=1)
    assert len(results) == 2
    assert [s for s, _ in tf.sweep([HIGH], universe=UNIVERSE, days=1,
                                   ticks_per_day=10)] == [HIGH]
    for call in (lambda: tf.run_many([1, -1], universe=UNIVERSE),
                 lambda: list(tf.sweep([2**64], universe=UNIVERSE)),
                 lambda: tf.rank(tf.baselines.reference_agents, seeds=[1, 2.5],
                                 universe=UNIVERSE)):
        with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
            call()


def test_the_gym_takes_a_high_seed_at_construction_and_reset():
    pytest.importorskip("numpy")
    from tradefloor.gym import TradingEnv
    env = TradingEnv(universe=UNIVERSE, seed=HIGH, days=1,
                            steps_per_day=2, ticks_per_step=10)
    _, info = env.reset()
    assert info["seed"] == HIGH
    _, info = env.reset(seed=TOP)
    assert info["seed"] == TOP
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        env.reset(seed=-1)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        TradingEnv(universe=UNIVERSE, seed=1.5)


# -- the paths a seed travels ----------------------------------------------------

def world(seed=HIGH):
    return tf.World(seed=seed, universe=list(UNIVERSE),
                    agent=tf.baselines.Momentum(), steps_per_day=2,
                    ticks_per_step=10)


def test_a_fork_of_a_high_seed_world_continues_its_market():
    root = world().run(2)
    a, b = root.fork("a", "b")
    a.run(2)
    b.run(2)
    assert a.digest() == b.digest()
    assert a.seed == HIGH
    straight = world().run(4)
    assert straight.digest() == a.digest()
    assert world(HIGH & 0xFFFF_FFFF).run(4).digest() != a.digest()


def test_a_high_seed_replays_and_round_trips_a_checkpoint():
    engine = tf.Engine(seed=TOP, universe=UNIVERSE)
    engine.run_days(3, ticks_per_day=20, record=False)
    replayed = tf.replay(engine.order_log, seed=TOP, universe=UNIVERSE)
    assert replayed.prices() == engine.prices()

    point = tf.Checkpoint.of(engine, universe=UNIVERSE, seed=TOP)
    text = point.to_json()
    assert json.loads(text)["seed"] == TOP
    restored = tf.Checkpoint.from_json(text)
    assert restored.seed == TOP
    assert restored.resume().prices() == engine.prices()
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.Checkpoint.of(engine, universe=UNIVERSE, seed=2**64)


def test_a_high_seed_manifest_round_trips_and_reproduces():
    engine = tf.Engine(seed=HIGH, universe=UNIVERSE)
    ledger = tf.DayLedger(snapshots=True)
    engine.run_days(3, ticks_per_day=20, record=False, ledger=ledger)
    manifest = tf.RunManifest.of(engine, seed=HIGH, universe=UNIVERSE,
                                 ledger=ledger)
    text = manifest.to_json()
    assert json.loads(text)["seed"] == HIGH
    loaded = tf.RunManifest.from_json(text)
    assert loaded.seed == HIGH
    assert loaded.reproduce().prices() == engine.prices()
    report = tf.manifest.verify(loaded, ledger, 2, seed=TOP)
    assert report.ok
    # The seed is inside the inputs fingerprint: its high half is not
    # editable in transit any more than its low half.
    payload = json.loads(text)
    payload["seed"] = HIGH & 0xFFFF_FFFF
    with pytest.raises(tf.ValidationError, match="seed was edited"):
        tf.RunManifest.from_json(json.dumps(payload))


def test_the_verification_sample_no_longer_masks_its_seed():
    from tradefloor.manifest import _sample_days
    assert _sample_days(50, 5, 7) == _sample_days(50, 5, 7)
    assert _sample_days(50, 5, 2**32 + 7) != _sample_days(50, 5, 7)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        _sample_days(50, 5, -1)


def test_a_surgery_takes_a_high_root_and_a_high_surgery_seed():
    kinds = ["uniform", "normal", "uniform"]
    draws = {(root, g): tuple(tf.Engine.surgery_draws(root, "news", g, kinds))
             for root in (5, 5 | 2**32, HIGH) for g in (7, 7 | 2**32, TOP)}
    assert len(set(draws.values())) == len(draws)
    root = world().run(2)
    w, again = root.fork("w", "again")
    w.window("news", (3, 3), surgery_seed=TOP)
    again.window("news", (3, 3), surgery_seed=TOP)
    w.run(1)
    again.run(1)
    assert w.digest() == again.digest()
    assert w.surgeries[-1]["surgery_seed"] == TOP
    with pytest.raises(tf.ValidationError, match=r"^surgery_seed must"):
        root.fork("x")[0].window("news", (3, 3), surgery_seed=2**64)


# -- a sealed battery ----------------------------------------------------------

def test_commit_reveal_and_the_sealed_battery_take_64_bit_seeds():
    import secrets
    cells = len(tf.battery().cells)
    seeds = [secrets.randbits(64) for _ in range(cells)]
    seeds[0] = TOP
    salt = b"salt"
    commitment = tf.commit(seeds, salt)
    assert tf.reveal(commitment, seeds, salt)
    sealed = tf.sealed_battery(seeds, salt)
    assert [c.seed for c in sealed.cells] == seeds

    assert not tf.reveal(commitment, [*seeds[:-1], 2**64], salt)
    assert not tf.reveal(commitment, [*seeds[:-1], -1], salt)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.commit([1, -1], salt)
    with pytest.raises(tf.ValidationError, match=r"2\*\*64 - 1"):
        tf.sealed_battery([*seeds[:-1], 2**64], salt)


def test_a_narrow_commitment_is_the_one_it_was():
    """The canonical form is the sorted integers, so a commitment made on
    0.8.4 to 32-bit seeds verifies here unchanged."""
    import hashlib
    seeds = [9, 3, 2**32 - 1]
    expected = hashlib.sha256(b"[3,9,4294967295]" + b"s").hexdigest()
    assert tf.commit(seeds, b"s") == expected


# -- the MCP server ------------------------------------------------------------

def test_the_mcp_tools_take_a_high_seed_and_refuse_a_bad_one():
    mcp = pytest.importorskip("tradefloor.mcp")
    momentum = {"signal": {"kind": "momentum", "lookback_days": 1.0}}
    ok = mcp.evaluate_strategies({"m": momentum}, seed=HIGH, universe_size=4,
                                 universe_seed=TOP, days=1)
    assert ok.get("ok", True), ok
    for call in (
        lambda: mcp.evaluate_strategies({"m": momentum}, seed=-1, days=1),
        lambda: mcp.evaluate_strategies({"m": momentum}, universe_seed=2**64,
                                        days=1),
        lambda: mcp.rank_strategies({"m": momentum}, seeds=[1, 2**64], days=1),
        lambda: mcp.explain_price_move(seed=2**64),
        lambda: mcp.build_universe(seed=-5),
    ):
        refused = call()
        assert refused["ok"] is False
        assert "2**64 - 1" in refused["error"]
