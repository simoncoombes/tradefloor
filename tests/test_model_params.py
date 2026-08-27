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

DEFAULT = pretium.ModelParams.from_preset().fingerprint

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
    named = market_state(run_market(pretium.ModelParams.from_preset().fingerprint))
    built = market_state(run_market(pretium.ModelParams.from_preset()))
    assert default == named
    assert default == built


def test_an_override_equal_to_the_preset_is_the_preset():
    # Bit-identity is the membership rule, not construction history: the
    # same value must produce the same model, fingerprint and trajectory.
    same = pretium.ModelParams.from_preset(
        **{k: v for k, v in pretium.ModelParams.from_preset().to_dict().items()
           if k in pretium.ModelParams.settable()})
    assert same.fingerprint == pretium.ModelParams.from_preset().fingerprint
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
    # The crisis blend's source only acts above the crisis threshold, which a
    # 3-day probe at the default VIX never reaches; it is shown live under a
    # held VIX 45 in tests/test_sector_crisis.py. The sector draw's VIX
    # coupling IS live here: the endogenous VIX leaves the anchor on day one,
    # so the ratio is never exactly 1.0 and the sigma moves. A first draft
    # marked it False on the anchor argument and the probe disagreed.
    ("crisis_blend_source", 0.0, False),     # perturbed away from the default (1.0 since pt-v7); still needs VIX > the crisis threshold, which three sessions do not reach
    # 0.25, not 1.0: the default carries 1.0 since pt-v11, so perturbing TO
    # it is a no-op and the test read "not wired through". The value has to
    # differ from whatever the default holds, which is what an era boundary
    # keeps breaking.
    ("sector_vix_coupling", 0.25, True),
    ("garch_omega", 1e-5, True),
    ("garch_alpha", 0.12, True),
    ("garch_beta", 0.7, True),
    ("garch_gamma", 0.0, True),
    # 1.01 bound under pt-v1 and stopped binding at pt-v3: the calibrated
    # persistence is lower (alpha+beta 0.740 against 0.820), so a 3-day
    # probe never drives the variance 1% above its long-run level for the
    # ceiling to catch. 0.9 puts the ceiling below that level, which binds
    # on any preset and keeps the parameter's wiring proven rather than
    # excused.
    ("garch_vix_coupling", 0.8, False),    # scales the clamp reference by (vix/anchor)^2, and the harness runs at the anchor, where that is exactly 1.0 at any coupling
    ("garch_ceiling_multiple", 0.9, False),   # measured not to bind under pt-v10 at 0.9, 1.05, 2.0 or 20.0: the trimmed idio scale keeps per-name variance under the clamp's reference
    ("garch_floor_multiple", 0.99, True),
    ("market_vol_alpha", 0.2, True),
    ("market_vol_beta", 0.7, True),
    ("market_vol_ceiling_multiple", 0.5, True),
    ("market_vol_floor_multiple", 2.0, True),
    ("market_vol_vix_coupling", 0.0, True),
    # The slow variance component, added at pt-v4. The WEIGHT is the
    # switch: the update takes an explicit zero-weight branch, so with
    # weight 0 neither of the other two can reach the variance at all.
    # With weight non-zero the mixture is live even at zero persistence
    # and gain, because the slow component then sits at the target rather
    # than tracking the fast one -- which is a different variance, so the
    # weight moves the market on its own. That asymmetry is why it reads
    # True here and the other two read False.
    ("market_vol_slow_persistence", 0.99, False),   # needs a non-zero weight
    ("market_vol_slow_gain", 0.1, False),           # needs a non-zero weight
    ("market_vol_slow_weight", 0.5, True),
    ("market_vol_vix_anchor", 22.0, True),
    ("mispricing_half_life_days", 10.0, True),
    ("momentum_theta", 0.5, True),
    ("mispricing_cap", 0.001, True),
    ("crowd_valuation_gain", 0.05, True),
    ("crowd_momentum_gain", 0.2, True),
    ("crowd_lean_cap", 0.0, True),
    ("price_breaker_fraction", 0.0001, True),
    ("price_hard_cap", 25.0, True),
    # Volume tracking the market factor's variance, added at pt-v4. Unlike
    # the three slow-variance rows this one acts alone: the variance it
    # reads is already moving under any preset.
    ("volume_variance_gain", 1.5, True),
    # Universe memory (pt-v4). Inert here for exactly the reason
    # crisis_blend_ramp and crisis_blend_cap are: the blend they modify
    # only exists above CRISIS_VIX_THRESHOLD, and a 3-day probe starting
    # at the default VIX of 15 never gets there. Shown live under that
    # condition by `test_universe_memory_acts_above_the_crisis_threshold`.
    ("universe_stress_decay", 0.97, False),    # needs VIX > threshold
    ("universe_stress_weight", 1.0, False),    # needs VIX > threshold
    # The business cycle reaching the market. Inert in the probe for a
    # third variant of the same reason: it feeds the crisis blend, which
    # only exists above CRISIS_VIX_THRESHOLD, AND a 3-day probe starts in
    # Expansion, whose stress intensity is exactly 0.0.
    ("regime_stress_points", 12.0, False),     # needs a non-expansion phase
    # Information transfer between sector peers (pt-v4). Inert in the probe
    # for the simplest reason of all: the probe fires no news, and with no
    # company-tagged event there is no announcer whose surprise could reach
    # anyone. Shown live by `test_peer_transfer_moves_a_name_the_news_never_named`.
    ("news_peer_weight", 0.2, False),          # needs company-tagged news
    ("news_peer_weight_down", 0.5, False),     # needs company-tagged BAD news
    # Decoupling the slow variance component's target from VIX (pt-v4).
    # Inert in the probe for two reasons at once: the slow component has zero
    # weight in every shipped preset, so there is no slow target to decouple,
    # and a 3-day probe at a flat default VIX has no spike to track anyway.
    ("market_vol_slow_vix_damp", 0.5, False),  # needs slow weight AND a VIX move
    # Endogenous jumps (pt-v4). Every row here is inert ALONE, and each waits
    # on a different partner, which is why they are listed separately rather
    # than as one mechanism. An intensity of 1.0 fires a jump on every day and
    # still moves nothing, because the size is zero -- occurrence without
    # magnitude is not an event. A size or a mean moves nothing either,
    # because nothing occurs. The pair is shown live by
    # `test_jumps_move_prices_when_intensity_is_on`.
    ("jump_intensity_market", 1.0, True),   # live since pt-v10 turned this mechanism on
    ("jump_intensity_idio", 1.0, True),   # live since pt-v10 turned this mechanism on
    ("jump_mean_market", -0.05, False),        # needs an occurrence
    ("jump_sigma_market", 0.05, False),        # needs an occurrence
    ("jump_sigma_idio", 0.05, False),          # needs an occurrence
    # Whether herding continues a jump. Inert for the same reason as the
    # sizes above and one more: at the default preset no jump ever fires, so
    # there is nothing for the share to withhold from the momentum term.
    ("jump_momentum_share", 1.0, False),     # perturbed away from the default (0.0 since pt-v6); needs a jump inside the three sessions, which is a 7% chance a day
    # A spread across names, applied at the day close. It DOES move the
    # trajectory on its own: unlike the jump parameters it needs no
    # occurrence, only a roster with more than one market cap in it.
    ("garch_beta_dispersion", 0.05, True),
    # The book floor extended to profitable companies. Live immediately and
    # not subtly: 42.8% of instruments from `Universe.random` have
    # `eps * pe` below `book * 1.2`, so at 1.0 a large part of any universe
    # is re-valued on the first tick.
    ("fair_value_book_floor", 1.0, True),
    # The persistent volume component (pt-v4). The innovation is live alone:
    # even at zero persistence it injects a fresh multiplier each day, and
    # that reaches volume on the next tick. Persistence is NOT, because at a
    # zero innovation there is nothing to persist -- `0.9 * 0.0 + 0.0 * z`
    # is 0.0 forever. Measured, after this table first claimed otherwise.
    ("volume_persistence", 0.9, True),   # live since pt-v10 turned this mechanism on
    ("volume_innovation_sigma", 0.3, True),
    # The continuous size effect (pt-v4). Smoothness is live alone: at the
    # shipped exponent it immediately moves every name off its tier. The
    # exponent is NOT, because at zero smoothness the power law is never
    # evaluated -- it waits on the blend, exactly as the jump sizes wait on
    # an occurrence.
    ("size_effect_smoothness", 1.0, True),
    ("size_effect_exponent", 0.30, False),     # needs smoothness > 0
    # The continuous SPREAD curve (pt-v4). Same shape of dependency: the
    # blend is live alone, the exponent waits on it.
    ("spread_size_smoothness", 1.0, True),
    ("spread_size_exponent", 0.80, False),     # needs smoothness > 0
    # The crisis gates, promoted from carried-read-only (pt-v4). VIX mean
    # reversion is live in the probe -- it moves the macro chain every day.
    # The crisis threshold is NOT, for the reason crisis_blend_ramp is not:
    # a 3-day probe at the default VIX of 15 never reaches 25.5, so moving
    # the gate moves nothing. Shown live by
    # `test_the_crisis_threshold_acts_above_itself`.
    ("vix_mean_reversion", 0.30, True),
    ("vix_realised_vol_weight", 0.5, True),
    ("vix_cycle_amplitude", 1.0, True),     # perturbed AWAY from the default, which is 0.0 since the 2026-08-26 boundary
    ("vix_return_source", 0.0, True),        # perturbed AWAY from the default, which is 1.0 since pt-v10
    ("vix_return_gain", 150.0, True),         # the channel reads the day since pt-v10, and the harness's days fall
    ("vix_return_gain_up", 60.0, False),     # needs an UP day; under this default the channel reads the DAY and the harness's three sessions fall
    ("vix_return_clamp", 0.12, True),
    ("vix_target_shock_cap", 40.0, False),   # binds only past a 12-point excursion
    ("inflation_ceiling", 10.0, False),       # binds only when inflation reaches 6%
    ("inflation_floor", -3.0, False),         # binds only when inflation reaches -1%
    ("inflation_reversion", 0.15, False),      # monthly; reaches prices via the bond yield at the first meeting (day 45)
    ("crisis_vix_threshold", 18.0, False),     # needs VIX above the gate
    ("jump_vix_coupling", 1.0, False),
    ("crisis_blend_gain", 2.0, False),
    # Was inert with reason "sigma ships at 0.0, so alone this generates
    # zero-impact news". True since pt-v11 put sigma at 0.03 and pt-v12 made
    # it the default, so raising the rate now moves the market on its own.
    ("endogenous_news_intensity", 0.25, True),
    ("endogenous_news_sigma", 0.05, False),      # the other half of the pair: no events exist to carry an impact until intensity is non-zero
    ("news_peer_vix_coupling", 4.0, False),      # multiplies a peer weight that is zero on every preset, and a crisis spike the harness never reaches
    ("sector_loading", 1.0, True),               # the literal 0.5 made reachable: doubling a name's exposure to its own sector moves it from the first tick
    ("sector_loading_beta_slope", 0.8, True),    # spreads the loading across names by beta, so the cross-section moves even though the mean loading does not
    ("volume_idio_variance_gain", 1.0, True),    # couples volume to the name's own variance, which is non-trivial from the first tick
    # 0.5, not 12.0: RAISING the cap is inert in this short window because
    # no name here moves more than the shipped 4 percent from its open,
    # which is the measurement that makes the cap a CRISIS dial. Lowering
    # it binds on any day past half a percent, so it tests the wiring.
    ("volume_move_cap", 0.5, True),              # saturates volume on any day past half a percent
    ("volume_move_floor", 0.9, True),            # every name trades more on every day
    ("volume_move_noise", 0.05, True),           # narrows the return-unrelated part of volume
    ("volume_move_response", 0.9, True),         # steepens volume against the size of the day's move
    ("garch_cascade_components", 6.0, True),     # replaces one variance timescale with six
    # Inert ALONE: both only read inside the cascade, and the cascade only
    # runs when garch_cascade_components >= 1. A PAIR, like the endogenous
    # news dials above.
    ("garch_cascade_ratio", 5.0, False),
    ("garch_cascade_weight", 0.5, False),
    ("volume_idio_persistence", 0.8, False),     # a PAIR like the news dials: persistence alone carries an innovation of zero, so every per-name state stays exactly 0.0
    ("volume_idio_sigma", 0.25, True),           # the innovation half: non-zero sigma moves volume from the first day, and volume reaches price through the book      # the crisis blend only fires above the VIX gate, which the harness does not cross         # anchored: the harness runs near market_vol_vix_anchor, where the rate scale is exactly 1.0 at any coupling
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
    # Perturb the DEFAULT preset, not a named one: `base` is the default
    # engine, so building the perturbation from any other preset compares
    # a preset change and a parameter change at once and calls the sum a
    # parameter effect. That is what happened at the pt-v3 era boundary --
    # six parameters documented as inert "failed" because the baseline had
    # moved underneath them.
    custom = pretium.ModelParams.from_preset(**{name: value})
    assert custom.fingerprint.startswith("custom-")
    perturbed = market_state(run_market(custom))

    assert perturbed["draws"] == base["draws"], \
        f"{name} moved the draw schedule"
    moved = any(perturbed[k] != base[k] for k in perturbed if k != "draws")
    assert moved == moves, (
        f"{name}={value}: expected moved={moves}, got {moved} — either a "
        "parameter is not wired through, or an inert reason above is stale"
    )


def test_the_slow_variance_component_acts_when_its_three_parts_agree():
    """The other half of the three inert rows above, measured.

    The market factor's variance carries two timescales from pt-v4: a fast
    one tracking the VIX-scaled target and a slow one carrying long-horizon
    clustering. The slow half is off in every shipped preset, and off means
    bit-identical rather than merely small -- the composed update branches
    on a zero weight instead of adding `0.0 * deviation`.

    So all three parts must be present for it to do anything, and that is
    what makes the three inert rows above honest rather than a hole an
    optimiser could walk through.
    """
    base = market_state(run_market())
    both = pretium.ModelParams.from_preset(
        "pt-v3", market_vol_slow_gain=0.1, market_vol_slow_weight=0.5)
    assert both.fingerprint.startswith("custom-")
    moved = market_state(run_market(both))
    assert moved["draws"] == base["draws"], "the slow component moved the draw schedule"
    assert any(moved[k] != base[k] for k in moved if k != "draws")


def test_universe_memory_acts_above_the_crisis_threshold():
    """The state that lets a crisis OUTLIVE itself, measured.

    Without it the crisis correlation blend is a lookup on today's VIX:
    the tick VIX falls back under the threshold and the whole
    cross-section decouples in the same tick, so a panic leaves the
    universe exactly as it found it. With it, remembered stress holds the
    blend up while it decays.

    Driven through a scenario that spikes VIX and then lets it fall,
    because that is the only shape in which the difference exists at all
    -- at a flat VIX there is nothing to remember.
    """
    import pretium.scenario as sc

    def final_prices(model=None):
        kwargs = {} if model is None else {"model": model}
        shock = sc.Scenario.vix_shock(calm=15.0, peak=45.0, at=3, over=5)
        engine = sc.run_scenario(shock, seed=42, universe=UNIVERSE, days=20,
                                 ticks_per_day=40, **kwargs)
        return struct.unpack("<%dd" % len(engine.tickers),
                             engine.column("price"))

    forgetful = final_prices(pretium.ModelParams.from_preset("pt-v3"))
    remembering = final_prices(pretium.ModelParams.from_preset(
        "pt-v3", universe_stress_decay=0.97, universe_stress_weight=1.0))
    assert forgetful != remembering, (
        "universe memory changed nothing across a VIX spike and decay; "
        "the state is not reaching the correlation blend"
    )


def test_no_parameter_dominates_the_deviation_penalty():
    """A parameter whose box move costs 400x another's is a regulariser bug.

    The search minimises `L_real + lambda * sum_j dev_j^2`, and `deviation()`
    returns a LOG ratio for scale parameters and a RAW difference for bounded
    ones. Classify a level as bounded and the raw difference carries its
    magnitude straight into a squared penalty: `crisis_vix_threshold` ships
    at 25.5, so a move to its box edge read a deviation near 19 -- squared
    372, times a lambda of 10, roughly 3,700 against a realism loss of order
    one.

    The search that hit it spent its entire budget minimising the
    regulariser rather than the market, and nothing said so; the only clue
    was an objective three orders of magnitude larger than the shipped
    preset's. This asserts the penalties stay comparable, so the next
    mis-classified parameter fails here instead.
    """
    import sys, statistics
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "tools" / "calibration"))
    from calibrate import calibration_box, deviation
    from instrumentlib import shipped_values

    ship = shipped_values()
    costs = {}
    for name, value in ship.items():
        lo, hi = calibration_box(name, value)
        # The worse of the two edges: what a search can actually spend.
        worst = max(abs(deviation(name, lo, value)),
                    abs(deviation(name, hi, value))) ** 2
        if worst > 0:
            costs[name] = worst
    assert costs, "no parameter produced a measurable box deviation"
    median = statistics.median(costs.values())
    outliers = {n: c for n, c in costs.items() if c > 100 * median}
    assert not outliers, (
        f"these parameters cost more than 100x the median squared deviation "
        f"at their box edge, so the penalty is about them rather than about "
        f"the model: "
        + ", ".join(f"{n} {c:.1f} vs median {median:.3f}"
                    for n, c in sorted(outliers.items()))
    )


def test_the_horizon_axis_holds_out_seeds_as_well_as_the_horizon():
    """A validation axis that shares the training seeds validates nothing.

    `--holdout-days-seeds` used to default to `TRAIN_SEEDS`, so the
    `holdout_horizon` axis held out the HORIZON and not the paths. Any
    effect that was a property of those thirty seeds passed through it
    unchallenged.

    One did. A candidate was declared shippable on a 13% improvement in the
    504-day loss; measured on four seed blocks the gap read +0.1297 where it
    was found, then -0.0315, +0.0209 and +0.0233 -- reversing sign once and
    five times smaller everywhere else. The discovery sweep and its
    validation both used the training seeds, so re-measuring reproduced the
    same fluctuation exactly and reported it as confirmation.

    Asserted here so the default cannot drift back to something that
    confirms a number instead of testing an effect.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / "tools" / "calibration"))
    import instrumentlib as lib

    train = set(lib.TRAIN_SEEDS)
    confirm = set(lib.CONFIRM_SEEDS)
    assert not (train & confirm), (
        f"the confirmation block shares {sorted(train & confirm)} with the "
        "training seeds; an effect found on those seeds would confirm itself"
    )
    assert len(confirm) == len(train), (
        f"the confirmation block has {len(confirm)} seeds against the "
        f"training set's {len(train)}; it has to DETECT a difference, and "
        "less power than the set that found it cannot"
    )


def test_the_calibration_instrument_knows_every_settable_parameter():
    """A settable parameter with no calibration spec cannot be searched.

    `instrumentlib.PARAM_SPECS` is a THIRD hand-maintained registry of the
    settable surface, after `settable_names` and `carried_read_only_pairs`.
    When the surface grew and this one did not, nineteen parameters became
    silently unsearchable -- every jump parameter, both size curves, the
    volume process and all four crisis levers among them.

    The failure mode was not a warning. It was a `KeyError` inside
    `shipped_values()` that killed a 96-core search one minute after it
    started, on parameters a hand sweep had just shown carried free gains.
    Asserted here rather than left for the next search to rediscover.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / "tools" / "calibration"))
    from instrumentlib import PARAM_SPECS

    settable = set(pretium.ModelParams.settable())
    missing = sorted(settable - set(PARAM_SPECS))
    assert not missing, (
        f"settable but with no calibration spec, so no search can reach "
        f"them: {missing}"
    )
    orphaned = sorted(set(PARAM_SPECS) - settable)
    assert not orphaned, (
        f"specs for parameters that are no longer settable: {orphaned}"
    )


def test_the_crisis_threshold_acts_above_itself():
    """Where a crisis STARTS is now a calibration question, not a const.

    `crisis_vix_threshold` gates three mechanisms at once -- the
    sector-to-market correlation blend, the universe stress memory, and the
    economy's crisis premium -- and until pt-v4 it was carried read-only, so
    no search could reach the trigger point of any of them.

    Measured under a VIX shock that crosses both the shipped gate and a
    lowered one, because a probe below the gate cannot tell them apart:
    that is the whole reason this parameter reads inert in the table above.
    """
    import pretium.scenario as sc

    def prices(model):
        shock = sc.Scenario.vix_shock(calm=15.0, peak=40.0, at=3, over=6)
        engine = sc.run_scenario(shock, seed=42, universe=UNIVERSE, days=18,
                                 ticks_per_day=40, model=model)
        return struct.unpack("<%dd" % len(engine.tickers),
                             engine.column("price"))

    shipped = prices(pretium.ModelParams.from_preset("pt-v3"))
    earlier = prices(pretium.ModelParams.from_preset(
        "pt-v3", crisis_vix_threshold=18.0))
    assert shipped != earlier, (
        "lowering the crisis threshold changed nothing across a VIX spike "
        "that crosses both gates; the parameter is not reaching its call "
        "sites"
    )


def test_jumps_move_prices_when_intensity_is_on():
    """The model has no discontinuities without this.

    Prices diffuse; real markets gap. Nothing here ever surprised the market
    unless a caller injected news by hand, which is why excess kurtosis reads
    5.2 over 504-day windows against real markets' 7.1 to 22.

    Asserted three ways, because "the prices differ" alone would pass on a
    mechanism that merely perturbed the stream: the jump must move prices,
    a market-wide jump must move EVERY name (it is common), and a negative
    mean must push the cross-section DOWN rather than merely around.
    """
    def run(model=None, days=6):
        kwargs = {} if model is None else {"model": model}
        engine = pretium.Engine(seed=42, universe=UNIVERSE, **kwargs)
        for _ in range(days):
            engine.open_market()
            engine.run_session(9, 30, 3, 40)
            engine.close_market()
        return struct.unpack("<%dd" % len(engine.tickers), engine.column("price"))

    quiet = run(pretium.ModelParams.from_preset("pt-v3"))
    crashing = run(pretium.ModelParams.from_preset(
        "pt-v3", jump_intensity_market=1.0, jump_mean_market=-0.06,
        jump_sigma_market=0.01))

    moved = [i for i in range(len(quiet)) if quiet[i] != crashing[i]]
    assert len(moved) == len(quiet), (
        f"a market-wide jump moved {len(moved)} of {len(quiet)} names; it is "
        "common, so it must reach all of them"
    )
    # A negative mean must move the cross-section down on balance, not just
    # scatter it. Median rather than mean, so one name cannot carry it.
    down = sum(1 for i in range(len(quiet)) if crashing[i] < quiet[i])
    assert down > len(quiet) // 2, (
        f"only {down} of {len(quiet)} names fell under a jump with mean "
        "-0.06; the sign is not reaching the price"
    )


def test_jumps_are_inert_at_the_shipped_intensity():
    """Zero intensity must be bit-identical, not merely close.

    The jump draws happen EVERY day whether or not a jump fires -- a
    conditional schedule would make the stream position depend on the
    parameters. They land on their own RNG stream, so the market, economy and
    external streams are untouched and every shipped preset reproduces
    exactly. This is the assertion that guards that claim.
    """
    def prices(model):
        engine = pretium.Engine(seed=7, universe=UNIVERSE, model=model)
        engine.open_market()
        engine.run_session(9, 30, 3, 40)
        engine.close_market()
        return struct.unpack("<%dd" % len(engine.tickers), engine.column("price"))

    shipped = prices(pretium.ModelParams.from_preset("pt-v3"))
    explicit_zero = prices(pretium.ModelParams.from_preset(
        "pt-v3", jump_intensity_market=0.0, jump_intensity_idio=0.0,
        jump_sigma_market=0.0, jump_sigma_idio=0.0, jump_mean_market=0.0))
    assert shipped == explicit_zero


def test_peer_transfer_moves_a_name_the_news_never_named():
    """A company-tagged event must reach that company's sector peers.

    Before pt-v4 it could not: the news dispatch is an if/else-if chain whose
    sector arm requires `company_id is None`, so an earnings beat at one name
    moved exactly that name and nothing else. Sector co-movement existed, but
    only as exogenous shared shocks -- a per-tick sector factor draw and market
    beta -- never as contagion from a member.

    Asserted on a name OTHER than the announcer, because the announcer moves
    identically either way: it is matched by id, before sector is consulted.
    """
    universe = pretium.Universe.random(40, seed=3)
    announcer = universe.tickers()[0]

    def prices(model=None):
        kwargs = {} if model is None else {"model": model}
        engine = pretium.Engine(seed=42, universe=universe, **kwargs)
        engine.open_market()
        engine.run_session(
            9, 30, 3, 39,
            news=[pretium.News(ticker=announcer, price_impact=0.05)],
        )
        engine.close_market()
        return struct.unpack("<%dd" % len(engine.tickers),
                             engine.column("price"))

    isolated = prices(pretium.ModelParams.from_preset("pt-v3"))
    transferring = prices(pretium.ModelParams.from_preset(
        "pt-v3", news_peer_weight=0.2, news_peer_weight_down=0.5))

    moved = [i for i in range(1, len(isolated))
             if isolated[i] != transferring[i]]
    assert moved, (
        "a company-tagged event reached no other name in the roster; the "
        "information-transfer channel is not wired to the news dispatch"
    )
    assert isolated[0] == transferring[0], (
        "the announcer's own move changed with the peer weight; it must be "
        "matched by id before sector is consulted, so transfer cannot dilute it"
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
        pretium.ModelParams.from_preset("pt-v999")
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
        pretium.model_preset("pt-v999")


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
    assert manifest.fingerprints["model"] == DEFAULT
    rebuilt = pretium.RunManifest.from_json(manifest.to_json()).reproduce()
    assert rebuilt.model_fingerprint == DEFAULT
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
    assert default["idle"].model_fingerprint == DEFAULT
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


# -- property 5: every runner passes the model through ----------------------
#
# Phase 1 threaded `model=` into Engine, evaluate, replay, Checkpoint and
# fork, and named the rest as follow-ups. These tests close the list. The
# failure mode they guard against is specific and silent: a caller who
# built a custom ModelParams and handed it to a runner that ignored it
# would get the shipped market back, labelled with their intent — a wrong
# answer that looks right. Every runner must therefore (a) run the model,
# shown by the trajectory moving against the default, and (b) say so,
# wherever its result carries provenance at all.

#: One perturbation, shared by the whole section. market_factor_sigma is
#: the loudest single lever (it scales the shared component of every
#: return), so any runner that quietly dropped the model fails fast.
CUSTOM = pretium.ModelParams.from_preset("pt-v1", market_factor_sigma=0.03)

SMALL = pretium.Universe.random(6, seed=2)


class _Idle:
    def act(self, obs):
        return {}


class _BuyFirst:
    def __init__(self):
        self.done = False

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        ticker = obs.tickers[0]
        return {ticker: 0.01 * obs.avg_volume(ticker)}


def test_facts_measure_runs_the_model_and_names_it():
    """The seam the calibration search evaluates through: the panel at a
    candidate vector is measure(model=candidate), and the row it returns
    names the vector it measured."""
    kwargs = dict(seed=1, universe=SMALL, days=35)
    default = pretium.facts.measure(**kwargs)
    custom = pretium.facts.measure(**kwargs, model=CUSTOM)
    assert default["model_fingerprint"] == DEFAULT
    assert custom["model_fingerprint"] == CUSTOM.fingerprint
    # The statistics were measured on the custom market, not merely
    # relabelled: tripling the factor sigma moves pooled volatility.
    assert custom["annualised_vol_pct"] != default["annualised_vol_pct"]
    # And the shipped default is untouched by the parameter existing.
    assert pretium.facts.measure(**kwargs, model=DEFAULT) == default


def test_tca_analyse_runs_the_model_in_both_worlds():
    kwargs = dict(seed=5, universe=SMALL, days=1, steps_per_day=2,
                  ticks_per_step=10)
    default = pretium.tca.analyse(_BuyFirst(), **kwargs)
    custom = pretium.tca.analyse(_BuyFirst(), **kwargs, model=CUSTOM)
    assert default.model_fingerprint == DEFAULT
    assert custom.model_fingerprint == CUSTOM.fingerprint
    assert custom.as_dict()["model_fingerprint"] == CUSTOM.fingerprint
    # The custom model is a different market...
    assert custom.baseline_final != default.baseline_final
    # ...but BOTH of its worlds ran it, so the counterfactual is still
    # clean: on one day nothing untraded can move under any model.
    assert custom.untouched_moved() == []


def test_run_scenario_and_compare_run_the_model():
    scenario = pretium.Scenario(label="flat").hold(vix=15.0)
    kwargs = dict(seed=3, universe=SMALL, days=1, ticks_per_day=30)
    default = pretium.run_scenario(scenario, **kwargs)
    custom = pretium.run_scenario(scenario, **kwargs, model=CUSTOM)
    assert default.model_fingerprint == DEFAULT
    assert custom.model_fingerprint == CUSTOM.fingerprint
    assert custom.prices() != default.prices()

    from pretium.scenario import compare

    result = compare(
        pretium.Scenario.vix_shock(calm=15.0, peak=45.0, at=0, over=2),
        seed=3, universe=SMALL, days=3, model=CUSTOM)
    assert result["model_fingerprint"] == CUSTOM.fingerprint
    # The membership rule holds through the runner: a model changes what
    # the draws are multiplied into, never the schedule, so the comparison
    # stays exact under a custom model too.
    assert result["exact"] is True


def test_run_many_runs_the_model_and_stamps_every_row():
    kwargs = dict(universe=SMALL, days=1, ticks=30, collect="summary")
    default = pretium.run_many([1, 2], **kwargs)
    custom = pretium.run_many([1, 2], **kwargs, model=CUSTOM)
    for row in default:
        assert row["model_fingerprint"] == DEFAULT
    for before, after in zip(default, custom):
        assert after["model_fingerprint"] == CUSTOM.fingerprint
        assert after["prices"] != before["prices"]
        # The CRN guard, through the runner: same seed, same schedule.
        assert after["draws_consumed"] == before["draws_consumed"]
    # The model survives the crossing into worker threads.
    threaded = pretium.run_many([1, 2], **kwargs, model=CUSTOM, workers=2)
    assert [r["prices"] for r in threaded] == [r["prices"] for r in custom]


def test_sweep_runs_the_model():
    import pyarrow as pa

    def closes(model=None):
        ((_, table),) = pretium.sweep([9], universe=SMALL, days=1,
                                      ticks_per_day=30, model=model)
        return pa.table(table).to_pydict()["close"]

    assert closes(CUSTOM) != closes()


def test_flow_impact_runs_the_model_in_both_worlds():
    flow = {SMALL[0].ticker: (50_000.0, 0.0)}
    kwargs = dict(seed=4, universe=SMALL, order_flow=flow, ticks=30)
    default = pretium.flow_impact(**kwargs)
    custom = pretium.flow_impact(**kwargs, model=CUSTOM)
    assert custom.baseline != default.baseline
    # One day, both worlds under the one model: nothing untraded moves.
    assert custom.untouched_moved() == []


def test_the_gym_env_runs_the_model_and_reports_it_at_reset():
    numpy = pytest.importorskip("numpy")
    from pretium.gym import TradingEnv

    kwargs = dict(universe=SMALL, seed=6, days=1, steps_per_day=1,
                  ticks_per_step=10)
    default = TradingEnv(**kwargs)
    _, info = default.reset()
    assert info["model_fingerprint"] == DEFAULT
    custom = TradingEnv(**kwargs, model=CUSTOM)
    _, info = custom.reset()
    assert info["model_fingerprint"] == CUSTOM.fingerprint
    assert custom.engine.model_fingerprint == CUSTOM.fingerprint
    hold = numpy.zeros(len(SMALL))
    obs_default = default.step(hold)[0]
    obs_custom = custom.step(hold)[0]
    # Same seed, different coefficients: the episode is a different market.
    assert not numpy.array_equal(obs_default, obs_custom)


def test_rank_runs_the_model_and_the_ranking_records_it():
    kwargs = dict(seeds=[1], universe=SMALL, days=1, steps_per_day=1,
                  ticks_per_step=10)
    default = pretium.rank(lambda: {"idle": _Idle()}, **kwargs)
    assert default.model_fingerprint == DEFAULT
    ranking = pretium.rank(lambda: {"idle": _Idle()}, **kwargs, model=CUSTOM)
    assert ranking.model_fingerprint == CUSTOM.fingerprint
    assert ranking.as_dict()["model_fingerprint"] == CUSTOM.fingerprint
    assert CUSTOM.fingerprint in ranking.report()


def test_a_state_snapshot_refuses_to_restore_across_models():
    """The low-level fork path, guarded like the high-level ones. branch()
    builds its engines from the parent's own model, but state_snapshot /
    restore_state are public, and a custom run's state restored onto a
    default-built engine would continue under pt-v1 with no symptom."""
    parent = pretium.Engine(seed=11, universe=SMALL, model=CUSTOM)
    parent.open_market()
    parent.run_session(9, 30, 3, 20)
    snapshot = parent.state_snapshot()
    assert snapshot["model_fingerprint"] == CUSTOM.fingerprint

    imposter = pretium.Engine(seed=11, universe=SMALL)
    with pytest.raises(pretium.ValidationError, match="across models"):
        imposter.restore_state(snapshot)

    # A snapshot from before the fingerprint was recorded has no key and
    # restores as it always did -- the caller vouches for the context,
    # exactly as they do for the universe.
    del snapshot["model_fingerprint"]
    twin = pretium.Engine(seed=11, universe=SMALL, model=CUSTOM)
    twin.restore_state(snapshot)
    assert twin.prices() == parent.prices()


def test_an_engine_batch_member_is_the_standalone_custom_engine():
    seeds = [7, 8]
    b = pretium.EngineBatch(seeds=seeds, universe=SMALL, model=CUSTOM)
    assert b.model_fingerprint == CUSTOM.fingerprint
    assert b.model == CUSTOM
    b.open_market()
    b.run_session(9, 30, 3, 30)
    rows = struct.unpack("<%dd" % (len(seeds) * len(SMALL)), b.prices())
    for i, seed in enumerate(seeds):
        alone = pretium.Engine(seed=seed, universe=SMALL, model=CUSTOM)
        alone.open_market()
        alone.run_session(9, 30, 3, 30)
        expected = struct.unpack("<%dd" % len(SMALL), alone.prices())
        assert rows[i * len(SMALL):(i + 1) * len(SMALL)] == expected, seed

    assert pretium.EngineBatch(seeds=seeds,
                               universe=SMALL).model_fingerprint == DEFAULT
    with pytest.raises(pretium.ValidationError, match="model must be"):
        pretium.EngineBatch(seeds=seeds, universe=SMALL, model=0.12)
