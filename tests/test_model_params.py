"""The runtime parameter seam: ModelParams, Engine(model=), the fingerprint.

Four properties, each with the test that fails if it is lost:

- **Bit-identity at defaults** — the phase-1 acceptance gate. An engine
  built from an explicit `ModelParams.from_preset("pt-v1")` reproduces the
  const build's market bit for bit, draw for draw. This is the known-answer
  machinery gaining a second subject (CALIBRATION.md §5.3), NOT a change to
  the KAT itself: `known_answer.py` stays untouched, its committed v8
  digest guards the const build, and this file proves the preset build is
  that build.
- **A perturbation moves the market** — otherwise the identity above would
  be evidence of a stale wheel, not of a seam. Every settable parameter is
  either shown to move the trajectory or is asserted inert for a documented
  reason under the probe's conditions.
- **The draw schedule cannot move** — `draws_consumed` is identical across
  parameter vectors per seed. That is the §5.2 membership rule made
  mechanical, and the CRN guard the calibration instrument stands on.
- **The fingerprint cannot lie** — bit-identity with the shipped preset is
  the ONLY way to be called "pt-v1"; everything else is custom-XXXXXXXX,
  immutably, and it travels: Scorecard, RunManifest, Checkpoint, fork.
"""

import json
import struct

import pytest

import pretium

UNIVERSE = pretium.Universe.random(10, seed=3)


def run_market(model=None, *, seed=42, days=3, universe=UNIVERSE):
    kwargs = {} if model is None else {"model": model}
    engine = pretium.Engine(seed=seed, universe=universe, **kwargs)
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 78)
        engine.close_market()
    return engine


def market_state(engine):
    """Every continuous column plus the draw count, as exact bits."""
    n = len(engine.tickers)
    out = {}
    for field in ("price", "previous_close", "open", "high", "low", "volume",
                  "market_cap", "mispricing_s", "garch_variance"):
        out[field] = struct.unpack("<%dd" % n, engine.column(field))
    out["draws"] = engine.draws_consumed
    return out


# -- property 1: bit-identity at defaults ----------------------------------

def test_the_preset_constructed_engine_is_the_const_build_bit_for_bit():
    """The acceptance gate. Three constructions, one market: the default,
    the preset named as a string, and an explicitly built ModelParams. The
    committed known-answer digest guards the first; this makes the other
    two the same engine."""
    default = market_state(run_market())
    named = market_state(run_market("pt-v1"))
    built = market_state(run_market(pretium.ModelParams.from_preset("pt-v1")))
    assert default == named
    assert default == built


def test_an_override_equal_to_the_preset_is_the_preset():
    # Bit-identity is the membership rule, not construction history: the
    # same value must produce the same model, fingerprint and trajectory.
    same = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.02)
    assert same.fingerprint == "pt-v1"
    assert market_state(run_market(same)) == market_state(run_market())


def test_the_shipped_half_life_keeps_the_recorded_bits():
    # The derived-bits policy: sameness of value means sameness of bits.
    same = pretium.ModelParams.from_preset("pt-v1",
                                           mispricing_half_life_days=60.0)
    assert struct.pack(">d", same.mispricing_phi).hex() == "3fefa1e827a1b38c"
    assert struct.pack(">d", same.s_phi_tick).hex() == "3fefffc1e1385e9e"
    assert same.fingerprint == "pt-v1"


# -- property 2: a perturbation moves the market ----------------------------

#: Every settable parameter, with a perturbed value and whether the 3-day
#: 10-name probe above is expected to see it move. The inert entries are
#: not dead parameters — each names the condition its effect waits on, and
#: a parameter that moved nothing WITHOUT such a reason fails the test.
PERTURBATIONS = [
    ("market_factor_sigma", 0.02, True),
    ("sector_factor_sigma", 0.004, True),
    ("idio_sigma_scale", 1.0, True),
    ("order_flow_coefficient", 80.0, False),   # needs order flow; none sent
    ("informed_flow_fraction", 0.5, False),    # needs order flow; none sent
    ("news_sector_weight", 0.6, False),        # needs news; none sent
    ("news_market_weight", 0.4, False),        # needs news; none sent
    ("crash_amplifier_threshold", 1.0, True),
    ("crash_amplifier_slope", 0.4, True),
    ("crisis_blend_ramp", 0.7, False),         # needs VIX > 25.5; macro
    ("crisis_blend_cap", 0.4, False),          # starts at the default 15
    ("garch_omega", 1e-5, True),
    ("garch_alpha", 0.12, True),
    ("garch_beta", 0.7, True),
    ("garch_gamma", 0.0, True),
    ("garch_ceiling_multiple", 1.01, True),
    ("garch_floor_multiple", 0.99, True),
    ("market_vol_alpha", 0.2, True),
    ("market_vol_beta", 0.7, True),
    ("market_vol_ceiling_multiple", 0.5, True),
    ("market_vol_floor_multiple", 2.0, True),
    ("market_vol_vix_coupling", 0.0, True),
    ("market_vol_vix_anchor", 22.0, True),
    ("mispricing_half_life_days", 10.0, True),
    ("momentum_theta", 0.5, True),
    ("mispricing_cap", 0.001, True),
    ("crowd_valuation_gain", 0.05, True),
    ("crowd_momentum_gain", 0.2, True),
    ("crowd_lean_cap", 0.0, True),
    ("price_breaker_fraction", 0.0001, True),
    ("price_hard_cap", 25.0, True),
]


def test_the_perturbation_table_covers_the_whole_settable_surface():
    assert sorted(name for name, _, _ in PERTURBATIONS) == \
        pretium.ModelParams.settable()


@pytest.mark.parametrize("name,value,moves", PERTURBATIONS,
                         ids=[p[0] for p in PERTURBATIONS])
def test_each_settable_parameter_moves_the_market_or_names_why_not(
        name, value, moves):
    """The stale-wheel counterproof, per parameter — and the CRN guard: the
    trajectory moves (or is inert for the documented reason) while the draw
    count NEVER does. A parameter that changed `draws_consumed` would have
    changed the draw schedule, which no preset member may (§5.2)."""
    base = market_state(run_market())
    custom = pretium.ModelParams.from_preset("pt-v1", **{name: value})
    assert custom.fingerprint.startswith("custom-")
    perturbed = market_state(run_market(custom))

    assert perturbed["draws"] == base["draws"], \
        f"{name} moved the draw schedule"
    moved = any(perturbed[k] != base[k] for k in perturbed if k != "draws")
    assert moved == moves, (
        f"{name}={value}: expected moved={moves}, got {moved} — either a "
        "parameter is not wired through, or an inert reason above is stale"
    )


def test_the_conditionally_inert_parameters_act_under_their_conditions():
    """The four flow/news parameters from the table above, shown live under
    the inputs they wait on — so 'inert without input' is measured, not
    assumed."""
    def run_with_inputs(model=None):
        kwargs = {} if model is None else {"model": model}
        engine = pretium.Engine(seed=42, universe=UNIVERSE, **kwargs)
        news = [pretium.News(sector="technology", price_impact=0.04),
                pretium.News(price_impact=0.02)]
        engine.open_market()
        engine.run_session(9, 30, 3, 39, news=news,
                           order_flow={UNIVERSE.tickers()[0]: (200_000.0, 0.0)})
        engine.close_market()
        return market_state(engine)

    base = run_with_inputs()
    for name, value in [("order_flow_coefficient", 80.0),
                        ("informed_flow_fraction", 0.5),
                        ("news_sector_weight", 0.6),
                        ("news_market_weight", 0.4)]:
        custom = pretium.ModelParams.from_preset("pt-v1", **{name: value})
        moved = run_with_inputs(custom)
        assert moved["draws"] == base["draws"], name
        assert moved != base, f"{name} did not act even under its inputs"


def test_a_recomputed_half_life_still_halves_in_its_stated_days():
    fast = pretium.ModelParams.from_preset("pt-v1",
                                           mispricing_half_life_days=30.0)
    decayed = 1.0
    for _ in range(30):
        decayed *= fast.mispricing_phi
    assert abs(decayed - 0.5) < 1e-13
    compounded = 1.0
    for _ in range(390):
        compounded *= fast.s_phi_tick
    assert abs(compounded - fast.mispricing_phi) < 1e-12


# -- property 3: the fingerprint cannot lie ---------------------------------

def test_the_fingerprint_is_stable_distinct_and_honestly_shaped():
    a = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    b = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    c = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.13)
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    assert a.fingerprint.startswith("custom-")
    assert len(a.fingerprint) == len("custom-") + 8
    assert a == b and a != c


def test_a_built_params_cannot_be_mutated():
    params = pretium.ModelParams.from_preset("pt-v1")
    with pytest.raises(AttributeError):
        params.garch_alpha = 0.5


def test_unknown_read_only_and_derived_names_are_refused():
    with pytest.raises(pretium.ValidationError, match="unknown model parameter"):
        pretium.ModelParams.from_preset("pt-v1", garch_alfa=0.1)
    with pytest.raises(pretium.ValidationError, match="not yet runtime-settable"):
        pretium.ModelParams.from_preset("pt-v1", oil_baseline=80.0)
    with pytest.raises(pretium.ValidationError, match="derived"):
        pretium.ModelParams.from_preset("pt-v1", mispricing_phi=0.9)
    with pytest.raises(pretium.ValidationError, match="finite"):
        pretium.ModelParams.from_preset("pt-v1", garch_alpha=float("nan"))
    with pytest.raises(pretium.ValidationError, match="unknown model preset"):
        pretium.ModelParams.from_preset("pt-v9")
    with pytest.raises(pretium.ValidationError, match="model must be"):
        pretium.Engine(seed=1, universe=UNIVERSE, model=0.12)


def test_the_dict_round_trips_and_a_foreign_constant_is_refused():
    custom = pretium.ModelParams.from_preset("pt-v1", momentum_theta=0.4)
    d = custom.to_dict()
    assert d["name"] == custom.fingerprint
    rebuilt = pretium.ModelParams.from_dict(d)
    assert rebuilt == custom and rebuilt.fingerprint == custom.fingerprint

    # A dict claiming a different value for a coefficient this build cannot
    # set describes a model this build cannot run.
    foreign = dict(d)
    foreign["oil_baseline"] = 80.0
    with pytest.raises(pretium.ValidationError, match="oil_baseline"):
        pretium.ModelParams.from_dict(foreign)


def test_the_engine_reports_the_model_it_runs():
    custom = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    engine = pretium.Engine(seed=1, universe=UNIVERSE, model=custom)
    assert engine.model_fingerprint == custom.fingerprint
    assert engine.model == custom
    assert engine.model_params["name"] == custom.fingerprint
    assert engine.model_params["garch_alpha"] == 0.12


def test_model_preset_keeps_its_kat_frozen_shape_and_gains_name():
    legacy = pretium.model_preset()
    assert sorted(legacy) == sorted(pretium.model_preset("pt-v1"))
    # The KAT hashes every value in this dict; its shape is load-bearing
    # until the next deliberate KAT bump. See the function's docstring.
    assert sorted(legacy) == [
        "crowd_lean_cap", "crowd_momentum_gain", "crowd_valuation_gain",
        "daily_shock_cap", "mispricing_cap", "mispricing_half_life_days",
        "mispricing_phi", "momentum_theta", "name",
    ]
    with pytest.raises(pretium.ValidationError, match="unknown model preset"):
        pretium.model_preset("pt-v9")


# -- property 4: the fingerprint travels ------------------------------------

def test_a_custom_preset_round_trips_through_a_manifest():
    """The §9 composition: a run under a custom model is captured, travels
    as JSON, and reproduces on the other side — under the recorded
    coefficients, to the recorded digest."""
    custom = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12,
                                             market_factor_sigma=0.018)
    engine = run_market(custom)
    manifest = pretium.RunManifest.of(engine, seed=42, universe=UNIVERSE)
    assert manifest.fingerprints["model"] == custom.fingerprint
    assert manifest.model["name"] == custom.fingerprint

    loaded = pretium.RunManifest.from_json(manifest.to_json())
    rebuilt = loaded.reproduce()
    assert rebuilt.model_fingerprint == custom.fingerprint
    assert market_state(rebuilt) == market_state(engine)


def test_a_default_manifest_still_reproduces_and_names_its_preset():
    engine = run_market()
    manifest = pretium.RunManifest.of(engine, seed=42, universe=UNIVERSE)
    assert manifest.fingerprints["model"] == "pt-v1"
    rebuilt = pretium.RunManifest.from_json(manifest.to_json()).reproduce()
    assert rebuilt.model_fingerprint == "pt-v1"
    assert market_state(rebuilt) == market_state(engine)


def test_a_tampered_custom_model_dict_is_refused_before_replay():
    custom = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    manifest = pretium.RunManifest.of(run_market(custom), seed=42,
                                      universe=UNIVERSE)
    payload = json.loads(manifest.to_json())
    payload["written_by"]["model"]["garch_alpha"] = 0.19
    with pytest.raises(pretium.ValidationError, match="no longer"):
        pretium.RunManifest.from_json(json.dumps(payload)).reproduce()


def test_a_checkpoint_of_a_custom_run_resumes_under_that_model():
    custom = pretium.ModelParams.from_preset("pt-v1", momentum_theta=0.4)
    engine = run_market(custom)
    mark = pretium.Checkpoint.of(engine, universe=UNIVERSE, seed=42)
    resumed = pretium.Checkpoint.from_json(mark.to_json()).resume()
    assert resumed.model_fingerprint == custom.fingerprint
    assert market_state(resumed) == market_state(engine)


def test_a_fork_of_a_custom_run_prices_under_the_parents_model():
    custom = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    parent = run_market(custom, days=2)
    child = pretium.branch(parent, 1, universe=UNIVERSE, seed=42)[0]
    assert child.model_fingerprint == custom.fingerprint
    # One more day each: identical futures, which only holds if the child
    # runs the parent's coefficients. Columns only — `draws_consumed` is a
    # per-engine diagnostic counter, and a fork deliberately counts its own
    # draws from zero rather than inheriting the parent's total.
    for engine in (parent, child):
        engine.open_market()
        engine.run_session(9, 30, 3, 78)
        engine.close_market()
    parent_state = market_state(parent)
    child_state = market_state(child)
    del parent_state["draws"], child_state["draws"]
    assert child_state == parent_state


def test_the_scorecard_carries_the_model_fingerprint():
    class Idle:
        def act(self, obs):
            return {}

    custom = pretium.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    small = pretium.Universe.random(4, seed=5)
    default = pretium.evaluate({"idle": Idle()}, seed=9, universe=small,
                               days=1, steps_per_day=2, ticks_per_step=10)
    assert default["idle"].model_fingerprint == "pt-v1"
    scored = pretium.evaluate({"idle": Idle()}, seed=9, universe=small,
                              days=1, steps_per_day=2, ticks_per_step=10,
                              model=custom)
    assert scored["idle"].model_fingerprint == custom.fingerprint
    assert scored["idle"].as_dict()["model_fingerprint"] == custom.fingerprint


def test_replay_accepts_the_model_and_reproduces_the_custom_run():
    custom = pretium.ModelParams.from_preset("pt-v1", garch_beta=0.7)
    engine = run_market(custom)
    replayed = pretium.replay(engine.order_log, seed=42, universe=UNIVERSE,
                              model=custom)
    assert market_state(replayed) == market_state(engine)
    # And WITHOUT the model it replays the default market instead — the
    # reason the manifest must carry the coefficients.
    wrong = pretium.replay(engine.order_log, seed=42, universe=UNIVERSE)
    assert market_state(wrong) != market_state(engine)
