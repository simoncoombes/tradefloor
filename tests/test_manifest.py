"""The run manifest: one artifact a stranger reproduces a run from.

Three properties, each with a test that fails if it is lost: the manifest
reproduces (a loaded manifest replays to a bit-identical market), it verifies
itself (the reader is told they got the same market, rather than eyeballing
numbers), and it refuses rather than misleads (a different era, an edited
component, or a missing piece fails loudly and names the culprit).
"""

import json

import pytest

import pretium
from pretium.manifest import _canonical, _sha, era_fingerprint, market_digest
from pretium.scenario import Scenario

UNIVERSE = pretium.Universe.random(10, seed=3)
MACRO_KWARGS = dict(vix=18.0, federal_funds_rate=0.03,
                    corporate_bond_yield=0.05)


def full_run(seed=42, days=3):
    """A run using every kind of input: macro, scenario pins, order flow."""
    macro = pretium.Macro(**MACRO_KWARGS)
    shock = Scenario.rate_shock(start=0.03, end=0.05, over=2)
    engine = pretium.Engine(seed=seed, universe=UNIVERSE, macro_state=macro)
    for day in range(days):
        shock.apply(engine, day)
        engine.open_market()
        flow = {UNIVERSE.tickers()[0]: (250_000.0, 0.0)} if day == 1 else None
        engine.run_session(9, 30, 3, 78, order_flow=flow)
        engine.close_market()
    return engine, macro, shock


def manifest_of(engine, macro, shock, **kwargs):
    return pretium.RunManifest.of(engine, seed=42, universe=UNIVERSE,
                                  macro=macro, scenario=shock, **kwargs)


def tampered(manifest, mutate):
    payload = json.loads(manifest.to_json())
    mutate(payload)
    return json.dumps(payload)


# -- property 1: it reproduces --------------------------------------------

def test_a_manifest_reproduces_the_run_bit_for_bit():
    # End to end: run with a macro, a scenario and order flow, write the
    # manifest, cross a JSON boundary, and rebuild the identical market from
    # nothing but the document. Identical means the continuous state too, not
    # only prices a cent grid has already rounded.
    engine, macro, shock = full_run()
    text = manifest_of(engine, macro, shock).to_json()

    rebuilt = pretium.RunManifest.from_json(text).reproduce()

    assert rebuilt.prices() == engine.prices()
    assert rebuilt.draws_consumed == engine.draws_consumed
    assert rebuilt.column("mispricing_s") == engine.column("mispricing_s")
    assert rebuilt.column("garch_variance") == engine.column("garch_variance")


def test_a_minimal_run_needs_only_seed_universe_and_log():
    engine = pretium.Engine(seed=7, universe=UNIVERSE)
    engine.run_days(2, record=False)
    manifest = pretium.RunManifest.of(engine, seed=7, universe=UNIVERSE)

    rebuilt = pretium.RunManifest.from_json(manifest.to_json()).reproduce()
    assert rebuilt.prices() == engine.prices()
    assert manifest.result["days"] == 2
    assert manifest.macro is None and manifest.scenario is None


def test_the_manifest_carries_every_component_as_data():
    engine, macro, shock = full_run()
    loaded = pretium.RunManifest.from_json(
        manifest_of(engine, macro, shock,
                    strategy=pretium.StrategySpec.momentum(),
                    universe_source={"constructor": "random", "n": 10,
                                     "seed": 3}).to_json())

    assert loaded.universe.fingerprint == UNIVERSE.fingerprint
    assert loaded.macro.vix == MACRO_KWARGS["vix"]
    assert loaded.scenario.to_json(3) == shock.to_json(3)
    assert loaded.strategy == pretium.StrategySpec.momentum()
    assert loaded.universe_source == {"constructor": "random", "n": 10,
                                      "seed": 3}
    assert loaded.result["days"] == 3


# -- property 2: it verifies itself ---------------------------------------

def test_the_reader_is_told_when_the_market_is_not_the_same():
    # The digest is the point: reproduction is self-checking, not a page of
    # numbers to eyeball. A recorded result the replay does not rebuild must
    # fail loudly.
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock), lambda p: p["result"].
                    __setitem__("digest", "0" * 64))
    with pytest.raises(pretium.ValidationError,
                       match="did not rebuild the recorded market"):
        pretium.RunManifest.from_json(text).reproduce()


def test_the_market_digest_covers_state_not_only_prints():
    # Two different seeds must digest apart, and the digest must include the
    # continuous columns: a digest a divergence could hide inside would give
    # confidence it had not earned.
    a, macro, shock = full_run(seed=42)
    b, _, _ = full_run(seed=43)
    assert market_digest(a) != market_digest(b)
    assert len(market_digest(a)) == 64


def test_each_component_mismatch_is_named_not_generic():
    engine, macro, shock = full_run()
    manifest = manifest_of(engine, macro, shock,
                           strategy=pretium.StrategySpec.momentum())

    cases = [
        (lambda p: p["universe"]["instruments"][0].__setitem__("eps", 99.0),
         "universe"),
        (lambda p: p["macro"].__setitem__("vix", 55.0), "macro"),
        (lambda p: p["scenario"]["path"][1].__setitem__(
            "federal_funds_rate", 0.049), "scenario"),
        (lambda p: p["order_log"].pop(0), "order log"),
        (lambda p: p.__setitem__("seed", 43), "seed was edited"),
        (lambda p: p["strategy"]["spec"]["portfolio"].__setitem__(
            "top_k", 4), "strategy spec"),
    ]
    for mutate, culprit in cases:
        with pytest.raises(pretium.ValidationError, match=culprit):
            pretium.RunManifest.from_json(tampered(manifest, mutate))


def test_a_fingerprint_without_its_spec_is_refused():
    # A fingerprint identifies; it cannot reconstruct. A manifest whose
    # carried spec went missing must say that, not verify the hole.
    engine, macro, shock = full_run()
    manifest = manifest_of(engine, macro, shock,
                           strategy=pretium.StrategySpec.momentum())
    text = tampered(manifest, lambda p: p.__setitem__(
        "strategy", {"reference": "lost"}))
    with pytest.raises(pretium.ValidationError, match="cannot reconstruct"):
        pretium.RunManifest.from_json(text)


# -- property 3: it refuses rather than misleads ---------------------------

def test_a_different_era_is_refused_before_anything_replays():
    # The live hazard: the 2026-08 era boundary moved every trajectory while
    # pt.version() and the preset name held still. A manifest from the other
    # side of such a boundary must refuse, because replaying it would produce
    # a plausible market that is not the recorded one — worse than no
    # manifest at all.
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock),
                    lambda p: p["written_by"]["era"].__setitem__(
                        "digest", "0" * 64))
    with pytest.raises(pretium.ValidationError,
                       match="does not reproduce the manifest's era"):
        pretium.RunManifest.from_json(text).reproduce()


def test_a_moved_coefficient_is_named_by_key():
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock),
                    lambda p: p["written_by"]["model"].__setitem__(
                        "momentum_theta", 0.30))
    with pytest.raises(pretium.ValidationError, match="momentum_theta"):
        pretium.RunManifest.from_json(text).reproduce()


def test_a_different_preset_name_is_refused():
    # Renaming the model dict is now caught one gate earlier than it was:
    # the manifest records the model fingerprint beside the strategy's, so
    # a name that no longer matches it is an in-transit edit before it can
    # ever become an era question.
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock),
                    lambda p: p["written_by"]["model"].__setitem__(
                        "name", "custom-a41f9c02"))
    with pytest.raises(pretium.ValidationError,
                       match="model dictionary|model preset"):
        pretium.RunManifest.from_json(text).reproduce()


def test_a_run_under_a_shipped_non_default_preset_reproduces_under_it():
    """A second preset in the table must reproduce as itself, not as the
    default.

    The path this guards is the one that opened the moment `pt-v2` joined
    `preset_names()`: the manifest records a NAME rather than a `custom-`
    dictionary, so a replay that reads "not custom, therefore the engine's
    default" runs a different model and reports success — the substitution
    the model fingerprint exists to prevent, reached through a shortcut
    that was correct only while the table had one row. Both halves are
    asserted, because either alone would pass under the bug: the era gate
    must not refuse the run, and the market must come back bit-identical.
    """
    macro = pretium.Macro(**MACRO_KWARGS)
    shock = Scenario.rate_shock(start=0.03, end=0.05, over=2)
    engine = pretium.Engine(seed=42, universe=UNIVERSE, macro_state=macro,
                            model="pt-v2")
    for day in range(3):
        shock.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, 78)
        engine.close_market()
    assert engine.model_fingerprint == "pt-v2"

    text = manifest_of(engine, macro, shock).to_json()
    assert json.loads(text)["written_by"]["model"]["name"] == "pt-v2"

    rebuilt = pretium.RunManifest.from_json(text).reproduce()
    assert rebuilt.model_fingerprint == "pt-v2"
    assert rebuilt.prices() == engine.prices()


def test_a_legacy_manifest_naming_a_different_preset_is_refused():
    # A manifest written BEFORE the model fingerprint joined `fingerprints`
    # carries only the dict; renaming that must still refuse, at the era
    # gate, by preset name. Emulated by rebuilding the fingerprint block
    # the way an old writer would have (no "model" key).
    engine, macro, shock = full_run()

    def rewrite(p):
        p["written_by"]["model"]["name"] = "pt-v9"
        fps = p["fingerprints"]
        fps.pop("model", None)
        check = {k: v for k, v in fps.items() if k != "inputs"}
        fps["inputs"] = _sha(_canonical({"seed": p["seed"], **check}))

    text = tampered(manifest_of(engine, macro, shock), rewrite)
    with pytest.raises(pretium.ValidationError, match="model preset"):
        pretium.RunManifest.from_json(text).reproduce()


def test_an_unknown_probe_version_refuses_to_conclude():
    # A future probe's digests are a different measurement, not a mismatch.
    # Comparing them anyway could refuse a run that reproduces, or worse.
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock),
                    lambda p: p["written_by"]["era"].__setitem__("probe", 99))
    with pytest.raises(pretium.ValidationError, match="cannot be compared"):
        pretium.RunManifest.from_json(text).reproduce()


def test_a_newer_manifest_schema_is_refused():
    engine, macro, shock = full_run()
    text = tampered(manifest_of(engine, macro, shock),
                    lambda p: p.__setitem__("schema", 99))
    with pytest.raises(pretium.ValidationError, match="newer"):
        pretium.RunManifest.from_json(text)


def test_the_era_fingerprint_is_stable_within_a_build():
    a, b = era_fingerprint(), era_fingerprint()
    assert a == b
    assert len(a) == 64 and int(a, 16) is not None


# -- the completeness rule -------------------------------------------------

def test_a_hand_written_agent_is_honestly_incomplete():
    # The escape hatch working as designed: no fingerprint, a recorded
    # reference, and a manifest that SAYS it is incomplete. The market still
    # replays in full, because the agent's orders are data in the log.
    engine, macro, shock = full_run()
    manifest = manifest_of(engine, macro, shock,
                           strategy="github.com/example/strat at 58837b3")
    loaded = pretium.RunManifest.from_json(manifest.to_json())

    assert not loaded.complete
    assert any("58837b3" in gap for gap in loaded.gaps)
    assert "INCOMPLETE" in repr(loaded)
    assert "REFERENCED" in loaded.describe()
    assert loaded.strategy is None
    assert loaded.fingerprints["strategy"] is None
    assert loaded.reproduce().prices() == engine.prices()


def test_a_carried_spec_makes_the_manifest_complete():
    engine, macro, shock = full_run()
    spec = pretium.StrategySpec.momentum(top_k=3)
    loaded = pretium.RunManifest.from_json(
        manifest_of(engine, macro, shock, strategy=spec).to_json())
    assert loaded.complete and loaded.gaps == []
    assert loaded.fingerprints["strategy"] == spec.fingerprint
    assert loaded.strategy == spec


def test_an_agent_object_is_refused_with_directions():
    # Accepting an object would embed a repr while implying it embedded a
    # strategy. The refusal tells the author what to pass instead.
    engine, macro, shock = full_run()
    with pytest.raises(pretium.ValidationError, match="repo and\\s+commit"):
        manifest_of(engine, macro, shock,
                    strategy=pretium.baselines.Momentum())
    with pytest.raises(pretium.ValidationError, match="nothing"):
        manifest_of(engine, macro, shock, strategy="   ")


def test_universe_source_must_travel_as_data():
    engine, macro, shock = full_run()
    with pytest.raises(pretium.ValidationError, match="JSON-serialisable"):
        manifest_of(engine, macro, shock, universe_source=object())


# -- the scenario round trip, the gap this work closed ---------------------

def test_a_scenario_round_trips_byte_identically():
    shock = Scenario.rate_shock(start=0.03, end=0.05, over=2)
    restored = Scenario.from_json(shock.to_json(3))
    assert restored.to_json(3) == shock.to_json(3)
    assert restored.fields == shock.fields


def test_a_restored_scenario_replays_the_same_market():
    # The property that matters: the restored path drives an identical run,
    # so a manifest's scenario is the scenario, not a description of one.
    shock = Scenario.vix_shock(calm=15.0, peak=45.0, at=1, over=1)
    restored = Scenario.from_json(shock.to_json(3))
    kwargs = dict(seed=11, universe=UNIVERSE, days=3, ticks_per_day=78)
    assert (pretium.run_scenario(restored, **kwargs).prices()
            == pretium.run_scenario(shock, **kwargs).prices())


def test_a_restored_scenario_holds_its_final_values_beyond_the_horizon():
    # The same rule ramp applies after its end: defined on every day of any
    # run length, rather than an IndexError forty days into a longer study.
    shock = Scenario.rate_shock(start=0.03, end=0.05, over=2)
    restored = Scenario.from_json(shock.to_json(3))
    assert restored.at(50) == shock.at(2)


def test_an_inconsistent_scenario_document_is_refused():
    shock = Scenario.rate_shock(start=0.03, end=0.05, over=2)
    payload = json.loads(shock.to_json(3))

    for mutate, why in [
        (lambda p: p.__setitem__("schema", 99), "newer"),
        (lambda p: p.__setitem__("days", 7), "7 days"),
        (lambda p: p["path"].reverse(), "contiguous"),
        (lambda p: p["path"][0].__setitem__("nonsense_field", 1.0),
         "unknown macro field"),
        (lambda p: p["path"][2].pop("vix" if "vix" in p["path"][2]
                                    else "federal_funds_rate"), "fixed at"),
        (lambda p: p["path"][1].__setitem__("federal_funds_rate", 5.0),
         "FRACTIONS"),
    ]:
        broken = json.loads(json.dumps(payload))
        mutate(broken)
        with pytest.raises(pretium.ValidationError, match=why):
            Scenario.from_json(json.dumps(broken))
