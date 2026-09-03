"""Shared machinery for the calibration instrument (CALIBRATION.md §4).

Phase 2 builds three measurements on top of the phase-1 seam, and this
module is what they share:

- **The evaluation core**: one (parameter vector, seed) panel measurement
  through `tradefloor.facts.measure` under `Engine(model=...)`, in a worker
  process, with the two runtime assertions Appendix B specifies (distinct
  fingerprints per vector; `draws_consumed` equal across vectors per
  seed — the CRN guard that makes panel differences parameter effects
  rather than reshuffled noise).
- **The parameter surface**: the 31 runtime-settable coefficients with
  their deviation classes (§6.3: log for scale parameters, absolute for
  bounded shares and multiples), step-sizing units, search boxes, and the
  stationarity constraints the search must not leave.
- **The legacy vector**: the runtime reconstruction of the pre-fix model
  law — symmetric GARCH, constant-sigma market factor, unscaled
  idiosyncratic noise — used to test the instrument against the four
  verdicts that were measured by hand at the cost of wheel builds.

## The facts.measure shim, stated plainly

`facts.measure` builds its engine internally and does not (yet) take
`model=` — `facts.py` belongs to another stream. Until it grows the
argument, the worker substitutes the `Engine` symbol in `tradefloor.facts`
with a partial application that adds `model=`, calls the UNMODIFIED
`measure`, and restores the symbol in a `finally:`. Every statistic is
computed by the library's own code; only the constructor call is
intercepted. This is the same shim `eval_model_params.py` documents;
when `facts.measure(model=...)` lands, `evaluate_panel` collapses by
five lines.

## Deviation units and step sizes are two different things

The §4.3 SVD wants a dimensionless matrix: rows in units of each
statistic's own seed noise, columns in units of the §6.3 regulariser's
deviation measure — one LOG unit for scale parameters, one RAW unit for
bounded shares and multiples ("the units are comparable after the
transform"). Those units are fixed by the loss geometry and are not
free.

Step sizes are free, and are chosen against kink density (§4.2), not
against the deviation unit: a whole raw unit of `garch_alpha` is an
absurd step, and 5% of `price_hard_cap` is a fine one. Each parameter
therefore carries a `step_unit`; a secant at step parameter `h` brackets
the base value by `h * step_unit` in its own class's geometry, and the
difference quotient is then expressed per DEVIATION unit so every
Jacobian column means the same thing: panel movement per unit of
regularised deviation.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

#: The published panel method (facts.py, CALIBRATION.md §1).
PANEL_UNIVERSE_N = 40
PANEL_UNIVERSE_SEED = 111
PANEL_DAYS = 252

#: The eight statistics with real-market bands — the Jacobian's rows.
PANEL_STATS = (
    "annualised_vol_pct",
    "excess_kurtosis",
    "return_acf1",
    "abs_return_acf1",
    "cross_sectional_corr",
    "volume_abs_return_corr",
    "leverage_effect",
    "volume_change_acf1",
)

#: Extra numeric outputs of facts.measure, carried through the JSONs
#: because they are free and occasionally explanatory, never in the SVD.
SUPPLEMENTARY_STATS = (
    "skew",
    "abs_return_acf5",
    "abs_return_acf20",
)

#: §8's fixed training seed list: the thirty-seed panel the phase-2
#: baseline and s_k re-estimate are measured on.
TRAIN_SEEDS = tuple(range(101, 131))

#: The published six-seed panel (held out of any search).
PUBLISHED_SEEDS = (1, 2, 3, 4, 5, 6)

#: A thirty-seed block disjoint from `TRAIN_SEEDS`, for confirming that an
#: effect is a property of the MODEL rather than of the paths it was found
#: on.
#:
#: This exists because a candidate was declared shippable on a 13%
#: improvement that did not survive contact with other seeds. Measured on
#: four blocks, the gap read +0.1297 on the discovery block, then -0.0315,
#: +0.0209 and +0.0233 -- reversing sign once, and five times smaller
#: everywhere it was not found. Both the discovery sweep and its
#: "validation" used `TRAIN_SEEDS`, so re-measuring reproduced the same
#: fluctuation exactly and reported it as confirmation. It tested
#: reproducibility of the MEASUREMENT, not of the EFFECT.
#:
#: Thirty rather than six, because this axis has to detect a difference and
#: `PUBLISHED_SEEDS` has a quarter of the power for that job.
CONFIRM_SEEDS = tuple(range(201, 231))


# ---------------------------------------------------------------------------
# The parameter surface
# ---------------------------------------------------------------------------

#: Deviation class and step-sizing per runtime-settable parameter.
#:
#: kind "log": deviation is ln(theta / ship) (§6.3, scale parameters);
#:   a secant step of h brackets the value by exp(±h · step_unit) with
#:   step_unit 1.0, i.e. h is directly in log units.
#: kind "abs": deviation is theta − ship in raw units (§6.3, bounded
#:   shares and multiples); a secant step of h brackets the value by
#:   ±h · step_unit raw units. Shares carry step_unit 0.1 (a tenth of
#:   the unit interval); multiples carry half their shipped value so the
#:   step is proportionate.
#:
#: `box` is the search region for falsification: (lo, hi) in raw units,
#: roughly [1/4x, 4x] for log parameters (§6.3) and the natural hard
#: range for bounded ones, tightened where a stationarity constraint
#: lives (enforced separately by `feasibility_violation`).
PARAM_SPECS: dict[str, dict] = {
    # Added when the surface grew and PARAM_SPECS did not. Every one of
    # these was settable and unreachable by any search, because a missing
    # spec is a KeyError in `shipped_values()`. That is how the first crisis
    # search died one minute in.
    # The pt-v16-era dials (rounds 96-119). All four ship at zero and are
    # bounded shares/levels, so "abs" with steps matched to their measured
    # useful neighbourhoods (asym folded at 0.025, screened to 0.04; the
    # smooth measured at 3 and 10 days; the stock gain's premium-matching
    # value is ~7, see atlas ZERO_SHIPPED_RANGES).
    # The response-curve exponent ships at 2.0 (the vix-squared target).
    # Rounds 112-114 measured 2.15-3.5 with closed-form endpoint pinning;
    # every cell broke the lever, co-movement, or the p252 volume floor,
    # so the surveyed box brackets the shipped value tightly on purpose.
    "market_vol_vix_exponent": {"kind": "abs", "step_unit": 0.05, "hard_range": (1.0, 4.0)},
    "market_beta_down_asym": {"kind": "abs", "step_unit": 0.005, "hard_range": (0.0, 0.5)},
    "market_beta_down_asym_lag": {"kind": "abs", "step_unit": 0.005, "hard_range": (0.0, 0.5)},
    # A SHARE of the injected first moment, so its range is [0, 1] and
    # not an open coefficient: above 1.0 it would inject an upward
    # drift of its own, which is the defect inverted rather than a
    # richer model.
    "market_beta_down_asym_recentre": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # The SHARE of nominal output growth the valuation's earnings carry.
    # A share, so [0, 1] rather than an open coefficient: 1.0 holds the
    # earnings share of nominal output constant, which is the reading the
    # economy supports, and above it earnings outgrow output for ever.
    "earnings_nominal_growth": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # A SHARE of demand, so [0, 1]. Above 1.0 supply outruns demand every
    # day and inventory ramps upward instead of downward, which is the
    # defect inverted.
    "oil_supply_response": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # A SHARE of the asymmetry removed, so [0, 1]. Past 1.0 the branches
    # cross and the rule pushes the other way.
    "oil_opec_symmetry": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # WHERE the seasonal shape acts, as a SHARE of its own amplitude, so
    # [0, 1]. 0.0 puts all of it on the price level, where it compounds,
    # and 1.0 all of it on the reversion target. Past 1.0 the level carries
    # a negative amplitude, which is the shape inverted.
    "oil_seasonality_target": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # A SHARE of the hazard's unit correction, so [0, 1]. 0.0 draws a rate
    # whose scale is in months once a day, 1.0 reads it on the 30-day month
    # the phase clock keeps, and past 1.0 the cycle runs slower than its own
    # scale states.
    "cycle_hazard_per_month": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # The yield at which the target multiple sits on its sector anchor. A
    # LOG box like the other rate-like dials, and no explicit hard range,
    # because it ships at 0.04 rather than at zero and the calibration
    # convention's own [1/4x, 4x] gives 0.01 to 0.16. Both ends are outside
    # anything the economy reaches, so the box is wide rather than chosen.
    "neutral_discount_rate": {"kind": "log"},
    # A SHARE of the jump drift returned, so [0, 1]. 1.0 is the
    # martingale and past it the compensator overshoots.
    "jump_mean_compensated": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # A SHARE of the ladders' asymmetry removed, so [0, 1]. Past 1.0 they
    # cross and the upside ladder becomes the larger of the two.
    "cascade_symmetry": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    "market_vol_vix_smooth": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 60.0)},
    "qe_pe_stock_gain": {"kind": "abs", "step_unit": 0.5, "hard_range": (0.0, 20.0)},
    "universe_stress_weight": {"kind": "abs", "step_unit": 0.1, "hard_range": (0.0, 2.0)},
    "universe_stress_decay": {"kind": "abs", "step_unit": 0.02, "hard_range": (0.0, 0.995)},
    # Also a level rather than a share, and shipped at 0.0 -- which a log
    # deviation cannot express at all (log of zero). Kept "abs" for that
    # reason, with a step matched to its natural range so the raw deviation
    # stays the same order as everything else's.
    "regime_stress_points": {"kind": "abs", "step_unit": 1.0},
    # LOG, not abs. `deviation()` returns a RAW difference for "abs", and
    # this is a LEVEL with magnitude ~25 rather than a bounded share: moving
    # it to a box edge gives a deviation near 19, squared 372, times a
    # lambda of 10 -- roughly 3,700 of penalty against a realism loss of
    # order 1. The first search using it spent its whole budget minimising
    # my own regulariser. Scale parameters take the log class, the case the
    # log/raw split exists for.
    "crisis_vix_threshold": {"kind": "log"},
    # The dollar's own crisis gate, split from the crisis threshold at 0.4.3.
    # Same class and box as its sibling: it is a VIX level, so a raw deviation
    # penalty on it would be about the regulariser rather than the model.
    "usd_crisis_vix_threshold": {"kind": "log"},
    "vix_mean_reversion": {"kind": "abs", "step_unit": 0.02},
    # Fear-gap era: decay-side multiplier on the VIX mean reversion.
    # 1.0 ships (symmetric); real spike asymmetry says the decay side
    # runs slower, so the box reaches down to a fifth of the rate.
    "vix_decay_ratio": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.2, 1.5)},
    "vix_jump_intensity": {"kind": "abs", "step_unit": 0.5, "hard_range": (0.0, 24.0)},
    "vix_jump_scale": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 30.0)},
    "forced_flow_gain": {"kind": "abs", "step_unit": 0.0001, "hard_range": (0.0, 0.01)},
    "forced_flow_threshold": {"kind": "abs", "step_unit": 1.0, "hard_range": (20.0, 60.0)},
    "forced_flow_beta_exponent": {"kind": "abs", "step_unit": 0.25, "hard_range": (0.0, 3.0)},
    "forced_flow_reservoir": {"kind": "abs", "step_unit": 50.0, "hard_range": (0.0, 5000.0)},
    "forced_flow_replenish": {"kind": "abs", "step_unit": 0.01, "hard_range": (0.0, 0.5)},
    # The volatility feedback (§68). Real markets move the VIX 165 points
    # per unit return on a heavy down day against the shipped 25, so the
    # box reaches well past the shipped value rather than around it.
    # "log", not "abs": these are scale parameters an order of magnitude
    # above the panel's other values, and a raw deviation penalty on them
    # would be about the regulariser rather than the model (trap 4 in the
    # calibration notes).
    "vix_realised_vol_weight": {"kind": "abs", "step_unit": 0.05,
                                "hard_range": (0.0, 1.0)},
    "vix_return_gain": {"kind": "log", "hard_range": (1.0, 250.0)},
    "vix_return_source": {"kind": "abs", "step_unit": 0.1,
                          "hard_range": (0.0, 1.0)},
    "vix_cycle_amplitude": {"kind": "abs", "step_unit": 0.1,
                            "hard_range": (0.0, 2.0)},
    "vix_return_gain_up": {"kind": "log", "hard_range": (1.0, 250.0)},
    # The clamp's units follow `vix_return_source`: a FRACTION when the
    # channel reads the closing tick (shipped 0.03) and PERCENTAGE POINTS
    # when it reads the day (pt-v9 uses 15.0). One box has to hold both, so
    # it is log-scaled and spans three orders of magnitude.
    "vix_return_clamp": {"kind": "log", "hard_range": (0.005, 30.0)},
    "vix_target_shock_cap": {"kind": "log", "hard_range": (1.0, 70.0)},
    "inflation_reversion": {"kind": "abs", "step_unit": 0.05},
    "inflation_ceiling": {"kind": "log"},
    # Negative-valued, so the multiplicative default box inverts. Real CPI
    # year-on-year bottomed at -0.2 in 2015-2025 and -2.0 in 2009 (FRED
    # CPIAUCSL), and the shipped clamp is -1.0, so the box spans a floor
    # that never binds to one deeper than any modern deflation.
    "inflation_floor": {"kind": "abs", "step_unit": 0.5,
                        "hard_range": (-4.0, 0.0)},
    "news_peer_weight": {"kind": "abs", "step_unit": 0.05,
                         "hard_range": (0.0, 1.0)},
    "news_peer_weight_down": {"kind": "abs", "step_unit": 0.05,
                              "hard_range": (0.0, 1.0)},
    # A parameter that SHIPS AT 0.0 has no multiplicative box: `default_box`
    # returns (0.0, 0.0) and the search explores a single point while looking
    # exactly like a search that ran. §24 lost a 96-core run to that, and
    # these seven would have lost another -- every one of them ships inert on
    # pt-v1, and all seven are wanted for the jump/volume search.
    #
    # The ranges are the ones the 4000-vector Atlas survey actually explored,
    # so the box is traceable to a measurement rather than invented here.
    "jump_intensity_market": {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 0.25)},
    "jump_intensity_idio": {"kind": "abs", "step_unit": 0.01,
                            "hard_range": (0.0, 0.25)},
    "jump_mean_market": {"kind": "abs", "step_unit": 0.01,
                         "hard_range": (-0.08, 0.0)},
    "jump_sigma_market": {"kind": "abs", "step_unit": 0.01,
                          "hard_range": (0.0, 0.08)},
    "jump_sigma_idio": {"kind": "abs", "step_unit": 0.01,
                        "hard_range": (0.0, 0.08)},
    # How much of a jump the herding term continues. 1.0 is every shipped
    # preset and is what couples the 504-day tail to 252-day return
    # autocorrelation; 0.0 lets a jump fatten the tail without being
    # amplified into continuation. The full unit interval is the whole
    # mechanism, so the hard range is the whole interval.
    "jump_momentum_share": {"kind": "abs", "step_unit": 0.1,
                            "hard_range": (0.0, 1.0)},
    # Cross-sectional spread in volatility persistence, raw beta units. The
    # upper bound is the headroom to the GJR persistence ceiling on pt-v6:
    # 0.97 - alpha 0.0595 - gamma/2 0.0916 leaves beta room to 0.819 against
    # a shipped 0.685, so 0.15 is about the widest spread that does not spend
    # the clamp on every large name.
    "garch_beta_dispersion": {"kind": "abs", "step_unit": 0.02,
                              "hard_range": (0.0, 0.15)},
    # A blend weight over its whole domain; 0.1 steps span it in ten.
    "fair_value_book_floor": {"kind": "abs", "step_unit": 0.1,
                              "hard_range": (0.0, 1.0)},
    # Two blend weights over their whole domain (§60, CRISIS-BLEND-SECTOR.md).
    "crisis_blend_source": {"kind": "abs", "step_unit": 0.1,
                            "hard_range": (0.0, 1.0)},
    "sector_vix_coupling": {"kind": "abs", "step_unit": 0.1,
                            "hard_range": (0.0, 1.0)},
    "volume_persistence": {"kind": "abs", "step_unit": 0.05,
                           "hard_range": (0.0, 0.99)},
    "volume_innovation_sigma": {"kind": "abs", "step_unit": 0.05,
                                "hard_range": (0.0, 0.6)},
    "size_effect_smoothness": {"kind": "abs", "step_unit": 0.1, "hard_range": (0.0, 1.0)},
    "size_effect_exponent": {"kind": "abs", "step_unit": 0.02},
    "spread_size_smoothness": {"kind": "abs", "step_unit": 0.1, "hard_range": (0.0, 1.0)},
    "spread_size_exponent": {"kind": "abs", "step_unit": 0.05},
    "market_vol_slow_vix_damp": {"kind": "abs", "step_unit": 0.1, "hard_range": (0.0, 1.0)},
    # -- scale parameters (log deviation) ---------------------------------
    "market_factor_sigma":      {"kind": "log"},
    "sector_factor_sigma":      {"kind": "log"},
    "idio_sigma_scale":         {"kind": "log"},
    "garch_omega":              {"kind": "log"},
    "market_vol_vix_anchor":    {"kind": "log"},
    "mispricing_half_life_days": {"kind": "log"},
    "crash_amplifier_slope":    {"kind": "log"},
    "crash_amplifier_threshold": {"kind": "log"},
    "crisis_blend_ramp":        {"kind": "log"},
    "crowd_lean_cap":           {"kind": "log"},
    "crowd_momentum_gain":      {"kind": "log"},
    "crowd_valuation_gain":     {"kind": "log"},
    "order_flow_coefficient":   {"kind": "log"},
    "price_hard_cap":           {"kind": "log"},
    # -- bounded shares (absolute deviation, step_unit 0.1) ---------------
    "garch_alpha":              {"kind": "abs", "step_unit": 0.1},
    "garch_beta":               {"kind": "abs", "step_unit": 0.1},
    "garch_gamma":              {"kind": "abs", "step_unit": 0.1},
    "momentum_theta":           {"kind": "abs", "step_unit": 0.1},
    "market_vol_alpha":         {"kind": "abs", "step_unit": 0.1},
    "market_vol_beta":          {"kind": "abs", "step_unit": 0.1},
    # The market factor's GJR leverage. The same quantity as
    # `garch_gamma` one level up, so the same spec: a bounded share
    # measured in absolute deviation. It shipped without one, which
    # made every guard that walks the settable surface fail and put
    # the parameter out of reach of any search.
    # `hard_range` as well as a step, which `garch_gamma` does not need
    # because it ships non-zero: a parameter shipped at 0.0 has no
    # multiplicative box, so the range has to be stated.
    #
    # 2.0 is the parameter's own domain boundary, not a taste: at
    # gamma = 2 the leverage term alone consumes the whole persistence
    # budget and alpha + beta + gamma/2 reaches 1 with both other terms
    # at zero. What actually binds is the stationarity check below, and
    # the transformed box tops out at 1.896 -- so a ceiling of 1.0,
    # as this said first, would have rejected planned vectors
    # for leaving a range narrower than the sampler.
    "market_vol_gamma":         {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 2.0)},
    "market_vol_vix_coupling":  {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    # The slow variance component (pt-v4). All three ship at 0.0, so the
    # multiplicative [1/4x, 4x] box collapses on them and the hard range is
    # what a search actually gets -- see `calibration_box`.
    "market_vol_slow_persistence": {"kind": "abs", "step_unit": 0.1,
                                    "hard_range": (0.0, 0.999)},
    "market_vol_slow_gain":     {"kind": "abs", "step_unit": 0.05,
                                 "hard_range": (0.0, 0.5)},
    "market_vol_slow_weight":   {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 2.0)},
    "volume_variance_gain":     {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 8.0)},
    "informed_flow_fraction":   {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    "news_market_weight":       {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    "news_sector_weight":       {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    "crisis_blend_cap":         {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    "mispricing_cap":           {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    "price_breaker_fraction":   {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 1.0)},
    # -- bounded multiples (absolute deviation, proportionate step) -------
    # The four variance clamps carry an EXPLICIT hard range, and the reason
    # is a defect that survived every calibration this project has run.
    #
    # `calibrate.calibration_box` reads `spec.get("hard_range", (0.0, 0.999))`
    # -- a default that is correct for a bounded SHARE and wrong for a
    # MULTIPLE. For a ceiling shipped at 8.0 it computed
    # `hi = min(8 * 4, 0.999) = 0.999` and `lo = max(8 / 4, 0.0) = 2.0`, so
    # LOW EXCEEDED HIGH. Probed through `DevSpace.repair`, every search
    # coordinate from -2.0 to +2.0 mapped to one identical point: the
    # parameter was FROZEN rather than searched, at a value produced by
    # clamping the shipped 8.0 into an inverted box.
    #
    # That is why `ptv4`'s certificate reported `market_vol_ceiling_multiple`
    # = 2.0 "driven to its box floor". Nothing drove it. The clamp put it
    # there before the first search step ran, and CALIBRATION-FOLLOWUPS §16
    # attributed a halved crisis lever to an optimiser trade that never
    # happened. §24 retracts that.
    #
    # The ranges below are floors and ceilings on a variance clamp expressed
    # as a multiple of the long-run level, so the honest bound is "wide
    # enough to contain any defensible clamp, narrow enough that a search
    # cannot disable the clamp entirely". A ceiling below 1.0 would put the
    # cap under the mean it is a multiple of; a floor above 1.0 would do the
    # reverse. The ordering constraint between floor and ceiling is enforced
    # separately, by the repair step in `calibrate.py`.
    # LOG, not "abs", and the reclassification is forced by the ranges above.
    #
    # §6.3's rule of thumb is "log for scale parameters, absolute for bounded
    # shares and multiples", and these were classed "abs" on the strength of
    # the word "multiples". That was harmless only while the default hard
    # range silently clipped their boxes below 1.0, which made them behave
    # like bounded shares. Give them the honest ranges above and the raw
    # deviation carries their magnitude straight into a squared penalty: a
    # ceiling shipped at 8.0 with a box to 32 reads a deviation of 24,
    # squared 576, against a median squared box deviation of 0.998. The
    # penalty-dominance guard in `tests/test_model_params.py` caught it at
    # 576x and 225x the median.
    #
    # §6.3's INTENT is that deviation units be comparable across parameters,
    # and a quantity spanning [2, 32] is a scale. Under log the same box-edge
    # move costs ln(32/8)^2 = 1.92, the same as every other scale parameter
    # here. Following the rule's letter would have reproduced, in a new
    # place, exactly the mis-scaled penalty that made a 96-core search
    # optimise its own regulariser.
    "garch_vix_coupling": {"kind": "abs", "step_unit": 0.05,
                          "hard_range": (0.0, 1.0)},
    # How much a jump's arrival RATE follows the VIX (§84). A share like the
    # other couplings, so the hard range is the unit interval.
    "jump_vix_coupling": {"kind": "abs", "step_unit": 0.05,
                          "hard_range": (0.0, 1.0)},
    # How hard a crisis loads names onto the market factor (§97). 0.5 was
    # the literal; the range reaches 2.0 to give headroom above the old
    # ceiling of 0.5 x 0.98.
    "crisis_blend_gain": {"kind": "abs", "step_unit": 0.05,
                          "hard_range": (0.0, 2.0)},
    # How far the crisis injection is decoupled from the market factor's
    # magnitude (§80). A damping exponent applied as |factor/base|^-d, so its
    # hard range is the unit interval: 0 is the shipped behaviour and 1
    # removes the dependence entirely. Measured across that span, crisis
    # co-movement's level falls from +0.002 off centre to -0.139, so the far
    # end is known to be broken and the step is small enough to find an
    # interior optimum if one exists.
    "crisis_blend_variance_damp": {"kind": "abs", "step_unit": 0.05,
                                   "hard_range": (0.0, 1.0)},
    # Re-assert the credit spread floors on every daily step (#48). Shipped at
    # 0.0 on every preset, which is the reference implementation's behaviour
    # and the one that lets an investment-grade yield sit under the risk-free
    # curve for months between meetings. 1.0 enforces both floors in full. The
    # unit interval is the whole domain: it scales a floor, so past 1.0 it
    # would be inventing a spread rather than defending one.
    "daily_credit_floor_gain": {"kind": "abs", "step_unit": 0.1,
                                "hard_range": (0.0, 1.0)},
    # Per-name idiosyncratic volatility as beta^k (§47). Zero is the shipped
    # behaviour; 3 is where the 504-day panel collapses (§71), and the
    # interquartile volatility ratio is still rising there, so the hard range
    # spans in-band to broken rather than stopping at the useful part.
    "idio_sigma_beta_exponent": {"kind": "abs", "step_unit": 0.1,
                                 "hard_range": (0.0, 3.0)},
    # Gain on the QE valuation channel (§76). 1.0 is the shipped behaviour --
    # this is the only macro channel that had no gain -- and 0 disables it.
    # The upper edge is 2.0 for symmetry about the shipped value, not because
    # anything has measured there.
    "qe_pe_gain": {"kind": "abs", "step_unit": 0.05,
                   "hard_range": (0.0, 2.0)},
    # Endogenous company news (§101). Intensity is a per-day probability, so
    # 0.25 is roughly one event a week per name, already a chatty market.
    "endogenous_news_intensity": {"kind": "abs", "step_unit": 0.01,
                                  "hard_range": (0.0, 0.25)},
    "endogenous_news_sigma": {"kind": "abs", "step_unit": 0.005,
                              "hard_range": (0.0, 0.10)},
    # How much harder news transfers to a peer in a crisis (§105). A
    # multiplier on the spike, and the spike is capped at ~0.98, so a
    # coupling of 8 is about a ninefold crisis weight. Wide on purpose: the
    # calm panel cannot see this dial at all.
    "news_peer_vix_coupling": {"kind": "abs", "step_unit": 0.25,
                               "hard_range": (0.0, 8.0)},
    # Per-name volume persistence (§107). Same shape as the common pair.
    # The sector loading and its beta slope (§108).
    "sector_loading": {"kind": "abs", "step_unit": 0.05,
                       "hard_range": (0.0, 1.5)},
    "sector_loading_beta_slope": {"kind": "abs", "step_unit": 0.05,
                                  "hard_range": (0.0, 1.0)},
    # Volume following a NAME's own conditional variance (§112). Same shape
    # as volume_variance_gain, which does this for the market factor.
    "volume_idio_variance_gain": {"kind": "abs", "step_unit": 0.05,
                                  "hard_range": (0.0, 2.0)},
    "volume_idio_persistence": {"kind": "abs", "step_unit": 0.05,
                                "hard_range": (0.0, 0.99)},
    "volume_idio_sigma": {"kind": "abs", "step_unit": 0.02,
                          "hard_range": (0.0, 0.6)},
    # The variance cascade (§122). `components` is a COUNT: integer-valued,
    # so its step is one whole component and a search that lands between two
    # is truncated by the engine rather than interpolated.
    "garch_cascade_components": {"kind": "abs", "step_unit": 1.0,
                                 "hard_range": (0.0, 8.0)},
    "garch_cascade_ratio": {"kind": "log", "step_unit": None,
                            "hard_range": (1.5, 8.0)},
    "garch_cascade_weight": {"kind": "abs", "step_unit": 0.05,
                             "hard_range": (0.0, 1.0)},
    # The volume-move expression (§113); ships at the old literals.
    # LOG, not abs: the cap ships at 4.0 with a box out to 30, and a raw
    # difference of 26 squared dominates a regulariser whose median squared
    # box deviation is order 0.01. It is a positive scale (a percentage move),
    # so the log ratio is also the honest classification, not a workaround.
    # test_no_parameter_dominates_the_deviation_penalty caught this.
    "volume_move_cap": {"kind": "log", "step_unit": None,
                        "hard_range": (1.0, 30.0)},
    "volume_move_floor": {"kind": "abs", "step_unit": 0.05,
                          "hard_range": (0.0, 2.0)},
    "volume_move_noise": {"kind": "abs", "step_unit": 0.02,
                          "hard_range": (0.0, 1.0)},
    "volume_move_response": {"kind": "abs", "step_unit": 0.05,
                             "hard_range": (0.0, 2.0)},
    "garch_ceiling_multiple":   {"kind": "log",
                                 "hard_range": (1.0, 50.0)},
    "garch_floor_multiple":     {"kind": "log",
                                 "hard_range": (0.001, 1.0)},
    "market_vol_ceiling_multiple": {"kind": "log",
                                    "hard_range": (1.0, 50.0)},
    "market_vol_floor_multiple": {"kind": "log",
                                  "hard_range": (0.001, 1.0)},
}

#: §3.9's searched set — the identifiability filter the SVD tests.
SEARCHED_9 = (
    "market_factor_sigma",
    "sector_factor_sigma",
    "idio_sigma_scale",
    "garch_alpha",
    "garch_beta",
    "garch_omega",
    "garch_ceiling_multiple",
    "garch_floor_multiple",
    "momentum_theta",
)

#: The runtime reconstruction of the PRE-FIX model law, for testing the
#: instrument against the hand-measured verdicts:
#:
#: - `market_vol_alpha = market_vol_beta = market_vol_vix_coupling = 0`
#:   makes the factor-variance update return exactly the baseline
#:   variance every close (omega = 1.0 * base; both dynamic terms zero;
#:   the clamp cannot bind at 1x base) — the market factor is again iid
#:   Gaussian at constant sigma, the finding-14 regime.
#: - `garch_gamma = 0` restores the symmetric GARCH (the gamma branch
#:   adds +0.0, documented bit-inert), with alpha/beta at the values the
#:   symmetric model shipped (0.09 / 0.90).
#: - `idio_sigma_scale = 1.0` removes the funding reallocation
#:   (bit-inert multiply), and `market_factor_sigma = 0.0075` is the
#:   value the constant-sigma model shipped.
#:
#: Two caveats, named rather than hidden: `crisis_vix_threshold` is
#: preset-carried but compile-time (25.5 today, 40 in the old model), so
#: the crisis blend arms at a lower VIX here than it did — inert in
#: practice for 252-day endogenous runs, which `endogenous_vix_ceiling`
#: measures rather than assumes; and the KAT era has moved since the
#: hand sweeps, so per-seed realisations differ while the process law is
#: the same — legacy medians are compared to the committed sweep medians
#: as estimates of one law, not bit-for-bit.
LEGACY_OVERRIDES: dict[str, float] = {
    "market_vol_alpha": 0.0,
    "market_vol_beta": 0.0,
    "market_vol_vix_coupling": 0.0,
    "garch_gamma": 0.0,
    "garch_alpha": 0.09,
    "garch_beta": 0.90,
    "idio_sigma_scale": 1.0,
    "market_factor_sigma": 0.0075,
}


def shipped_values() -> dict[str, float]:
    """The pt-v1 values of every settable parameter, read from the wheel."""
    import tradefloor

    full = tradefloor.ModelParams.from_preset("pt-v1").to_dict()
    return {name: float(full[name]) for name in PARAM_SPECS}


def step_unit(name: str, base_value: float) -> float:
    """The step-sizing unit for `name` around `base_value` (see header)."""
    spec = PARAM_SPECS[name]
    if spec["kind"] == "log":
        return 1.0
    unit = spec.get("step_unit")
    if unit is None:  # a multiple: proportionate step
        return abs(base_value) / 2.0 if base_value else 0.5
    return unit


def bracket(name: str, base_value: float, h: float) -> tuple[float, float, float]:
    """The secant bracket around `base_value` at step parameter `h`.

    Returns (lo_value, hi_value, dev_distance) where dev_distance is the
    lo-to-hi distance in DEVIATION units (log units for scale
    parameters, raw units for bounded ones), i.e. the denominator of the
    central difference quotient.
    """
    import math

    spec = PARAM_SPECS[name]
    if spec["kind"] == "log":
        if base_value <= 0:
            raise ValueError(f"{name}: log-class parameter at {base_value}")
        lo = base_value * math.exp(-h)
        hi = base_value * math.exp(h)
        return lo, hi, 2.0 * h
    unit = step_unit(name, base_value)
    lo = base_value - h * unit
    hi = base_value + h * unit
    return lo, hi, 2.0 * h * unit


def default_box(name: str, ship: float) -> tuple[float, float]:
    """The falsification search box for `name` (§6.3's ~[1/4x, 4x] rule).

    Bounded shares get their natural hard range; stationarity couplings
    across parameters are enforced by `feasibility_violation`, not here.
    """
    spec = PARAM_SPECS[name]
    if spec["kind"] == "log":
        return ship / 4.0, ship * 4.0
    if "hard_range" in spec:
        return spec["hard_range"]
    if spec.get("step_unit") == 0.1:  # shares without an explicit range
        return 0.0, 0.999
    # multiples: [1/4x, 4x] like scales, in raw units
    return ship / 4.0, ship * 4.0


def feasibility_violation(vector: dict[str, float],
                          ship: dict[str, float]) -> str | None:
    """The named constraint a vector violates, or None if none.

    These are §6.3's hard bounds: the search must not leave the regime
    the library's stationarity claims cover. `ModelParams.from_preset`
    itself accepts anything numeric — measured, not assumed — so the
    instrument carries its own gate.
    """
    def val(name: str) -> float:
        return vector.get(name, ship[name])

    a, b, g = val("garch_alpha"), val("garch_beta"), val("garch_gamma")
    if min(a, b, g) < 0:
        return "garch alpha/beta/gamma must be non-negative"
    if a + b + g / 2.0 >= 1.0:
        return f"GJR stationarity: alpha+beta+gamma/2 = {a + b + g / 2.0:.4f} >= 1"
    # The market factor carries the same GJR asymmetry the per-name
    # process does, so it carries the same stationarity condition:
    # alpha + beta + gamma/2 < 1, not alpha + beta < 1. The gamma term
    # was missing here, and at the shipped market-vol persistence of
    # 0.989 there is under 0.011 of headroom, so a survey that set the
    # dial would have planned a non-stationary factor variance and this
    # gate would have passed it.
    ma, mb = val("market_vol_alpha"), val("market_vol_beta")
    mg = val("market_vol_gamma")
    if min(ma, mb, mg) < 0:
        return "market_vol alpha/beta/gamma must be non-negative"
    if ma + mb + mg / 2.0 >= 1.0:
        return ("factor-variance GJR stationarity: alpha+beta+gamma/2 = "
                f"{ma + mb + mg / 2.0:.4f} >= 1")
    if not 0.0 <= val("momentum_theta") < 1.0:
        return "momentum_theta must lie in [0, 1)"
    for name in ("market_factor_sigma", "sector_factor_sigma",
                 "idio_sigma_scale", "garch_omega"):
        if val(name) <= 0:
            return f"{name} must be positive"
    if val("garch_ceiling_multiple") <= val("garch_floor_multiple"):
        return "garch ceiling must exceed floor"
    if val("market_vol_ceiling_multiple") <= val("market_vol_floor_multiple"):
        return "market_vol ceiling must exceed floor"
    for name, spec in PARAM_SPECS.items():
        rng = spec.get("hard_range")
        if rng and not rng[0] <= val(name) <= rng[1]:
            return f"{name} must lie in [{rng[0]}, {rng[1]}]"
    return None


# ---------------------------------------------------------------------------
# The evaluation core
# ---------------------------------------------------------------------------

def evaluate_panel(job: tuple) -> dict:
    """One (vector, seed) panel evaluation, in a worker process.

    `job` is (overrides, seed, days, universe_n, universe_seed). Returns
    the full numeric panel plus the identity that makes the two runtime
    assertions checkable in the parent: the model fingerprint and the
    engine's total `draws_consumed`.
    """
    overrides, seed, days, universe_n, universe_seed = job
    import tradefloor
    import tradefloor.facts as facts

    model = tradefloor.ModelParams.from_preset("pt-v1", **overrides)
    universe = tradefloor.Universe.random(universe_n, seed=universe_seed)

    # The seam the header's caveat was waiting for: `facts.measure` takes
    # `model=` now, so the vector goes in through the library's own
    # argument and no symbol is substituted to deliver it. The Engine
    # substitution that remains does one thing the public surface still
    # does not expose — hand back the engine so `draws_consumed` can be
    # read for the CRN guard — and it adds nothing to the call.
    engines: list = []
    original = facts.Engine

    def engine_capture(**kwargs):
        engine = original(**kwargs)
        engines.append(engine)
        return engine

    started = time.perf_counter()
    facts.Engine = engine_capture
    try:
        panel = facts.measure(seed=seed, universe=universe, days=days,
                              model=model)
    finally:
        facts.Engine = original
    elapsed = time.perf_counter() - started

    engine = engines[0]
    assert engine.model_fingerprint == model.fingerprint, (
        "the engine ran a different model than the vector asked for — "
        "recording this panel would mislabel it"
    )
    assert panel["model_fingerprint"] == model.fingerprint, (
        "the panel reports a different model than the vector asked for"
    )
    numeric = {
        key: value for key, value in panel.items()
        if isinstance(value, (int, float)) and key != "seed"
    }
    return {
        "fingerprint": model.fingerprint,
        "overrides": overrides,
        "seed": seed,
        "seconds": elapsed,
        "draws_consumed": engine.draws_consumed,
        "draws_by_stream": dict(engine.draws_by_stream()),
        "panel": numeric,
    }


def run_pool(jobs: list[tuple], workers: int) -> list[dict]:
    """Evaluate jobs on a worker pool, order-preserving."""
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(evaluate_panel, jobs, chunksize=1))


def vector_key(overrides: dict[str, float]) -> str:
    """A canonical identity for a vector, for grouping results."""
    return json.dumps(overrides, sort_keys=True)


#: The stream whose draw count CRN actually rests on. See `crn_streams`.
CRN_STREAM = "market"


def crn_streams(results: list[dict]) -> dict:
    """Per-stream CRN analysis: what must not move, and what may.

    The 2026-08 stream split gave the engine three generators, and it
    made the §5.2 membership rule's operative quantity narrower than the
    rule's wording. `Engine.draws_by_stream`'s own docstring states the
    split's terms:

      the MARKET stream's schedule is a pure function of (market status,
      active roster, sector count) — nothing a preset can reach — so two
      runs with equal `market` counts consumed, and therefore saw, an
      identical market noise sequence;

      the ECONOMY stream's count "genuinely varies with macro state (a
      chain in contraction draws a shock the expansion never rolls)",
      and macro state is driven by the market's realised volatility and
      return, which every searched parameter moves.

    So `draws_consumed` — the TOTAL — cannot be invariant under a
    parameter change, and was never the right thing to assert. A guard on
    the total reports a violation of §5.2 every time a vector is extreme
    enough to reroute the macro chain, which is a real event about the
    economy and says nothing about whether the two markets saw the same
    noise. The market count is the sharp question, and this function raises
    on that one.

    Returns::

        {"market": {seed: draws},
         "economy_deviations": [...], "external_deviations": [...],
         "total_deviations": [...]}

    where each deviation names the seed, the reference and observed
    counts, and the overrides that produced them. Economy and external
    divergences are DATA, not failures: they are the documented coupling,
    and a certificate that records them lets a reader see the macro chain
    branch instead of inferring it from a silence.

    Raises only when the market stream moves, which would make every
    secant across that pair re-alignment noise rather than a parameter
    effect — exactly what this instrument exists not to measure.

    Rows measured before `evaluate_panel` recorded the split fall back to
    asserting on the total, which is the older and stricter claim.
    """
    split = all("draws_by_stream" in row for row in results)
    by_seed: dict[int, dict[str, dict]] = {}
    for row in results:
        by_seed.setdefault(row["seed"], {})[vector_key(row["overrides"])] = row

    market: dict[int, int] = {}
    deviations: dict[str, list[dict]] = {
        "economy_deviations": [], "external_deviations": [],
        "total_deviations": [],
    }
    for seed, rows in sorted(by_seed.items()):
        reference = next(iter(rows.values()))

        def count(row: dict, stream: str) -> int:
            if stream == "total" or not split:
                return row["draws_consumed"]
            return row["draws_by_stream"][stream]

        guarded = CRN_STREAM if split else "total"
        distinct = {key: count(row, guarded) for key, row in rows.items()}
        if len(set(distinct.values())) != 1:
            raise AssertionError(
                f"seed {seed}: {guarded} draw counts differ across vectors — "
                f"{distinct} — a parameter moved the draw schedule"
            )
        market[seed] = next(iter(distinct.values()))

        for stream, bucket in (("economy", "economy_deviations"),
                               ("external", "external_deviations"),
                               ("total", "total_deviations")):
            if stream != "total" and not split:
                continue
            base = count(reference, stream)
            for key, row in rows.items():
                observed = count(row, stream)
                if observed != base:
                    deviations[bucket].append({
                        "seed": seed, "stream": stream, "expected": base,
                        "observed": observed, "delta": observed - base,
                        "overrides": row["overrides"]})
    return {"market": market, "guarded_stream": CRN_STREAM if split else
            "total (per-stream counts unavailable)", **deviations}


def assert_crn(results: list[dict]) -> dict[int, int]:
    """The CRN guard, on the stream it actually rests on.

    Thin wrapper over `crn_streams` for callers that only want the
    {seed: draws} map and the assertion. See `crn_streams` for why the
    market stream and not the total is the quantity §5.2's rule is about.
    """
    return crn_streams(results)["market"]


def group_by_vector(results: list[dict]) -> dict[str, list[dict]]:
    """Results grouped by vector identity, seed order preserved."""
    grouped: dict[str, list[dict]] = {}
    for row in results:
        grouped.setdefault(vector_key(row["overrides"]), []).append(row)
    return grouped


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

def provenance() -> dict:
    """Where and when a result was measured — every JSON carries this."""
    here = Path(__file__).resolve().parent
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=here,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=here,
            capture_output=True, text=True, check=True,
        ).stdout.strip() != ""
    except Exception:
        rev, dirty = "unknown", True
    return {
        "date": _dt.date.today().isoformat(),
        "git_rev": rev + ("-dirty" if dirty else ""),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


def write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"written: {path}")
