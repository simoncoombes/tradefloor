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
    # WHERE the lagged wire's condition is sampled. A SWITCH, not a share:
    # the condition is read at the open or read live, and there is no half
    # sampling, so the step is the whole interval and a search either takes
    # the form or leaves it. The range stops at 1.0 on purpose -- 2.0 is the
    # registered SIGN CONTROL of `corr-asymmetry-repair.md` F4, a diagnostic
    # arm and never a shipping value, so no search may land on it.
    "market_beta_down_asym_lag_live": {"kind": "abs", "step_unit": 1.0,
                                       "hard_range": (0.0, 1.0)},
    # A SHARE of the injected first moment, so its range is [0, 1] and
    # not an open coefficient: above 1.0 it would inject an upward
    # drift of its own, which is the defect inverted rather than a
    # richer model.
    "market_beta_down_asym_recentre": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    "market_beta_down_asym_lag_recentre": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    "fair_value_market_linear": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0)},
    # The variance-neutral down-tick reallocation. Ships at 0.0, so the
    # multiplicative box collapses and the hard range is what a search gets.
    # The top is the construction's own domain rather than a taste: the down
    # scale is `1 - c`, so at 1.0 a down tick's idiosyncratic shock is
    # silenced entirely and the up tick's carries the whole budget at
    # `sqrt(2)`, and past it `1 - c` would go negative and FLIP the shock's
    # sign, which is not a reallocation of variance at all.
    # `idio_suppress_scales` clamps there, so the surface above 1.0 is flat
    # by construction and a search that walked into it would be reading a
    # plateau. The step is 0.01 because the registered curve is 0.05 apart
    # and the predicted solve sits near 0.15.
    "market_idio_down_suppress": {"kind": "abs", "step_unit": 0.01,
                                  "hard_range": (0.0, 1.0)},
    # The SHARE of nominal output growth the valuation's earnings carry.
    # A share, so [0, 1] rather than an open coefficient: 1.0 holds the
    # earnings share of nominal output constant, which is the reading the
    # economy supports, and above it earnings outgrow output for ever.
    "earnings_nominal_growth": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # A SWITCH, not a degree, and the box is [0, 1] only because the
    # registry needs one. The branch is at zero and every nonzero value
    # selects the same measured law, so this axis has two levels and a
    # search over it reports a step rather than a gradient. Deliberate:
    # the exponents either side of the knee are read off the cited
    # measurements, not fitted, and a tunable exponent is the defect
    # this dial exists to remove.
    "order_flow_impact_law": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0)},
    # Also a SWITCH, and the same box for the same reason. 0.0 denominates
    # the crash amplifier's shock in the BASELINE factor sigma and every
    # nonzero value denominates it in the tick's own CONDITIONAL sigma;
    # there is no half-normalised shock, so the axis has two levels and a
    # search over it reports a step. What chooses it is the VIX loop's
    # stability condition (`market::index_var`), not a panel row, which is
    # why a step is the right reading and a gradient would be a fiction.
    "crash_amplifier_conditional_sigma": {
        "kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0)},
    # THE THIRD TWO-LEVEL SWITCH, and the same reading applies. At 0.0 the
    # factor's variance target reads the VIX's LEVEL against a fixed anchor
    # and at every nonzero value it reads the VIX's EXCURSION above the
    # level the index's own conditional variance implies. There is no half
    # excursion: `engine.rs` branches at `== 0.0` and the two admissible
    # values are the two ends, so the axis has two levels and a search over
    # it reports a step. What chooses it is the loop's static gain -- the
    # variance arm of theta, about 0.45 of 0.62 -- and not a panel row.
    "market_vol_vix_excursion": {
        "kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0)},
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
    "trough_growth_floor": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    "phase_target_range_draw": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # The yield at which the target multiple sits on its sector anchor. A
    # LOG box like the other rate-like dials, and no explicit hard range,
    # because it ships at 0.04 rather than at zero and the calibration
    # convention's own [1/4x, 4x] gives 0.01 to 0.16. Both ends are outside
    # anything the economy reaches, so the box is wide rather than chosen.
    "neutral_discount_rate": {"kind": "log", "derived": True},
    # Days the economy is advanced alone before day zero. A COUNT, so an
    # absolute box, and its top is the horizon the burn-in that measured it
    # ran to: the transient table reaches 1095 days and the last field the
    # valuation reads enters its band at 755.
    "macro_burn_in_days": {"kind": "abs", "step_unit": 30.0,
                           "hard_range": (0.0, 1095.0)},
    # A SHARE of earnings, so [0, 1]. Past 1.0 a company returns more than
    # it earns every year, which is a claim about leverage this model does
    # not carry.
    "buyback_payout_share": {"kind": "abs", "step_unit": 0.05,
                             "hard_range": (0.0, 1.0)},
    # The overnight move's variance as a fraction of a session's. A RATIO
    # of variances, so an absolute box from zero; 1.0 is a night as large
    # as a session, and the real share of 0.23 to 0.43 sits well below it
    # even with the jump realised at the open.
    "overnight_variance_ratio": {"kind": "abs", "step_unit": 0.05,
                                 "hard_range": (0.0, 2.0)},
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
    # The VIX-dynamics dials (programme/results/vix-dynamics.md). Each is a
    # measured quantity with its own error bar, so the box is the measured
    # value's neighbourhood and not a search range: the tape decides them.
    # The level exponents are the response's power in the VIX (down
    # measured +0.49 +/- 0.12, up -0.85 +/- 0.12); the innovation sigmas are
    # fractions of the level per session (0.033, 0.018 per per cent); the
    # jump scale is in innovation-scale units (1.7) and the return-driven
    # rate in events a year per per cent of down session (6.2).
    "vix_return_level_exponent": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.5),
                                  "derived": True},
    "vix_return_exponent_up": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.2, 1.5)},
    "vix_return_level_exponent_up": {"kind": "abs", "step_unit": 0.05, "hard_range": (-2.0, 0.5),
                                     "derived": True},
    "vix_innovation_sigma": {"kind": "abs", "step_unit": 0.005, "hard_range": (0.0, 0.1)},
    "vix_innovation_return_sigma": {"kind": "abs", "step_unit": 0.005, "hard_range": (0.0, 0.06)},
    "vix_jump_level_scale": {"kind": "abs", "step_unit": 0.25, "hard_range": (0.0, 5.0)},
    "vix_jump_return_intensity": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 24.0),
                                  "derived": True},
    # The per-component states (vix-dynamics.md 19): measured on the
    # reference panel with their bars, so the boxes are the measurements'
    # neighbourhoods, not search ranges.
    "sector_vol_alpha": {"kind": "abs", "step_unit": 0.01, "hard_range": (0.0, 0.3)},
    "sector_vol_beta": {"kind": "abs", "step_unit": 0.02, "hard_range": (0.0, 0.99)},
    "jump_idio_excitation": {"kind": "abs", "step_unit": 0.25, "hard_range": (0.0, 5.0)},
    "jump_idio_excitation_decay": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 0.95)},
    "jump_idio_vix_decoupled": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0)},
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
    # The level identity is a SWITCH, not a quantity: 0.0 is the phase
    # table and 1.0 is the index's own variance, and a search that
    # landed on 0.4 would be running neither. The range is opened to
    # both ends so an atlas can reach the arm, with a step that walks
    # the whole interval in one move.
    "vix_level_identity": {"kind": "abs", "step_unit": 1.0,
                           "hard_range": (0.0, 1.0)},
    # The premium is MEASURED at 0.252 with a per-year IQR of 1.128 to
    # 1.398 on the ratio, so 0.128 to 0.398 here. The box is that IQR
    # widened to the P10-P90 of the rolling-window estimator (1.049 to
    # 1.473) rather than opened to anything a search finds comfortable:
    # outside it the value is not one the tape supports.
    "vix_variance_premium": {"kind": "abs", "step_unit": 0.02,
                             "hard_range": (0.049, 0.473)},
    "vix_return_gain": {"kind": "log", "hard_range": (1.0, 250.0)},
    "vix_return_source": {"kind": "abs", "step_unit": 0.1,
                          "hard_range": (0.0, 1.0)},
    "vix_cycle_amplitude": {"kind": "abs", "step_unit": 0.1,
                            "hard_range": (0.0, 2.0)},
    # UNDETERMINED, so it stays searchable -- and its box was simply WRONG.
    # (1, 250) was drawn when the up gain was 17.0 under a level-blind law.
    # On pt-v19 the up law is the RATIO form (`vix_return_level_exponent_up`
    # -1) and the gain's units changed with it: the dial ships at 0.049, over
    # an order below the old box's floor, so `feasibility_violation` refused
    # the SHIPPED pt-v19 vector on this line before anything else.
    #
    # The floor is the log convention's own 1/4x of 0.049. The TOP is left at
    # the old 250 rather than moved to 4x, for the reason `vix_return_clamp`
    # two entries down already carries: the dial's UNITS follow the law, one
    # box has to hold both laws, and pt-v1 (10.0), pt-v16 and pt-v18 (17.0)
    # are shipped presets the gate must keep accepting. A box of [0.012,
    # 0.196] -- the convention applied at both ends -- reads the pt-v19
    # neighbourhood correctly and makes three shipped presets infeasible,
    # which is a worse defect than the one it fixes. So: log-scaled, spanning
    # both eras, and the search's step is multiplicative so the width costs
    # nothing at either end.
    #
    # `vix-dynamics.md` 2.3 does NOT supply a bar for this. Its `gain_u`
    # column (0.0365 to 0.0683 per era) is the TAPE's up gain; the dial is
    # that net of the read-back at `vix_mean_reversion`, and the same note's
    # memory table runs the dial from 0.008 to 0.450 across the plausible
    # memory range. Neither is an error bar on the dial at a fixed memory.
    "vix_return_gain_up": {"kind": "log", "hard_range": (0.012, 250.0)},
    # The exponent is a SHAPE and its plausible span is narrow: the tape
    # puts it at 1.200 with a 95 per cent interval of 1.112 to 1.287 and a
    # count-weighted reading of 1.132. The hard range is opened to 1.0
    # (the linear form, so a search can always return to what shipped) and
    # to 2.0, well past anything the measurement supports, so the bound is
    # a guard rather than a prior. `abs` and not `log`, because the
    # interesting span is a few hundredths wide.
    "vix_return_exponent": {"kind": "abs", "step_unit": 0.02,
                            "hard_range": (1.0, 2.0)},
    # The crisis-fear pair, added with the dials themselves so no search
    # reaches them before a box exists.
    #
    # The ceiling's box is drawn around the two levels its own docstring
    # names rather than around the shipped 80.0: the real index closed at
    # 82.69 on 2020-03-16, and the derived response peaks at 93.0 driven
    # over that year with the ceiling off. A box from 60 to 100 holds both,
    # so a search can reach values that truncate the 2020 peak and values
    # that are inert on that tape, and the difference between them is
    # something it can measure rather than something the box decided.
    #
    # DERIVED, and off the search surface (2026-09-17). The box above is the
    # level-blind era's and it is not corrected, because correcting it would
    # not make the column mean anything. Inside (60, 100) the secant measures
    # CLIPPING: the record's measured maximum VIX is 60.59 at 504 days over
    # twelve rosters and 73.9 in the b4fix9 census, so every point in the box
    # truncates paths the shipped 181.3295 does not. Widening it to hold
    # 181.3295 makes the column zero by construction, which is the ceiling
    # entry's own B3 argument. Either way the secant measures clipping or
    # nothing, and sweeping the ceiling is a census experiment on a pin ladder.
    "vix_ceiling": {"kind": "abs", "step_unit": 1.0, "hard_range": (60.0, 100.0),
                    "derived": True},
    # Ships at 0.0, so the multiplicative default cannot serve it and the
    # range has to be explicit. It cancels a standing POSITIVE excursion an
    # asymmetric return gain puts on the target, so the useful side is
    # negative; the box is symmetric because the sign of the asymmetry is
    # not a constant of the model. On the 2020 tape the bias is 10.5 points
    # of excursion and 7.34 of level, so +-12 clears both with headroom.
    "vix_target_offset": {"kind": "abs", "step_unit": 0.5,
                          "hard_range": (-12.0, 12.0)},
    # The clamp's units follow `vix_return_source`: a FRACTION when the
    # channel reads the closing tick (shipped 0.03) and PERCENTAGE POINTS
    # when it reads the day (pt-v9 uses 15.0). One box has to hold both, so
    # it is log-scaled and spans three orders of magnitude.
    "vix_return_clamp": {"kind": "log", "hard_range": (0.005, 30.0)},
    #
    # DERIVED, and off the search surface (2026-09-17). On pt-v19 the cap IS
    # the down spike's supremum, `gain * clamp**p * 10**-g` = 158.85236, so a
    # column for it measures the panel's response to a cap sitting OFF its
    # supremum -- which is the response to re-introducing a shape parameter,
    # and any nonzero secant there measures the level-blind defect rather
    # than the model. The (1, 70) box does not even contain the shipped
    # value, and it is left alone for the same reason as the ceiling's.
    "vix_target_shock_cap": {"kind": "log", "hard_range": (1.0, 70.0),
                             "derived": True},
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
    # How much of the market jump's log return joins the day's factor
    # innovation. A share of one return, so the unit interval is the whole
    # mechanism and the derivation says 1.0.
    "jump_market_variance_share": {"kind": "abs", "step_unit": 0.1,
                                   "hard_range": (0.0, 1.0)},
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
    # DERIVED on pt-v19: `rho - alpha - gamma/2` at the tape's per-name decay
    # rate 0.9416. A search can move it, but a moved value is a value that
    # follows from nothing. `SEARCHED_9` below still lists it, because that
    # set is pt-v1's and stays as history; a pt-v19 search must not inherit it.
    "garch_beta":               {"kind": "abs", "step_unit": 0.1,
                                 "derived": True},
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
    # How far the factor's shock share rotates with its own variance
    # excursion. Ships at 0.0, so the multiplicative box collapses and the
    # hard range is what a search gets. The top is the parameter's own
    # domain rather than a taste: `alpha_beta_at` clamps the rotation at the
    # value holding the GJR fourth-moment coefficient at 0.999, so past the
    # point where the clamp binds on a typical excursion the dial buys
    # nothing and the surface is flat there by construction. The `alphax2`
    # box measured 0.0 to 0.40 and found it flat across the whole of it.
    "market_vol_alpha_excursion": {"kind": "abs", "step_unit": 0.05,
                                   "hard_range": (0.0, 0.5)},
    # The slow variance LEVEL, a lognormal AR(1) on the factor's variance
    # target. Both ship at 0.0 and both are bounded by the tape rather than
    # by convention; the derived pair is 0.9977 and 0.047. The persistence
    # stops short of 1.0 because at 1.0 there is no stationary dispersion to
    # normalise against and the level is a random walk -- a different model
    # rather than a further setting -- and starts at 0.95, a half-life of 13
    # sessions, where the level is no longer slower than the fast component
    # it sits under.
    "market_vol_level_persistence": {"kind": "abs", "step_unit": 0.01,
                                     "hard_range": (0.0, 0.9995),
                                     # DERIVED: the tape's window-to-window
                                     # autocorrelation, inverted by bisection.
                                     "derived": True},
    "market_vol_level_sigma":   {"kind": "abs", "step_unit": 0.02,
                                 "hard_range": (0.0, 0.15)},
    # The VIX's own slow log-level (2026-09-21): a lognormal AR(1) on what
    # the VIX prices. DERIVED from the two-pole fit to log VIX's ACF
    # (design repo, vix-level-derivation.txt): 0.9965 and 0.0256.
    "vix_level_persistence": {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 0.9995), "derived": True},
    "vix_level_sigma":        {"kind": "abs", "step_unit": 0.005,
                              "hard_range": (0.0, 0.08), "derived": True},
    # The loop's transmission of that level into the VIX, divided out of the
    # level's dispersion (2026-09-21). DERIVED from the read-back's held-VIX
    # elasticity, 1.75 at the shipped exponent and 2.47 at 4.9; 0.0 is the
    # branch not taken and the range opens above one because the loop's own
    # gain is 1 / (1 - h) and never below it.
    "vix_level_loop_gain":    {"kind": "abs", "step_unit": 0.1,
                              "hard_range": (0.0, 6.0), "derived": True},
    # The VIX's own slow reversion toward the identity's anchor (2026-09-22),
    # the third thing the loop lacks: the VIX reverts to the read-back and the
    # read-back reverts to the VIX, and neither reverts to a level. DERIVED
    # 0.046 as the kappa at which the two-state loop's slow pole equals the
    # tape's 0.9965 at market_vol_vix_exponent 1.83. 0.0 is the branch not
    # taken; the top is the invariant's own, a rate at or above one landing
    # on or past the anchor every session.
    "vix_anchor_reversion":   {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 0.5), "derived": True},
    # The same anchor in the VIX's TARGET instead of its rate (2026-09-23):
    # target = implied^(1-a) * anchor^a. DERIVED 0.61 at exponent 4.0 as the
    # weight that puts the loop's slow pole at the tape's 0.9965; the fast
    # pole then stays at 0.717 whatever the exponent.
    "vix_anchor_weight":      {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 0.9), "derived": True},
    # The rate of the anchor's slow memory of the read-back (2026-09-23).
    # 0.0 is the instantaneous form. A probe dial, not derived.
    "vix_anchor_memory":      {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 1.0), "derived": False},
    # The macro compounding clock (2026-09-23): 252 sessions to the year the
    # economy actually steps through, where 365 was written. Derived, not fitted.
    "macro_compound_days_per_year": {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (252.0, 365.0), "derived": True},
    # The rest of the macro calendar on the session clock (2026-09-23,
    # macro-cycle): 252 steps to the macro year. Derived, not fitted.
    "macro_calendar_days_per_year": {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (252.0, 365.0), "derived": True},
    # Switches, 0 shipped and 1 on (2026-09-23, macro-cycle): the NBER/BEA
    # cycle table, the Fed's lift-off branch, buybacks in market_pe.
    "cycle_us_calibration":   {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (0.0, 1.0), "derived": True},
    "fed_liftoff_rule":       {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (0.0, 1.0), "derived": True},
    "market_pe_buybacks":     {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (0.0, 1.0), "derived": True},
    # The anchor's centre below the derived anchor, in logs, and the weight's
    # level law and its cap (2026-09-23, vix-law-levels). Probe dials.
    "vix_anchor_centre":      {"kind": "abs", "step_unit": 0.01,
                              "hard_range": (0.0, 1.0), "derived": False},
    "vix_anchor_weight_level": {"kind": "abs", "step_unit": 0.05,
                               "hard_range": (0.0, 2.0), "derived": False},
    "vix_anchor_weight_level_cap": {"kind": "abs", "step_unit": 0.05,
                                   "hard_range": (0.0, 4.0), "derived": False},
    "vix_anchor_weight_level_knee": {"kind": "abs", "step_unit": 0.01,
                                    "hard_range": (0.0, 1.0), "derived": False},
    "market_vol_vix_exponent_below": {"kind": "abs", "step_unit": 0.05,
                                     "hard_range": (0.0, 5.0), "derived": False},
    "vix_anchor_weight_level_below": {"kind": "abs", "step_unit": 1.0,
                                     "hard_range": (0.0, 1.0), "derived": False},
    "vix_anchor_weight_level_knee_fixed": {"kind": "abs", "step_unit": 1.0,
                                          "hard_range": (0.0, 1.0), "derived": False},
    # How fast an endogenous news event's move is priced (2026-09-23,
    # news-speed). Derived from intraday event studies (design repository,
    # programme/results/news-speed/): 0.6-tick half-life, 12% drift at a
    # 42-tick half-life, the maker re-quoting on news.
    "news_absorption_half_life": {"kind": "abs", "step_unit": 0.05,
                                  "hard_range": (0.0, 5.0), "derived": True},
    "news_absorption_drift_share": {"kind": "abs", "step_unit": 0.01,
                                    "hard_range": (0.0, 0.5), "derived": True},
    "news_absorption_drift_half_life": {"kind": "abs", "step_unit": 1.0,
                                        "hard_range": (0.0, 120.0), "derived": True},
    "news_quote_revision": {"kind": "abs", "step_unit": 1.0,
                            "hard_range": (0.0, 1.0), "derived": True},
    # pt-v20 (2026-09-24, design repository programme/ptv20-registration.md).
    # The two tape switches and the fair-value share are derived as the
    # values that make the mechanism what it says (1.0 each); the opening
    # spread and the ladder's scale are measured.
    "quote_model_weight": {"kind": "abs", "step_unit": 0.1,
                           "hard_range": (0.0, 1.0), "derived": True},
    "closing_auction": {"kind": "abs", "step_unit": 1.0,
                        "hard_range": (0.0, 1.0), "derived": True},
    "fair_value_news_share": {"kind": "abs", "step_unit": 0.05,
                              "hard_range": (0.0, 1.0), "derived": True},
    "opening_mispricing_sigma": {"kind": "abs", "step_unit": 0.002,
                                 "hard_range": (0.0, 0.3)},
    "opening_market_sigma": {"kind": "abs", "step_unit": 0.005,
                             "hard_range": (0.0, 0.3)},
    "fair_value_market_share": {"kind": "abs", "step_unit": 0.05,
                                "hard_range": (0.0, 1.0)},
    "treasury_10y_noise": {"kind": "abs", "step_unit": 0.0025, "hard_range": (0.0, 0.1)},
    "treasury_2y_noise": {"kind": "abs", "step_unit": 0.0025, "hard_range": (0.0, 0.1)},
    "flight_to_quality_gain": {"kind": "abs", "step_unit": 0.001, "hard_range": (0.0, 0.05)},
    "flight_to_quality_day": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0),
                              "derived": True},
    "corporate_yield_daily": {"kind": "abs", "step_unit": 1.0, "hard_range": (0.0, 1.0),
                              "derived": True},
    "earnings_cycle_depth": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.5)},
    "earnings_cycle_upside": {"kind": "abs", "step_unit": 0.01, "hard_range": (0.0, 1.0)},
    "earnings_cycle_half_life": {"kind": "abs", "step_unit": 5.0, "hard_range": (1.0, 2520.0)},
    "earnings_cycle_sigma": {"kind": "abs", "step_unit": 0.0005, "hard_range": (0.0, 0.05)},
    "earnings_anticipation_half_life": {"kind": "abs", "step_unit": 10.0, "hard_range": (0.0, 5040.0)},
    "rate_pe_sensitivity": {"kind": "rel", "step_unit": 0.05, "hard_range": (0.0, 10.0)},
    "cascade_gain": {"kind": "abs", "step_unit": 0.05, "hard_range": (0.0, 1.0)},
    # The agent-facing book (2026-09-24, feature/order-book-depth). Read only
    # on an agent's path, so no untraded statistic moves with any of them.
    "book_depth_coefficient": {"kind": "abs", "step_unit": 0.05,
                               "hard_range": (0.0, 10.0), "derived": False},
    "book_depth_exponent": {"kind": "abs", "step_unit": 0.05,
                            "hard_range": (0.0, 1.0), "derived": False},
    "book_depth_reach": {"kind": "abs", "step_unit": 0.25,
                         "hard_range": (0.0, 10.0), "derived": False},
    "book_shared": {"kind": "abs", "step_unit": 1.0,
                    "hard_range": (0.0, 1.0), "derived": False},
    "book_refill_half_life": {"kind": "abs", "step_unit": 1.0,
                              "hard_range": (0.0, 390.0), "derived": True},
    "book_resting": {"kind": "abs", "step_unit": 1.0,
                     "hard_range": (0.0, 1.0), "derived": False},
    "fill_impact_coefficient": {"kind": "abs", "step_unit": 0.01,
                                "hard_range": (0.0, 5.0), "derived": False},
    # The crisis epicentre's extra volatility, DERIVED 1.93 as the median of
    # the tape's three epicentre episodes (2.43, 1.93, 1.41). 0.0 is the
    # branch not taken; the range opens at zero to hold it and stops at 3.0,
    # above the largest episode the record has and below the 4.0307 where
    # the solve runs out of the rest of the roster's variance to move.
    "crisis_epicentre_extra": {"kind": "abs", "step_unit": 0.05,
                              "hard_range": (0.0, 3.0), "derived": True},
    # The hysteresis, in SESSIONS, so the step unit is a step in sessions
    # and not a fraction. 5 is a trading week, which is the resolution the
    # tape's episode windows are dated at; anything finer is below it.
    "crisis_epicentre_end_sessions": {"kind": "abs", "step_unit": 5.0,
                                      "hard_range": (0.0, 63.0)},
    # The market-side warm-up, in SESSIONS, so the step unit is a step in
    # sessions and not a fraction: 63 is one quarter, the block the
    # transient was traced in (`level-sigma-horizon.md` 2.2), and anything
    # finer is below the resolution at which the envelope was measured.
    # The top is twice the registered 504; see `atlas_survey`'s entry for
    # why the map is flat past about 700.
    "market_burn_in_sessions":  {"kind": "abs", "step_unit": 63.0,
                                 "hard_range": (0.0, 1008.0)},
    # The slow variance component (pt-v4). All three ship at 0.0, so the
    # multiplicative [1/4x, 4x] box collapses on them and the hard range is
    # what a search actually gets -- see `calibration_box`.
    "market_vol_slow_persistence": {"kind": "abs", "step_unit": 0.1,
                                    "hard_range": (0.0, 0.999),
                                    # DERIVED: the two-exponential fit to the
                                    # tape's variance impulse response.
                                    "derived": True},
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
    # The exponent on the same ratio. Ranged like the market factor's
    # `market_vol_vix_exponent`, and the range holds both laws the tape
    # offers: 2.0, which ships, and 1.4176, which is twice the roster's
    # realised-volatility exponent.
    "garch_vix_exponent": {"kind": "abs", "step_unit": 0.05,
                           "hard_range": (1.0, 4.0)},
    # How much of the GJR's innovation is the name's OWN noise, put back in
    # the units the coefficients were fitted in. A share between the two
    # innovations, so the unit interval is the whole mechanism; 0.0 is the
    # column that ships and the derivation says 1.0.
    "garch_innovation_commensurate": {"kind": "abs", "step_unit": 0.1,
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
    # How much of a jump the volume scale counts as an intraday move. A
    # share, so the unit interval is the whole mechanism; 1.0 ships and
    # nothing on the tape has yet said where in it the truth is.
    "volume_move_jump_share": {"kind": "abs", "step_unit": 0.1,
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
    # The two floor dials (programme/idio-vol-floor.md). Both must be able
    # to reach the END of their range, not a multiple of the shipped value:
    # the whole content of each is what happens at one endpoint, so a
    # convention box around the ship would explore everything except the
    # answer.
    "garch_omega_sector_scaled": {"kind": "abs", "step_unit": 0.1,
                                  "hard_range": (0.0, 1.0)},
    # The stationary day-zero opening (programme/stationary-opening-design.md).
    # A switch: 0.0 and 1.0 are the only values that mean anything, so the
    # step is the whole interval and a search either takes the mechanism or
    # leaves it.
    "cycle_stationary_opening": {"kind": "abs", "step_unit": 1.0,
                                 "hard_range": (0.0, 1.0)},
    # In daily VARIANCE units. 0.0 removes the floor, which is the point of
    # the dial; the top is 4x the shipped 1e-4, the convention multiple.
    "idio_sigma_floor": {"kind": "abs", "step_unit": 2.5e-5,
                         "hard_range": (0.0, 4.0e-4)},
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


#: The dials `provenance.py` marks `derived` AND that are closed forms or
#: rule-derivations over something the vector carries -- design note
#: `derived-dial-enforcement.md` classes A and B. They come off the search
#: surface: a Jacobian column for one of them measures the response to
#: BREAKING an identity, which is a reading of the break rather than of the
#: model. The eleven class-C switches stay, because a switch has no identity
#: to break beyond the excursion/identity pairing the engine now refuses.
DERIVED_DIALS = tuple(sorted(
    name for name, spec in PARAM_SPECS.items() if spec.get("derived")))


def searchable(names) -> list[str]:
    """`names` with the derived dials dropped, in order."""
    return [n for n in names if n not in DERIVED_DIALS]


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
        if spec.get("derived"):
            # A derived dial's hard range is not a constraint on the model,
            # it is a leftover box from whichever era drew it, and on pt-v19
            # two of them do not contain the shipped value. Its value follows
            # from the identity `provenance.py` records; the engine checks the
            # two that are closed forms (`ModelParams.from_preset` refuses a
            # pt-v19 vector whose cap is off its image), and this gate has
            # nothing to add. Skipped rather than corrected -- see the specs.
            continue
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
