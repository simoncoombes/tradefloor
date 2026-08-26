"""The Atlas survey: every settable parameter, measured, not searched.

Six calibration searches were rejected in two days, and the mechanical
pattern is now understood: a scalar objective picks a trade for you and
hides it, so the optimiser sells whatever the objective cannot see. Every
one of those searches was a flashlight in an unsurveyed room. This driver
runs the survey -- `pretium.atlas` -- over the whole settable surface at
both certified horizons plus the two scenario gates, so the next search
direction is read off a map instead of guessed.

What it measures, per sampled vector:

- the ten-statistic realism panel at 252 days AND at 504 days, six seeds
  each under common random numbers (same seed list for every vector, so a
  cross-vector difference at a fixed seed is a parameter effect);
- `loss_252` / `loss_504` / `loss` via `pretium.loss.dual_horizon_loss`,
  which scores each horizon against ITS OWN bands and seed sds
  (`REAL_MARKETS`+`SEED_SD` at 252, `REAL_MARKETS_504`+`SEED_SD_504` at
  504). Pairing one horizon's measurement with the other's ruler is a
  mistake this project made three separate times; the loss function is
  used precisely because it cannot make it;
- the two scenario gates -- `shock_ratio_median` and `vol_lever` (plus
  `corr_blend`, free from the same rows) -- through the same `_held_job` /
  `_shock_job` workers `scenario_response.measure_vector` runs, aggregated
  by the same `scenario_response.aggregate`, so the numbers cannot drift
  from the published instrument's.

Six seeds is a SCREENING resolution and every artifact this writes says
so: on the shipped preset, eight of ten panel statistics have their
across-seed p10-p90 range crossing a band edge, so medians at six seeds
rank and describe -- they do not certify. Thirty seeds, never three (nor
six), for verdicts.

# Which parameters, and over what ranges

All 54. The survey exists because nobody has measured which parameters
matter; pre-selecting the ones that "obviously" do would bake the guess
this tool replaces into the tool itself, and under Latin hypercube
sampling extra axes cost nothing per point.

Ranges are the design decision, and they are all in this file, each with
its reason. The defaults follow the calibration convention (`calibrate.
calibration_box`: ~[1/4x, 4x] around pt-v3, intersected with hard
ranges), with three classes of deliberate exception:

- **The 19 parameters shipped inert at 0.0** have no multiplicative box
  (`pretium.atlas` refuses to invent one) and get explicit ranges in
  `ZERO_SHIPPED_RANGES`, each justified in place.
- **The crisis dials get ranges far past the convention.** The most
  recent 96-core search found nothing because the [1/4x, 4x] box capped
  `crisis_blend_ramp` at 5.6 while the best known value is 6.0 -- outside
  it. Its surveyed range reaches 50, where the mechanism is effectively
  off (endogenous VIX tops out near 26.6, so a 50-point ramp above the
  25.5 threshold never gets past a few percent of the blend), so the map
  spans saturating-to-disabled and can show whether there is an interior
  optimum. `crisis_blend_cap` spans its whole (0, 1) hard range.
- **The two variance processes are sampled in (persistence, share)
  coordinates**, not raw alpha/beta: pt-v3's market-factor persistence
  (0.98906) sits exactly at the identifiability cap, so independent boxes
  around raw `market_vol_alpha` and `market_vol_beta` that contain the
  preset put roughly half their joint mass across the stationarity line
  -- measured on a candidate plan, not estimated -- and every rejection
  dents the Latin stratification the sample's value rests on. Persistence
  is also the quantity every finding about these processes is stated in
  (half-lives, the persistence cap), so the map answers in the vocabulary
  the decisions use. `vector_to_params` is the (pure, invertible)
  translation; the stationarity gate `instrumentlib.feasibility_violation`
  still runs on every translated vector, and a violation is recorded as
  that vector's result rather than measured -- a sweep once ran two
  non-stationary vectors and reported the best as the day's result.

# Running it

    python atlas_survey.py plan                       # inspect, cost, gate
    python atlas_survey.py run --out results/atlas-2026-08 --workers 96
    python atlas_survey.py collect --out results/atlas-2026-08

`run` is resumable and streams: every finished task is appended to
`tasks.jsonl` and fsynced BEFORE progress is printed (persist before you
print -- two result files were lost to a dead-man switch firing before
collection). Kill it anywhere; re-running resumes ONLY under the same
plan fingerprint -- a changed configuration is refused, and so are rows
whose `meta.json` is missing, because rows are keyed by plan index and
must never outlive the plan that names them. A failed task records its
failure with a kind: a model refusal is deterministic and final, while an
infrastructure failure (OOM, dead worker) is re-runnable with
`--retry-errors` -- left final, those cluster in the expensive corners
and quietly bias the marginals against a region. `collect` builds a
usable `atlas-survey.json` + `atlas-report.txt` from whatever is on disk,
so the survey is readable mid-flight. Progress lines are timestamped;
silence longer than a few task-lengths IS the hang indicator.

Cost, measured (one M-series laptop core, 2026-08-24): panel-252 ~10s,
panel-504 ~20s, held-VIX ~2.5s, shock ~0.3s; about 4.2 core-minutes per
vector, so 4,000 vectors is ~280 core-hours -- roughly 3 hours on the
96-core box (inferred from the laptop measurement, not measured there).

Seeds are the first six TRAINING seeds (101-106). The published seeds
(1-6) stay out: they are held out of every search, and although Atlas is
not a search, its map will steer searches -- reading the held-out seeds
into it would quietly spend their independence.
"""

from __future__ import annotations

import argparse
import hashlib
import dataclasses
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import instrumentlib as lib               # noqa: E402
import scenario_response as sr            # noqa: E402
from calibrate import calibration_box     # noqa: E402

import pretium                            # noqa: E402
from pretium import atlas                 # noqa: E402
from pretium.facts import REAL_MARKETS    # noqa: E402

#: Measured by `facts.measure`, judged by nothing yet, recorded per horizon
#: as `<stat>_<days>` beside the thirteen. See CALIBRATION-FOLLOWUPS.md §64.
DIAGNOSTIC_STATS = ("corr_persistence_acf1",)
from pretium.loss import dual_horizon_loss  # noqa: E402

#: Ranges centred here. The vectors themselves set every parameter, so the
#: pt-v1 base inside `instrumentlib.evaluate_panel` and the scenario jobs
#: is fully overridden -- calibration trap #1 ("the search builds from
#: pt-v1") cannot bite a survey that leaves nothing to the base.
BASE_PRESET = "pt-v3"
#: A restriction of the survey to named axes, or None for all of them. Set
#: from `--only`. Every parameter NOT surveyed is pinned at BASE_PRESET's
#: value inside each vector, so a task still sets every parameter and the
#: pt-v1 base inside `instrumentlib.evaluate_panel` never shows through
#: (calibration trap #1). Added 2026-08-25 for the §62 sector surface on the
#: pt-v6 base.
ONLY: tuple[str, ...] | None = None

#: Screening seeds: the first six TRAINING seeds. See the module header
#: for why the published held-out seeds are not used.
SCREEN_SEEDS = lib.TRAIN_SEEDS[:6]

HORIZONS = (252, 504)
DEFAULT_SAMPLES = 4000
DEFAULT_PLAN_SEED = atlas.DEFAULT_SEED

#: Explicit ranges for the 19 parameters shipped at exactly 0.0. Each is a
#: modelling decision, made here ON PURPOSE rather than defaulted --
#: `atlas.axes_for` refuses to invent a box around zero because the choice
#: decides what the map can see. Sources: the perturbation table in
#: `tests/test_model_params.py` (values proven to move the market),
#: `instrumentlib.PARAM_SPECS` hard ranges, and the mechanism's own units.
ZERO_SHIPPED_RANGES: dict[str, tuple[float, float]] = {
    # The VIX's feedback weight (§68): a share, so its box is the unit
    # interval. At 1.0 the loop gain is exactly one by construction, since
    # the implied VIX is the forward coupling's own inverse, so the top of
    # the box is a boundary worth sampling rather than an arbitrary cap.
    "vix_realised_vol_weight": (0.0, 1.0),
    # A name's own variance following the VIX (§78).
    "garch_vix_coupling": (0.0, 1.0),
    # A jump's arrival RATE following the VIX (§84). A share, so the unit
    # interval is the whole domain: at 1.0 the rate is the shipped rate times
    # (vix / 15)^2, which at a pinned VIX 65 is about eighteen times as many
    # jump days and at VIX 5 about a ninth.
    "jump_vix_coupling": (0.0, 1.0),
    # Endogenous news (§101). Both ship at zero, so both need explicit
    # ranges. Intensity to 0.25 is about one event a week per name; sigma to
    # 0.10 spans a 10% surprise, which is a large earnings move.
    "endogenous_news_intensity": (0.0, 0.25),
    "endogenous_news_sigma": (0.0, 0.10),
    # Crisis contagion (§105). Ships at zero so it needs an explicit range,
    # and the interesting region is well above zero because the spike it
    # multiplies is below one.
    "news_peer_vix_coupling": (0.0, 8.0),
    # How much of the business cycle's VIX swing survives (§77). A scale on
    # the phase VIX around its mean, so the unit interval is the whole
    # domain and both ends are shipped positions rather than guesses: every
    # preset through pt-v8 ran 1.0, pt-v9 ran 0.6, and pt-v10 ships 0.0,
    # having moved the volatility regime onto what the market does itself.
    "vix_cycle_amplitude": (0.0, 1.0),
    # Which return the VIX reads: a share between the last tick and the day.
    "vix_return_source": (0.0, 1.0),
    # Jumps. Intensity is per-day probability: 0.25 is roughly one jump a
    # week, already a violent market; the perturbation table's 1.0 proves
    # wiring, not plausibility. Market jump mean is signed and surveyed
    # NEGATIVE-to-zero -- market-wide jumps model crashes; an upward-jump
    # regime is a different mechanism nobody has proposed.
    "jump_intensity_market": (0.0, 0.25),
    "jump_intensity_idio": (0.0, 0.25),
    "jump_mean_market": (-0.08, 0.0),
    "jump_sigma_market": (0.0, 0.08),
    "jump_sigma_idio": (0.0, 0.08),
    # Volatility-persistence spread across names. Ships at zero, so no
    # multiplicative box exists; the range is the headroom to the GJR
    # persistence ceiling.
    "garch_beta_dispersion": (0.0, 0.15),
    # Fraction of the loss-maker book floor applied to profitable companies.
    # A blend weight, so the range is the whole domain by definition. 1.0 is
    # the monotonic valuation; anything between is a partial floor.
    "fair_value_book_floor": (0.0, 1.0),
    # Where the crisis blend takes from, and whether sector variance follows
    # VIX. Both blend weights, whole domain (§60, CRISIS-BLEND-SECTOR.md).
    "crisis_blend_source": (0.0, 1.0),
    "sector_vix_coupling": (0.0, 1.0),
    # News peer transfer: weights of a peer's surprise, natural unit range.
    "news_peer_weight": (0.0, 1.0),
    "news_peer_weight_down": (0.0, 1.0),
    # VIX points added by a non-expansion regime. From the resting VIX of
    # 15, +20 crosses the 25.5 crisis gate with margin; beyond that the
    # survey would mostly measure a permanently-in-crisis market.
    "regime_stress_points": (0.0, 20.0),
    # Blend weights between tiered and continuous size/spread curves.
    "size_effect_smoothness": (0.0, 1.0),
    "spread_size_smoothness": (0.0, 1.0),
    # Universe stress memory: a decay of 0.995 is a 138-day half-life,
    # about the longest a 252-day window can read as a rate rather than a
    # level (the persistence-cap reasoning, applied as a range).
    "universe_stress_decay": (0.0, 0.995),
    "universe_stress_weight": (0.0, 2.0),
    # The persistent volume component; innovation 0.3 is proven live, the
    # range doubles it.
    "volume_persistence": (0.0, 0.99),
    "volume_innovation_sigma": (0.0, 0.6),
    # A damping fraction.
    "market_vol_slow_vix_damp": (0.0, 1.0),
    # The pt-v4 slow-variance component and volume-variance coupling:
    # PARAM_SPECS declares their hard ranges; survey all of each.
    "market_vol_slow_persistence": (0.0, 0.999),
    "market_vol_slow_gain": (0.0, 0.5),
    "market_vol_slow_weight": (0.0, 2.0),
    "volume_variance_gain": (0.0, 8.0),
}

#: Ranges chosen against the convention on purpose. The crisis dials are
#: the ones whose default box already cost a 96-core run -- see the module
#: header. Both known-good values (ramp 6.0, cap 0.98) are asserted inside
#: these ranges at plan time.
EXPLICIT_RANGES: dict[str, tuple[float, float]] = {
    # The sector draw's sigma. Shipped 0.002 gives a [1/4x, 4x] box topping
    # out at 0.008, and §59 measured the band reached at 0.012 and overshot
    # at 0.020, so the box is the range that can see the answer.
    "sector_factor_sigma": (0.0, 0.02),
    # The crisis market-factor gain (§97). Shipped 0.5, and the [1/4x, 4x]
    # convention box would top out at 2.0 anyway; stated explicitly because
    # the interesting region is ABOVE the shipped value, not around it.
    "crisis_blend_gain": (0.25, 2.0),
    # The idiosyncratic scale. Its [1/4x, 4x] box around 0.81 spans 0.2 to
    # 3.3, most of which is annualised volatility far outside any band; §60
    # measured 0.65 to 0.81 as the region where the trim is a trade rather
    # than a wreck, so the range is the one a retrim can use.
    "idio_sigma_scale": (0.5, 1.0),
    # The market factor's variance clamp, in multiples of the target. pt-v10
    # ships 32, whose [1/4x, 4x] convention box tops out at 128 against a
    # hard range of [1.0, 50.0]: a third of a 3000-vector plan planned above
    # the cap and was thrown away as infeasible before it measured anything.
    # The box is the shipped value's lower quarter up to the cap itself.
    "market_vol_ceiling_multiple": (8.0, 50.0),
    # The fear channel's two bounded quantities (§70, §71). Their
    # multiplicative default boxes run past the hard ranges the calibration
    # specs give them, which the stationarity gate then rejects one vector
    # at a time; naming the box here makes every sampled point feasible.
    #
    # The clamp is in PERCENT on both paths, which is the unit bug §70 found:
    # pt-v3's 0.03 clamps the fear channel at three basis points where the
    # arithmetic reads as though it were three percent, and pt-v9's 15.0 is
    # fifteen points. One box has to contain both, so it is log-scaled across
    # four decades, and the low half of it maps the market with no fear
    # channel at all rather than being wasted.
    "vix_return_clamp": (0.005, 30.0),
    "vix_target_shock_cap": (10.0, 70.0),
    # The market factor's GARCH memory (§64), in the survey's own transformed
    # axes (persistence is alpha + beta, alpha_frac is alpha's share, so
    # every point maps to a stationary process). Shipped 0.989 and 0.473
    # has no fourth moment (3a^2 + 2ab + b^2 = 1.42); the textbook region is
    # persistence 0.95 to 0.995 at a share of 0.05 to 0.15. Narrower than
    # the published TRANSFORMED_AXES box on purpose.
    "market_vol_persistence": (0.90, 0.998),
    "market_vol_alpha_frac": (0.03, 0.55),
    # The factor's calm sigma. A freed memory raises the factor's typical
    # variance (the heavy-tailed process spent most days below its mean),
    # so the level has to be able to come down: half of shipped to 1.5x.
    "market_factor_sigma": (0.006, 0.024),
    # Market jumps take over the tails the old alpha was making. Shipped
    # sigma 0.0024 is a quarter-percent jump; real index jumps run to a few
    # percent, so the box reaches 3 percent; it starts at zero because pt-v3
    # ships jumps off and every range must contain its base.
    "jump_sigma_market": (0.0, 0.03),
    "crisis_blend_ramp": (0.35, 50.0),
    "crisis_blend_cap": (0.0, 1.0),
    # Ships at 1.0, so it has a multiplicative box in principle, and that box
    # is wrong: it is a share on the unit interval and doubling it is not a
    # defined thing to ask for. The whole interval IS the mechanism. At 1.0 a
    # jump is a re-rating that momentum_theta continues, which is what
    # couples 504-day kurtosis to 252-day return autocorrelation. At 0.0 the
    # jump moves the momentum reference with it, so herding never sees it.
    "jump_momentum_share": (0.0, 1.0),
}


#: Lags the decay exponent is fitted over. The same three the panel already
#: measures, so the slope costs no extra simulation: it is a pure function
#: of numbers every surveyed vector already produces.
DECAY_LAGS = (1, 5, 20)


def decay_slope(panel_medians: dict[str, float], days: int) -> float | None:
    """Log-log slope of the `|r|` autocorrelation through lags 1, 5 and 20.

    A power law is a straight line on log-log axes and a sum of exponentials
    bends. Real markets read about -0.436 over these lags; the shipped preset
    reads about -0.95 (CALIBRATION-FOLLOWUPS §54, §56).

    This exists because the survey measured the region containing the answer
    and could not report it. `garch_persistence` spans (0.21, 0.99) in
    TRANSFORMED_AXES, and a vector at 0.94 moves the slope more than a third
    of the way to real markets, but the decay exponent is not one of the ten
    panel statistics, so four thousand samples were scored and filed without
    it. An outcome nobody records cannot be optimised, and looks immovable
    however much of the space is covered (§56a).

    None when any of the three autocorrelations is non-positive, which
    happens at long lags in a fast-decaying vector and has no logarithm.
    """
    pts = []
    for lag in DECAY_LAGS:
        v = panel_medians.get(f"abs_return_acf{lag}_{days}")
        if v is None or v <= 0.0:
            return None
        pts.append((math.log(lag), math.log(v)))
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den == 0.0:
        return None
    return sum((x - mx) * (y - my) for x, y in pts) / den


#: The five raw parameters replaced by (persistence, share) axes.
REPARAMETERISED = ("garch_alpha", "garch_beta", "garch_gamma",
                   "market_vol_alpha", "market_vol_beta")

#: The replacement axes. Ships (pt-v3): garch persistence 0.8364 with
#: alpha fraction 0.0711 and gamma/2 fraction 0.1095; market-vol
#: persistence 0.98906 (the identifiability cap exactly) with alpha share
#: 0.4731. Every range contains its ship; every point in the box maps to a
#: stationary, non-negative coefficient set by construction (fractions sum
#: below one), which is what buys back the ~half of the raw-coordinate box
#: the stationarity gate would have rejected.
TRANSFORMED_AXES = (
    atlas.Axis("garch_persistence", 0.21, 0.99),
    atlas.Axis("garch_alpha_frac", 0.01, 0.50),
    atlas.Axis("garch_gamma_frac", 0.0, 0.45),
    atlas.Axis("market_vol_persistence", 0.25, 0.998),
    atlas.Axis("market_vol_alpha_frac", 0.05, 0.95),
)


def vector_to_params(vector: dict[str, float]) -> dict[str, float]:
    """Axis coordinates -> the raw 54-parameter override dict.

    garch_alpha = frac_a * p; garch_gamma = 2 * frac_g * p;
    garch_beta = p * (1 - frac_a - frac_g), so alpha + beta + gamma/2 = p
    exactly. Same shape for the market-vol pair. Pure, so a survey row is
    reconstructible from its parameters without this process's state.
    """
    p = dict(vector)
    pg = p.pop("garch_persistence")
    af = p.pop("garch_alpha_frac")
    gf = p.pop("garch_gamma_frac")
    p["garch_alpha"] = af * pg
    p["garch_gamma"] = 2.0 * gf * pg
    p["garch_beta"] = pg * (1.0 - af - gf)
    mp = p.pop("market_vol_persistence")
    ms = p.pop("market_vol_alpha_frac")
    p["market_vol_alpha"] = ms * mp
    p["market_vol_beta"] = (1.0 - ms) * mp
    return p


def params_to_vector(params: dict[str, float]) -> dict[str, float]:
    """The inverse of `vector_to_params`, for placing presets on the map."""
    v = {k: float(v) for k, v in params.items()
         if k not in REPARAMETERISED}
    a, b, g = (float(params[k]) for k in
               ("garch_alpha", "garch_beta", "garch_gamma"))
    pg = a + b + g / 2.0
    v["garch_persistence"] = pg
    v["garch_alpha_frac"] = a / pg
    v["garch_gamma_frac"] = (g / 2.0) / pg
    ma, mb = float(params["market_vol_alpha"]), float(params["market_vol_beta"])
    v["market_vol_persistence"] = ma + mb
    v["market_vol_alpha_frac"] = ma / (ma + mb)
    return v


def survey_axes() -> list[atlas.Axis]:
    """The 54 survey axes, deterministic order, every range accounted for."""
    settable = pretium.ModelParams.settable()
    ship = pretium.ModelParams.from_preset(BASE_PRESET).to_dict()
    zeros = {n for n in settable if float(ship[n]) == 0.0}
    # These guards are raises, not asserts, on purpose: they are the same
    # registry-drift class this project added tests for AFTER a missing
    # PARAM_SPECS entry killed a 96-core search, and an assert vanishes
    # under `python -O` -- which is exactly the kind of flag a "fast"
    # production launch might add.
    #
    # Only the UNRANGED direction is an error. A base other than pt-v3 ships
    # some of these nonzero (pt-v6 carries jumps), and for those the explicit
    # zero range is simply not used: the convention box around the base's
    # value takes over below.
    unranged = zeros - set(ZERO_SHIPPED_RANGES) - set(EXPLICIT_RANGES)
    if unranged:
        raise RuntimeError(
            "the zero-shipped set moved and ZERO_SHIPPED_RANGES did not: "
            f"unranged {sorted(unranged)}. Choose ranges on purpose; atlas "
            "refuses to guess them.")
    # The stale direction is checked against the union of the presets a
    # survey is actually based on, rather than against pt-v3 alone. pt-v3 was
    # the only base when this guard was written; the 2026-08-26 era boundary
    # moved the default to pt-v10, which ships `vix_cycle_amplitude` at zero
    # where pt-v3 ships it at one. Pinning the check to one preset would
    # either refuse a legitimate range or force a base-specific registry.
    zero_on_any = set()
    for preset in ("pt-v3", pretium.ModelParams.from_preset().fingerprint):
        d = pretium.ModelParams.from_preset(preset).to_dict()
        zero_on_any |= {n for n in settable if float(d[n]) == 0.0}
    stale = set(ZERO_SHIPPED_RANGES) - zero_on_any
    if stale:
        raise RuntimeError(
            "stale ZERO_SHIPPED_RANGES entries, zero on neither pt-v3 nor "
            f"the shipped default: {sorted(stale)}")

    raw = sorted(n for n in settable if n not in REPARAMETERISED)
    ranges: dict[str, tuple[float, float]] = {}
    for name in raw:
        spec = lib.PARAM_SPECS[name]
        if name in EXPLICIT_RANGES:
            ranges[name] = EXPLICIT_RANGES[name]
        elif name in ZERO_SHIPPED_RANGES and float(ship[name]) == 0.0:
            ranges[name] = ZERO_SHIPPED_RANGES[name]
        elif spec["kind"] == "abs" and spec.get("step_unit") is None:
            # A bounded MULTIPLE (the garch/market-vol ceiling and floor
            # multiples). `calibration_box` clips every "abs" parameter to
            # a default hard range of (0, 0.999) that was written for
            # shares, which for a ceiling shipped at 5.0 yields the empty
            # box (1.25, 0.999) -- so the convention box is applied
            # directly, as `instrumentlib.default_box`'s multiples branch
            # does. Not fixed in calibrate.py here: that box arithmetic
            # backs committed certificates, and re-scoping it is not this
            # survey's call.
            ranges[name] = (float(ship[name]) / 4.0, float(ship[name]) * 4.0)
        else:
            ranges[name] = calibration_box(name, float(ship[name]))
        # A multiplicative box around a NEGATIVE base value comes out
        # inverted, (base/4, base*4) with base*4 the smaller number. pt-v3
        # ships no negative coefficient, so the full survey never saw this;
        # pt-v6 ships jump_mean_market at -0.85 percent and did.
        lo, hi = ranges[name]
        if lo > hi:
            ranges[name] = (hi, lo)
    log = tuple(n for n in raw
                if lib.PARAM_SPECS[n]["kind"] == "log" and ranges[n][0] > 0)
    axes = atlas.axes_for(raw, preset=BASE_PRESET, ranges=ranges, log=log)
    # A transformed axis keeps its published box unless EXPLICIT_RANGES
    # names it: §64's survey wants the market factor's memory sampled where
    # the answer can be, not across the 0.25 to 0.90 of persistence that
    # every earlier search has already scored as a wreck.
    axes += [dataclasses.replace(a, low=EXPLICIT_RANGES[a.name][0],
                                 high=EXPLICIT_RANGES[a.name][1])
             if a.name in EXPLICIT_RANGES else a
             for a in TRANSFORMED_AXES]
    if ONLY is not None:
        known = {a.name for a in axes}
        unknown = sorted(set(ONLY) - known)
        if unknown:
            raise RuntimeError(f"--only names axes that do not exist: {unknown}; "
                               f"axes are {sorted(known)}")
        axes = [a for a in axes if a.name in ONLY]

    # The specific failure this file exists to prevent, checked rather
    # than remembered: the best known crisis vector must be inside the box.
    by_name = {a.name: a for a in axes}
    for name, best in (("crisis_blend_ramp", 6.0), ("crisis_blend_cap", 0.98)):
        if name not in by_name:
            continue
        a = by_name[name]
        if not a.low <= best <= a.high:
            raise RuntimeError(
                f"{name} range ({a.low}, {a.high}) excludes the best known "
                f"value {best} -- the crisisearch4 failure, again")
    # And the preset itself must be on the map, or nothing found on it can
    # be anchored to the model actually being run.
    ship_vector = params_to_vector({n: ship[n] for n in settable})
    for a in axes:
        v = ship_vector[a.name]
        if not a.low <= v <= a.high:
            raise RuntimeError(
                f"{a.name} range ({a.low}, {a.high}) excludes the "
                f"{BASE_PRESET} value {v}")
    return axes


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def task_list(index: int, overrides: dict[str, float]) -> list[dict]:
    """The 48 measurement tasks for one vector: 12 panels, 30 held, 6 shock."""
    tasks: list[dict] = []
    for days in HORIZONS:
        for seed in SCREEN_SEEDS:
            tasks.append({"id": f"{index}:panel{days}:{seed}",
                          "index": index, "kind": "panel", "days": days,
                          "seed": seed, "overrides": overrides})
    for vix in sr.HELD_VIX:
        for seed in SCREEN_SEEDS:
            tasks.append({"id": f"{index}:held{vix:g}:{seed}",
                          "index": index, "kind": "held", "vix": vix,
                          "seed": seed, "overrides": overrides})
    for seed in SCREEN_SEEDS:
        tasks.append({"id": f"{index}:shock:{seed}",
                      "index": index, "kind": "shock", "seed": seed,
                      "overrides": overrides})
    return tasks


def error_kind(exc: BaseException) -> str:
    """Which of two very different things an exception is.

    "model" -- the library refused the vector's region (its ValidationError
    / OrderError). Deterministic: re-running reproduces it, so resume may
    treat it as final, and it is a finding about the model.

    "infrastructure" -- the MACHINE failed (out of memory, a dead worker,
    an interrupted syscall). Not a property of the vector, except that it
    correlates with one: OOMs cluster in the expensive high-volatility,
    high-jump corners, so treating these as final quietly biases every
    marginal against exactly a REGION while the drop tally looks benign.
    `--retry-errors` re-runs these.

    Everything else is "unclassified" and retried with infrastructure,
    because the safe default for an unknown failure is to look again.
    """
    if isinstance(exc, (pretium.ValidationError, pretium.OrderError)):
        return "model"
    if isinstance(exc, (MemoryError, OSError, TimeoutError,
                        ConnectionError)):
        return "infrastructure"
    return "unclassified"


def completed_ids(rows: list[dict], retry_errors: bool = False) -> set[str]:
    """The task ids a resume may skip.

    Successes and infeasible markers are always done. Errored tasks are
    done ONLY when they are model refusals or the caller did not ask to
    retry: with `retry_errors`, infrastructure and unclassified failures
    are re-run, because a transient failure recorded as permanent is a
    dropped measurement wearing a legitimate-looking error string.
    """
    done = set()
    for row in rows:
        if (retry_errors and "error" in row
                and row.get("error_kind", "unclassified") != "model"):
            continue
        done.add(row["id"])
    return done


def run_task(task: dict) -> dict:
    """One task, in a worker process. Never raises: an exception is the
    task's RESULT (a region that breaks the model is a fact about the
    model), and a raise here would take the whole pool's future with it.
    The result carries `error_kind` so a model refusal and a machine
    failure -- which deserve opposite treatment on resume -- never blur."""
    started = time.perf_counter()
    out = {"id": task["id"], "index": task["index"], "kind": task["kind"],
           "seed": task["seed"]}
    try:
        if task["kind"] == "panel":
            row = lib.evaluate_panel((task["overrides"], task["seed"],
                                      task["days"], lib.PANEL_UNIVERSE_N,
                                      lib.PANEL_UNIVERSE_SEED))
            out.update(days=task["days"], panel=row["panel"],
                       fingerprint=row["fingerprint"],
                       draws_by_stream=row["draws_by_stream"])
        elif task["kind"] == "held":
            vix, seed, p = sr._held_panel(
                (task["overrides"], task["vix"], task["seed"]))
            out.update(vix=vix, vol=p["annualised_vol_pct"],
                       corr=p["cross_sectional_corr"],
                       sector_ex=p["sector_excess_corr"],
                       kurt=p["excess_kurtosis"])
        elif task["kind"] == "shock":
            seed, ratio = sr._shock_job((task["overrides"], task["seed"]))
            out.update(ratio=ratio)
        else:
            raise ValueError(f"unknown task kind {task['kind']!r}")
    except Exception as exc:  # noqa: BLE001 - the error IS the result
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["error_kind"] = error_kind(exc)
    out["seconds"] = round(time.perf_counter() - started, 3)
    return out


# ---------------------------------------------------------------------------
# The streamed result file
# ---------------------------------------------------------------------------

def append_row(handle, row: dict) -> None:
    """One JSON line, flushed AND fsynced before returning: persist before
    you print. Two result files died with the instance that held them."""
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def read_rows(path: Path) -> list[dict]:
    """Every complete row of a tasks.jsonl; a torn final line (the run was
    killed mid-write) is dropped with a warning rather than poisoning the
    resume."""
    rows: list[dict] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"warning: dropping unparseable line {lineno} of "
                      f"{path} (a torn write from a killed run)")
    return rows


def plan_fingerprint(axes, samples: int, plan_seed: int) -> str:
    doc = {"axes": [[a.name, a.low, a.high, a.log] for a in axes],
           "samples": samples, "plan_seed": plan_seed,
           "base_preset": BASE_PRESET, "only": list(ONLY) if ONLY else None,
           "seeds": list(SCREEN_SEEDS), "horizons": list(HORIZONS),
           "panel_universe": [lib.PANEL_UNIVERSE_N, lib.PANEL_UNIVERSE_SEED],
           "gate_universe": [sr.UNIVERSE_N, sr.UNIVERSE_SEED]}
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def build_meta(axes, samples: int, plan_seed: int, fingerprint: str) -> dict:
    """The survey's identity, written once per outdir and verified on
    every resume. One constructor, shared with the tests that exercise
    `collect` against fabricated rows, so a drift between what `run`
    writes and what `collect` reads fails in CI rather than on a box."""
    return {
        "plan_fingerprint": fingerprint,
        "base_preset": BASE_PRESET,
        "only": list(ONLY) if ONLY else None,
        "samples": samples,
        "plan_seed": plan_seed,
        "seeds": list(SCREEN_SEEDS),
        "horizons": list(HORIZONS),
        "axes": [{"name": a.name, "low": a.low, "high": a.high,
                  "log": a.log} for a in axes],
        "panel_universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                          f"seed={lib.PANEL_UNIVERSE_SEED})",
        "gate_universe": f"Universe.random({sr.UNIVERSE_N}, "
                         f"seed={sr.UNIVERSE_SEED})",
        "provenance": lib.provenance(),
    }


def build_plan(samples: int, plan_seed: int):
    axes = survey_axes()
    vectors = atlas.plan(axes, samples, plan_seed)
    if ONLY is not None:
        # Complete every vector with the base preset's coordinates on the
        # axes not surveyed, so `vector_to_params` yields the full override
        # set and nothing falls through to evaluate_panel's pt-v1 base.
        base = pretium.ModelParams.from_preset(BASE_PRESET).to_dict()
        pinned = params_to_vector({n: base[n] for n in pretium.ModelParams.settable()})
        vectors = [dict({k: v for k, v in pinned.items() if k not in ONLY}, **vec)
                   for vec in vectors]
    ship = lib.shipped_values()
    feasibility = [lib.feasibility_violation(vector_to_params(v), ship)
                   for v in vectors]
    return axes, vectors, feasibility


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_plan(args) -> int:
    axes, vectors, feasibility = build_plan(args.samples, args.plan_seed)
    ship = pretium.ModelParams.from_preset(BASE_PRESET).to_dict()
    ship_vector = params_to_vector(
        {n: ship[n] for n in pretium.ModelParams.settable()})
    print(f"{'axis':30s} {'low':>12s} {'high':>12s} {'scale':>6s} "
          f"{BASE_PRESET:>12s}")
    for a in axes:
        print(f"{a.name:30s} {a.low:>12.6g} {a.high:>12.6g} "
              f"{'log' if a.log else 'lin':>6s} "
              f"{ship_vector[a.name]:>12.6g}")
    bad = sum(1 for f in feasibility if f)
    tasks = len(vectors) * (len(HORIZONS) * len(SCREEN_SEEDS)
                            + len(sr.HELD_VIX) * len(SCREEN_SEEDS)
                            + len(SCREEN_SEEDS))
    per_vector_s = (len(SCREEN_SEEDS) * (10 + 20)
                    + len(sr.HELD_VIX) * len(SCREEN_SEEDS) * 2.5
                    + len(SCREEN_SEEDS) * 0.3)
    core_hours = len(vectors) * per_vector_s / 3600.0
    print(f"\nvectors {len(vectors)}  infeasible {bad}  "
          f"tasks {tasks}  seeds {list(SCREEN_SEEDS)}")
    print(f"estimated ~{core_hours:.0f} core-hours "
          "(per-task timings measured on one laptop core 2026-08-24; "
          "inferred, not measured, for any other machine)")
    if bad:
        first = next((f, i) for i, f in enumerate(feasibility) if f)
        print(f"first violation: vector {first[1]}: {first[0]}")
    fingerprint = plan_fingerprint(axes, args.samples, args.plan_seed)
    print(f"plan fingerprint {fingerprint}")

    # Write the plan out when asked, so that a `run` at a DIFFERENT --samples
    # hits the fingerprint refusal in cmd_run instead of quietly measuring
    # something else.
    #
    # MEASURED, 2026-08-25: this printed a forecast and wrote nothing, and
    # `--samples` is one top-level argument that `run` re-reads from its own
    # default of 4000. So `plan --samples 1000 --out D` followed by `run --out
    # D` forecast "vectors 1000, tasks 48000, ~71 core-hours" and then ran 4000
    # vectors and 192000 tasks, four times the size, under a different plan
    # fingerprint printed one line apart in the same log.
    #
    # That is not only a wrong forecast. The operator sizes the dead-man switch
    # off it: the first survey launch was killed by `shutdown -h +90` at 63.9%
    # complete having been forecast at a quarter of what it ran. The refusal is
    # cheap and the guard for it already existed; it simply had nothing to
    # compare against.
    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        meta_path = outdir / "meta.json"
        if meta_path.exists():
            existing = json.loads(meta_path.read_text())
            if existing["plan_fingerprint"] != fingerprint:
                print(f"\nREFUSING to overwrite {meta_path}: it holds plan "
                      f"{existing['plan_fingerprint']} and this is {fingerprint}. "
                      "Use a fresh --out rather than mixing two plans in one "
                      "directory.")
                return 2
        else:
            meta_path.write_text(json.dumps(
                build_meta(axes, args.samples, args.plan_seed, fingerprint),
                indent=1, sort_keys=True) + "\n")
            print(f"wrote {meta_path}; `run` against a different --samples "
                  "will now refuse rather than measure a different plan")
    return 0


def cmd_run(args) -> int:
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    axes, vectors, feasibility = build_plan(args.samples, args.plan_seed)
    fingerprint = plan_fingerprint(axes, args.samples, args.plan_seed)

    meta_path = outdir / "meta.json"
    meta = build_meta(axes, args.samples, args.plan_seed, fingerprint)
    rows_exist = (outdir / "tasks.jsonl").exists()
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        if existing["plan_fingerprint"] != fingerprint:
            print(f"REFUSING to resume: {meta_path} was written for plan "
                  f"{existing['plan_fingerprint']}, this configuration is "
                  f"{fingerprint}. An index-based resume against a "
                  "different plan would silently mislabel every vector. "
                  "Use a fresh --out.")
            return 2
    elif rows_exist:
        # Measurements without the plan that produced them. The fingerprint
        # refusal above cannot fire, so writing a fresh meta here would adopt
        # every existing row as belonging to THIS configuration -- and the
        # rows are keyed by index, so vector 0's measurement would be
        # attributed to a different vector 0 entirely. Demonstrated during
        # review: deleting meta.json and re-running at a different sample
        # count adopted 48 rows and mislabelled them on 54 of 54 parameters.
        #
        # This is the `model_preset()` failure again -- a record describing a
        # thing that is no longer the thing -- and it needs exactly the
        # partial-retrieval mishap this project has already had twice.
        print(f"REFUSING to resume: {outdir / 'tasks.jsonl'} exists but "
              f"{meta_path} does not. The rows are keyed by plan index, so "
              "without the plan that produced them there is no way to tell "
              "which vector each measurement belongs to -- and adopting them "
              "under a fresh meta would mislabel every one. Restore "
              "meta.json, or use a fresh --out and re-measure.")
        return 2
    else:
        meta_path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")

    limit = args.limit if args.limit else len(vectors)
    rows_path = outdir / "tasks.jsonl"
    done = completed_ids(read_rows(rows_path),
                         retry_errors=args.retry_errors)

    pending: list[dict] = []
    n_infeasible = 0
    with open(rows_path, "a", encoding="utf-8") as sink:
        for index, vector in enumerate(vectors[:limit]):
            # Shard on the GLOBAL index so every shard agrees about which
            # vector is which, and a concatenation of their tasks.jsonl
            # files is exactly the whole run.
            if index % args.shard_n != args.shard_i:
                continue
            if feasibility[index]:
                n_infeasible += 1
                marker_id = f"{index}:infeasible"
                if marker_id not in done:
                    append_row(sink, {"id": marker_id, "index": index,
                                      "kind": "infeasible",
                                      "violation": feasibility[index]})
                    done.add(marker_id)
                continue
            overrides = vector_to_params(vector)
            pending += [t for t in task_list(index, overrides)
                        if t["id"] not in done]

        total = len(pending)
        shard = (f"shard {args.shard_i}/{args.shard_n} of "
                 if args.shard_n > 1 else "")
        print(f"plan {fingerprint}: {shard}{limit} vectors, {n_infeasible} "
              f"infeasible (recorded), {len(done)} tasks already done, "
              f"{total} to run on {args.workers} workers", flush=True)
        if not pending:
            print("nothing to do; collect can run now")
            return cmd_collect(args)

        started = time.time()
        finished = errors = 0
        last_print = 0.0
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_task, t) for t in pending]
            for future in as_completed(futures):
                row = future.result()
                append_row(sink, row)          # persisted...
                finished += 1
                if "error" in row:
                    errors += 1
                now = time.time()
                if now - last_print >= 30 or finished == total:
                    rate = finished / max(now - started, 1e-9)
                    eta_h = (total - finished) / max(rate, 1e-9) / 3600.0
                    stamp = time.strftime("%H:%M:%S")
                    print(f"[{stamp}] {finished}/{total} tasks "
                          f"({100.0 * finished / total:.1f}%)  "
                          f"{rate * 60:.1f}/min  eta {eta_h:.1f}h  "
                          f"errors {errors}  last {row['id']} "
                          f"in {row.get('seconds', 0):.1f}s",
                          flush=True)         # ...before printed
                    last_print = now
    print(f"run complete: {finished} tasks, {errors} errors")
    return cmd_collect(args)


def cmd_collect(args) -> int:
    outdir = Path(args.out)
    meta = json.loads((outdir / "meta.json").read_text())
    axes = [atlas.Axis(**a) for a in meta["axes"]]
    vectors = atlas.plan(axes, meta["samples"], meta["plan_seed"])
    if plan_fingerprint(axes, meta["samples"], meta["plan_seed"]) \
            != meta["plan_fingerprint"]:
        # A raise, not an assert: this is the guard against reading rows
        # under the wrong plan, and it must not vanish under `python -O`.
        raise RuntimeError(
            f"{outdir / 'meta.json'} does not reproduce its own plan "
            "fingerprint; the rows cannot be attributed to vectors")

    by_index: dict[int, dict[str, dict]] = {}
    for row in read_rows(outdir / "tasks.jsonl"):
        # Among successes, first write wins: a duplicated task (two runs
        # against one outdir) must not let a later measurement silently
        # replace an earlier one. The one sanctioned replacement is a
        # success over an error -- that is what `--retry-errors` appends,
        # and keeping the stale error over its retried measurement would
        # undo the retry.
        existing = by_index.setdefault(row["index"], {})
        prev = existing.get(row["id"])
        if prev is None or ("error" in prev and "error" not in row):
            existing[row["id"]] = row

    seeds = meta["seeds"]
    expected = (len(meta["horizons"]) * len(seeds)
                + len(sr.HELD_VIX) * len(seeds) + len(seeds))
    survey = atlas.Survey(axes=axes)
    pending = 0
    errors_by_kind: dict[str, int] = {}
    market_draws: dict[tuple, dict[int, int]] = {}
    for index, vector in enumerate(vectors):
        rows = by_index.get(index)
        if not rows:
            pending += 1
            continue
        marker = rows.get(f"{index}:infeasible")
        if marker:
            survey.record(index, vector,
                          error=f"infeasible: {marker['violation']}")
            continue
        failed = [r for r in rows.values() if "error" in r]
        if failed:
            # The kind travels into the survey row, and the row keeps its
            # full parameter vector -- so a reader can test the drops for
            # spatial clustering (errors() rows against the axes) instead
            # of trusting a benign-looking tally. An infrastructure
            # cluster in one corner of the space biases every marginal
            # near it.
            first = failed[0]
            kind = first.get("error_kind", "unclassified")
            errors_by_kind[kind] = errors_by_kind.get(kind, 0) + 1
            survey.record(index, vector,
                          error=f"[{kind}] {first['id']}: {first['error']}")
            continue
        if len(rows) < expected:
            pending += 1
            continue

        outputs: dict[str, float] = {}
        panels = {days: [rows[f"{index}:panel{days}:{s}"]["panel"]
                         for s in seeds] for days in meta["horizons"]}
        for days, batch in panels.items():
            for stat in REAL_MARKETS:
                outputs[f"{stat}_{days}"] = statistics.median(
                    p[stat] for p in batch)
            # Diagnostics: measured, unbanded, recorded so the map can see
            # them. Correlation persistence (§64) is the first.
            for stat in DIAGNOSTIC_STATS:
                vals = [p[stat] for p in batch if p.get(stat) is not None]
                if vals:
                    outputs[f"{stat}_{days}"] = statistics.median(vals)
        # Free: the slope is a pure function of acf1/5/20, which the panel
        # above already recorded. No extra simulation.
        for _d in meta["horizons"]:
            _sl = decay_slope(outputs, _d)
            if _sl is not None:
                outputs[f"decay_slope_{_d}"] = _sl
        loss = dual_horizon_loss(panels[252], panels[504])
        outputs["loss_252"] = loss["loss_252"]
        outputs["loss_504"] = loss["loss_504"]
        outputs["loss"] = loss["loss"]
        held = [(rows[f"{index}:held{vix:g}:{s}"]["vix"], s,
                 rows[f"{index}:held{vix:g}:{s}"]["vol"],
                 rows[f"{index}:held{vix:g}:{s}"]["corr"])
                for vix in sr.HELD_VIX for s in seeds]
        shock = [(s, rows[f"{index}:shock:{s}"]["ratio"]) for s in seeds]
        gates = sr.aggregate(held, shock, seeds)
        outputs["shock_ratio_median"] = gates["shock_ratio_median"]
        outputs["vol_lever"] = gates["vol_lever"]
        outputs["corr_blend"] = gates["corr_blend"]
        # The crisis state's sector excess, kurtosis and CO-MOVEMENT at VIX
        # 45. §62 named the first as the column the next survey needed;
        # co-movement was added after the crisis-survey run of §102, which
        # could not answer the question it was launched for because
        # cross-sectional correlation at VIX 45 was measured on every row
        # and never surfaced. `corr_blend` is a RATIO of crisis correlation
        # to calm, so a vector that raises calm correlation reads LOW on it
        # while having the higher crisis LEVEL, which is what a preset is
        # judged on. The level is what belongs here. Rows streamed by an
        # older driver lack these, so a resume without them is tolerated.
        for key, name in (("sector_ex", "sector_ex_45"), ("kurt", "kurt_45"),
                          ("corr", "xs_45")):
            vals = [rows[f"{index}:held45:{s}"].get(key) for s in seeds]
            vals = [v for v in vals if v is not None]
            if vals:
                outputs[name] = statistics.median(vals)
        survey.record(index, vector, outputs=outputs)

        for days in meta["horizons"]:
            for s in seeds:
                counts = rows[f"{index}:panel{days}:{s}"]["draws_by_stream"]
                market_draws.setdefault((days, s), {})[index] = \
                    counts.get(lib.CRN_STREAM, -1)

    # The CRN ledger. Across vectors at a fixed seed the market stream's
    # draw count should not move -- when it does, the vector changed the
    # active roster (a delisting under an extreme regime, say), and its
    # panel difference is partly re-aligned noise rather than purely a
    # parameter effect. In a survey that is DATA about the region, not a
    # failure, so it is counted and named rather than asserted on.
    deviating: set[int] = set()
    for key, counts in market_draws.items():
        modal = statistics.mode(counts.values())
        deviating.update(i for i, c in counts.items() if c != modal)

    survey.meta = {
        **{k: meta[k] for k in ("plan_fingerprint", "base_preset", "samples",
                                "plan_seed", "seeds", "horizons",
                                "panel_universe", "gate_universe",
                                "provenance")},
        "resolution": (
            f"{len(seeds)} seeds per vector is a SCREENING resolution: on "
            "the shipped preset eight of ten panel statistics have their "
            "across-seed p10-p90 range crossing a band edge, so these "
            "medians rank and describe -- they do not certify. Thirty "
            "seeds for any verdict."),
        "reparameterisation": (
            "garch_{persistence,alpha_frac,gamma_frac} and "
            "market_vol_{persistence,alpha_frac} replace the raw "
            "alpha/beta/gamma axes; atlas_survey.vector_to_params is the "
            "pure translation"),
        "vectors_pending": pending,
        "errors_by_kind": errors_by_kind,
        "crn_market_deviations": sorted(deviating)[:50],
        "crn_market_deviation_count": len(deviating),
    }
    out_path = outdir / "atlas-survey.json"
    survey.save(str(out_path))
    report_path = write_report(survey, outdir)
    prov = survey.provenance()
    print(f"written: {out_path}")
    print(f"written: {report_path}")
    print(f"rows {prov['rows']}  measured {prov['measured']}  "
          f"errors {prov['errors']}  pending {pending}  "
          f"crn deviations {len(deviating)}")
    return 0


def write_report(survey: atlas.Survey, outdir: Path) -> Path:
    """The first read of the map, written to disk before it is printed."""
    sections: list[str] = []

    def section(title: str, render) -> None:
        try:
            body = render()
        except Exception as exc:  # noqa: BLE001 - a refusal is the content
            body = f"not available: {exc}"
        sections.append(f"== {title} ==\n{body}")

    prov = survey.provenance()
    sections.append(
        f"atlas survey: {prov['measured']} measured of {prov['rows']} "
        f"recorded vectors ({prov['errors']} errors); "
        f"{survey.meta.get('resolution', '')}")
    for output in ("loss", "loss_252", "loss_504",
                   "shock_ratio_median", "vol_lever"):
        section(f"what moves {output}", lambda o=output: survey.explain(o))
    section(
        "the frontier: realism at both horizons against the crisis gates",
        lambda: survey.report_front({"loss_252": "min", "loss_504": "min",
                                     "shock_ratio_median": "max",
                                     "vol_lever": "max"}))

    def candidate_attribution() -> str:
        ship = pretium.ModelParams.from_preset(BASE_PRESET).to_dict()
        base = params_to_vector(
            {n: ship[n] for n in pretium.ModelParams.settable()})
        candidate = dict(base, crisis_blend_ramp=6.0, crisis_blend_cap=0.98)
        return survey.attribution(base, candidate, "loss")["summary"]

    section(f"attribution: {BASE_PRESET} -> the pt-v4 candidate "
            "(ramp 6.0, cap 0.98), on combined loss", candidate_attribution)

    path = outdir / "atlas-report.txt"
    path.write_text("\n\n".join(sections) + "\n")
    return path


def main() -> int:
    global BASE_PRESET, ONLY
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("plan", "run", "collect"))
    parser.add_argument("--out", default=None,
                        help="result directory (required for run/collect)")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--base", default=BASE_PRESET,
                        help="preset the ranges are centred on and every "
                             "unsurveyed parameter is pinned to (default: "
                             "%(default)s)")
    parser.add_argument("--only", default=None,
                        help="comma list of axes to survey; every other "
                             "parameter is pinned at --base's value inside "
                             "each vector, so a task still sets all of them")
    parser.add_argument("--plan-seed", type=int, default=DEFAULT_PLAN_SEED)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N vectors -- a smoke run "
                             "before committing a big box to the rest")
    parser.add_argument("--shard", default=None, metavar="I/N",
                        help="run only vectors where index %% N == I, so one "
                             "plan can span N boxes. The plan, its "
                             "fingerprint and the global vector indices are "
                             "identical on every shard, which is what lets "
                             "the tasks.jsonl files be concatenated and "
                             "collected as one run. Spot quota, not money, "
                             "is what caps N.")
    parser.add_argument("--retry-errors", action="store_true",
                        help="re-run tasks whose recorded failure was "
                             "infrastructure (OOM, dead worker) rather "
                             "than a model refusal. Transient failures "
                             "cluster in the expensive corners of the "
                             "space, so leaving them final biases the "
                             "marginals against a region.")
    args = parser.parse_args()
    args.shard_i, args.shard_n = 0, 1
    if args.shard:
        try:
            i, n = (int(x) for x in args.shard.split("/", 1))
        except ValueError:
            raise SystemExit(f"--shard wants I/N, got {args.shard!r}")
        if not (n >= 1 and 0 <= i < n):
            raise SystemExit(
                f"--shard {args.shard}: need N >= 1 and 0 <= I < N")
        args.shard_i, args.shard_n = i, n
    BASE_PRESET = args.base
    ONLY = tuple(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    if args.command != "plan" and not args.out:
        parser.error(f"{args.command} needs --out")
    return {"plan": cmd_plan, "run": cmd_run,
            "collect": cmd_collect}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
