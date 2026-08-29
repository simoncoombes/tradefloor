"""The strategy spec: round-trips, hashes, and versioned semantics.

Each of the three properties the spec exists to provide has tests that would
fail if it lost them. Round-trip failures make it documentation; a
fingerprint that moves with formatting makes it decoration; semantics that
drift under a held version make it a lie that looks like reproducibility.
"""

import json
import struct

import pytest

import tradefloor
from tradefloor import SPEC_VERSION, StrategySpec, ValidationError
from tradefloor.baselines import (
    BuyAndHold,
    MeanReversion,
    Momentum,
    Oracle,
    RandomTrader,
)
from tradefloor.harness import Observation
from tradefloor.portfolio import Portfolio
from tradefloor.spec import _BlendAgent

UNIVERSE = tradefloor.Universe.random(12, seed=3)


def _f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _catalogue():
    """One spec per corner of the grammar."""
    return [
        StrategySpec.hold(),
        StrategySpec.hold(gross=1.5, max_participation=0.03),
        StrategySpec.random(seed=7),
        StrategySpec.random(seed=7, cadence="daily"),
        StrategySpec.momentum(),
        StrategySpec.momentum(lookback_days=5.0, top_k=3, gross=2.0),
        StrategySpec.mean_reversion(lookback_days=2.5),
        StrategySpec.oracle(top_k=2),
        StrategySpec.oracle(cadence="daily"),
        StrategySpec.blend([
            {"kind": "momentum", "weight": 0.6, "lookback_days": 1.0},
            {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 5.0},
        ], top_k=10),
        StrategySpec.blend([
            {"kind": "oracle", "weight": 0.5},
            {"kind": "random", "weight": 0.5},
        ], seed=11),
        StrategySpec.blend([
            {"kind": "momentum", "weight": -1.0, "lookback_days": 1.0},
        ]),
    ]


# --------------------------------------------------------------------------
# Property one: it round-trips
# --------------------------------------------------------------------------


def test_json_round_trip_across_the_whole_grammar():
    for spec in _catalogue():
        rebuilt = StrategySpec.from_json(spec.to_json())
        assert rebuilt == spec, spec
        assert rebuilt.fingerprint == spec.fingerprint


def test_agent_round_trip_returns_the_original_spec():
    # The second half of the round-trip: an agent built from a spec must be
    # able to say which spec built it, or a result produced with the agent
    # cannot cite the strategy that earned it.
    for spec in _catalogue():
        assert spec.build().spec is spec


def test_round_trip_survives_compact_and_pretty_forms():
    spec = StrategySpec.momentum(lookback_days=2.0)
    assert StrategySpec.from_json(spec.to_json(indent=None)) == spec
    assert StrategySpec.from_json(spec.to_json(indent=8)) == spec


def test_equality_is_content_not_identity():
    assert StrategySpec.momentum() == StrategySpec.momentum()
    assert StrategySpec.momentum() != StrategySpec.mean_reversion()
    assert StrategySpec.momentum() != "momentum"
    # Hashable, so specs can key result tables directly.
    assert len({StrategySpec.momentum(), StrategySpec.momentum()}) == 1


def test_specs_are_immutable():
    spec = StrategySpec.momentum()
    with pytest.raises(AttributeError):
        spec.seed = 4
    # And the accessors hand back copies, so mutating one changes nothing.
    spec.signal["lookback_days"] = 99.0
    spec.portfolio["top_k"] = 99
    assert spec == StrategySpec.momentum()


# --------------------------------------------------------------------------
# Property two: it hashes, over content rather than keystrokes
# --------------------------------------------------------------------------


def test_fingerprint_ignores_key_order_and_whitespace():
    spec = StrategySpec.momentum()
    scrambled = json.dumps(json.loads(spec.to_json()), indent=7,
                           sort_keys=True)
    assert StrategySpec.from_json(scrambled).fingerprint == spec.fingerprint


def test_fingerprint_materialises_defaults():
    # Writing a default explicitly and not writing it are the same strategy,
    # so they must be the same fingerprint.
    sparse = StrategySpec({"kind": "momentum"})
    explicit = StrategySpec(
        {"kind": "momentum", "lookback_days": 1.0},
        portfolio={"top_k": 5, "gross": 1.0},
        execution={"cadence": "step", "max_participation": 0.02},
    )
    assert sparse == explicit
    assert sparse.fingerprint == explicit.fingerprint
    # And the serialised form SHOWS the defaults: a reader of the JSON sees
    # every parameter the strategy ran under.
    assert '"lookback_days"' in sparse.to_json()
    assert '"max_participation"' in sparse.to_json()


def test_fingerprint_ignores_blend_weight_scale_and_order():
    reference = StrategySpec.blend([
        {"kind": "momentum", "weight": 0.6, "lookback_days": 1.0},
        {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 5.0},
    ])
    scaled_and_reordered = StrategySpec.blend([
        {"kind": "mean_reversion", "weight": 0.8, "lookback_days": 5.0},
        {"kind": "momentum", "weight": 1.2, "lookback_days": 1.0},
    ])
    assert scaled_and_reordered.fingerprint == reference.fingerprint


def test_fingerprint_merges_duplicate_components():
    # The same signal named twice is one signal with the summed weight.
    split = StrategySpec.blend([
        {"kind": "momentum", "weight": 0.3, "lookback_days": 1.0},
        {"kind": "momentum", "weight": 0.3, "lookback_days": 1.0},
        {"kind": "oracle", "weight": 0.4},
    ])
    merged = StrategySpec.blend([
        {"kind": "momentum", "weight": 0.6, "lookback_days": 1.0},
        {"kind": "oracle", "weight": 0.4},
    ])
    assert split.fingerprint == merged.fingerprint


def test_a_blend_of_one_ranked_signal_is_that_signal():
    # Rank-then-select is invariant under a positive weight on a single
    # component, so the canonical form collapses to the bare signal and the
    # two spellings share a fingerprint. A single 'random' must NOT collapse
    # (the bare signal weights by draw magnitude, the component by draw
    # order), and a negative single weight must not either.
    collapsed = StrategySpec.blend(
        [{"kind": "momentum", "weight": 2.0, "lookback_days": 1.0}])
    assert collapsed == StrategySpec.momentum()

    random_blend = StrategySpec.blend(
        [{"kind": "random", "weight": 1.0}], seed=1)
    assert random_blend.signal["kind"] == "blend"

    inverted = StrategySpec.blend(
        [{"kind": "momentum", "weight": -1.0, "lookback_days": 1.0}])
    assert inverted.signal["kind"] == "blend"
    assert inverted != StrategySpec.mean_reversion()


def test_fingerprint_moves_when_the_strategy_does():
    distinct = {
        StrategySpec.momentum().fingerprint,
        StrategySpec.momentum(lookback_days=5.0).fingerprint,
        StrategySpec.momentum(top_k=3).fingerprint,
        StrategySpec.momentum(gross=2.0).fingerprint,
        StrategySpec.momentum(max_participation=0.05).fingerprint,
        StrategySpec.momentum(cadence="daily").fingerprint,
        StrategySpec.mean_reversion().fingerprint,
        StrategySpec.oracle().fingerprint,
        StrategySpec.random(seed=0).fingerprint,
        StrategySpec.random(seed=1).fingerprint,
        StrategySpec.hold().fingerprint,
    }
    assert len(distinct) == 11


def test_the_reference_fingerprints_are_pinned():
    # Hard-coded, deliberately. If this test fails, the canonical form -- and
    # with it the meaning of every published fingerprint -- has changed, and
    # that is a spec_version bump, not a patch: bump SPEC_VERSION, record why
    # in the docs page, and re-pin. See the module docstring of
    # pretium/spec.py for what counts as a semantic change.
    assert StrategySpec.momentum().fingerprint == (
        "e6bbc35c6f0968b1f178e1f7ee926d449a8d3fa72440e476dd8b25c7a6a50895")
    assert StrategySpec.oracle().fingerprint == (
        "f383b9900dc8270d222fc58e695889cec0a54e235441d51b673d370068418f27")
    assert StrategySpec.blend([
        {"kind": "momentum", "weight": 0.6, "lookback_days": 1.0},
        {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 5.0},
    ], top_k=10).fingerprint == (
        "3358b7dfbe701f457f1881a29650a3b5e4d0930e021fb62321bba37a15c71270")


def test_fingerprint_covers_spec_version():
    # The version pins what the words mean, so it must be inside the hash:
    # identical JSON under a later version is a different strategy.
    import hashlib

    spec = StrategySpec.momentum()
    doc = json.loads(spec.to_json())
    assert doc["spec_version"] == SPEC_VERSION
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    assert spec.fingerprint == hashlib.sha256(
        canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Property three: versioned on semantics
# --------------------------------------------------------------------------


def test_a_newer_spec_version_is_refused_not_partially_read():
    doc = json.loads(StrategySpec.momentum().to_json())
    doc["spec_version"] = SPEC_VERSION + 1
    with pytest.raises(ValidationError, match="Upgrade pretium"):
        StrategySpec.from_json(json.dumps(doc))


def test_malformed_versions_are_refused():
    doc = json.loads(StrategySpec.momentum().to_json())
    for bad in (0, -1, None, "1", 1.0, True):
        doc["spec_version"] = bad
        with pytest.raises(ValidationError):
            StrategySpec.from_json(json.dumps(doc))


def test_unknown_fields_are_refused_everywhere():
    # An unknown field silently dropped would round-trip to a strategy nobody
    # wrote, while fingerprinting as though nothing happened.
    with pytest.raises(ValidationError, match="unknown"):
        StrategySpec.from_json(json.dumps({
            "spec_version": 1, "signal": {"kind": "oracle"},
            "stop_loss": 0.05,
        }))
    with pytest.raises(ValidationError, match="unknown"):
        StrategySpec({"kind": "momentum", "halflife": 3})
    with pytest.raises(ValidationError, match="unknown"):
        StrategySpec({"kind": "oracle"}, portfolio={"top_k": 5, "beta": 1})
    with pytest.raises(ValidationError, match="unknown"):
        StrategySpec({"kind": "oracle"}, execution={"venue": "dark"})
    with pytest.raises(ValidationError, match="unknown"):
        StrategySpec.blend([{"kind": "oracle", "weight": 1.0,
                             "lookback_days": 2.0}])


# --------------------------------------------------------------------------
# Validation: refusals happen at construction, where the mistake is visible
# --------------------------------------------------------------------------


def test_the_grammar_refuses_what_it_cannot_mean():
    with pytest.raises(ValidationError, match="unknown signal kind"):
        StrategySpec({"kind": "value"})
    # Concentration on strategies that do not rank.
    with pytest.raises(ValidationError, match="top_k"):
        StrategySpec({"kind": "hold"}, portfolio={"top_k": 5})
    with pytest.raises(ValidationError, match="top_k"):
        StrategySpec({"kind": "random"}, portfolio={"top_k": 5}, seed=0)
    # A cadence on a strategy that trades once.
    with pytest.raises(ValidationError, match="hold"):
        StrategySpec({"kind": "hold"}, execution={"cadence": "daily"})
    with pytest.raises(ValidationError, match="cadence"):
        StrategySpec({"kind": "oracle"}, execution={"cadence": "hourly"})
    # hold ranks nothing, so it cannot be blended.
    with pytest.raises(ValidationError, match="hold"):
        StrategySpec.blend([{"kind": "hold", "weight": 1.0}])
    with pytest.raises(ValidationError, match="components"):
        StrategySpec.blend([])


def test_seed_is_required_exactly_when_randomness_exists():
    with pytest.raises(ValidationError, match="seed"):
        StrategySpec({"kind": "random"})
    with pytest.raises(ValidationError, match="seed"):
        StrategySpec.blend([{"kind": "random", "weight": 0.5},
                            {"kind": "oracle", "weight": 0.5}])
    # And refused when there is nothing for it to seed: two identical
    # deterministic strategies must not fingerprint apart.
    with pytest.raises(ValidationError, match="deterministic"):
        StrategySpec({"kind": "momentum"}, seed=3)
    with pytest.raises(ValidationError, match="32 bits"):
        StrategySpec({"kind": "random"}, seed=2 ** 32)
    with pytest.raises(ValidationError):
        StrategySpec({"kind": "random"}, seed=-1)


def test_degenerate_weights_are_refused():
    with pytest.raises(ValidationError, match="weight 0"):
        StrategySpec.blend([{"kind": "oracle", "weight": 0.0}])
    with pytest.raises(ValidationError, match="cancel"):
        StrategySpec.blend([
            {"kind": "momentum", "weight": 0.5, "lookback_days": 1.0},
            {"kind": "momentum", "weight": -0.5, "lookback_days": 1.0},
        ])
    with pytest.raises(ValidationError, match="weight"):
        StrategySpec.blend([{"kind": "oracle"}])


def test_numeric_nonsense_is_refused():
    with pytest.raises(ValidationError):
        StrategySpec.momentum(lookback_days=0.0)
    with pytest.raises(ValidationError):
        StrategySpec.momentum(lookback_days=float("nan"))
    with pytest.raises(ValidationError):
        StrategySpec.momentum(top_k=0)
    with pytest.raises(ValidationError):
        StrategySpec.momentum(top_k=2.5)
    with pytest.raises(ValidationError):
        StrategySpec.momentum(gross=0.0)
    with pytest.raises(ValidationError):
        StrategySpec.momentum(max_participation=-0.02)
    with pytest.raises(ValidationError):
        StrategySpec.momentum(top_k=True)


# --------------------------------------------------------------------------
# Building: the spec names the shipped baselines, exactly
# --------------------------------------------------------------------------


def test_specs_build_the_shipped_classes():
    assert isinstance(StrategySpec.hold().build(), BuyAndHold)
    assert isinstance(StrategySpec.random(seed=0).build(), RandomTrader)
    assert isinstance(StrategySpec.momentum().build(), Momentum)
    assert isinstance(StrategySpec.mean_reversion().build(), MeanReversion)
    assert isinstance(StrategySpec.oracle().build(), Oracle)


def test_spec_built_baselines_score_identically_to_hand_built_ones():
    # The claim "this spec names that agent" is tested rather than asserted:
    # the same market, one evaluation from specs, one from the shipped
    # classes, and the scorecards must agree to the bit. If this fails, a
    # baseline's meaning has moved out from under the spec, and that is a
    # SPEC_VERSION question before it is anything else.
    specs = {
        "hold": StrategySpec.hold(),
        "random": StrategySpec.random(seed=0),
        "momentum": StrategySpec.momentum(),
        "mean_reversion": StrategySpec.mean_reversion(),
        "oracle": StrategySpec.oracle(),
    }
    classes = {
        "hold": BuyAndHold(),
        "random": RandomTrader(seed=0),
        "momentum": Momentum(lookback_days=1.0),
        "mean_reversion": MeanReversion(lookback_days=1.0),
        "oracle": Oracle(),
    }
    from_specs = tradefloor.evaluate(specs, seed=2026, universe=UNIVERSE, days=3)
    from_classes = tradefloor.evaluate(classes, seed=2026, universe=UNIVERSE,
                                    days=3)
    for name in specs:
        a, b = from_specs[name].as_dict(), from_classes[name].as_dict()
        # Everything except the strategy fingerprint, which only the spec
        # side can carry -- that asymmetry is the module's reason to exist.
        a.pop("strategy_fingerprint")
        b.pop("strategy_fingerprint")
        assert a == b, name
        assert from_specs[name].strategy_fingerprint == \
            specs[name].fingerprint
        assert from_classes[name].strategy_fingerprint == ""


def test_a_blend_of_one_trades_exactly_like_the_baseline():
    # The canonical collapse of a one-component blend rests on this: the
    # grammar's own rank machinery must reproduce the shipped agent trade
    # for trade, ties included. Construct the internal agent directly,
    # because the collapse means no spec can build it.
    blend = _BlendAgent(
        [{"kind": "momentum", "weight": 1.0, "lookback_days": 1.0}],
        top_k=5, gross=1.0, max_participation=0.02, seed=None)
    shipped = Momentum(lookback_days=1.0)

    a = tradefloor.evaluate({"agent": blend}, seed=2026, universe=UNIVERSE,
                         days=3)["agent"]
    b = tradefloor.evaluate({"agent": shipped}, seed=2026, universe=UNIVERSE,
                         days=3)["agent"]
    assert a.pnl == b.pnl
    assert a.trades == b.trades
    assert a.turnover == b.turnover


def test_a_real_blend_runs_and_is_its_own_strategy():
    blend = StrategySpec.blend([
        {"kind": "momentum", "weight": 0.6, "lookback_days": 1.0},
        {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 2.0},
    ])
    scores = tradefloor.evaluate(
        {"blend": blend,
         "momentum": StrategySpec.momentum(),
         "mean_reversion": StrategySpec.mean_reversion(lookback_days=2.0)},
        seed=2026, universe=UNIVERSE, days=3)
    card = scores["blend"]
    assert card.errors == []
    assert card.trades > 0
    # A mixture is not either ingredient.
    assert card.pnl != scores["momentum"].pnl
    assert card.pnl != scores["mean_reversion"].pnl


def test_blends_declare_privilege_when_they_borrow_the_oracle():
    # A spec naming the oracle declares access no real trader has. The flag
    # must survive blending and the cadence wrapper, or a results table
    # would present a privileged mixture as a peer.
    assert StrategySpec.oracle().build().privileged is True
    assert StrategySpec.oracle(cadence="daily").build().privileged is True
    with_oracle = StrategySpec.blend([
        {"kind": "oracle", "weight": 0.5},
        {"kind": "momentum", "weight": 0.5, "lookback_days": 1.0},
    ])
    assert with_oracle.build().privileged is True
    without = StrategySpec.blend([
        {"kind": "momentum", "weight": 0.5, "lookback_days": 1.0},
        {"kind": "mean_reversion", "weight": 0.5, "lookback_days": 1.0},
    ])
    assert without.build().privileged is False


def test_the_cadence_wrapper_forwards_explain():
    agent = StrategySpec.oracle(cadence="daily").build()
    assert callable(getattr(agent, "explain", None))


# --------------------------------------------------------------------------
# Cadence: the strategy's decision rule, not the harness's step count
# --------------------------------------------------------------------------


def test_daily_cadence_decides_only_at_the_open():
    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
    portfolio = Portfolio(cash=1_000_000.0, max_leverage=2.0)
    adv = [inst.avg_volume for inst in UNIVERSE]
    agent = StrategySpec.momentum(cadence="daily").build()

    steps_per_day = 6
    acted_on = []
    step = 0
    for day in range(3):
        for step_in_day in range(steps_per_day):
            obs = Observation(step, day, engine.tickers,
                              _f64(engine.prices()), portfolio, engine, adv,
                              steps_per_day)
            if agent.act(obs):
                acted_on.append((day, step_in_day))
            step += 1

    # Never off the open. Day zero is warm-up (a one-day lookback needs one
    # prior daily observation), so the first trade is day one's open.
    assert acted_on == [(1, 0), (2, 0)]


def test_daily_cadence_resolves_lookback_in_days_not_steps():
    # Under daily cadence a one-day lookback is one daily observation,
    # whatever steps_per_day the harness runs. If the wrapper leaked the
    # harness's step count into the wrapped agent, a lookback of one day
    # would become six daily observations and the strategy would quietly
    # mean something else.
    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
    portfolio = Portfolio(cash=1_000_000.0, max_leverage=2.0)
    adv = [inst.avg_volume for inst in UNIVERSE]
    agent = StrategySpec.momentum(cadence="daily", lookback_days=1.0).build()
    inner = agent._inner

    obs = Observation(0, 0, engine.tickers, _f64(engine.prices()), portfolio,
                      engine, adv, 6)
    agent.act(obs)
    assert inner.lookback == 1


def test_step_cadence_is_the_default_and_matches_the_shipped_agents():
    assert StrategySpec.momentum().execution["cadence"] == "step"
    # Built without a wrapper: the shipped class IS the step-cadence agent.
    assert isinstance(StrategySpec.momentum().build(), Momentum)


# --------------------------------------------------------------------------
# evaluate() accepts specs, and the scorecard carries the fingerprint
# --------------------------------------------------------------------------


def test_evaluate_accepts_specs_and_stamps_the_fingerprint():
    spec = StrategySpec.oracle(top_k=2)
    scores = tradefloor.evaluate({"oracle": spec}, seed=2026, universe=UNIVERSE,
                              days=2)
    card = scores["oracle"]
    assert card.strategy_fingerprint == spec.fingerprint
    assert card.universe_fingerprint == UNIVERSE.fingerprint
    assert "strategy_fingerprint" in card.as_dict()


def test_a_hand_written_agent_carries_no_fingerprint():
    # Honesty over convenience: a Python callable is the escape hatch, and
    # its results are citable only as code at a commit. An empty field says
    # so; inventing one would claim a reproducibility that does not exist.
    class Custom:
        def act(self, obs):
            return {}

    scores = tradefloor.evaluate({"custom": Custom()}, seed=2026,
                              universe=UNIVERSE, days=1)
    assert scores["custom"].strategy_fingerprint == ""


def test_a_built_agent_still_carries_its_spec_through_evaluate():
    spec = StrategySpec.momentum()
    scores = tradefloor.evaluate({"m": spec.build()}, seed=2026,
                              universe=UNIVERSE, days=2)
    assert scores["m"].strategy_fingerprint == spec.fingerprint


def test_specs_are_safe_to_reuse_where_agents_are_not():
    # Agents are stateful: BuyAndHold trades once EVER, so a reused instance
    # does nothing in its second evaluation. A spec builds fresh inside every
    # call, so reuse is safe -- this is the trap that made rank() refuse
    # plain mappings, closed at the source.
    spec = StrategySpec.hold()
    first = tradefloor.evaluate({"hold": spec}, seed=2026, universe=UNIVERSE,
                             days=2)
    second = tradefloor.evaluate({"hold": spec}, seed=2026, universe=UNIVERSE,
                              days=2)
    assert first["hold"].as_dict() == second["hold"].as_dict()
    assert first["hold"].trades > 0

    instance = BuyAndHold()
    tradefloor.evaluate({"hold": instance}, seed=2026, universe=UNIVERSE, days=2)
    again = tradefloor.evaluate({"hold": instance}, seed=2026, universe=UNIVERSE,
                             days=2)
    assert again["hold"].trades == 0  # the instance already spent itself


def test_rank_accepts_a_factory_of_specs():
    ranking = tradefloor.rank(
        lambda: {"momentum": StrategySpec.momentum(),
                 "oracle": StrategySpec.oracle()},
        seeds=range(2), universe=UNIVERSE, days=2)
    assert set(ranking.records) == {"momentum"}
