"""Where every shipped dial value came from, as data rather than as prose.

# The defect this exists for

A dial whose justification is another mechanism's defect is indistinguishable,
at read time, from a dial derived off an identity. Both are a float in a
constructor with a paragraph beside it. One is a model; the other is a
compensator holding two errors in balance, and it stops working the moment
either error is fixed.

Four of those were found in one day: fear dials cancelling each other while
the channel they gate measures 0.64x-0.87x of real; a volatility floor that
deletes sector differentiation with a second dial to route it back in; a gain
recorded as "derived" that was the one point where two errors cancelled; and
an offset recorded as converged by a field named `converged_offset` whose
tolerance never fired.

The common cause is none of those values. **Nothing in the repository
required a dial to carry its derivation**, so no reader and no test could tell
the two kinds apart. This module is that requirement.

# The three kinds, and why `undetermined` passes

`derived`     an identity. Carries the algebra and, for each term, the line
              it is read from. `oil_supply_response = 1.0` is the value at
              which inventory is driftless: a stationarity condition, not a
              matter of degree.
`measured`    a tape or corpus figure. Carries the source, the date, the
              script, and a RESIDUAL OR STANDARD ERROR. An entry without one
              is refused at the schema level, because a figure shipped
              without an error bar is a chosen constant with more decimal
              places.
`undetermined` nobody knows. Carries what it would take to find out.

`undetermined` is a first-class PASSING state on purpose. If the only way to
go green is to claim a derivation, people will claim derivations, and a
claimed derivation is worse than an admitted gap: it is the same defect with
a paragraph in front of it.

# What a solve may claim

Where a value comes from a solve, the entry records the tolerance and whether
it was met. **A solve that did not converge produces NO entry** -- not a last
iterate under a name that asserts convergence. `converged_offset` is why: a
field name asserted a condition nothing checked, and the value it carried was
read as settled for hours.

# Scope

`REQUIRED_PRESETS` are the presets whose vectors a user actually runs: the
shipped default and any candidate for it. A dial needs an entry where one of
those sets it to something other than `BASELINE`'s value -- that is the set of
choices the project has made, as against the baseline it measures everything
from.

`UNPROVENANCED` is the committed list of those dials with no entry yet. It is
asserted as a SET in both directions, so a dial cannot be added without a
decision and provenance cannot be written without the list shrinking. The
list is the actual state of the shipped preset, and until 2026-09-05 nobody
had written it down.

# The hole a frozen baseline leaves, and `POST_BASELINE`

`pt-v1` is frozen and bit-reproducing, so a dial added to `ModelParams`
afterwards carries in `pt-v1` **whatever default its own author gave it**.
"Differs from `pt-v1`" then cannot see that value at all, however chosen it
is. The first version of this module said so about `garch_omega`: 2e-06 in
every preset, 18.4x short of its own identity, and out of scope because
nobody moved it.

That was a footnote while the dials it hid were old. It is not one now.
`vix_variance_premium` ships 0.252 in `pt-v1` because the dial did not exist
when `pt-v1` was written; the number is this era's measurement, and the
audit was blind to it.

**Name the failure for what it is: a guard reporting green because its
subject moved out of its scope.** Every test here passed on 2026-09-06 with
a tape measurement, an exponent whose shipped value is not the measured one,
and a live per-name floor all outside the question being asked -- and the
suite could not have said so, because the completeness assertion is over
`required_dials()` and `required_dials()` was the thing that had shrunk. A
green run meant "everything in scope is accounted for", and scope was
silently the wrong set. The lesson generalises past this module: when a
scope is computed rather than declared, ask what has left it since the
computation was written.

`POST_BASELINE` names such dials and puts them in scope at the value each
required preset actually ships, so an entry still goes stale the moment one
of them moves.

# The partition, which is what actually closes the crack

An entry table and a declared list still leave the question "what is in
scope" answered by a computation, and a computation can quietly stop
covering things. So the settable surface is asserted as a PARTITION:
**every settable dial is in exactly one of `moved_dials()`,
`POST_BASELINE`, or `OUT_OF_SCOPE`**, and a dial in none of them fails the
suite. A dial added tomorrow lands in none. That is the deliverable; the
classifications are what it costs.

# Which surface, and why `settable()` and not `to_dict()`

`ModelParams.to_dict()` returns 158 names and `ModelParams.settable()`
returns 129. **The partition is over the 129**, because the question is
"what did somebody choose" and the other 29 cannot be chosen: fifteen are
module constants exposed read-only through the by-name getter
(`inflation_target`, `phillips_curve_coeff`, `fiscal_multiplier` and so
on), twelve are the sector table's per-sector sigmas read through a
`strip_prefix` branch, and two -- `mispricing_phi` and `s_phi_tick` -- are
struct fields DERIVED from `mispricing_half_life_days` whose setter refuses
them by name. Every dial already in scope is inside the 129, so nothing was
lost by choosing it.

Those 29 are not thereby blessed. Fifteen of them are hardcoded numbers
somebody picked, and "no preset can move it" is a statement about the API,
not about the number. They are a different surface with a different
question, and this module does not pretend to ask it.

# What `OUT_OF_SCOPE` may and may not mean

It means **this dial cannot be a choice that needs justifying** -- it is
inert at the shipped value, or unread because a partner dial gates it. Each
entry names the gate, because "the docstring says it is bit-identical" is
the kind of evidence this module exists to distrust:
`market_beta_down_asym`'s docstring says 0.0 is what every preset ships and
both required presets ship 0.025.

**"Never read" is not an available reason, and the reason it is not is a
mistake made here.** `price_breaker_fraction` was classified dead because
nothing outside `params.rs` mentions it. It is not dead: its setter derives
`breaker_up` and `breaker_down` from it (`params.rs:4022`), the engine reads
those, and `the_breaker_band_is_derived_once_at_construction` pins the
behaviour. **A dial can reach the engine under another name**, so searching
for its own name in the consumer measures the wrong thing, and a negative
result from the wrong instrument is not evidence.

Two routes were then audited across all 129 settable dials rather than
argued about. Setter arms that write a field other than their own: exactly
**two**, `mispricing_half_life_days` and `price_breaker_fraction`, and
`tests/test_dial_provenance.py` pins that pair and refuses to let either be
declared out of scope. Dials copied into a differently named field at a
struct literal: **none**. So every other out-of-scope reason rests on a
gate that was read, not on a name that was searched for.

It does **not** mean "nobody has looked". A dial that is LIVE at its
shipped value and that no preset moves is a chosen constant, and it belongs
in `POST_BASELINE` with no entry -- which is to say in `UNPROVENANCED`,
whose whole purpose is to record an admitted gap. Filing a live constant
under out-of-scope would hide it, and hiding it is the failure this
partition was built to end.
"""

from __future__ import annotations

from typing import Any

from ._core import ModelParams, ValidationError

#: The preset every choice is measured against. pt-v1 is the baseline the
#: seed sds are frozen at and the preset the others were built from, so a
#: difference from it is a decision somebody took.
BASELINE = "pt-v1"

#: The presets whose dials must carry provenance: what ships, and what is
#: proposed to ship. A preset nobody runs is history, and history is not
#: made better by demanding derivations for it now.
REQUIRED_PRESETS = ("pt-v16", "pt-v18")

KINDS = ("derived", "measured", "undetermined")

#: Fields each kind must carry. `value` and `presets` are required of every
#: entry: an entry that does not say WHICH value it justifies goes stale
#: silently when the dial moves, which is how pt-v18's published level
#: figures survived a coefficient change.
REQUIRED_FIELDS = {
    "derived": ("identity", "terms", "source"),
    "measured": ("source", "date", "script"),
    "undetermined": ("what_would_determine_it",),
}

#: A `measured` entry must carry at least one of these. See the module note:
#: an exponent shipped without an error bar is a chosen constant with more
#: decimal places.
MEASURED_ERROR_FIELDS = ("residual", "standard_error")

#: Dials in scope that no required preset moves, because the BASELINE
#: PREDATES THEM. See the module note: `pt-v1` is frozen, so the value it
#: carries for a dial added later is that dial author's default and not a
#: choice `pt-v1` made, and the difference-from-baseline rule is blind to it.
#:
#: Each entry says why the dial is here. Membership is asserted in
#: `tests/test_dial_provenance.py`: every name is a real dial, and a name a
#: required preset DOES move is refused, because that dial is already in
#: scope under the rule above and listing it here as well would hide that
#: somebody chose it.
#:
#: PARTIAL, ON PURPOSE, and the module note says how to derive the whole
#: set. These five are the dials `programme/PT-V19-CHARTER.md` section 3.1
#: names in its ledger that the difference-from-baseline rule cannot reach.
POST_BASELINE = {
    "vix_level_identity":
        "added 2026-09-05 for the VIX level identity; pt-v19 sets it to 1.0 "
        "(charter 3.1) and every shipped preset leaves it at 0.0",
    "cycle_stationary_opening":
        "added 2026-09-05 for the stationary opening; pt-v19 sets it to 1.0 "
        "(charter 3.1, ruling R2) and every shipped preset leaves it at 0.0",
    "vix_variance_premium":
        "the measured variance risk premium. Read only while "
        "`vix_level_identity` is non-zero, so no preset has had to move it "
        "-- and 0.252 is a measurement, which is exactly the kind of number "
        "that must not be invisible because it arrived as a default",
    "vix_return_exponent":
        "the power form's exponent, shipped inert at 1.0 under ruling R4 "
        "while the tape reads 1.200. A dial whose shipped value is not the "
        "measured one is the case this module exists for",
    "idio_sigma_floor":
        "the per-name sigma floor, which replaced an inline literal at the "
        "value the literal carried. Ships at 1e-4 in every preset and binds "
        "on 61 to 90 per cent of name-days in nine of twelve sectors, so it "
        "is a live constraint that no preset has ever chosen",

    # ---- live at the shipped value and moved by no preset -------------
    # Everything below entered scope with the partition. Each one is READ
    # on the shipped path -- the read site is named -- and sits at a value
    # `pt-v1` carries because the dial did not exist when `pt-v1` was
    # written. That is a chosen constant by the charter's definition, so it
    # is in scope and it is in `UNPROVENANCED` until somebody derives or
    # measures it. None of them is being called wrong here; they are being
    # called unaccounted for, which they are.
    "garch_omega":
        "THE ONE WITH A MEASURED CONSEQUENCE. 2e-06 in every preset, read "
        "at rust/src/market/garch.rs:348. At pt-v16's persistence 0.836415 "
        "the steady-state idiosyncratic variance is 1.2226e-05, sigma "
        "0.00350, against `idio_sigma_floor`'s variance 1e-4, sigma "
        "0.01000 -- so the floor sits 8.2x ABOVE the process fixed point "
        "and the tick draws with the FLOOR for every sector, which is why "
        "the sector table's 0.008 to 0.025 spread does not reach the "
        "running model. Charter 2.6. Its partner "
        "`garch_omega_sector_scaled` ships 0.0 and is inert by branch, so "
        "the identity that would derive this value is not on the shipped "
        "path",
    "crash_amplifier_slope":
        "0.2, read at market/factors.rs:552 whenever the shock clears the "
        "threshold",
    "crash_amplifier_threshold":
        "2.0 baseline sigmas, the branch condition at market/factors.rs:551",
    "crisis_blend_ramp":
        "1.4, the divisor of the crisis spike at market/tick.rs:768",
    "crowd_lean_cap":
        "0.02, the clamp on the crowd's daily shock at mispricing.rs:178. "
        "Its own docstring calls it a guard; it is a guard with a number",
    "crowd_momentum_gain":
        "0.02, read at mispricing.rs:177",
    "crowd_valuation_gain":
        "0.006, read at mispricing.rs:177",
    "garch_ceiling_multiple":
        "5.0, the per-name variance ceiling at market/garch.rs:190. Its "
        "docstring says it was searched under bounds and measured as "
        "BINDING on clustering, so it is not a dormant guard",
    "garch_floor_multiple":
        "0.25, the per-name variance floor at market/garch.rs:191",
    "inflation_ceiling":
        "6.0 per cent, the clamp at economy/daily.rs:641. Its docstring "
        "records the series sitting ON this clamp once the reversion is "
        "loosened, against a real 9.0 in June 2022",
    "inflation_floor":
        "-1.0 per cent, the clamp at economy/daily.rs:640",
    "inflation_reversion":
        "0.55 of the gap per month, read at economy/daily.rs:577. The "
        "docstring records it as the hard-coded value promoted to a dial "
        "and names the miss it produces: monthly acf1 0.936 against a real "
        "0.978",
    "informed_flow_fraction":
        "0.35, the permanent share of order-flow impact at "
        "market/factors.rs:422",
    "market_vol_floor_multiple":
        "0.05, the market factor's variance floor at "
        "market/factor_vol.rs:363",
    "market_vol_vix_exponent":
        "2.0, and the branch at market/factor_vol.rs:375 takes the literal "
        "square at exactly this value. The square is a modelling choice, "
        "not an inert default -- the docstring records round 100 measuring "
        "it too convex through mid-VIX",
    "mispricing_cap":
        "0.9, the bound on |s| at market/tick.rs:1338",
    "mispricing_half_life_days":
        "60.0 trading days, and the one settable knob for the decay: it "
        "recomputes `mispricing_phi` and `s_phi_tick`, which the setter "
        "refuses directly",
    "news_market_weight":
        "0.3, read at market/factors.rs:408",
    "news_sector_weight":
        "0.5, read at market/factors.rs:406",
    "order_flow_coefficient":
        "50.0, the impact coefficient before the informed fraction at "
        "market/factors.rs:421",
    "price_hard_cap":
        "50000.0, applied at market/tick.rs:1054",
    "usd_crisis_vix_threshold":
        "25.5, the safe-haven gate at economy/daily.rs:983. A SEPARATE "
        "dial from `crisis_vix_threshold`, which the required presets move "
        "to 30.88325108, so the two gates have silently diverged",
    "vix_ceiling":
        "80.0, applied at economy/daily.rs:1145 -- and its own docstring "
        "says 'A CHOSEN constant and not a derived one, declared here so a "
        "reader can disagree with it'. A dial that admits this in prose "
        "and is invisible to the audit is the exact pairing this partition "
        "exists to stop",
    "volume_move_floor":
        "0.6, one of four tick-engine literals promoted to dials in 0.3.0 "
        "at the values they already had",
    "volume_move_noise":
        "0.2, promoted with it",
    "price_breaker_fraction":
        "0.25, the circuit-breaker half-band. LIVE, and it was briefly "
        "recorded here as dead: nothing outside params.rs names it, but "
        "the setter derives `breaker_up` and `breaker_down` from it "
        "(params.rs:4022) and the engine reads those, which "
        "`the_breaker_band_is_derived_once_at_construction` pins. The "
        "shipped value is the module constant "
        "`tick::PRICE_BREAKER_FRACTION` and no preset moves it",
}

#: Settable dials that cannot be a choice needing justification, and why.
#:
#: NOT "nobody has looked" -- that is what `UNPROVENANCED` is for. An entry
#: here claims the dial is INERT at the value every preset ships, or unread
#: because a partner gates it, or not read at all, and it names the gate so
#: the claim can be checked. Every reason below was read off the engine
#: rather than off the dial's own docstring, because a docstring is exactly
#: the evidence this module distrusts.
#:
#: A dial whose gate is another dial is only inert while that partner sits
#: where it sits. Move the partner and this entry becomes false -- which is
#: why each one names the partner rather than saying "inert".
OUT_OF_SCOPE = {
    "crisis_blend_variance_damp":
        "inert at 0.0: market/factors.rs:473 branches on `== 0.0`",
    "fair_value_book_floor":
        "inert at 0.0: the book floor is not applied to profitable "
        "companies, and the valuation is the reference implementation's",
    "forced_flow_gain":
        "inert at 0.0: market/tick.rs:962 branches on `!= 0.0`, so the "
        "whole forced-flow segment is absent",
    "forced_flow_beta_exponent":
        "unread while `forced_flow_gain` is 0.0; and inert at 0.0 in its "
        "own right (market/tick.rs:968)",
    "forced_flow_replenish":
        "unread while `forced_flow_gain` is 0.0 (engine.rs:1976 requires "
        "gain != 0 and reservoir > 0)",
    "forced_flow_reservoir":
        "unread while `forced_flow_gain` is 0.0, and 0.0 itself fails the "
        "`> 0.0` condition at engine.rs:1976",
    "forced_flow_threshold":
        "unread while `forced_flow_gain` is 0.0; the 40.0 is the VIX level "
        "the absent segment would wake at",
    "garch_beta_dispersion":
        "inert at 0.0: market/garch.rs:252 spreads persistence by this "
        "width, and a width of zero leaves every name on the common beta",
    "garch_cascade_components":
        "inert at 0.0: economy/daily.rs:204 takes the cascade path only at "
        "`>= 1.0`, so the single-component GJR recursion runs",
    "garch_cascade_ratio":
        "unread while `garch_cascade_components` is 0.0 -- its only read "
        "site, market/garch.rs:180, is inside the cascade",
    "garch_cascade_weight":
        "unread while `garch_cascade_components` is 0.0 -- its only read "
        "site, market/garch.rs:199, is inside the cascade",
    "garch_omega_sector_scaled":
        "inert at 0.0: market/garch.rs:347 branches on `== 0.0` and takes "
        "the constant `garch_omega`. See `garch_omega`'s entry -- this "
        "being inert is why that value is not derived on the shipped path",
    "idio_sigma_beta_exponent":
        "inert at 0.0: market/factors.rs:98 branches on `== 0.0`",
    "market_beta_down_asym_lag":
        "inert at 0.0: market/factors.rs:450 branches on `== 0.0`",
    "market_vol_gamma":
        "inert at 0.0: the GJR term at market/factor_vol.rs:395 loads "
        "`gamma` on the squared shock and omega compensates by `gamma/2`, "
        "so zero is the symmetric update exactly",
    "market_vol_vix_smooth":
        "inert at 0.0: market/factor_vol.rs:536 branches on `== 0.0` and "
        "reads the raw print",
    "order_flow_impact_law":
        "inert at 0.0: market/factors.rs:773 branches on `== 0.0` and "
        "takes the shipped law",
    "overnight_variance_ratio":
        "inert at 0.0: the overnight move is this ratio of a session's "
        "variance (engine.rs:1856), and nothing moved a price between "
        "sessions before the dial existed",
    "phase_target_range_draw":
        "inert at 0.0: economy/daily.rs:479 branches on `!= 0.0` and takes "
        "the range's midpoint",
    "qe_pe_stock_gain":
        "inert at 0.0: it multiplies `ln(qe_assets_ratio)` into the target "
        "P/E, so zero contributes nothing",
    "regime_stress_points":
        "inert at 0.0: engine.rs:440 multiplies the phase intensity by "
        "this, so the business cycle reaches the market not at all",
    "size_effect_smoothness":
        "inert at 0.0: market/factors.rs:295 returns the step value by an "
        "early return, so the power law is never evaluated",
    "size_effect_exponent":
        "unread at the shipped `size_effect_smoothness` of 0.0. The guard "
        "is market/factors.rs:294-297 -- `let s = "
        "params.size_effect_smoothness; if s == 0.0 { return stepped; }` "
        "-- and the dial's only read, market/factors.rs:307, sits after "
        "that early return. The 0.15 is a fitted number the shipped "
        "configuration never evaluates",
    "spread_size_smoothness":
        "inert at 0.0: microstructure.rs:207 takes the stepped spread on "
        "`size_smoothness == 0.0`, reached from the params through "
        "market/tick.rs:1201",
    "spread_size_exponent":
        "unread at the shipped `spread_size_smoothness` of 0.0. Both dials "
        "DO reach the settlement path -- market/tick.rs:1201-1202 passes "
        "them from the params -- and the guard is microstructure.rs:207, "
        "`if size_smoothness == 0.0 || mcap_billions <= 0.0 { stepped }`, "
        "so the else branch holding this exponent is never evaluated. The "
        "0.455 is a fitted number the shipped configuration never reaches. "
        "Noted separately: engine.rs:2789's `book_for` hardcodes 0.0 and "
        "the module constant instead of reading the params, so a preset "
        "that smoothed the curve would get an unsmoothed inspection book; "
        "at the shipped 0.0 the two agree exactly",
    "trough_growth_floor":
        "inert at 0.0: economy/daily.rs:481 leaves the trough range at the "
        "shipped (-1.0, 0.5)",
    "universe_stress_weight":
        "inert at 0.0: market/tick.rs:760 branches on `== 0.0` and the "
        "blend reads today's VIX alone",
    "universe_stress_decay":
        "unread while `universe_stress_weight` is 0.0, and 0.0 itself "
        "multiplies the remembered stress to nothing (engine.rs:443)",
    "vix_jump_intensity":
        "inert at 0.0: economy/daily.rs:1128 branches on `!= 0.0`, so no "
        "exogenous fear event is ever drawn",
    "vix_jump_scale":
        "unread while `vix_jump_intensity` is 0.0, and 0.0 itself scales "
        "any jump to nothing (economy/daily.rs:1132)",
    "vix_target_offset":
        "inert at 0.0: a constant added to the VIX target, and the level "
        "identity retires it outright",
    "volume_idio_persistence":
        "inert at 0.0: the per-name volume state has no memory and no "
        "innovation, so the common multiplier is the whole of it",
    "volume_idio_sigma":
        "unread while `volume_idio_persistence` is 0.0",
    "volume_idio_variance_gain":
        "inert at 0.0: volume follows the market factor's variance and "
        "nothing of the name's own",
}

#: The provenance of each shipped dial value.
#:
#: SEEDED, NOT FILLED IN. Three entries, one of each kind, all read off the
#: code rather than invented, so the schema is exercised by real data. The
#: other sixty-one dials are in `UNPROVENANCED` and belong to the
#: workstreams that own them. Filling them in from here would be inventing
#: derivations, which is the failure this module exists to prevent.
DIAL_PROVENANCE: dict[str, dict[str, Any]] = {
    "oil_supply_response": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0},
        "identity": "the value at which supply equals demand in expectation, "
                    "so inventory_change is the noise term alone and "
                    "inventory is driftless",
        "terms": {
            "1.0": "the stationarity condition of the inventory process; "
                   "the 0.15 the term uses is the coefficient already there",
        },
        "source": "rust/src/params.rs, ModelParams::oil_supply_response, "
                  "section 'Why 1.0 is derived and not fitted'",
        "note": "not a matter of degree: the value that makes a random walk "
                "driftless is one value",
    },
    "buyback_payout_share": {
        # The code calls this "the one CHOSEN constant in this era" and
        # sources it to the US large-cap filing record. That is a MEASURED
        # claim, and it carries no residual -- the filing record's
        # across-firm dispersion is cited nowhere -- so it cannot be
        # recorded as measured under this schema. Recording it as
        # undetermined is not a demotion of the source; it is the schema
        # refusing to call a point estimate a measurement.
        "kind": "undetermined",
        "presets": {"pt-v18": 1.0 / 3.0},
        "what_would_determine_it": "the dispersion of net buyback yield "
                                   "across the US large-cap filing record "
                                   "the value is taken from. The point "
                                   "estimate is sourced and the spread is "
                                   "not, so there is no error bar to ship "
                                   "beside it and no way to say whether a "
                                   "third is distinguishable from a quarter",
        "declared_chosen_in_source": True,
        "source": "rust/src/params.rs, pt_v18, 'The one CHOSEN constant in "
                  "this era'",
    },
    "sector_factor_sigma": {
        # A DERIVATION EXISTS AND IT IS NOT FOR THIS VALUE. ws-b derived
        # 0.01070690368 by inverting `excess = a + b*sigma^2` onto the real
        # windows' median 0.164, measured residual -0.0016, form tested on a
        # fourth point at -0.0037. pt-v16 and pt-v18 ship 0.008583053614.
        # Those are different numbers, and a residual attaches to the value
        # it was computed for.
        #
        # The design note adds a second reason not to promote it: it was
        # derived on a model whose sector volatility does not run, so
        # whether any value is needed is itself open.
        "kind": "undetermined",
        "presets": {"pt-v16": 0.008583053614, "pt-v18": 0.008583053614},
        "what_would_determine_it": "the same inversion run against the "
                                   "SHIPPED value, or the shipped value "
                                   "replaced by the derived one. A residual "
                                   "of -0.0016 is recorded for "
                                   "0.01070690368 and says nothing about "
                                   "0.008583053614",
        "source": "programme/RESUME.md, ws-b's derived pair; the arm is not "
                  "the shipped value",
        "derivation_exists_for_another_value": 0.01070690368,
    },
    "garch_gamma": {
        # No admissible derived value EXISTS, which is a stronger statement
        # than "not yet derived" and is why this entry is worth writing.
        # Derived from the real leverage effect the GJR asymmetry is 0.6548,
        # which puts persistence at 1.0722 -- non-stationary, 1.28x the
        # largest admissible value, and worse on the omega identity
        # manifold at 1.039. Under either omega no stationary GJR asymmetry
        # reproduces the real -0.042.
        #
        # So the shipped 0.18318536187800277 is a search optimum, and the
        # thing that would derive it is currently proved not to exist.
        "kind": "undetermined",
        "presets": {"pt-v16": 0.18318536187800277,
                    "pt-v18": 0.18318536187800277},
        "what_would_determine_it": "a mechanism change that lets a "
                                   "STATIONARY asymmetry reach the real "
                                   "leverage effect of -0.042. Today the "
                                   "derived value is 0.6548 at a "
                                   "persistence of 1.0722, which "
                                   "`ModelParams.check_stationary` refuses, "
                                   "so no admissible derived value exists "
                                   "and the shipped figure cannot be "
                                   "reached by derivation",
        "source": "programme/RESUME.md, ws-b's leverage-effect solve",
        "no_admissible_derived_value": True,
    },
    "vix_return_gain_up": {
        # THE DOCSTRING'S 2:1 IS REFUTED AND THE SHIPPED PAIR IS SYMMETRIC.
        # The claim that the real up response is about half the down one
        # had no series, window or estimator attached; the tape gives
        # 2.070/2.440 = 0.848 at near-identical bucket sizes, so the up side
        # is about 85 per cent of the down.
        #
        # And the pair that ships is 17.0/17.0 -- a DIAL ratio of 1.000,
        # which every preset from pt-v9 carries. The design note's table
        # labels 0.40 as "shipped"; 25.0/10.0 is pt-v1 through pt-v8. So the
        # shipped dials sit on the far side of the tape figure from every
        # candidate discussed, not between them.
        #
        # NOT CLAIMED: that the shipped RESPONSE ratio is 1.000. The gains
        # feed a channel with a clamp and a target, so the dial ratio need
        # not be the response ratio, and nobody has measured the shipped
        # one.
        "kind": "undetermined",
        "presets": {"pt-v16": 17.0, "pt-v18": 17.0},
        "what_would_determine_it": "the shipped pair's RESPONSE ratio at 2 "
                                   "per cent, measured the way the tape's "
                                   "0.848 was, and a value for this dial "
                                   "that reproduces it. The tape figure "
                                   "refutes the docstring's 0.5; it does "
                                   "not derive 17.0, and the shipped dial "
                                   "ratio of 1.000 has never been compared "
                                   "with it",
        "source": "programme/RESUME.md, ws-a's tape refutation at 98c5ac4",
        "docstring_claim_refuted": "the real up response is about half the "
                                   "down one; the tape says 0.848",
    },
    "market_vol_slow_gain": {
        "kind": "undetermined",
        "presets": {"pt-v16": 0.05, "pt-v18": 0.05},
        "what_would_determine_it": "a measured slow-component gain. ws-b "
                                   "withdrew this dial as undetermined "
                                   "rather than deriving it; 0.05 is a "
                                   "round number with no series behind it",
        "source": "programme/RESUME.md, ws-b's withdrawal",
    },
    "vix_variance_premium": {
        # The one MEASURED entry in the table, and the schema was built
        # around what it has to carry: a source, a date, a script, and an
        # error bar. The estimator is a choice and it is named, because
        # this quantity reads 1.076 pooled over one history and 1.252 per
        # calendar year, and the panel's own statistic is a per-window one.
        "kind": "measured",
        "presets": {"pt-v16": 0.252, "pt-v18": 0.252},
        "source": "^GSPC and ^VIX adjusted closes, 1990-01-03 to "
                  "2025-07-30, 8,959 aligned sessions with a return; "
                  "per-calendar-year estimator over 35 years",
        "date": "2026-09-05",
        "script": "programme/scripts/vix-rv-relation.py (design repo)",
        "residual": "the per-year IQR, 1.128 to 1.398 on the ratio, so "
                    "+/- 0.13 on pi. The rolling-252-session estimator "
                    "reads median 1.257, P10 1.049, P90 1.473",
        "estimator": "per calendar year, median of 35. Stated because the "
                     "pooled single-history figure is 1.076 and the "
                     "per-window one is 1.252, and the per-window "
                     "estimator is the like-for-like one for a certified "
                     "panel whose statistic runs on a 252-session window",
        "note": "equality is REFUTED rather than merely unsupported: 32 of "
                "35 calendar years and 92.5 per cent of rolling windows "
                "read above 1.0. The dial is not read at all while "
                "vix_level_identity is 0.0, so this value ships inert in "
                "every preset here -- and a default that is a refuted "
                "identity would be a chosen constant, which is why the "
                "measured value is the default and zero is not",
        "source_docstring": "rust/src/params.rs, "
                            "ModelParams::vix_variance_premium",
    },
    "vix_return_exponent": {
        # A MEASUREMENT EXISTS AND IT IS NOT OF THIS VALUE. The tape's down
        # side fits dVIX = 1.003 |r|^1.1996, R squared 0.9947 over eight
        # bucket medians on 8,960 sessions, standard error 0.0357 -- a 95
        # per cent interval of 1.112 to 1.287, and 1.132 count-weighted.
        # The shipped value is 1.0.
        #
        # 1.0 is the exponent at which the power form reduces to the linear
        # one, so the dial is inert and every preset before it is
        # bit-identical. That is a fact about the code and it is recorded
        # here; it is NOT a derivation of 1.0 as the right exponent, and
        # calling it one is precisely the move this module refuses. A
        # residual attaches to the value it was computed for, and 0.0357
        # was computed for 1.1996.
        "kind": "undetermined",
        "presets": {"pt-v16": 1.0, "pt-v18": 1.0},
        "what_would_determine_it": "the question asked on an arm whose "
                                   "index sd is near the tape's, which no "
                                   "arm in wsa16 or wsa17 was, with the "
                                   "shape residual read beside the "
                                   "per-bucket columns "
                                   "(PT-V19-CHARTER.md 2.4). wsa17 could "
                                   "not resolve it: its verdict rests on "
                                   "one bucket holding 336 of 7,560 model "
                                   "sessions against 22 of 8,959 real "
                                   "ones, and dropping that bucket leaves "
                                   "six thousandths of a log unit between "
                                   "the two",
        "source": "programme/joint-solve-scope.md, the exponent fit; "
                  "programme/scripts/vix-updown-fit.py (design repo)",
        "derivation_exists_for_another_value": 1.1996,
        "inert_at_shipped_value": True,
        "ruling": "R4 -- ruled out of pt-v19 until 2.4 is measured "
                  "properly, and the ruling stands",
    },
    "macro_burn_in_days": {
        # THE SOURCE CLAIMS A MEASUREMENT AND SHIPS NO ERROR BAR. The
        # docstring reads "The length is measured -- 755 is the day the
        # last field enters one stationary standard deviation of its mean
        # and stays there", with unemployment at 119 days, inflation 419
        # and the ten-year 705. No script, no date, and no dispersion: the
        # day a field enters a band is a random variable, and one path's
        # value for it is one draw.
        #
        # Under this schema that is not a measurement, and the refusal is
        # the point. Charter 3.2 reaches the same place from the other
        # side: 755.0 is pt-v18's and is inherited, and ruling R2 fixes
        # that both halves of the opening ship together without fixing the
        # length.
        "kind": "undetermined",
        "presets": {"pt-v18": 755.0},
        "what_would_determine_it": "the burn-in table re-run across the "
                                   "certified seed cohort, reporting the "
                                   "dispersion of the day each field "
                                   "enters and stays inside one stationary "
                                   "sd, and re-run on the STATIONARY "
                                   "OPENING rather than on pt-v18's "
                                   "expansion-at-age-zero start, since "
                                   "that is what pt-v19 ships and it moves "
                                   "the quantity being waited for",
        "source": "rust/src/params.rs, ModelParams::macro_burn_in_days, "
                  "section 'The length is measured'; "
                  "programme/PT-V19-CHARTER.md 3.2",
        "source_claims_a_measurement_without_an_error_bar": True,
    },
    "market_beta_down_asym": {
        # The dial's own docstring reads "0.0 -- every shipped preset -- is
        # bit-identical", and pt-v16 and pt-v18 both ship 0.025. Verified
        # 2026-09-05. So the sentence that would BE the derivation is about
        # a value the default does not use.
        "kind": "undetermined",
        "presets": {"pt-v16": 0.025, "pt-v18": 0.025},
        "what_would_determine_it": "a daily-scale measurement of what 0.025 "
                                   "does. The recorded argument for this "
                                   "dial says a per-tick tilt is CLT-washed "
                                   "over 390 ticks and contributes nothing "
                                   "at daily scale, which is an argument for "
                                   "0.0; the shipped value is 0.025 and no "
                                   "measurement of its daily effect is "
                                   "recorded either way",
        "source": "rust/src/params.rs, ModelParams::market_beta_down_asym",
        "docstring_disagrees_with_shipped_value": True,
    },
}

#: Dials in scope that carry NO entry.
#:
#: This list is the finding. Eighty-five of the ninety-five dials in scope
#: have no recorded derivation, and several
#: carry eight significant figures with no error bar anywhere --
#: `crisis_blend_gain` at 0.8275881, `crisis_vix_threshold` at 30.88325108,
#: `market_vol_vix_anchor` at 15.98426471. A search optimum with decimal
#: places is still a search optimum.
#:
#: NOT EVERY DIAL WITH A DEFECT IS IN THIS SET. `garch_omega` ships 2e-06 in
#: every preset and is not named in `POST_BASELINE`, so it is out of scope
#: here -- while the identity at `rust/src/market/garch.rs` puts the shipped
#: value 18.4x short. A dial every preset gets equally wrong is a defect the
#: difference rule is not shaped to catch, because that rule asks "what did
#: we choose", not "what is right". It enters the set the moment a preset
#: moves it, or the moment somebody declares it post-baseline.
#:
#: It shrinks when a workstream records a derivation and never grows without
#: someone deciding it should. `tests/test_dial_provenance.py` asserts it as
#: a SET in both directions, so a new dial fails until it is either given
#: provenance or added here on purpose.
UNPROVENANCED = (
    "cascade_symmetry",
    "crash_amplifier_slope",
    "crash_amplifier_threshold",
    "crisis_blend_cap",
    "crisis_blend_gain",
    "crisis_blend_ramp",
    "crisis_blend_source",
    "crisis_vix_threshold",
    "crowd_lean_cap",
    "crowd_momentum_gain",
    "crowd_valuation_gain",
    "cycle_hazard_per_month",
    "cycle_stationary_opening",
    "daily_credit_floor_gain",
    "earnings_nominal_growth",
    "endogenous_news_intensity",
    "endogenous_news_sigma",
    "garch_alpha",
    "garch_beta",
    "garch_ceiling_multiple",
    "garch_floor_multiple",
    "garch_omega",
    "garch_vix_coupling",
    "idio_sigma_floor",
    "idio_sigma_scale",
    "inflation_ceiling",
    "inflation_floor",
    "inflation_reversion",
    "informed_flow_fraction",
    "jump_intensity_idio",
    "jump_intensity_market",
    "jump_mean_compensated",
    "jump_mean_market",
    "jump_momentum_share",
    "jump_sigma_idio",
    "jump_sigma_market",
    "jump_vix_coupling",
    "market_beta_down_asym_recentre",
    "market_factor_sigma",
    "market_vol_alpha",
    "market_vol_beta",
    "market_vol_ceiling_multiple",
    "market_vol_floor_multiple",
    "market_vol_slow_persistence",
    "market_vol_slow_vix_damp",
    "market_vol_slow_weight",
    "market_vol_vix_anchor",
    "market_vol_vix_coupling",
    "market_vol_vix_exponent",
    "mispricing_cap",
    "mispricing_half_life_days",
    "momentum_theta",
    "neutral_discount_rate",
    "news_market_weight",
    "news_peer_vix_coupling",
    "news_peer_weight",
    "news_peer_weight_down",
    "news_sector_weight",
    "oil_opec_symmetry",
    "oil_seasonality_target",
    "order_flow_coefficient",
    "price_breaker_fraction",
    "price_hard_cap",
    "qe_pe_gain",
    "sector_loading",
    "sector_loading_beta_slope",
    "sector_vix_coupling",
    "usd_crisis_vix_threshold",
    "vix_ceiling",
    "vix_cycle_amplitude",
    "vix_decay_ratio",
    "vix_level_identity",
    "vix_mean_reversion",
    "vix_realised_vol_weight",
    "vix_return_clamp",
    "vix_return_gain",
    "vix_return_source",
    "vix_target_shock_cap",
    "volume_innovation_sigma",
    "volume_move_cap",
    "volume_move_floor",
    "volume_move_noise",
    "volume_move_response",
    "volume_persistence",
    "volume_variance_gain",
)


def settable_dials() -> tuple[str, ...]:
    """The surface the partition is over: what a preset can actually set.

    `ModelParams.settable()`, not `to_dict()`. The module note says why the
    two differ and why this is the right one; it is a function rather than a
    constant so a test can substitute a surface with one more name on it and
    check that the unclassified dial fails.
    """
    return tuple(ModelParams.settable())


def _dict(name: str) -> dict[str, float]:
    values = ModelParams.from_preset(name).to_dict()
    return {k: v for k, v in values.items() if k != "name"}


def moved_dials() -> dict[str, dict[str, float]]:
    """Every dial a required preset sets to something other than the baseline.

    The difference rule on its own. Kept separate from `required_dials`
    because the distinction is the finding: a dial in here is a choice
    somebody took against `BASELINE`, and a dial that is in scope only
    through `POST_BASELINE` is one the baseline could not express a choice
    about.
    """
    base = _dict(BASELINE)
    out: dict[str, dict[str, float]] = {}
    for preset in REQUIRED_PRESETS:
        for key, value in _dict(preset).items():
            if key in base and value != base[key]:
                out.setdefault(key, {})[preset] = value
    return out


def required_dials() -> dict[str, dict[str, float]]:
    """Every dial in scope, and what each required preset ships for it.

    Two rules, and the second is there because the first cannot see past a
    frozen baseline: a dial a required preset MOVES off `BASELINE`, and a
    dial named in `POST_BASELINE`, recorded at the value each preset ships
    whether or not it differs.

    Keyed by dial, then by preset, so a dial two presets set differently
    shows both values and an entry has to justify each. A `POST_BASELINE`
    dial is recorded the same way, so an entry for it goes stale on the same
    rule the moment a preset moves it.
    """
    out = moved_dials()
    for preset in REQUIRED_PRESETS:
        values = _dict(preset)
        for key in POST_BASELINE:
            if key in values:
                out.setdefault(key, {})[preset] = values[key]
    return out


def partition() -> dict[str, Any]:
    """Every settable dial, in exactly one bucket, with the leftovers named.

    `unclassified` is the one that matters. A dial added to `ModelParams`
    lands in no bucket, appears here, and fails `check()` until somebody
    decides which bucket it belongs in and writes the reason down. That is
    the whole point: the scope stops being a computation that can silently
    stop covering things.

    `overlapping` catches the other direction. A dial declared out of scope
    that a preset then moves would otherwise be counted twice and read as
    settled in one place while being a live choice in the other.
    """
    surface = set(settable_dials())
    moved = set(moved_dials())
    buckets = {
        "moved": moved,
        "post_baseline": set(POST_BASELINE),
        "out_of_scope": set(OUT_OF_SCOPE),
    }

    faults: list[str] = []
    for label, names in buckets.items():
        for dial in sorted(names - surface):
            faults.append(
                f"{dial}: declared in {label.upper()} and is not a settable "
                "dial of this build")

    overlapping: list[str] = []
    labels = list(buckets)
    for i, left in enumerate(labels):
        for right in labels[i + 1:]:
            for dial in sorted(buckets[left] & buckets[right]):
                overlapping.append(
                    f"{dial}: in both {left.upper()} and {right.upper()}; a "
                    "dial belongs to exactly one, and a live choice filed "
                    "as out of scope is the failure this partition ends")

    claimed = set().union(*buckets.values())
    unclassified = sorted(surface - claimed)

    return {
        "surface": len(surface),
        "moved": sorted(buckets["moved"] & surface),
        "post_baseline": sorted(buckets["post_baseline"]),
        "out_of_scope": sorted(buckets["out_of_scope"]),
        "unclassified": unclassified,
        "overlapping": overlapping,
        "faults": faults,
    }


def validate_entry(dial: str, entry: Any) -> list[str]:
    """Everything wrong with one entry, as a list; empty means valid."""
    problems: list[str] = []
    if not isinstance(entry, dict):
        return [f"{dial}: entry is {type(entry).__name__}, not a mapping"]

    kind = entry.get("kind")
    if kind not in KINDS:
        problems.append(f"{dial}: kind {kind!r} is not one of {list(KINDS)}")
        return problems

    for field in REQUIRED_FIELDS[kind] + ("presets",):
        if not entry.get(field):
            problems.append(f"{dial}: {kind} entry has no {field!r}")

    if kind == "measured" and not any(
            entry.get(f) is not None for f in MEASURED_ERROR_FIELDS):
        problems.append(
            f"{dial}: a measured entry carries no {' or '.join(MEASURED_ERROR_FIELDS)}. "
            "A figure shipped without an error bar is a chosen constant with "
            "more decimal places, and it is refused here rather than read as "
            "a measurement"
        )

    solve = entry.get("solve")
    if solve is not None:
        if not isinstance(solve, dict) or "tolerance" not in solve or "met" not in solve:
            problems.append(
                f"{dial}: a solve records its tolerance and whether it was met"
            )
        elif solve.get("met") is not True:
            problems.append(
                f"{dial}: the solve behind this value did not meet its "
                f"tolerance ({solve.get('tolerance')!r}), so there is no "
                "converged value to record. A last iterate under a name that "
                "asserts convergence is the defect this rule exists for: "
                "remove the entry rather than shipping the iterate"
            )

    presets = entry.get("presets")
    if isinstance(presets, dict):
        for preset in presets:
            if preset not in REQUIRED_PRESETS:
                problems.append(
                    f"{dial}: entry names preset {preset!r}, which is not in "
                    f"REQUIRED_PRESETS {list(REQUIRED_PRESETS)}"
                )
    return problems


def audit() -> dict[str, Any]:
    """The state of the table against what the presets actually ship.

    `missing` are dials with no entry and no place in `UNPROVENANCED`;
    `stale_unprovenanced` are names listed there that no longer need one;
    `invalid` are entries that fail the schema; `mismatched` are entries
    whose recorded value is not what the preset ships, which is exactly how
    a record goes stale when a dial moves under it; `post_baseline` are
    faults in the declared list itself; `unclassified` are settable dials
    in no bucket of the partition at all, which is what a newly added dial
    looks like before anyone has decided about it.
    """
    base = _dict(BASELINE)
    moved = moved_dials()
    required = required_dials()
    covered = set(DIAL_PROVENANCE) | set(UNPROVENANCED)

    post_baseline: list[str] = []
    for dial in sorted(POST_BASELINE):
        if dial not in base:
            post_baseline.append(
                f"{dial}: named in POST_BASELINE and is not a dial of "
                f"{BASELINE}")
        elif dial in moved:
            post_baseline.append(
                f"{dial}: named in POST_BASELINE, and "
                f"{', '.join(sorted(moved[dial]))} moves it off {BASELINE}. "
                "It is already in scope under the difference rule, and "
                "declaring it here as well hides that somebody chose it")

    part = partition()
    post_baseline.extend(part["faults"])
    post_baseline.extend(part["overlapping"])

    invalid: list[str] = []
    for dial, entry in sorted(DIAL_PROVENANCE.items()):
        invalid.extend(validate_entry(dial, entry))

    mismatched: list[str] = []
    for dial, entry in sorted(DIAL_PROVENANCE.items()):
        if dial not in required:
            mismatched.append(
                f"{dial}: has provenance but is not in scope -- no required "
                f"preset moves it off {BASELINE}, and it is not named in "
                "POST_BASELINE"
            )
            continue
        for preset, value in (entry.get("presets") or {}).items():
            shipped = required[dial].get(preset)
            if shipped is None:
                mismatched.append(
                    f"{dial}: entry claims {preset} sets it, and {preset} "
                    f"leaves it at the {BASELINE} value")
            elif shipped != value:
                mismatched.append(
                    f"{dial}: entry records {value!r} for {preset}, which "
                    f"ships {shipped!r}")

    return {
        "required": sorted(required),
        "moved": sorted(moved),
        "post_baseline_in_scope": sorted(set(required) - set(moved)),
        "post_baseline": post_baseline,
        "unclassified": part["unclassified"],
        "out_of_scope": part["out_of_scope"],
        "surface": part["surface"],
        "provenanced": sorted(DIAL_PROVENANCE),
        "missing": sorted(set(required) - covered),
        "unprovenanced": sorted(set(required) & set(UNPROVENANCED)),
        "stale_unprovenanced": sorted(set(UNPROVENANCED) - set(required)),
        "invalid": invalid,
        "mismatched": mismatched,
        "by_kind": {
            kind: sorted(d for d, e in DIAL_PROVENANCE.items()
                         if e.get("kind") == kind)
            for kind in KINDS
        },
    }


def report() -> str:
    """The audit as a table, for a human deciding what to derive next."""
    a = audit()
    lines = [
        f"{a['surface']} settable dials, partitioned",
        f"  {len(a['out_of_scope'])} out of scope, each with its reason",
        f"  {len(a['unclassified'])} unclassified",
        f"{len(a['required'])} dials in scope for "
        f"{', '.join(REQUIRED_PRESETS)}",
        f"  {len(a['moved'])} moved off {BASELINE}",
        f"  {len(a['post_baseline_in_scope'])} in scope because "
        f"{BASELINE} predates them",
        f"  {len(a['provenanced'])} with provenance "
        + ", ".join(f"{k} {len(v)}" for k, v in a["by_kind"].items()),
        f"  {len(a['unprovenanced'])} declared unprovenanced",
        f"  {len(a['missing'])} with neither",
    ]
    for label in ("missing", "invalid", "mismatched", "stale_unprovenanced",
                  "post_baseline", "unclassified"):
        for item in a[label]:
            lines.append(f"  {label.upper()}: {item}")
    return "\n".join(lines)


def check() -> None:
    """Raise unless the table describes what the presets actually ship."""
    a = audit()
    faults = (
        [f"no provenance and not declared unprovenanced: {d}" for d in a["missing"]]
        + a["invalid"] + a["mismatched"] + a["post_baseline"]
        + [f"settable and in no bucket of the partition -- decide whether it "
           f"is a choice needing provenance or record why it is not: {d}"
           for d in a["unclassified"]]
        + [f"declared unprovenanced but not in scope for any required "
           f"preset: {d}" for d in a["stale_unprovenanced"]]
    )
    if faults:
        raise ValidationError(
            "dial provenance does not describe the shipped presets:\n  "
            + "\n  ".join(faults))
