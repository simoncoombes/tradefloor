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
#: made better by demanding derivations for it now. pt-v19 is the one
#: proposed to ship: composed 2026-09-10, selectable, NOT the default.
REQUIRED_PRESETS = ("pt-v16", "pt-v18", "pt-v19")

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
#: set. These four are the dials `programme/PT-V19-CHARTER.md` section 3.1
#: names in its ledger that the difference-from-baseline rule cannot reach.
#: `vix_level_identity` was the fifth until pt-v19 moved it to 1.0 on
#: 2026-09-10; it is in scope under the difference rule now and has its
#: entry in `DIAL_PROVENANCE`, which is the transition this list exists
#: to make visible rather than absorb.
POST_BASELINE = {
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

#: Presets that RETURN a moved dial to the baseline value, on purpose and on
#: a measurement. Declared, because the difference rule is blind to a
#: return: the dial is in scope through the preset that moved it, and the
#: preset that moved it back reads as "leaves it at the pt-v1 value" -- the
#: one thing an entry is refused for claiming. Each name here puts that
#: preset in scope for the dial at the baseline value, so its entry can
#: record what was measured. `audit()` refuses a declaration whose preset
#: does not ship the baseline for the dial, or whose dial nobody moves.
RETURNED_TO_BASELINE = {
    "vix_decay_ratio": {
        "pt-v19": "pt-v16 moved it 1.0 -> 0.6 without provenance; pt-v19 "
                  "returns it to 1.0 as one of the four dials measured at "
                  "120 seeds against pt-v18, whose 0.6 is the paired control "
                  "(DIAL_PROVENANCE entry)",
    },
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
        "inert at 0.0: engine.rs update_volume_idio skips the per-name "
        "volume STATE when both it and `volume_idio_sigma` are 0.0, so the "
        "state has no memory and no innovation. Its partner "
        "`volume_idio_variance_gain` is a separate, stateless channel "
        "(market/tick.rs volume_multiplier, a name's GARCH variance over "
        "its sector's base) that pt-v19 switches on; this dial stays 0.0 "
        "there and stays inert",
    "volume_idio_sigma":
        "unread while `volume_idio_persistence` is 0.0",
}

#: The provenance of each shipped dial value.
#:
#: PARTIAL, AND THE REST IS DECLARED. Twenty-four entries: twenty read off
#: the code or off the doc comment that already carried the derivation,
#: and four (pt-v19's) read off the design repository's measured record,
#: rather than invented, so the schema is exercised by real data. The
#: other seventy-four names are in `UNPROVENANCED` and belong to the
#: workstreams that own them. Filling them in from here would be inventing
#: derivations, which is the failure this module exists to prevent.
DIAL_PROVENANCE: dict[str, dict[str, Any]] = {
    "oil_supply_response": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
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
        "presets": {"pt-v18": 1.0 / 3.0, "pt-v19": 1.0 / 3.0},
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
        "presets": {"pt-v16": 0.008583053614, "pt-v18": 0.008583053614, "pt-v19": 0.008583053614},
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
        "presets": {"pt-v16": 17.0, "pt-v18": 17.0, "pt-v19": 17.0},
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
        "presets": {"pt-v16": 0.05, "pt-v18": 0.05, "pt-v19": 0.05},
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
        "presets": {"pt-v16": 0.252, "pt-v18": 0.252, "pt-v19": 0.252},
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
        "presets": {"pt-v16": 1.0, "pt-v18": 1.0, "pt-v19": 1.0},
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
        "presets": {"pt-v18": 755.0, "pt-v19": 755.0},
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
        "presets": {"pt-v16": 0.025, "pt-v18": 0.025, "pt-v19": 0.025},
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

    # ---- the eight dials pt-v18 moves whose derivations were already
    # written, in `params.rs`'s doc comments, and were nowhere the audit
    # could see them. Transcribed 2026-09-07, one entry each, kind decided
    # off what the source actually establishes rather than off how long it
    # is: seven give an identity and its terms, and `cascade_symmetry`'s
    # own docstring declines the derivation in a section headed "What is
    # NOT derived here". Where a source is precise it is quoted; where a
    # derivation has an exception, the exception is in the entry, because
    # an entry that records only the identity overstates it.
    "market_beta_down_asym_lag": {
        "kind": "measured",
        "presets": {"pt-v18": 0.375, "pt-v19": 0.375},
        "source": "the certified panel plus index drift, the fear gauge and "
                  "the VIX's own persistence, scored by `loss.rule_table` at "
                  "nineteen rows, on thirty seeds over roster 40 @ seed 111 "
                  "at both certified horizons. One dial moved on pt-v18 and "
                  "nothing else",
        "date": "2026-09-07",
        "script": "programme/scripts/armboth.py via ptv19dials-jobs.sh "
                  "(design repo), arm B; registered at 053f3bf before the "
                  "run and recorded at 3175a4c",
        "residual": "the argmin of a SEVEN-POINT GRID -- 0, 0.25, 0.375, "
                    "0.5, 0.625, 0.75, 1.0 -- so the value is located to the "
                    "grid and not below it. Its neighbours read S_252 47.74 "
                    "and 46.59 against 46.25 here, and S_504 37.44 and 40.09 "
                    "against 36.05, so the surface is flat to about a point "
                    "of S across +/- 0.125 and nothing finer was measured",
        "estimator": "median across thirty seeds per row, the model error "
                     "each row's own across-seed spread, `df_model` 29",
        "note": "BOTH horizons pick this point, so unlike `vix_mean_reversion` "
                "beside it there is no frontier and no ruling. Full on is "
                "worse than off at 504 (82.52 against 49.72). An eight-seed "
                "screen read the argmin as 0.5 and a ten-mechanism survey "
                "marginal read it as monotone toward 1.0; both were wrong, in "
                "opposite directions, which is why this entry cites a "
                "thirty-seed one-dial arm and not either of them",
    },
    "vix_mean_reversion": {
        "kind": "measured",
        "presets": {"pt-v16": 0.06, "pt-v18": 0.10, "pt-v19": 0.10},
        "source": "the same nineteen-row objective and the same thirty-seed "
                  "arm, with `market_beta_down_asym_lag` pinned at 0.375",
        "date": "2026-09-07",
        "script": "programme/scripts/armboth.py via ptv19armd-jobs.sh "
                  "(design repo), arm D; registered at 92463f9 before the "
                  "run and recorded at 3175a4c",
        "residual": "THE OBJECTIVE DOES NOT PICK THIS VALUE. Three points "
                    "are non-dominated on the pair (`S_252`, `S_504`): 0.10 "
                    "at 28.69/29.78, 0.12 at 25.65/32.84 and 0.15 at "
                    "23.87/42.79. The spread across that frontier is 4.8 "
                    "points at 252 and 13.0 at 504, and it is a partial "
                    "order rather than an error bar",
        "estimator": "median across thirty seeds per row, the model error "
                     "each row's own across-seed spread, `df_model` 29",
        "chosen_from_a_frontier": "RULED by Simon on 2026-09-07 under R10, "
                                  "R6 having forbidden a combined number "
                                  "that would pick one of the three and hide "
                                  "the rest. A different ruling would have "
                                  "been equally consistent with the "
                                  "measurement, and this field exists so no "
                                  "reader mistakes the value for one the "
                                  "objective determined",
        "note": "what the ruling was made on: 0.10 is the only one of the "
                "three holding `vix_ar1_debiased` inside two standard errors "
                "of its ruler at both horizons (+0.7 and -2.0, against -2.7 "
                "and -6.8 at 0.15), and the only one where the two horizons "
                "score alike rather than one being bought at the other's "
                "expense. The shipped 0.06 is DOMINATED -- 0.12 beats it at "
                "both horizons -- so it was not going to stay whatever the "
                "ruling. Before the persistence row joined the rule the "
                "eighteen-row objective wanted 0.15 at 252 and 0.20 at 504; "
                "adding the row did not close that disagreement, it reversed "
                "which end was which",
    },

    # ---- the four dials pt-v19 moves off pt-v18, composed 2026-09-10 ------
    # All four MEASURED, on the design repository's record, and every
    # figure below is either read from a result note that names its box or
    # recomputed from that box's per-seed panels with the library's own
    # nineteen-row rule at fix/dn3-error-bar (`loss.scoring_rule`, blind on
    # nothing). Where the two routes differ -- the notes' script route puts
    # pt-v18 at 54.90 / 47.37 and the library at 56.00 / 48.59 on the same
    # 120 panels -- the library's figure is the one quoted and the note's
    # is given beside it. Golf scores, lower is better; "paired" means the
    # difference across the same seeds with a paired seed bootstrap (100
    # resamples of the seed index, the same index for both arms).
    #
    # Held-roster figures carry NO band verdict; the band verdicts are the
    # varying-roster certification's (`cert4b`), and they are stated as
    # such.
    "vix_level_identity": {
        "kind": "measured",
        "presets": {"pt-v19": 1.0},
        "source": "the nineteen-row scoring rule over ONE HUNDRED AND TWENTY "
                  "seeds (101-220) on roster 40 @ seed 111 at both certified "
                  "horizons, pt-v18 as the paired control in the same run; "
                  "composed with `vix_decay_ratio` 1.0 and `sector_loading` "
                  "0.8 as one cell of a 2 x 2 x 3 factorial and never "
                  "measured apart from them at 120 seeds. Band verdicts "
                  "from the varying-roster certification protocol "
                  "(`facts.LEVEL_PROTOCOL`, seeds 101-130, roster varying "
                  "with the seed), where the four-dial cell reads 18 of 18 "
                  "rows in band at BOTH horizons and pt-v18 reproduces its "
                  "published certification to four places in the same run",
        "date": "2026-09-09 (sectorcomp, resolve120); 2026-09-10 (cert4b)",
        "script": "programme/scripts/resolve120-jobs.sh (design repo), arm C "
                  "on pin 3d6462a, registered in resolve120-registration.md "
                  "before the box; the composition chosen on "
                  "sector-corr-result.md section 3 (run sectorcomp, thirty "
                  "seeds); certified by cert4-jobs.sh (cert4-registration.md, "
                  "run cert4b, i-04ce864fc91b0ab3e)",
        "residual": "A SWITCH, so there is no error bar on the value; the "
                    "residual is what it leaves and what it cannot be "
                    "separated from. The three-dial cell against pt-v18, "
                    "paired over 120 seeds: S_252 56.00 -> 32.58 (-23.42 "
                    "+/- 2.78, t -8.4) and S_504 48.59 -> 29.37 (-19.22 +/- "
                    "4.20, t -4.6); the notes' route reads 54.90 -> 32.90 "
                    "and 47.37 -> 30.05. Holding on the ninety seeds "
                    "(131-220) it was not selected on: -22.99 at 252. The "
                    "identity's OWN share is not separable: switched back "
                    "alone on the four-dial base at thirty seeds it costs "
                    "S_19 23.6 -> 50.8 / 23.5 -> 69.2 (volumescreen cell 3), "
                    "and the identity x decay interaction measures -14.7 on "
                    "S_504 (jointsolve-i, DECISIONS 2026-09-09), so this "
                    "dial and `vix_decay_ratio` are one regime with two "
                    "names. What it leaves: `vix_ar1_debiased` +1.77 se "
                    "HIGH at 252 and -2.35 se LOW at 504 on the four-dial "
                    "base -- opposite directions, so no VIX-side dial "
                    "centres both -- and `cross_sectional_corr` LOW by 1.1 "
                    "/ 5.2 tape se, of which the identity alone carries "
                    "-0.2 / -1.0 (crosscorr-result.md section 2)",
        "estimator": "per-row medians across seeds (mean for the level row, "
                     "the pooled median for fear_gauge_dn3, the pooled rate "
                     "for the tail), model error the across-seed spread, "
                     "Welch against the tape row, `df_model` 119; paired "
                     "seed bootstrap for differences",
        "note": "what the switch does: the VIX targets the level the index's "
                "own conditional variance implies, so the anchor is DERIVED "
                "(19.53 on the certified roster against the dial's 15.98) "
                "and the mean VIX moves 16.8 -> 21.5. Alone on pt-v18 at "
                "the shipped decay 0.6 it makes the VIX too persistent (AR1 "
                "0.9867 against the 252 ruler 0.9299, level-fix-arms.md), "
                "which is why it ships only with the decay ratio beside it. "
                "In that regime `fear_gauge_dn3` centres: 5.8162 against "
                "5.73, z_tape +0.13 on the varying roster (cert4b), from "
                "pt-v18's 3.2473 (z -3.80). Charter bar B4 -- a fear "
                "response that RISES across the graded range -- is NOT met "
                "by this switch or by any dial (programme/"
                "code-work-required.md section 1)",
    },
    "vix_decay_ratio": {
        "kind": "measured",
        "presets": {"pt-v16": 0.6, "pt-v18": 0.6, "pt-v19": 1.0},
        "source": "the same 120-seed paired run and the same varying-roster "
                  "certification as `vix_level_identity` above: the two were "
                  "composed and measured together and are one regime. "
                  "pt-v19 RETURNS the dial to the pt-v1 value "
                  "(RETURNED_TO_BASELINE). pt-v16 moved it to 0.6 without "
                  "provenance and pt-v18 inherited that; what the record "
                  "says about 0.6 is that it is the paired CONTROL of this "
                  "measurement -- the regime the fear rows read wrong in "
                  "(fear_gauge_dn3 3.2473 against 5.73, z -3.80 on the "
                  "varying roster) -- and, with the identity on, the "
                  "dominated point of the two measured",
        "date": "2026-09-09 (sectorcomp, resolve120); 2026-09-10 (cert4b, "
                "crosscorr)",
        "script": "programme/scripts/resolve120-jobs.sh (design repo), arm C; "
                  "sector-corr-result.md section 3 for the composition; "
                  "crosscorr-prior.py / crosscorr-result.md section 2 for "
                  "the attribution of what it costs",
        "residual": "Measured at TWO LEVELS ONLY, 0.6 and 1.0, in the 2 x 2 "
                    "x 3 composition and at 120 seeds; nothing between them "
                    "was measured, so the value is located to an endpoint of "
                    "a two-point grid and not to an optimum. Switched back "
                    "to 0.6 alone on the four-dial base at thirty seeds it "
                    "reads S_19 23.6 -> 47.0 / 23.5 -> 63.7 (volumescreen "
                    "cell 2); it is the dial that carries the fear fix -- "
                    "turning it back costs `fear_gauge_dn1` nine points and "
                    "`fear_gauge_dn3` twelve -- and it is also the dial that "
                    "carries TWO THIRDS of the four-dial cell's "
                    "`cross_sectional_corr` cost at 504: switched back it "
                    "moves that row +0.0772 +/- 0.0106 at 252 and +0.1012 "
                    "+/- 0.0120 at 504 (+2.3 / +10.5 tape se), against the "
                    "loading's +0.7 / +2.2. That row is a floor on this "
                    "base: three dials move it 3-4 tape se with the sector "
                    "row held and every one pays `vix_ar1_debiased`, "
                    "`corr_persistence_acf1` and `excess_kurtosis` back by "
                    "as much at 120 seeds (crosscorr-result.md section 4, "
                    "stop condition X9 triggered). At 1.0 the VIX sits at "
                    "its floor on 3.0 per cent of days (loopgain2, arm D)",
        "estimator": "as `vix_level_identity`",
        "note": "mechanism: the VIX reverts at the full rate rising and at "
                "`vix_decay_ratio` of it falling (economy/daily.rs). At 0.6 "
                "under the identity the asymmetry held the mean VIX above "
                "the derived anchor -- (VIX/anchor)^2 averaging 1.30 -- and "
                "fired the crisis blend on six per cent of days; at 1.0 the "
                "mean falls under the anchor (0.70) and the market factor's "
                "variance runs at 0.71 of base while a name's own stays at "
                "0.96, which is the whole of the cross-sectional cost "
                "(sector-corr-result.md section 1, crosscorr-result.md "
                "section 1)",
    },
    "sector_loading": {
        "kind": "measured",
        "presets": {"pt-v16": 0.58821442, "pt-v18": 0.58821442, "pt-v19": 0.8},
        "source": "pt-v16 and pt-v18 ship 0.58821442, set by an earlier "
                  "preset without provenance; what the record says of it is "
                  "the sectorcomp cell in pt-v18's own regime (identity "
                  "off, decay 0.6, thirty seeds): S_19 32.5 / 34.2 at "
                  "0.588 against 32.9 / 31.8 at 0.7 and 43.6 / 42.5 at 0.8, "
                  "flat between 0.588 and 0.7 and worse at 0.5 and 0.85, so "
                  "on pt-v18 the value sits on a plateau whose floor is "
                  "somewhere in 0.6-0.75 and was not located "
                  "(sector-corr-result.md section 6). For pt-v19: "
                  "`sector_excess_corr` on the nineteen-row rule: the "
                  "identity and the decay ratio take the row from -3.5 to "
                  "-6.7 tape se at 252 (0.1330 -> 0.1040), and the loading "
                  "puts it back on centre. Chosen as the third level of the "
                  "2 x 2 x 3 composition (sectorcomp, thirty seeds, held "
                  "roster 111) and located on a five-point grid on the "
                  "four-dial base (crosscorr screen, thirty seeds); "
                  "confirmed at 120 seeds as part of the three-dial cell "
                  "and on the varying roster at cert4b",
        "date": "2026-09-09 (sectorcomp, resolve120); 2026-09-10 (crosscorr, "
                "cert4b)",
        "script": "programme/scripts/atlas16-jobs.sh with AXES_SET=sectorcomp "
                  "on the factorial runner (run sectorcomp, engine pin "
                  "d84367a on feat/atlas-factor-axes; its cells reproduce "
                  "on release/0.7.1 @ 3d6462a to the digit, "
                  "volume-acf-result.md section 0), cells and effects by "
                  "factorial-cells.py (sector-corr-result.md section 3); "
                  "crosscorr-jobs.sh and crosscorr-analyse.py "
                  "(crosscorr-result.md section 3.3, prediction X5); "
                  "resolve120-jobs.sh arm C",
        "residual": "Located to +/- 0.05 on a grid of 0.7, 0.75, 0.8, 0.85, "
                    "0.9 (thirty seeds, four-dial base): S_19 at 0.75 reads "
                    "-1.0 / +2.3 from 0.8 and at 0.85 +0.4 / +2.3, inside "
                    "one bootstrap sd (5-8 per cell); 0.7 and 0.9 are +4 to "
                    "+7 worse. So the surface is FLAT across 0.75-0.85 and "
                    "0.8 is the centre of a plateau, not a resolved optimum. "
                    "The trade it makes is resolved: -0.0105 of "
                    "`cross_sectional_corr` per +0.027 of "
                    "`sector_excess_corr` per 0.1 of loading at 504 (tape se "
                    "0.00965 and 0.01169), so it buys 2.3 tape se of the "
                    "sector row per 1.0 of the cross-sectional row. Where "
                    "the sector row lands: 0.1641 against centre 0.1640 at "
                    "252 on the held roster (thirty seeds); at 120 seeds "
                    "0.1672 (+0.0309 +/- 0.0011 over pt-v18, +3.45 tape "
                    "se); on the varying roster 0.1792, z_tape +1.70 at 252 "
                    "and 0.1746, +1.12 at 504 (cert4b, in band both). The "
                    "one-dial cost it carries: switched back to 0.588 alone "
                    "on the four-dial base the cross-sectional row gains "
                    "+0.0216 / +0.0213 (+0.7 / +2.2 tape se) and the sector "
                    "row loses 4.6 tape se at 504 (crosscorr-result.md "
                    "section 2)",
        "estimator": "as `vix_level_identity`; the sector row's tape error "
                     "is `facts.rule_row`'s 0.008954 at 252 (median of nine "
                     "non-crisis windows)",
        "note": "mechanism: `L_i = sector_loading * (1 + slope * (beta_i - "
                "1))` loads a name onto its sector factor (market/factors.rs); "
                "`L^2 V_s / sigma^2` is the sector row and enters every "
                "pair's denominator, which is why raising it lowers the "
                "cross-sectional row at a fixed exchange rate. "
                "`sector_factor_sigma` is the same curve at the same rate "
                "(-0.0105 of row per +0.027 of sector between 0.0075 and "
                "0.0095) and was not moved",
    },
    "volume_idio_variance_gain": {
        "kind": "measured",
        "presets": {"pt-v19": 0.20},
        "source": "`volume_change_acf1` traced (volume-acf-result.md section "
                  "1) to a per-name volume-variance channel every earlier "
                  "preset ships at 0.0 -- a name's volume following its OWN "
                  "GARCH variance over its sector's base, market/tick.rs "
                  "`volume_multiplier` -- and this dial found to centre the "
                  "row in a nine-dial screen (volumescreen, thirty seeds, "
                  "three-dial base); then measured at 120 seeds at two "
                  "levels, 0.20 (arm F) and 0.25 (arm E), against the "
                  "three-dial cell as the paired control (iterate5); the "
                  "four-dial vector reproduced on two later boxes at "
                  "max|delta| = 0 over every numeric field (gainsweep g17, "
                  "crosscorr-confirm centre) and certified on the varying "
                  "roster (cert4b)",
        "date": "2026-09-09 (volumescreen, iterate5); 2026-09-10 (cert4b)",
        "script": "programme/scripts/iterate5-jobs.sh (design repo), arms "
                  "E and F on pin 3d6462a, registered in "
                  "iterate5-registration.md before the box "
                  "(i-02c66532c89dbc295); the screen by volumescreen-jobs.sh "
                  "and volumescreen-analyse.py; the paired figures below "
                  "recomputed from the iterate5 per-seed panels with "
                  "`loss.scoring_rule` at fix/dn3-error-bar on 2026-09-10",
        "residual": "Two levels at 120 seeds and a five-cell ridge at "
                    "thirty, so the value is located to a PLATEAU and not to "
                    "an optimum: 0.25 against 0.20 reads -0.12 +/- 1.28 "
                    "(t -0.1) at 252 and +1.97 +/- 1.14 (t +1.7) at 504; "
                    "the thirty-seed screen read 0.3 alone at S_19 13.1 +/- "
                    "5.4 / 17.0 +/- 8.5 against 23.6 / 23.5 at 0 (levels 0, "
                    "0.3, 1.0, 2.0 -- 1.0 overshoots the row by 15 tape se), "
                    "and its 4 x 3 composition with `volume_variance_gain` "
                    "put five cells from (0.15, 0.2) to (0.3, 0.028) inside "
                    "one sd of each other, recommending 0.2-0.3 with the "
                    "partner left at its shipped 0.028 (volume-acf-result.md "
                    "section 4). 0.20 is the lower of two doses the "
                    "objective cannot tell apart. What it buys, paired over "
                    "120 seeds "
                    "against the three-dial cell: S_252 32.58 -> 22.53 "
                    "(-10.05 +/- 1.34, t -7.5) and S_504 29.37 -> 25.54 "
                    "(-3.83 +/- 1.94, t -2.0); on the ninety seeds it was "
                    "not selected on, -11.50 at 252. The row itself: "
                    "-0.2869 -> -0.2653 at 252 (+0.0201 +/- 0.0018, +3.11 "
                    "tape se, centre -0.2550) and -0.2618 -> -0.2446 at 504 "
                    "(+0.0157 +/- 0.0011, +2.86 tape se, centre -0.2498). "
                    "What it pays: `volume_abs_return_corr` +0.0077 +/- "
                    "0.0020 at 252 and +0.0033 +/- 0.0016 at 504 -- +0.45 "
                    "and +0.30 of that row's tape se, inside the "
                    "registered bar of one; nothing else moves by more "
                    "than 0.15 tape se. THE ROSTER QUESTION, which the "
                    "registration named as the prediction most likely to "
                    "fail (cert4-registration.md Q4): on the VARYING roster "
                    "the row reads -0.2701 at 252, z -1.52 on the rule "
                    "against the registered bar of 1.5 (z_tape -2.35), and "
                    "-0.2508 at 504, z -0.14. The letter of Q4 fails at 252 "
                    "by 0.02 of z and holds at 504; its intent -- that the "
                    "dial was tuned to one roster -- does not hold, because "
                    "the paired gain over pt-v18 transfers: +0.0174 +/- "
                    "0.0042 (+2.70 tape se) on the varying roster against "
                    "+0.0145 (+2.25) on the held one, and the control reads "
                    "worse on the varying roster too (z -3.79 against "
                    "-3.9). Q5 holds: `volume_abs_return_corr` candidate "
                    "minus control -0.15 / +0.42 tape se. The registration "
                    "says a failed Q4 takes the dial out of the candidate; "
                    "that is a ruling for Simon and this entry records the "
                    "numbers it would be made on",
        "estimator": "as `vix_level_identity`; on the varying roster the "
                     "rule's Welch z over thirty seeds",
        "note": "the dial is clamped: the multiplier is `clamp(1 + gain * "
                "(garch_i / base_sector - 1), 0.25, 4.0)` per tick, and its "
                "partners `volume_idio_persistence` and `volume_idio_sigma` "
                "stay at 0.0, so the per-name volume STATE remains "
                "memoryless (engine.rs update_volume_idio) and this is a "
                "stateless channel. Every other mover of the row in the "
                "nine-dial screen pays on `volume_abs_return_corr` by the "
                "same mechanism (volume-acf-result.md section 3.2); this "
                "one is the cheapest on that row at equal row effect at "
                "every level measured, which is why it is the one that "
                "ships",
    },
    "market_beta_down_asym_recentre": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "`E[f 1{f<0}] = -s / sqrt(2 pi)` for `f ~ N(0, s^2)`. "
                    "Scaling one side of a zero-mean draw moves its mean, so "
                    "the tilt adds `a * beta * -s / sqrt(2 pi)` to every name "
                    "every tick, and at 1.0 the whole of it is returned as "
                    "`this * a * beta * s / sqrt(2 pi)`. Every quantity in "
                    "the form is known exactly where it is applied, so the "
                    "correction is arithmetic and not an estimate",
        "terms": {
            "s": "the CONDITIONAL per-tick sigma the factor was actually "
                 "drawn with, `shared.market_sigma_tick`, rather than "
                 "`market_factor_sigma` (market/factors.rs:574-583). That is "
                 "what makes the correction proof against the variance "
                 "process, the VIX coupling and a scenario that pins VIX: a "
                 "hotter tick injects more and gives back more, in the same "
                 "ratio",
            "a": "`market_beta_down_asym`, the tilt being corrected. The "
                 "correction is gated on BOTH dials being nonzero, so a "
                 "preset that sets this without the tilt is bit-identical",
            "beta": "per name, because this is not a correction but the "
                    "algebra of the line being corrected -- the injection "
                    "into name `i` IS `beta_i` times the form. Using 1.0 "
                    "would leave a residual proportional to `beta_i - 1`, a "
                    "cross-sectional bias as well as a mean one",
            "sqrt(2 pi)": "`SQRT_TWO_PI`, the constant of the half-normal "
                          "first moment; nothing here is fitted",
        },
        "source": "rust/src/params.rs, "
                  "ModelParams::market_beta_down_asym_recentre, sections "
                  "'Why the tilt injects a first moment at all' and 'What is "
                  "given back, exactly'; applied at "
                  "rust/src/market/factors.rs:574-583",
        "not_given_back": "THE CRASH AMPLIFIER, and an entry that omits this "
                          "overstates the derivation. The amplifier "
                          "multiplies the market channel above a threshold in "
                          "baseline sigmas and it is exactly the tail the "
                          "tilt scales, so the true injected mean is the form "
                          "above times `E[f 1{f<0} A] / E[f 1{f<0}]`. That "
                          "ratio has NO closed form; it was measured at 1.00 "
                          "to 1.38 across conditional sigmas and sits near "
                          "1.01 at the sigmas that occur. Correcting it would "
                          "need either a new bit-pinned transcendental or a "
                          "fitted constant, so the residual is left rather "
                          "than approximated: known, signed, and about one "
                          "per cent of the term. The offset is applied AFTER "
                          "the amplifier for the same reason -- added before "
                          "it, the offset would itself be amplified and "
                          "deliver the form times `E[A]`",
        "note": "the mean it returns was nobody's choice: it is the "
                "by-product of a correlation mechanism, and it cost the "
                "equal-weight index 7.9 percentage points a year at pt-v16. "
                "The volatility path's -1.670 acts THROUGH it rather than "
                "beside it, because a hotter conditional sigma injects "
                "proportionally more",
    },
    "oil_opec_symmetry": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "at 1.0 both branches of the OPEC rule use one "
                    "probability and one magnitude range, so the expected "
                    "impact is equal and opposite either side of the 80 "
                    "target and zero on net. The shipped pair does not "
                    "mirror: 0.6 at 3-to-6 below the target against 0.5 at "
                    "2-to-5 above it, an expected +2.700 against -1.750, so "
                    "the cut is 1.54 times the increase and the rule pushes "
                    "the oil price up",
        "terms": {
            "0.55": "the mean of the rule's own two probabilities, 0.6 and "
                    "0.5 (economy/daily.rs:860-864)",
            "2.5 to 5.5": "the mean of its own two magnitude ranges, 3-to-6 "
                          "and 2-to-5; the width of 3.0 is the one both "
                          "branches already carry, so no number is invented",
            "1.0": "the unique share at which the two branches coincide. "
                   "Below it the direction is only partly removed; the dial "
                   "is a share of the gap between the branches, not a level",
        },
        "source": "rust/src/params.rs, ModelParams::oil_opec_symmetry, "
                  "sections 'The asymmetry nobody chose' and 'Symmetrised "
                  "rather than picked'; the branch is "
                  "economy/daily.rs:860-864",
        "size_is_not_preserved_exactly": "the source calls this 'the unique "
                                         "symmetric rule which preserves the "
                                         "total intervention the rule "
                                         "performs', and that clause is off "
                                         "by 1.1 per cent. Averaging the "
                                         "probability and the magnitude "
                                         "SEPARATELY drops their cross term: "
                                         "0.6*4.5 + 0.5*3.5 is 4.450 before "
                                         "and 2*(0.55*4.0) is 4.400 after, so "
                                         "a symmetric rule preserving the "
                                         "total exactly would need "
                                         "`p*M = 2.225` and this one does "
                                         "not. What IS exact is the "
                                         "direction, which is what the dial "
                                         "is for; the conservation clause is "
                                         "approximate and is recorded here as "
                                         "approximate",
        "note": "worth about +0.95 of oil price per firing, and the rule "
                "fires every 90 days, so this is a small term. It is "
                "corrected because it is wrong rather than because it is "
                "large",
    },
    "oil_seasonality_target": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "the amplitude is SPLIT, `1 + g*a` on the reversion "
                    "target against `1 + (1-g)*a` on the price level, so the "
                    "total is conserved at every `g` and the level carries "
                    "none of the shape at exactly `g = 1.0`. A shape applied "
                    "to a LEVEL compounds, because the daily factors "
                    "multiply: their product is 5.119 over the 252 game-days "
                    "a certified year passes and 0.921 over a full 365, the "
                    "shape being near neutral over its own period while the "
                    "horizon slices it asymmetrically -- 162 days of the up "
                    "leg against 90 of the down",
        "terms": {
            "a = 0.03": "the amplitude the term already carries. This dial "
                        "is a share of it, so what changes is WHERE a shape "
                        "acts rather than how large it is",
            "g = 1.0": "the value at which the level's factor `1 + (1-g)*a` "
                       "is 1 exactly (economy/daily.rs:895-900). Past 1.0 "
                       "the level would carry the shape inverted, so the "
                       "endpoint is not a matter of degree",
        },
        "source": "rust/src/params.rs, ModelParams::oil_seasonality_target, "
                  "sections 'A shape applied to a level compounds' and 'The "
                  "target rather than the level, at the same amplitude'",
        "what_is_not_achieved": "neutrality over the window. The source's "
                                "requirement is that 'a seasonal shape has to "
                                "be neutral over the WINDOW as well as over "
                                "its own period', and on the target the shape "
                                "still integrates to +0.672 per cent of oil "
                                "over a certified year. So 1.0 is derived as "
                                "the value that takes the shape OFF THE "
                                "LEVEL, which is exact, and not as the value "
                                "that makes it neutral, which it approaches "
                                "-- +0.672 per cent against a level-side "
                                "product of 5.119",
        "note": "the defect is not the shape's size. Summing the term's own "
                "contribution to each day's change over year one gives "
                "+365.80 of oil price against a net change of +72.10, so it "
                "pushed about five times harder than the price moved: oil "
                "had no fixed point under it and sat on its 150.0 clamp from "
                "day 180 on every seed, with the inflation term, the meeting "
                "rule and the discount rate following it there",
    },
    "cycle_hazard_per_month": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "`weibull_hazard` returns `(shape/scale) * "
                    "pow(months/scale, shape-1)` and every scale in "
                    "`cycle_hazard_params` is in MONTHS -- 36 for an "
                    "expansion, 6 for a peak, 12 for a contraction -- so its "
                    "value is a rate per month, while it was compared "
                    "against a uniform once a day. "
                    "`months_in_current_phase` advances by exactly `1/30` a "
                    "day, so the month this engine keeps is 30 days and the "
                    "conversion is the monthly rate over 30. At 1.0 `per_day` "
                    "computes `monthly * (30 - 29)/30`, which is "
                    "`monthly / 30` to the last bit",
        "terms": {
            "30": "read off the engine's own clock and not chosen: "
                  "`months_in_current_phase` advances by `1.0/30.0` a day at "
                  "economy/daily.rs:1231, and `per_day` is "
                  "economy/cycle.rs:55-61",
            "the placement": "the conversion is applied LAST, after "
                             "`adjust_transition_probability` and after the "
                             "clamp, because every operand before it is a "
                             "rate per month: the hazard's own cap of 0.8, "
                             "the ladder's additions of 0.1 and 0.15, and the "
                             "clamp at 0.3. Converting earlier would leave "
                             "the ladder as a daily probability against a "
                             "base hazard near 0.0004 a day, so an inverted "
                             "curve would raise the transition rate by 250 "
                             "times where it now triples it. The 9.7 years "
                             "assumes this placement; dividing before the "
                             "clamp gives 9.59, the difference sitting "
                             "entirely in the two short phases",
        },
        "source": "rust/src/params.rs, ModelParams::cycle_hazard_per_month, "
                  "sections 'A rate per month drawn once a day' and 'The "
                  "whole ladder is in months'; the conversion is `per_day` at "
                  "rust/src/economy/cycle.rs:55-61",
        "the_conversion_is_linear_in_the_rate": "both readings treat the "
                                                "monthly figure as a "
                                                "PROBABILITY -- it is "
                                                "compared against a uniform "
                                                "directly and clamped at 0.3 "
                                                "-- and dividing by 30 is the "
                                                "exact conversion of a RATE, "
                                                "not of a probability. "
                                                "`1 - (1-p)^(1/30)` reads "
                                                "0.002812 against this "
                                                "reading's 0.0027 at an "
                                                "expansion's 0.081, and "
                                                "0.011819 against 0.01 at the "
                                                "clamp: 4.0 and 15.4 per cent "
                                                "high. That bears on neither "
                                                "clock, which is what this "
                                                "dial decides, and it is "
                                                "recorded because the "
                                                "identity above says 'to the "
                                                "last bit' about the "
                                                "arithmetic and not about the "
                                                "statistics",
        "note": "read once a day the cycle ran about thirty times too fast: a "
                "full cycle in 2.6 trading years against 9.7 read per month, "
                "and a 252-day run opening at the start of an expansion left "
                "it 63 per cent of the time against 3. Both figures are the "
                "hazard alone, and the ladder is scaled with the base because "
                "the conversion is applied after it, so the ratio of thirty "
                "is unaffected",
    },
    "jump_mean_compensated": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "a jump arriving with probability `lambda` and mean `m` "
                    "contributes `lambda * m` to the expected return every "
                    "day whether it fires or not. Subtracting `lambda * m` is "
                    "the standard compensated-Poisson construction: it makes "
                    "the jump term a martingale, and because the compensator "
                    "is a DETERMINISTIC offset it moves the first moment and "
                    "leaves every central moment untouched. The skew and the "
                    "fat tail survive exactly, at the mean the calibration "
                    "chose, and the drift goes to zero -- so the mean does "
                    "not move at all: what was wrong was the missing "
                    "compensator and not the value",
        "terms": {
            "lambda": "the CONDITIONAL intensity, already scaled by the VIX "
                      "coupling, so the compensator tracks the arrival rate: "
                      "`jump_mean_compensated * (intensity_market * "
                      "jump_mean_market)` at rust/src/engine.rs:2110. The "
                      "investigation measured the realised drift at 1.084 "
                      "times the day-zero closed form, so a compensator on "
                      "the day-zero rate would have left that 8 per cent "
                      "behind",
            "m": "`jump_mean_market`, unmoved. It is negative so that crashes "
                 "are larger than rallies, which is a real property of index "
                 "returns and a legitimate thing to want",
        },
        "source": "rust/src/params.rs, ModelParams::jump_mean_compensated, "
                  "sections 'The mean is there for skew, and it also buys a "
                  "drift' and 'Compensated rather than re-derived'",
        "no_smaller_mean_exists": "the obvious repair -- solve for a mean "
                                  "that buys the skew without the drift -- is "
                                  "refused rather than skipped: for a "
                                  "compound Poisson jump the drift and the "
                                  "skew are both LINEAR in the mean, so "
                                  "trading one against the other is a matter "
                                  "of degree and any answer would be a fitted "
                                  "constant",
        "note": "the drift was never chosen; it was never visible. "
                "`jump_mean_market` was set once in the pt-v4 era by a search "
                "whose objective could not read a first moment and inherited "
                "unchanged through eleven presets, at -0.11769 per name per "
                "year at pt-v16's day-zero intensity and 2.6 percentage "
                "points of annual index level",
    },
    "earnings_nominal_growth": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "identity": "price is `fair_value * exp(s)` with `s` a stationary "
                    "AR(2) around zero and `eps` fixed when an instrument is "
                    "built, so the only time variation in fair value is the "
                    "discount rate and the expected log change of the index "
                    "is ZERO in a stationary economy and negative in one "
                    "whose yields rise. At 1.0 `eps` and "
                    "`book_value_per_share` are multiplied by "
                    "`1 + this * (N_t/N_0 - 1)` with `N = gdp * cpi`, which "
                    "holds the earnings share of nominal output CONSTANT. "
                    "That is the only value read off the process rather than "
                    "chosen: below 1.0 the share falls every year and above "
                    "1.0 it rises for ever, both assertions about a quantity "
                    "this model does not carry",
        "terms": {
            "N_t / N_0": "nominal output against its value when the engine "
                         "was built (market/tick.rs:318-327). The multiplier "
                         "is 1.0 on day 0 by construction, so the opening "
                         "valuation, and the lazy initial `s` taken from it, "
                         "are unchanged",
            "both fields": "`eps` AND `book_value_per_share`, because the "
                           "valuation is then homogeneous of degree one in "
                           "nominal terms on both of its paths -- a "
                           "profitable company through `eps * target_pe` and "
                           "a loss-making one through "
                           "`book * LOSS_MAKING_PRICE_TO_BOOK`. Scaling only "
                           "earnings would make a loss-maker's fair value "
                           "fall in real terms every year",
            "the clock": "the economy compounds `gdp` by `gdp_growth/100/365` "
                         "and `cpi` by `inflation_rate/100/365` on every day "
                         "it advances, and it advances once per market day, "
                         "so a certified year of 252 sessions delivers "
                         "`252/365` of every annual rate. Measured on "
                         "`Universe.random(40, seed=111)` over 252 days at "
                         "pt-v18, seeds 1 to 6: a median of +4.353 per cent "
                         "per trading year against mean growth 3.353 and mean "
                         "inflation 2.931, and `(3.353 + 2.931) * 252/365` is "
                         "4.339, which is the clock stated as a number",
        },
        "source": "rust/src/params.rs, ModelParams::earnings_nominal_growth, "
                  "sections 'Why the model has no expected return without "
                  "this', 'What it scales, exactly', 'The clock, which is the "
                  "part that is easy to get wrong' and 'What it does NOT "
                  "claim'",
        "why_not_a_drift_in_s": "a premium placed in `s` gives a LEVEL and "
                                "not growth. Under a constant drift `c` per "
                                "step the stationary mean solves "
                                "`m = phi*m + c`, so what is injected is "
                                "`c/(1-phi)`, reached on the 60-day half-life "
                                "and followed by no growth at all. Simulated "
                                "at 3, 6 and 9 per cent a year it gave levels "
                                "of +0.010, +0.021 and +0.031 with "
                                "third-year growth of ZERO. An expected "
                                "return has to enter fair value",
        "what_it_does_not_claim": "a real price index earns 3 to 4 points a "
                                  "year ABOVE nominal output growth, through "
                                  "buybacks and the drift of the earnings "
                                  "share. The model has nothing to derive "
                                  "that from, so this term does not attempt "
                                  "it and the gap is reported rather than "
                                  "closed. The +4.353 is also a property of "
                                  "the opening expansion rather than of the "
                                  "model: on the same roster, seed 1, over "
                                  "1008 days the run leaves expansion and "
                                  "ends in a trough, and nominal output "
                                  "reaches 1.0685, which is 1.67 per cent a "
                                  "year",
    },
    "neutral_discount_rate": {
        "kind": "derived",
        "presets": {"pt-v18": 0.0482, "pt-v19": 0.0482},
        "identity": "`compute_target_pe` compresses the multiple by "
                    "`(discount - neutral) * RATE_PE_SENSITIVITY * duration` "
                    "(fair_value.rs:189), so a name is valued exactly on its "
                    "sector anchor when the discount rate equals this dial. "
                    "The value that zeroes the day-zero term is therefore the "
                    "corporate yield the economy RESTS at under the arm that "
                    "ships with it, read off the process rather than chosen",
        "terms": {
            "0.0482": "the corner the dynamics reach. pt-v18 also ships "
                      "`macro_burn_in_days` at 755, so the year opens at the "
                      "corner rather than at the opening state's 0.0456, and "
                      "the dial is set to whichever of the two the arm "
                      "actually opens at",
            "0.04": "what it replaces -- the module constant "
                    "`fair_value::NEUTRAL_DISCOUNT_RATE`, carried by every "
                    "preset before pt-v18. The economy opens at 4.56 per cent "
                    "and settles at 4.82 and never visits 4.00, which is why "
                    "every profitable name opened about one per cent below "
                    "the price the generator drew for it",
            "the generator": "untouched, and it has to be: the multiple this "
                             "anchors is the same sector anchor the generator "
                             "draws its multiples around, so the two stay "
                             "consistent under any neutral rate and a roster "
                             "opens at fair value exactly when the engine's "
                             "discount rate equals this",
        },
        "source": "rust/src/params.rs, ModelParams::neutral_discount_rate, "
                  "sections 'A neutral point the economy never visits' and "
                  "'Read off the economy rather than chosen'",
        "the_identity_is_exact_and_the_term_is_not": "0.0482 is read off the "
                                                     "burn-in table, which is "
                                                     "the same source "
                                                     "`macro_burn_in_days` is "
                                                     "filed as undetermined "
                                                     "for: it records ONE "
                                                     "path and no dispersion. "
                                                     "The level a field rests "
                                                     "at is a random variable "
                                                     "as much as the day it "
                                                     "gets there is, so what "
                                                     "would close this is the "
                                                     "corner yield re-read "
                                                     "across the certified "
                                                     "seed cohort with its "
                                                     "spread beside it. The "
                                                     "identity does not "
                                                     "depend on the answer; "
                                                     "the shipped digit does",
        "note": "the defect it repairs is +0.0107 of day-zero mispricing at "
                "the opening and +0.014 at the corner, unwound over the year "
                "on a 60-day half-life. At the second sweep's measured "
                "-94.872 index points per unit of opening level that is 1.0 "
                "to 1.3 points of the first year, on every seed, and nothing "
                "in a stationary year. PROMOTED rather than renamed: the name "
                "was already on the carried read-only surface, and `to_pairs` "
                "merges that surface with the settable one and sorts, so "
                "moving it leaves every preset's pairs, fingerprint and "
                "coefficient digest untouched wherever the value has not "
                "moved",
    },
    "cascade_symmetry": {
        # THE SOURCE DECLINES THE DERIVATION, in a section headed "What is
        # NOT derived here, and is worth saying". It names the tilt, the
        # jump and the oil supply term as the ones that have a stationarity
        # condition or a closed form and puts this one on the other side of
        # that line. The mean-of-the-pair construction is a rule for picking
        # the numbers under the dial, not a value read off the process, and
        # the docstring says so in as many words. Recording it as derived
        # because the neighbouring entries are would be exactly the
        # inherited authority this module exists to refuse.
        "kind": "undetermined",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0},
        "what_would_determine_it": "the ladder's expected contribution "
                                   "measured at 1.0 over the returns this "
                                   "engine actually produces. The drift "
                                   "argument runs over 'a symmetric "
                                   "distribution of daily returns', and this "
                                   "model deliberately does not have one -- "
                                   "`jump_mean_market` is negative so that "
                                   "crashes are larger than rallies -- so "
                                   "what an odd-symmetric ladder contributes "
                                   "HERE is a measurement nobody has taken. "
                                   "Behind that sits the open question the "
                                   "source leaves open: whether the "
                                   "ladders' bare literals should be "
                                   "parameters at all, since nothing "
                                   "recorded a reason for "
                                   "the difference between the two ladders "
                                   "and nothing could reach them",
        "declared_not_derived_in_source": True,
        "source": "rust/src/params.rs, ModelParams::cascade_symmetry, section "
                  "'What is NOT derived here, and is worth saying'; the "
                  "ladders are at rust/src/market/factors.rs:617-627",
        "what_the_rule_does_achieve": "the mean construction is exact here, "
                                      "unlike the OPEC rule it copies. "
                                      "Threshold 0.025 and tiers 0.007, "
                                      "0.0045, 0.0025 and 0.0005 give 0.0145 "
                                      "a side, against a shipped 0.017 down "
                                      "(four tiers, gate 0.02) and 0.012 up "
                                      "(three tiers, gate 0.03), so the "
                                      "pair's total intervention is 0.029 "
                                      "before and after and neither side is "
                                      "chosen. The GATES are deliberately "
                                      "left alone -- a stop-loss sits under "
                                      "every long, a buy-stop needs shorts to "
                                      "exist, so the upside keeps its "
                                      "`short_interest_ratio > 0.1` condition "
                                      "-- and that is finance rather than an "
                                      "accident. None of it derives 1.0; it "
                                      "conserves a size and removes a "
                                      "direction whose correct value nobody "
                                      "has measured",
    },
}

#: Dials in scope that carry NO entry.
#:
#: This list is the finding. Seventy-four names declared here against
#: ninety-seven dials in scope (one of them, `vix_mean_reversion`, beside
#: an entry that predates this note), and several
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
#:
#: Four names left on 2026-09-10 with pt-v19's entries: `vix_level_identity`,
#: `vix_decay_ratio`, `sector_loading` and `volume_idio_variance_gain`. A
#: `measured` entry names EVERY in-scope preset's value (asserted in
#: `tests/test_dial_provenance.py`), so where pt-v16 and pt-v18 ship an
#: older value for one of these -- 0.6 and 0.58821442 -- the entry says
#: what the record measured about that value (the paired control; a
#: plateau) rather than leaving it here.
UNPROVENANCED = (
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
    "cycle_stationary_opening",
    "daily_credit_floor_gain",
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
    "jump_mean_market",
    "jump_momentum_share",
    "jump_sigma_idio",
    "jump_sigma_market",
    "jump_vix_coupling",
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
    "news_market_weight",
    "news_peer_vix_coupling",
    "news_peer_weight",
    "news_peer_weight_down",
    "news_sector_weight",
    "order_flow_coefficient",
    "price_breaker_fraction",
    "price_hard_cap",
    "qe_pe_gain",
    "sector_loading_beta_slope",
    "sector_vix_coupling",
    "usd_crisis_vix_threshold",
    "vix_ceiling",
    "vix_cycle_amplitude",
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

    And a third, DECLARED rather than computed: a preset named in
    `RETURNED_TO_BASELINE` for a dial is in scope for that dial at the
    baseline value it ships. The difference rule cannot see a return --
    pt-v19 sets `vix_decay_ratio` to 1.0, which is pt-v1's value, on a
    measurement that pt-v16's 0.6 is the dominated control of -- and
    under the movers-only rule no entry could name pt-v19 for it without
    being refused as claiming a preset that "leaves it at the baseline
    value". `moved_dials` is unchanged, so the audit still reports who
    moved what, and `audit()` refuses a declaration whose preset does not
    in fact ship the baseline or whose dial nobody moved.
    """
    out = moved_dials()
    for preset in REQUIRED_PRESETS:
        values = _dict(preset)
        for key in POST_BASELINE:
            if key in values:
                out.setdefault(key, {})[preset] = values[key]
        for key, presets in RETURNED_TO_BASELINE.items():
            if preset in presets and key in values and key in out:
                out[key].setdefault(preset, values[key])
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

    for dial, presets in sorted(RETURNED_TO_BASELINE.items()):
        if dial not in moved:
            post_baseline.append(
                f"{dial}: named in RETURNED_TO_BASELINE and no required "
                f"preset moves it off {BASELINE}, so there is nothing to "
                "return from")
            continue
        for preset in sorted(presets):
            if preset not in REQUIRED_PRESETS:
                post_baseline.append(
                    f"{dial}: RETURNED_TO_BASELINE names {preset}, which is "
                    f"not in REQUIRED_PRESETS {list(REQUIRED_PRESETS)}")
            elif preset in moved[dial]:
                post_baseline.append(
                    f"{dial}: RETURNED_TO_BASELINE names {preset}, and "
                    f"{preset} ships {moved[dial][preset]!r}, not the "
                    f"{BASELINE} value {base[dial]!r}. A return that is not "
                    "a return hides a move")

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
