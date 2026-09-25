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
#: proposed to ship: composed 2026-09-10, recomposed 2026-09-20 on the
#: factorial (the market variance family and the idio jumps back to pt-v18),
#: and composed a fifth time on 2026-09-23 with twenty-three dials, the
#: design programme's candidate LMN-Q25A375 (programme/results/ptv19-fifth/
#: RESULT.md in the design repository). Fifteen of those dials left
#: OUT_OF_SCOPE and three left POST_BASELINE for entries below, and
#: `market_vol_vix_excursion` went the other way, back to pt-v1's 0.0.
REQUIRED_PRESETS = ("pt-v16", "pt-v18", "pt-v19", "pt-v20")

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
#: to make visible rather than absorb. The same transition, three at once,
#: on 2026-09-23: `cycle_stationary_opening`,
#: `macro_calendar_days_per_year` and `macro_compound_days_per_year` left
#: when pt-v19's fifth composition moved all three off pt-v1.
POST_BASELINE = {
    "rate_pe_sensitivity":
        "the P/E compression per unit of yield, a constant (1.5) until it "
        "became a dial at 0.8.5 at the value it carried. Read on the shipped "
        "path at every valuation of every name, so 1.5 is a choice, and one "
        "the 2022 P/E response measures at about three times that",
    "earnings_cycle_half_life":
        "added for pt-v20 (2026-09-24) with the aggregate earnings cycle: "
        "how fast earnings reach the phase's level. Unread while "
        "`earnings_cycle_depth` is 0.0, as it ships through pt-v19; LIVE on "
        "pt-v20, which ships the default 60 sessions unsearched",
    "earnings_cycle_sigma":
        "added for pt-v20 (2026-09-24) with the aggregate earnings cycle: "
        "the level's own daily noise. Unread while `earnings_cycle_depth` "
        "is 0.0, as it ships through pt-v19; LIVE on pt-v20, where 0.0 is "
        "a choice (the phase path alone)",
    "fair_value_market_share":
        "added for pt-v20 (2026-09-24) beside `fair_value_news_share`: the "
        "share of MARKET-WIDE shocks that moves fair value for good. Every "
        "preset ships 0.0, so the market's mispricing keeps the whole market "
        "move and pulls it back on its half-life; that is a choice with a "
        "measured cost (the index's annual spread, B9) and the owner decides "
        "it on the ptv20g1 box's arms",
    "jump_idio_vix_decoupled":
        "added at 0.8.0 for the idiosyncratic arrival rate under the "
        "identity; pt-v19 carried 1.0 from 2026-09-14 until the 2026-09-20 "
        "recomposition and every shipped preset leaves it at 0.0. LIVE on "
        "its own at 1.0 (test_model_params measures it moving the market "
        "with `jump_idio_excitation` at 0.0), so it is a choice at 0.0 and "
        "not an inert partner",
    "vix_variance_premium":
        "the measured variance risk premium. Read only while "
        "`vix_level_identity` is non-zero, so no preset has had to move it "
        "-- and 0.252 is a measurement, which is exactly the kind of number "
        "that must not be invisible because it arrived as a default",
    "garch_vix_exponent":
        "the exponent on the VIX ratio in a name's variance reference, "
        "which replaced an inline literal 2.0 at the value the literal "
        "carried. Read on the shipped path -- `garch_vix_coupling` is "
        "0.14219611 on pt-v16 onward, so `market/daily.rs` evaluates the "
        "reference every close of every name -- and it is the SHAPE of the "
        "one channel that carries a regime into a name's variance as a "
        "level rather than as a shock, so it is a live constant no preset "
        "has ever chosen",
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
        "pt-v20": "inherits pt-v19's return to 1.0 unchanged",
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
    "cycle_publication_lag":
        "inert at 0.0 as shipped: `Engine::published_cycle_phase` returns "
        "the phase the economy is in and no history is kept. It moves what "
        "the engine reports as the phase, never a price or a draw (0.8.5, "
        "pt-v20 work: the published phase predicted returns, design repo "
        "programme/ptv20-status-2026-09-25.md)",
    "gdp_publication_lag":
        "inert at 0.0 as shipped: `Engine::published_gdp_growth` returns "
        "the growth the economy runs at and no quarter is averaged. It moves "
        "what the engine reports as GDP growth, never a price or a draw "
        "(0.8.5, pt-v20 work: the daily growth stepped at every turn of the "
        "cycle and gave the turn away, design repo "
        "programme/ptv20-status-2026-09-25.md)",
    "unemployment_adjustment_half_life":
        "inert at 0.0 as shipped: `DailyInputs::unemployment_adjustment` is "
        "0.0 and the monthly release adds the whole cyclical drive, the "
        "expression that stood; the impulse is never read or written. It "
        "moves the TRUE unemployment rate when set (0.8.5, pt-v20 work: the "
        "first release after a contraction began rose about 1.2 pp, four "
        "times the spread of a release otherwise, and announced the turn; "
        "84 sessions takes it to 0.16 pp, design repo "
        "programme/ptv20-status-2026-09-25.md)",
    "fear_greed_published_inputs":
        "inert at 0.0 as shipped: `DailyInputs::fear_greed_published` is "
        "None and the index reads the economy's own phase and growth, the "
        "expression that stood; with both publication lags at 0 the switch "
        "is inert as well. Nothing a price, the bank, the cycle or a draw "
        "reads is downstream of the index (0.8.5, pt-v20 work: the index "
        "fell about 35 points in the five sessions after a contraction "
        "began, design repo programme/ptv20-status-2026-09-25.md)",
    "macro_publication_repricing":
        "inert at 0.0 as shipped: `Engine::published_macro_marks` returns "
        "None, so `reprice_to_published_macro` writes nothing and the close's "
        "macro step reaches prices at the next session's first tick "
        "(0.8.5, pt-v20 audit finding 3)",
    "fair_value_market_vol_cap":
        "inert at 0.0 as shipped: no preset carries `fair_value_market_share`, "
        "the only reader. A ceiling on the volatility whose market shocks "
        "are permanent (0.8.5, pt-v20 work: audit major 5, the long-horizon "
        "reversion)",
    "fair_value_market_linear":
        "inert at 0.0 as shipped: no preset carries `fair_value_market_share`, "
        "the only reader. Chooses the plain loading on the market draw as the "
        "permanent part (0.8.5, pt-v20 work: audit major 5, the long-horizon "
        "reversion)",
    "market_beta_down_asym_lag_recentre":
        "inert at 0.0 as shipped: the recentring offset is not scaled on a "
        "lagged session, and the lagged tilt's mean (about -8 per cent a "
        "year of the market input on pt-v20) sits in `s` as a constant "
        "discount while `fair_value_market_share` is 0.0 (0.8.5, pt-v20 "
        "work: audit major 5, the long-horizon reversion)",
    "earnings_anticipation_half_life":
        "inert at 0.0 as shipped: `Engine::earnings_anticipation_terms` "
        "returns None and the valuation reads the earnings cycle's level "
        "alone (0.8.5, pt-v20 work, grid ptv20e6)",
    # RETURNED TO 0.0 BY THE FIFTH COMPOSITION (2026-09-23). pt-v19 carried
    # the excursion form for two days with a derivation this table held;
    # the entry is in this file as of the composition commit (4d8f9cf) and
    # the argument is in `ModelParams::market_vol_vix_excursion`.
    "market_vol_vix_excursion":
        "inert at 0.0: engine.rs `close_market` branches on `== 0.0` and the "
        "factor's variance target measures the VIX against `self.vix_anchor`, "
        "the expression that stood at the call site, and "
        "`forced_vix_denominator` returns the free close's denominator. "
        "Every shipped preset is there since 2026-09-23. pt-v19 carried 1.0, "
        "the EXCURSION form (the loop's double count removed at its "
        "mechanism, a switch with no interior, measured on b4fix2 and "
        "b4fix4), from its third composition until its fifth returned it to "
        "pt-v1's 0.0 for the ANCHOR form of the VIX law, LAWC-D, where the "
        "anchor enters the VIX's own target through `vix_anchor_weight` "
        "(programme/results/vix-slow-regime/ and results/vix-law-levels/, "
        "design repository). The two forms are alternatives, so the "
        "switch's value is the choice of form and the choice is recorded "
        "under the anchor dials' entries",
    # THE FIVE THE 2026-09-20 RECOMPOSITION RETURNED TO 0.0 (six, until
    # `market_vol_gamma` returned to pt-v19 on 2026-09-21). Each shipped a
    # non-zero value on pt-v19 for six days, with a derivation the design
    # repository still holds; the factorial (programme/results/bestof)
    # measured the market variance family and the idio jump family away from
    # the tape and every shipped preset now leaves them where pt-v1 did.
    "market_vol_level_sigma":
        "inert at 0.0: market/factor_vol.rs:696 takes the `level == 1.0` "
        "branch, the level's normal (drawn unconditionally on "
        "stream::MARKET_VOL_LEVEL) is scaled by 0.0, and the multiplier is "
        "exactly 1.0 on every shipped preset since 2026-09-20",
    "market_vol_level_persistence":
        "unread while `market_vol_level_sigma` is 0.0: a level that never "
        "leaves 1.0 has no persistence to read",
    "jump_idio_excitation":
        "inert at 0.0: engine.rs:963 branches on `== 0.0` and the per-name "
        "excitation state is never written; every shipped preset ships 0.0 "
        "since 2026-09-20",
    "jump_idio_excitation_decay":
        "unread while `jump_idio_excitation` is 0.0 (engine.rs:963)",
    "jump_market_variance_share":
        "inert at 0.0: engine.rs branches on `!= 0.0` after the jumps "
        "mechanism's generated body, so nothing is added to the market "
        "factor's day accumulator and its GJR update is the shipped one. "
        "DERIVED 1.0 and shipped 0.0: the index GJR the shipped "
        "coefficients come from was fitted on the tape's TOTAL index "
        "returns, jumps in, so the whole of a market jump's log return "
        "belongs in the day's shock "
        "(programme/results/ptv19refine/jump-derivation.txt, design "
        "repository). Shipped at 0.0 because a derivation is not a "
        "measurement of the panel",
    "vix_anchor_reversion":
        "inert at 0.0: economy/daily.rs branches on `!= 0.0` after the VIX "
        "step's own sum, so the term is not added and the step is the three "
        "terms it always was. DERIVED 0.046081 and shipped 0.0: it is the "
        "kappa at which the linearised VIX-variance loop's slow pole equals "
        "the tape's own slow pole of log VIX, 0.9965, at "
        "`market_vol_vix_exponent` 1.83 and the shipped "
        "`vix_mean_reversion` 0.27 "
        "(programme/loop-level-law-design-2026-09-22.md section 2(b) and "
        "results/ptv19loop/looppoles.py, design repository). Shipped at 0.0 "
        "because the arm it was derived for was MEASURED and refused: on "
        "the level law the held-VIX index lever reads 4.61x against the "
        "design's own falsifier of 7x, below the shipped form's 4.90x. The "
        "derivation and the measurements are in "
        "`ModelParams::vix_anchor_reversion`",
    "vix_anchor_weight_level_below":
        "inert at 0.0: `economy::daily::anchor_weight_at_level` holds the "
        "level at the knee from below (`max(x, knee)`) when it is 0.0, so "
        "under the knee the weight is `vix_anchor_weight` itself. That is "
        "the law pt-v19 runs since its fifth composition switched the level "
        "law on (`vix_anchor_weight_level` 1.0); on every preset without the "
        "level law it is unread. 1.0 runs the law below the knee too, "
        "lowering the weight toward zero there, which was screened and "
        "refused (programme/results/vix-law-levels/ sections 4 and 5, "
        "design repository). A probe, not adopted",
    "vix_anchor_weight_level_knee_fixed":
        "inert at 0.0: economy/daily.rs branches on `!= 0.0` and the knee "
        "reads `L * anchor` as it always has, which is the knee pt-v19's "
        "level law reads since its fifth composition; on every preset "
        "without the level law it is unread. 1.0 takes the slow regime level "
        "out of the knee, which the held map places at an absolute VIX "
        "(vix-slow-regime section 7, design repository): it moves the "
        "turbulent rows toward the tape and changes no gate, and it was not "
        "in the adopted vector. A probe, not adopted",
    "crisis_epicentre_end_sessions":
        "unread while `crisis_epicentre_extra` is 0.0: with no episode ever "
        "entered there is no counter to end. 21 sessions is a month and is a "
        "CONVENTION rather than a measurement -- the tape's five episode "
        "windows are dated to the month, so the record fixes the order of "
        "magnitude and not the number; what would determine it is the "
        "distribution of gaps back under `crisis_vix_threshold` WITHIN those "
        "episodes at daily resolution, which the derivation does not carry. "
        "The hysteresis is what makes one crisis one episode: the tape's "
        "episodes are months long and the VIX crosses back under the threshold "
        "repeatedly inside each of them, so a shorter counter would redraw the "
        "epicentre mid-crisis and average three sectors across one episode",
    "garch_innovation_commensurate":
        "inert at 0.0: engine.rs `daily_innovation_column` branches on "
        "`== 0.0` and returns the `random_noise` attribution column the "
        "close has always been fed. DERIVED 1.0 and shipped 0.0. The "
        "defect it repairs: that column is `market_component * "
        "crash_amplifier + tilt_recentre + sector_component + "
        "idiosyncratic_noise`, so a name's GJR is fed three draws that are "
        "not its variance. Measured on the held roster (40 names, "
        "`Universe.random(40, seed=111)`, 504 days): of the column's mean "
        "square of 1.387 h, the market's and sector's draws together are "
        "0.810 h and the name's own only 0.473 h, because the tick draws "
        "a name's "
        "noise at `sqrt(max(h, idio_sigma_floor)) * idio_sigma_scale * "
        "cap_mult * volatility_multiplier / sqrt(390)` per tick. The shock "
        "coefficient therefore acts on the name's own variance at "
        "`(alpha + gamma P(neg)) kappa^2` = 0.0728 instead of 0.1511, the "
        "self-persistence is 0.863 where the dial vector says 0.9416 and "
        "the tape's names read 0.938, and the market re-excites the name "
        "at a gain of 0.72. At 1.0 the innovation is the name's own noise "
        "divided by the kappa the tick drew it with, which is the units "
        "the coefficients were fitted in. It ships at 0.0 because it is "
        "not a dial that can be taken alone: without "
        "`garch_omega_sector_scaled` 1.0 beside it the level collapses "
        "onto the clamp floor once the common noise leaves the "
        "innovation, and that pair is a box on the panel and not a "
        "derivation. MEASURED on 8 seeds of the held roster at 504 days, "
        "with the pair: fitted GJR persistence of the model's names "
        "0.8943 -> 0.9259 against the tape's 0.938, the variance state's "
        "log AR(1) 0.8770 -> 0.9166, and the innovation the recursion is "
        "fed 1.28 h -> 1.09 h. The dial alone reads 0.9004 and 0.8673, "
        "which is the collapse the pair exists to stop",
    "volume_move_jump_share":
        "inert at 1.0: market/tick.rs phase 3 branches on `== 1.0` and "
        "takes the move-from-open expression that stood there, so the "
        "volume scale reads the day's whole move, jumps included. "
        "UNDETERMINED rather than derived -- what would settle it is "
        "volume on jump days read off the tape, the share of a gap day's "
        "volume that the gap itself explains, and nobody has read it. "
        "1.0 is the arithmetic that was there and not a choice",
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
    "market_burn_in_sessions":
        "inert at 0.0: engine.rs `close_market` branches on `> 0.0` inside "
        "the level's own stationary-opening arm, so no state moves and "
        "`MarketVarianceState::warm_to_level` is never entered. It takes NO "
        "draw at any value -- the warm-up is a deterministic function of "
        "the level draw the close already makes -- so the branch cannot "
        "reach the schedule either. Registered unrun: "
        "`programme/results/warmup-registration.md` (design repository) is "
        "the box that would give it a value, and until that box reports "
        "the 0.0 is the absence of a measurement rather than the result of "
        "one. What it would fix is MEASURED and is not in doubt: "
        "`level-sigma-horizon.md` section 2 measures the two halves of a "
        "504-session recording disagreeing at sigma 0.085 and agreeing at "
        "sigma 0",
    "market_vol_alpha_excursion":
        "inert at 0.0: market/factor_vol.rs `alpha_beta_at` branches on "
        "`k == 0.0` and returns the dialled pair unchanged. Measured and "
        "REFUTED as a mechanism by the `alphax2` box, which found the "
        "clustering response flat from 0.0 to 0.40; it stays in the tree at "
        "zero with the refutation beside it",
    "market_idio_down_suppress":
        "inert at 0.0: market/factors.rs branches on `== 0.0` after the "
        "draw and neither scale is applied, so no preset that predates the "
        "dial multiplies by a pair of ones. It takes NO draw at any value "
        "-- it reshapes a shock the tick has already taken -- so the branch "
        "cannot reach the schedule either. Registered unrun: "
        "`programme/results/asymneut-registration.md` (design repository) "
        "is the box that would give it a value, and until that box reports "
        "the 0.0 is the absence of a measurement rather than the result of "
        "one",
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
    # The seven VIX-dynamics dials of programme/results/vix-dynamics.md,
    # each derived from the tape and each shipped at the value where its
    # branch is not taken. They leave this bucket together with the preset
    # that turns them on.
    "vix_innovation_sigma":
        "inert at 0.0: economy/daily.rs takes the shipped `0.15 * "
        "volatility` scale by the same expression when it and "
        "`vix_innovation_return_sigma` are both 0.0; the draw is the same "
        "draw either way",
    # The two per-component states of vix-dynamics.md section 19 and the
    # idiosyncratic-rate switch; each a branch at 0.0.
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
#: PARTIAL, AND THE REST IS DECLARED. Twenty-six entries: twenty read off
#: the code or off the doc comment that already carried the derivation,
#: four (pt-v19's four dials) read off the design repository's measured
#: record, and two (`vix_target_shock_cap` and
#: `crash_amplifier_conditional_sigma`, both charter bar B4) read off the
#: identity the code now derives them through --
#: rather than invented, so the schema is exercised by real data. The
#: other seventy-three names are in `UNPROVENANCED` and belong to the
#: workstreams that own them. Filling them in from here would be inventing
#: derivations, which is the failure this module exists to prevent.
DIAL_PROVENANCE: dict[str, dict[str, Any]] = {
    "vix_ceiling": {
        "kind": "derived",
        "presets": {"pt-v19": 181.3295, "pt-v20": 181.3295},
        "identity": "a VIX already at C must come off it on a session at "
                    "the top of the graded range: `C - implied(C) >= F(C)`, "
                    "where `implied(C)` is the settled read-back the map "
                    "sustains with the VIX pinned at C and `F(C)` is the "
                    "target's fear term for that session. This is a LOWER "
                    "BOUND on admissible ceilings -- the left side is "
                    "monotone increasing in C and the right side is "
                    "non-increasing, so once met it stays met -- and it is "
                    "no longer a solve. Under the LEVEL-BLIND law b4fix7 "
                    "ran, `F` was the constant `vix_return_gain * "
                    "GRADED_ABS_R` = 17.0 * 6.39 = 108.63 and the smallest "
                    "admissible C was 181.3295, solved on b4fix7's settled "
                    "pin ladder (ten rosters, burn 250, 80 scored sessions, "
                    "pins 14 to 260, crisis blend OFF): `C - implied(C)` is "
                    "monotone, 107.653 at pin 180 and 122.350 at 200, "
                    "crossing at C* = 181.3295 with a residual of +/- 8.64. "
                    "Under the law pt-v19 SHIPS the fear term falls with "
                    "the level, `F(C) = 8.83 * 6.39 ** 1.4483 * C ** "
                    "-0.4483`, which at C = 181.3295 is 12.5920, and the "
                    "smallest admissible C collapses to 56 to 67 "
                    "(ceiling-and-omega.md 4 and 5). 181.3295 is therefore "
                    "b4fix7's solve CARRIED FORWARD and re-verified, not a "
                    "value the shipping law's condition picks out; see "
                    "`why_the_solve_was_not_retaken` below",
        "terms": {
            "vix_return_gain 8.83": "the tape's fear slope at the tape's "
                                    "memory (`vix-dynamics.md` 11). It "
                                    "enters as the target's fear term. The "
                                    "17.0 the identity's historical clause "
                                    "quotes is the value b4fix7 solved at "
                                    "and is no longer shipped",
            "GRADED_ABS_R 6.39": "the range the tape grades, "
                                 "economy/daily.rs, the constant "
                                 "`flattens_at` sweeps for charter bar "
                                 "B4",
            "vix_return_exponent 1.4483 and vix_return_level_exponent "
            "0.4483": "the two exponents of the down law. They are why the "
                      "fear term is a function of C rather than a constant, "
                      "and why it is 8.6 times SMALLER at the shipped "
                      "ceiling than the 108.63 the solve used",
            "vix_mean_reversion 0.27": "what the ceiling clamps is the "
                                       "STATE after `x + 0.27 (target - "
                                       "x)`. The reversion rate does not "
                                       "enter the condition above, whose "
                                       "fixed point is `implied(C) + F(C)` "
                                       "at any rate; it is recorded because "
                                       "it is what makes the graded range's "
                                       "image on the state from rest a "
                                       "fraction of the target's, and it "
                                       "moved from 0.10 with the gain",
            "implied(C)": "MEASURED, and the whole of the residual: the "
                          "settled read-back at a pin, 51.81 at pin 108 "
                          "and 77.65 at pin 200, whose spread across ten "
                          "rosters carried through the local slope is "
                          "+/- 8.64 on C*. The ladders before b4fix7 ran "
                          "a 40-session burn sized for a persistence of "
                          "0.97 where the factor's is 0.979 and read the "
                          "settled level 9 per cent low, which is how the "
                          "value this replaces (173.1087) came to sit "
                          "under its own condition",
            "vix_target_shock_cap 158.8524": "ORDERING WITHDRAWN 2026-09-14 "
                                             "(ceiling-and-omega.md 3). This "
                                             "term used to read 'must stay "
                                             "above the ceiling or the cap "
                                             "binds first'. On pt-v19 the "
                                             "cap is 22.4771 points BELOW "
                                             "the ceiling and nothing "
                                             "breaks: the cap truncates an "
                                             "additive term of the TARGET "
                                             "and the ceiling truncates the "
                                             "STATE, so neither preempts the "
                                             "other at any value. A cap "
                                             "under the ceiling makes the "
                                             "ceiling less sticky, not "
                                             "more. The property that IS "
                                             "load-bearing -- the cap cannot "
                                             "bind where the clamp does "
                                             "not -- holds exactly, because "
                                             "the cap is the spike's "
                                             "supremum. See that entry's "
                                             "`ordering` field",
        },
        "solve": {
            "condition": "C - implied(C) >= F(C), a LOWER BOUND on the "
                         "ceiling, on the settled blend-off pin ladder",
            "as_solved": "F constant at 108.63, the level-blind law: "
                         "bracket pins 180 and 200, C* 181.3295, "
                         "tolerance 8.64",
            "as_it_ships": "F(C) = 8.83 * 6.39 ** 1.4483 * C ** -0.4483. "
                           "F(181.3295) = 12.5920, so the condition holds "
                           "for any settled read-back under 168.7375. The "
                           "closed-form map re-run with today's dials reads "
                           "implied(181.3295) = 71.88 / 70.15 / 75.89 on "
                           "the three b4fix3 rosters, and the largest "
                           "read-back the engine admits at that VIX -- the "
                           "factor variance pinned at its own 32x clamp -- "
                           "is 121.765 / 124.962 / 134.477. Both are under "
                           "168.74, the second as a bound rather than an "
                           "estimate",
            "bracket": "pins 180 and 200",
            "tolerance": 8.64,
            "met": True,
        },
        "why_the_solve_was_not_retaken": "The smallest C meeting the "
            "condition on the shipping law is 55.975 / 56.329 / 58.430 at a "
            "variance-level multiplier of 1.0 and 63.361 / 64.098 / 66.691 "
            "at 2.0 (closed-form map, blend off, three rosters, "
            "ceiling-and-omega.md 5). Adopting it WOULD restore the "
            "cap/ceiling ordering, and it is refused: the measured maximum "
            "VIX on this vector is 60.59 over twelve rosters at 504 days and "
            "b4fix9's gain-0 census read a highest VIX of 73.9 and a highest "
            "read-back of 93.2, so a ceiling at 56 to 67 sits INSIDE the "
            "record's own realised range and would clip. The `derived` kind "
            "on this entry rests on charter bar B3's argument that a bound "
            "which never binds cannot have been tuned; a ceiling that binds "
            "has no such defence, and a value chosen because it restores an "
            "ordering is the defect the campaign exists to prevent. What the "
            "arithmetic shows is that the condition stopped CHOOSING a "
            "value: it is a bound, 181.3295 satisfies it with about ten "
            "times the margin it had, and the tightness of the derivation is "
            "demoted rather than the value moved. The closed-form map used "
            "reproduces b4fix7's own solve as a control -- at the "
            "level-blind law and gain 17.0 it returns 180.183 / 177.887 / "
            "185.861 against the ladder's 181.3295 -- but carries b4fix3's "
            "roster constants, from a build without the slow variance level "
            "or the sector loading. A pin ladder on the shipped vector is "
            "what would sharpen the 56 to 67, and is a multi-seed run",
        "source": "programme/results/b4fix6-registration.md section 2 "
                  "and programme/results/ceiling-derivation-independent"
                  ".md, design repository, for the solve; "
                  "programme/results/ceiling-and-omega.md sections 4, 5 "
                  "and 6 for the re-derivation on the shipping law; "
                  "rust/src/params.rs, ModelParams::pt_v19. The relations "
                  "the value rests on are asserted by "
                  "`economy::daily::a_graded_session_moves_the_state_"
                  "its_reversion_share_of_the_way_to_its_target`, "
                  "`a_vix_at_the_ceiling_is_held_there_iff_the_target_"
                  "is_at_or_above_it` and "
                  "`the_default_ceiling_is_pinned_to_its_solve_and_the_"
                  "cap_ordering_is_withdrawn`, on "
                  "`update_economy_daily` itself with a silent RNG. "
                  "All three were RED on pt-v19 from the composed "
                  "vector until 2026-09-14 and are GREEN now: the "
                  "module asserted a ONE-LAW, LEVEL-BLIND response "
                  "and the preset ships a two-law level-dependent "
                  "one, so the tests were stale and were rewritten "
                  "against the derived law "
                  "(programme/results/fear-response-shape.md). The "
                  "ceiling relation itself did not change: what "
                  "moved is the threshold read-back that pins a "
                  "state to the clamp, from -73.67 under the "
                  "level-blind law at a cap of 255 to 134.74 under "
                  "the shipped one, because the response at the "
                  "ceiling carries C^-0.4483 and the zero-mean "
                  "term is negative there",
        "clip_rate": {
            "at_gain_0": "0 of 30,240 seed-days on 120 rosters at 252 "
                         "sessions, highest VIX 73.9, highest read-back "
                         "93.2 (b4fix9 gain-0 census, ceiling 173.1; a "
                         "ceiling of 181.33 is above both). The 95 per "
                         "cent upper bound on a rate that reads zero in "
                         "30,240 is 1.2e-4 per seed-day",
            "at_173.1087_with_the_blend": "0 of 30,240, highest VIX 120.38 "
                                          "(b4fix6, b4fix7)",
            "at_108.63": "4 of 30,240 on 3 rosters. Decomposed per day: "
                         "on three of the four the read-back ALONE was "
                         "above the ceiling (146.17, 142.58, 114.10) "
                         "with sessions of -10.31, -4.10 and -9.73 per "
                         "cent; the fourth carried a read-back of 96.99 "
                         "and a -3.46 per cent session. The clamp was "
                         "binding on the identity's own level on "
                         "variance excursions of 8.5 to 30 times the "
                         "factor's base, with the fear channel a "
                         "passenger",
        },
        "what_the_condition_is_not": "sufficient for the state the VIX "
                                     "carries when it reaches a ceiling. "
                                     "`implied(C)` on the ladder is the "
                                     "settled level at a pin; on the "
                                     "days the state reached 108.63 the "
                                     "identity read 114 to 146, because "
                                     "the variance was at an excursion "
                                     "and not at its target. At any "
                                     "ceiling the read-back can exceed by "
                                     "108.63, a graded session holds a "
                                     "VIX on the clamp; with the factor "
                                     "at its 32x clamp that is a "
                                     "read-back of 176 to 190 and a "
                                     "ceiling near 300. What makes "
                                     "173.1087 inert on the record is "
                                     "that the STATE does not reach it, "
                                     "measured, and not the condition",
        "note": "CHARTER BAR B3, ruled 2026-09-12 (RULINGS R15): a bound "
                "that never binds on the record cannot have been tuned "
                "against any graded statistic, so inertness satisfies B3 "
                "and the entry stays `derived`, with the condition, the "
                "solve and the measured clip rate as its evidence. Two "
                "earlier values are withdrawn: 108.63, 'the image of the "
                "range the tape grades under the fear response', which "
                "read a term of the target as a bound on the state (fear "
                "alone, from rest, reaches 34 to 36); and 173.1087, the "
                "solve of this condition on a 40-session-burn ladder, "
                "which sat 8 under the settled crossing on the same map",
    },
    "vix_level_persistence": {
        "kind": "derived",
        "presets": {"pt-v19": 0.9979, "pt-v20": 0.9979},
        "identity": "the year-to-year persistence of the tape's VIX regime, "
                    "as a daily AR(1): the lag-one autocorrelation of the 35 "
                    "yearly medians of log VIX (1990-2024) is 0.590, and "
                    "0.590^(1/252) = 0.99791 (half-life 331 sessions)",
        "terms": {"0.590": "lag-one autocorrelation of the yearly medians of "
                           "log ^VIX, 35 calendar years with over 200 "
                           "sessions; lag two 0.261",
                  "yearly median": "what a calm year and a crisis year "
                                   "differ in; the within-year movement is "
                                   "the fast pole's and the GJR triple's"},
        "source": "programme/results/ptv19gjr/regime-level-derivation.txt "
                  "and RESULT.md (design repository); the two-pole fit's "
                  "0.9965 (vix-level-derivation.txt) is the whole-span "
                  "ACF's slow pole with the crisis decay inside it and was "
                  "measured on vixlevel1 to add within-year variance the "
                  "tape's calm years do not have",
        "date": "2026-09-21",
    },
    "vix_level_sigma": {
        "kind": "derived",
        "presets": {"pt-v19": 0.0181, "pt-v20": 0.0181},
        "identity": "the era spread the level must add to what the loop "
                    "makes on its own: sigma = sqrt((V* - V0) / A) * "
                    "sqrt(1 - 0.9979^2) = 0.0181, the innovation of a daily "
                    "AR(1) at `vix_level_persistence`",
        "terms": {
            "V* 0.0796": "0.282^2, the tape's yearly-median variance of log "
                         "VIX read by the arms' estimator on the tape's "
                         "process (90% set 0.0435 to 0.159)",
            "V0 0.0133": "what the LAW loop makes with the level off (the "
                         "OFF arm)",
            "A 0.8445": "the share of an AR(1)'s stationary variance a "
                        "year's mean keeps at 0.9979",
        },
        "source": "programme/results/vix-slow-regime/RESULT.md section 4 "
                  "(design repository), derived on LAWC-D; taken by pt-v19's "
                  "fifth composition (results/ptv19-fifth/RESULT.md)",
        "date": "2026-09-23",
        "note": "Not exact: the split `V* = V0 + T^2 A sigma^2` is linear on "
                "a convex loop. Derived on LAWC-D before the calm fix (anchor "
                "weight 0.375, exponent 2.5 below the anchor), and not "
                "re-derived on pt-v19's own loop. LAWC-D's box read the "
                "yearly-median sd at 0.275 against the 0.282 target",
        "superseded": "0.0173 from the ptv19gjr composition (2026-09-21) to "
                      "the fifth, DERIVED as the innovation giving the level "
                      "its stationary spread at `vix_level_persistence`: "
                      "0.2667 * sqrt(1 - 0.99791^2) = 0.01723, with 0.2667 "
                      "the sd of the yearly medians of log ^VIX over 35 "
                      "years (variance 0.0711). That spread was the VIX's "
                      "own, measured on the loop's OUTPUT; "
                      "`vix_level_loop_gain` divided the loop back out. "
                      "Source programme/results/ptv19gjr/"
                      "regime-level-derivation.txt (design repository)",
    },
    "garch_vix_exponent": {
        "kind": "undetermined",
        "presets": {"pt-v16": 2.0, "pt-v18": 2.0, "pt-v19": 2.0, "pt-v20": 2.0},
        "what_would_determine_it": "a box on the PAIR this dial belongs "
                                   "to, because the shipped 2.0 is the "
                                   "market factor's exponent borrowed for "
                                   "the roster and nothing measured it on "
                                   "the names. What the channel is: a "
                                   "name's GJR level `garch_omega` is one "
                                   "constant whose unconditional variance "
                                   "is 3.42e-5, under the clamp floor for "
                                   "eight of the twelve sectors and at most "
                                   "2.2x it for the other four, so the name "
                                   "rests on the floor and the shock rather "
                                   "than on the constant. The floor is "
                                   "`garch_floor_multiple` times "
                                   "`base * (1 - c + c (vix / anchor)^e)`. "
                                   "That reference is the only place a "
                                   "name's variance reads the VIX as a "
                                   "LEVEL. Everything else it gets from "
                                   "the regime arrives as a SHOCK: "
                                   "`close_day_with` feeds the recursion "
                                   "the day's `random_noise`, which "
                                   "`market/factors.rs` builds as "
                                   "`market_component * crash_amplifier + "
                                   "tilt_recentre + sector_component + "
                                   "idiosyncratic_noise`, so the market "
                                   "factor re-excites the name at a gain "
                                   "of `(alpha + gamma / 2) / (1 - beta)`. "
                                   "Measured on the held roster at a VIX "
                                   "of 5 against 65 (40 names, "
                                   "`Universe.random(40, seed=111)`, "
                                   "medians over 60 graded days): the "
                                   "reference moves 2.28x, a name's GARCH "
                                   "variance 5.41x, the variance the tick "
                                   "draws with 3.57x, and at coupling 0.0 "
                                   "the same three read 1.00x, 4.61x and "
                                   "3.16x. What the tape asks for instead "
                                   "is a law: its names scale like its "
                                   "index, realised volatility ~ "
                                   "`VIX^0.7088`, so a name's variance "
                                   "reference must go as `VIX^1.4176`. A "
                                   "blend is a law only at `c = 1`, so the "
                                   "value is a PAIR -- `garch_vix_coupling` "
                                   "1.0 with this dial at twice the tape's "
                                   "exponent -- and a box on that pair, "
                                   "carrying the VIX row at 252 and 504, "
                                   "is what would determine it. Nothing "
                                   "here is fitted to the lever row: "
                                   "0.7088 is the tape's own exponent",
    },

    "jump_idio_vix_decoupled": {
        "kind": "undetermined",
        "presets": {"pt-v16": 0.0, "pt-v18": 0.0, "pt-v19": 0.0, "pt-v20": 0.0},
        "what_would_determine_it": "a derivation of how the idiosyncratic "
                                   "jump arrival rate should couple to the "
                                   "VIX. The 2026-09-20 factorial's idio "
                                   "family, which carried this dial at 1.0 "
                                   "with the excitation on, moved no graded "
                                   "row beyond noise in 32 of 32 pairs at "
                                   "either horizon (design-repo "
                                   "programme/results/bestof), so the panel "
                                   "cannot choose between 0.0 and 1.0 and "
                                   "the recomposition took pt-v18's 0.0",
    },

    "market_vol_gamma": {
        "sandwich_bread": "CORRECTED 2026-09-14, defect-15: these bars were computed with the EXPECTED information in the sandwich's bread where Bollerslev-Wooldridge uses the OBSERVED HESSIAN. `arch` reproduces every point estimate to the sixth decimal and none of these bars; substituting the Hessian into our own sandwich reproduces `arch` to under 2e-6 with every other line unchanged (arch-crosscheck.md). The information-matrix equality that would make the two forms equivalent FAILS here and fails in the beta corner -- fifteen of sixteen elements of H - A within 0.4 se of zero, `(beta, beta)` at -4.44 -- so the expected form loses its justification and the Hessian form keeps its own. The year-block bootstrap agrees in direction. No shipped VALUE moves and the likelihood ratio 305 is untouched, so the GJR term's adoption is unaffected. ",
        "composed": "shipped on pt-v19 from 2026-09-14, left on 2026-09-20 with the market variance family, returned on 2026-09-21 with `market_vol_alpha` and `market_vol_beta` (ptv19gjr): the three are one fit",
        "kind": "measured",
        "date": "2026-09-07",
        "estimator": "GJR-GARCH(1,1) by Gaussian quasi-maximum "
                     "likelihood on the tape's index log returns, whole "
                     "span, fitted beside a symmetric GARCH(1,1) on the "
                     "same series and window by the same estimator, and "
                     "compared by likelihood ratio",
        "script": "the estimator module reproduced whole in "
                  "programme/garch-derive-design.md Appendix B, extended "
                  "to the GJR form (the score recursion gains the term "
                  "`1[r < 0] r^2`); the fit and its sandwich are in "
                  "programme/results/ceiling-derivation-independent.md "
                  "of the design repository, section 7",
        # The GJR fit's OWN sandwich bar. This entry shipped for a day
        # with its point estimate pasted into the bar field, and then
        # with no bar at all while the symmetric fit's bars sat on the
        # other two coefficients of the same triple. The three bars
        # below are from one fit of the GJR form on the same 8,959
        # returns the symmetric fit used: Bollerslev-Wooldridge sandwich,
        # residual kurtosis E[z^4] 5.06. The likelihood ratio stays
        # beside it because it is the evidence the term is there at all.
        "estimate": 0.1556,
        # Was 0.0180 under the expected-information bread; see the
        # `sandwich_bread` note on this entry.
        "standard_error": 0.0236,
        # THE FITTED MODEL IS NOT THE APPLIED MODEL, recorded 2026-09-14
        # (defect-16, programme/results/ceiling-and-omega.md 7 to 9).
        "applied_form": "FITTED with a FREE omega: `s2 = omega + (alpha + "
                        "gamma 1[r<0]) r^2 + beta s2`, four parameters, "
                        "omega 0.020241. APPLIED VARIANCE-TARGETED: "
                        "rust/src/market/factor_vol.rs `component_step` "
                        "sets `omega = (1 - alpha - beta - gamma/2) * "
                        "target_variance`. The `gamma/2` rebate this "
                        "entry's `source` already describes is one half of "
                        "that expression; the other half, unrecorded until "
                        "now, is that the WHOLE intercept is pinned to the "
                        "target rather than carried from the fit. THE GAP: "
                        "the fitted model's unconditional variance is "
                        "0.965629 against the tape's var(r) = 1.305298, "
                        "0.7398 of it; targeting sets that to one, an "
                        "intercept of 0.027361 against 0.020241, +1.461 "
                        "omega bars. Re-fitting the triple under the "
                        "constraint gives alpha 0.011047, gamma 0.170056, "
                        "beta 0.888355 -- this coefficient moves +0.014495, "
                        "the +0.80 bars the defect recorded against the "
                        "WITHDRAWN 0.0180 and +0.62 against the corrected "
                        "0.0236. The restriction is REJECTED (LR 7.058 on 1 "
                        "df, p 0.0079); the triple's location is not (joint "
                        "Wald 4.111 on 3 df, p 0.25); the constraint is "
                        "absorbed in persistence, 0.979038 -> 0.984431, "
                        "+1.142 of its 0.004724 bar. The likelihood ratio "
                        "305 on one degree of freedom that adopts the GJR "
                        "term at all is measured on the FREE-omega fits on "
                        "both sides and is untouched by any of this",
        "residual": {
            "kind": "likelihood ratio against the symmetric GARCH(1,1)",
            "statistic": 305.0,
            "degrees_of_freedom": 1,
            "nll_gjr": 3551.49,
            "nll_symmetric": 3703.97,
            "sandwich_correlations": "corr(alpha, gamma) +0.12, "
                                     "corr(gamma, beta) -0.21, "
                                     "corr(alpha, beta) -0.48",
        },
        "presets": {"pt-v19": 0.1556, "pt-v20": 0.1556},
        "identity": "the tape's leverage response, at a LIKELIHOOD RATIO "
                    "of 2 * 152.5 = 305 on one degree of freedom. Same "
                    "tape, same window, same estimator:\n"
                    "  GARCH(1,1) omega 0.0190 alpha 0.1059 beta 0.8787 "
                    "NLL 3703.97\n"
                    "  GJR(1,1)   omega 0.0202 alpha 0.0066 gamma 0.1556 "
                    "beta 0.8946 NLL 3551.49\n"
                    "garch-derive-design.md 2.4: 'the real index's "
                    "variance responds to DOWN moves almost exclusively; "
                    "the symmetric 0.1059 is the pseudo-true symmetric "
                    "approximation of that.' This ships the fit rather "
                    "than the approximation, so `market_vol_alpha` and "
                    "`market_vol_beta` carry the GJR triple's values and "
                    "not the symmetric fit's -- the three are ONE "
                    "measurement and moving any of them alone would ship "
                    "a vector no fit produced",
        "source": "programme/garch-derive-design.md 2.4, design "
                  "repository. The dial is applied at "
                  "rust/src/market/factor_vol.rs `component_step`, which "
                  "loads `alpha + gamma` on a down day and `alpha` on an "
                  "up one and gives back `gamma/2` through omega, so it "
                  "redistributes variance between the two states rather "
                  "than adding any; it passes 0.0 for the SLOW "
                  "component, which is where 2.4's fit does not reach",
        "note": "WHY IT WAS ADOPTED, having been recorded and declined. "
                "2.4 left it to Simon because `market_vol_gamma` was "
                "outside 2.2's dial list. What made it necessary is the "
                "envelope's SHAPE panel -- the fourteen rows measured on "
                "the HELD roster, which is the protocol that certifies "
                "`excess_kurtosis`. With the symmetric fit that row read "
                "6.7284 at 504 days against a band floor of 7.1, 13 of "
                "14 in band; with the triple it reads 7.3005 and 14 of "
                "14 at both horizons. The symmetric approximation spreads "
                "a one-sided response evenly and discards most of the "
                "fourth moment with it. MEASURED, b4fix5. The registered "
                "risk -- that putting variance behind down moves would "
                "cost `index_tail_dn3_pct`, which counts down moves -- "
                "did not materialise: the tail improved, 1.8194 to "
                "1.3280 at 252. The GJR fourth-moment coefficient "
                "`3a^2 + 3ag + 1.5g^2 + 2ab + bg + b^2` is 0.9908, under "
                "one, so the finite fourth moment survives the asymmetry",
    },
    "market_vol_vix_exponent": {
        # THE FIFTH COMPOSITION (2026-09-23) REPLACED THE VALUE AND ITS KIND.
        # 4.9 was derived for the EXCURSION form's fixed point, and pt-v19
        # left that form the same day; the identity below it no longer
        # describes anything that ships, so it is kept in `superseded` and
        # the entry records what 4.0 is: a ladder reading, not a derivation.
        "kind": "undetermined",
        "presets": {"pt-v19": 4.0, "pt-v20": 4.0},
        "what_would_determine_it": (
            "the anchor form's own identity against the tape's crisis lever, "
            "the analogue of the excursion form's `e = 2 s / (2 - s)` below. "
            "FITTED: 4.0 is read off route 1's exponent ladder, where the "
            "held-VIX index lever lands near the tape's (10.34x against "
            "10.50x at 4.0; programme/results/route1-anchor/RESULT.md section "
            "2), and route1-blend/RESULT.md section 8.4 lists it under what "
            "is fitted (design repository). Above the anchor only since the "
            "fifth composition: below it the target reads "
            "`market_vol_vix_exponent_below`. It is load-bearing -- the level "
            "law's knee, eta and cap were read off the engine's held map at "
            "4.0 -- so a derivation that moved it would move those too"),
        "superseded": (
            "4.9 from the third composition (2026-09-21) to the fifth, "
            "DERIVED: the exponent at which the excursion form's fixed point "
            "carries the tape's crisis lever. Under `target = base (1 - c + "
            "c (VIX / I)^e)` with the read-back `I ~ sqrt(v)`, a held VIX "
            "settles the variance at `v ~ VIX^(e / (1 + e/2))`; the tape's "
            "6.16x of volatility for 13x of VIX is `v ~ VIX^1.42`, so `e = "
            "2 s / (2 - s)` = 4.9 at s = 1.42 (6.16x the real crisis lever, "
            "annualised volatility above VIX 45 over below VIX 12 on the "
            "reference roster; 13x the lever protocol's held VIX 65 over "
            "held VIX 5; the read-back rising 3.0x at the square and 4.6x at "
            "4.9). Source programme/fixes-2026-09-21.md section 0.1 and "
            "results/ptv19fix/RESULT.md (design repository). 2.0, the literal "
            "square, from pt-v1 to the 2026-09-21 composition"),
        "date": "2026-09-23",
    },
    "vix_level_loop_gain": {
        "kind": "derived",
        "presets": {"pt-v19": 1.79, "pt-v20": 1.79},
        "identity": "the running loop's transmission of the level into the "
                    "VIX at the derived spread. The transmission rises with "
                    "the spread, so the gain is the self-consistent solution "
                    "of `gain = T(sigma / gain)`, interpolated between two "
                    "measured cells on the LAW loop",
        "terms": {
            "1.75": "the transmission measured at the shipped spread",
            "1.78": "the transmission measured at 1.27 times it",
            "form": "engine.rs `vix_level_sigma_applied`: the innovation "
                    "and the stationary opening divided by the gain",
        },
        "source": "programme/results/vix-slow-regime/RESULT.md section 4 "
                  "(design repository), derived on LAWC-D; taken by pt-v19's "
                  "fifth composition (results/ptv19-fifth/RESULT.md)",
        "date": "2026-09-23",
        "note": "Checked on the box: LAWC-D's transmission reads 1.78 [1.69, "
                "1.88] against the 1.79 it was set to. Interpolated, not "
                "solved to a tolerance, so no `solve` is claimed. Derived "
                "before the calm fix and not re-derived on pt-v19's own loop",
        "superseded": "2.4684 from the third composition (2026-09-21) to "
                      "the fifth, DERIVED as `1 / (1 - h)` with `h = "
                      "ln(ratio) / ln 13` the read-back's held-VIX "
                      "elasticity on the excursion form: the read-back rose "
                      "4.60x for 13x of held VIX at `market_vol_vix_exponent` "
                      "4.9, so h = 0.595 (1.7486 at the square). Source "
                      "programme/fixes-2026-09-21.md design C and "
                      "results/ptv19fix/RESULT.md (design repository). The "
                      "form it read no longer ships",
    },
    "crisis_epicentre_extra": {
        "kind": "derived",
        "presets": {"pt-v19": 1.93, "pt-v20": 1.93},
        "identity": "the median of the tape's three epicentre episodes: the "
                    "epicentre sector's episode volatility over the median "
                    "sector's, per-sector volatility being the median over the "
                    "roster's 39 real names of each name's episode volatility "
                    "over its own calm-day volatility (VIX under 12); 2.43 in "
                    "2008-09, 1.93 in 2011, 1.41 in 2020. The epicentre is the "
                    "sector furthest above the episode's median if 1.3x or more "
                    "above it, a rule stated before the numbers were read; "
                    "2000-02 and 2022 have none, so `none` carries 0.4 of the "
                    "draw and financial_services 0.6",
        "terms": {"episodes": "2000-09..2002-10, 2008-09..2009-04, 2011-08..2011-11, "
                              "2020-02..2020-05, 2022-01..2022-12 on ^VIX and the "
                              "roster's names via tools/shadow/data.py",
                  "applied multiples": "the dial is on a name's TOTAL volatility; "
                                       "the market component is untouched, so the "
                                       "pair on the non-market parts is solved from "
                                       "the tape's ratio and conservation of the "
                                       "roster's non-market variance: g_up 2.037 on "
                                       "the epicentre, g_down 0.802 on the rest, at "
                                       "the measured market share 0.3916 and "
                                       "financials' share 0.1019 (`crisis_epicentre_solve`)",
                  "weights": "`Sector::crisis_weight` in rust/src/sectors.rs; the "
                             "four engine sectors with no name on the tape carry "
                             "0.0 as undetermined"},
        "source": "programme/epicentre-design-2026-09-22.md, "
                  "results/ptv19refine/epicentre-derivation.json and "
                  "results/ptv19epi3/RESULT.md (design repository); shipped "
                  "0.0 from the build on 2026-09-22 until the fourth "
                  "composition the same day",
        "date": "2026-09-22",
    },
    "market_vol_alpha": {
        "composed": "shipped on pt-v19 from 2026-09-14, returned to pt-v18's on 2026-09-20 by the 2^6 factorial (design-repo programme/results/bestof/RESULT-504.md), which measured the market variance family as one block, and returned to pt-v19 on 2026-09-21 when ptv19gjr (design-repo programme/results/ptv19gjr/RESULT.md) measured the block's three parts apart: the cost the factorial saw was the factor level's, and this value with the slow pole and the regime level on the VIX law is the first vector to pass the whole gate",
        "sandwich_bread": "CORRECTED 2026-09-14, defect-15: these bars were computed with the EXPECTED information in the sandwich's bread where Bollerslev-Wooldridge uses the OBSERVED HESSIAN. `arch` reproduces every point estimate to the sixth decimal and none of these bars; substituting the Hessian into our own sandwich reproduces `arch` to under 2e-6 with every other line unchanged (arch-crosscheck.md). The information-matrix equality that would make the two forms equivalent FAILS here and fails in the beta corner -- fifteen of sixteen elements of H - A within 0.4 se of zero, `(beta, beta)` at -4.44 -- so the expected form loses its justification and the Hessian form keeps its own. The year-block bootstrap agrees in direction. No shipped VALUE moves and the likelihood ratio 305 is untouched, so the GJR term's adoption is unaffected. ",
        "kind": "measured",
        "date": "2026-09-07",
        "estimator": "GJR-GARCH(1,1) by Gaussian quasi-maximum "
                     "likelihood on the tape's index log returns, whole "
                     "span, with a Bollerslev-Wooldridge sandwich "
                     "covariance; the symmetric GARCH(1,1) fit on the "
                     "same series is recorded beside it",
        "script": "the estimator module reproduced whole in "
                  "programme/garch-derive-design.md Appendix B, in its "
                  "GJR form; numpy-only Gaussian QMLE, no scipy",
        # THE BAR IS THE GJR FIT'S, FOR THE GJR VALUE. This entry shipped
        # for a day carrying the symmetric fit's bar (0.0093, which
        # belongs to 0.1059) beside the GJR value 0.0066. The sandwich on
        # the GJR fit itself reads 0.0109, so the shipped alpha is 0.6
        # standard errors from zero: the tape's variance responds to
        # down moves through gamma and the symmetric-term alpha is not
        # distinguishable from nothing. That is a property of the tape
        # and is recorded rather than smoothed over.
        "estimate": 0.0066,
        # Was 0.0109 under the expected-information bread; OURS WAS THE
        # WIDER ONE here, 1.33x, so the correction tightens it.
        "standard_error": 0.0082,
        # THE FITTED MODEL IS NOT THE APPLIED MODEL, recorded 2026-09-14
        # (defect-16, programme/results/ceiling-and-omega.md 7 to 9).
        "applied_form": "FITTED with a FREE omega: `s2 = omega + (alpha + "
                        "gamma 1[r<0]) r^2 + beta s2`, four parameters, "
                        "omega 0.020241. APPLIED VARIANCE-TARGETED: "
                        "rust/src/market/factor_vol.rs `component_step` "
                        "sets `omega = (1 - alpha - beta - gamma/2) * "
                        "target_variance`, so there is no omega dial and "
                        "the recursion's unconditional variance is the "
                        "engine's target by construction. THE GAP: the "
                        "fitted model's own unconditional variance is "
                        "omega/(1 - alpha - gamma/2 - beta) = 0.965629 "
                        "against the tape's var(r) = 1.305298, i.e. 0.7398 "
                        "of it; variance targeting sets that ratio to one, "
                        "which on this tape is an intercept of 0.027361, "
                        "+1.461 of omega's own corrected bar (0.00487). "
                        "Re-fitting the triple under the constraint gives "
                        "alpha 0.011047, gamma 0.170056, beta 0.888355 -- "
                        "this coefficient moves +0.004430, which is +0.41 "
                        "of the withdrawn expected-information bar and "
                        "+0.54 of the corrected one. The restriction is "
                        "REJECTED by the tape (likelihood ratio 7.058 on 1 "
                        "df, p 0.0079) but the triple's LOCATION is not "
                        "(joint Wald 4.111 on 3 df, p 0.25): the fit "
                        "absorbs the constraint by raising persistence "
                        "0.979038 -> 0.984431, +1.142 of its own 0.004724 "
                        "bar. Nothing is adopted from this -- the level the "
                        "constraint pins is the TAPE's and the engine's is "
                        "set independently by `market_factor_sigma`, so the "
                        "rejection does not transfer -- but the applied "
                        "model runs the factor's unconditional variance "
                        "1.352x, +16.3 per cent in volatility, above where "
                        "an intercept carried across as a fraction of the "
                        "target would put it, and nothing checks the two "
                        "against each other",
        "presets": {"pt-v16": 0.28035004, "pt-v18": 0.28035004,
                    "pt-v19": 0.0066, "pt-v20": 0.0066},
        "identity": "GJR(1,1) by Gaussian QMLE on the tape's index over "
                    "the whole span: omega 0.0202, alpha 0.0066 "
                    "(sandwich se 0.0082), gamma 0.1556 (0.0236), beta "
                    "0.8946 (0.0181); corr(alpha, gamma) +0.12, "
                    "corr(alpha, beta) -0.48. The symmetric fit on the "
                    "same series reads alpha 0.1059 (0.0093), beta "
                    "0.8787 (0.0092), corr -0.88, and is the "
                    "pseudo-true symmetric approximation of this one",
        "source": "programme/garch-derive-design.md 0 and 2, design "
                  "repository; the estimator, its window and its two "
                  "residual treatments are 2.1 to 2.3",
        "note": "A MEASUREMENT REPLACING A SEARCH OPTIMUM, which is "
                "charter bar B3: pt-v14's 0.28035004 carries no error "
                "bar at all. It is only transportable because "
                "`market_vol_vix_excursion` removes the loop's double "
                "count -- finding 4 -- and measured without it the same "
                "value moves the index tail the wrong way, 3.0677 to "
                "3.2271. The 0.65/0.35 mixture dilutes the pair to "
                "about (0.085, 0.895), which is finding 1 and is a "
                "KNOWN RESIDUAL left uncorrected, because inflating a "
                "measured coefficient to cancel a mixture is a constant "
                "compensating for a mechanism",
    },
    "market_vol_beta": {
        "composed": "shipped on pt-v19 from 2026-09-14, returned to pt-v18's on 2026-09-20 by the 2^6 factorial (design-repo programme/results/bestof/RESULT-504.md), which measured the market variance family as one block, and returned to pt-v19 on 2026-09-21 when ptv19gjr (design-repo programme/results/ptv19gjr/RESULT.md) measured the block's three parts apart: the cost the factorial saw was the factor level's, and this value with the slow pole and the regime level on the VIX law is the first vector to pass the whole gate",
        "sandwich_bread": "CORRECTED 2026-09-14, defect-15: these bars were computed with the EXPECTED information in the sandwich's bread where Bollerslev-Wooldridge uses the OBSERVED HESSIAN. `arch` reproduces every point estimate to the sixth decimal and none of these bars; substituting the Hessian into our own sandwich reproduces `arch` to under 2e-6 with every other line unchanged (arch-crosscheck.md). The information-matrix equality that would make the two forms equivalent FAILS here and fails in the beta corner -- fifteen of sixteen elements of H - A within 0.4 se of zero, `(beta, beta)` at -4.44 -- so the expected form loses its justification and the Hessian form keeps its own. The year-block bootstrap agrees in direction. No shipped VALUE moves and the likelihood ratio 305 is untouched, so the GJR term's adoption is unaffected. ",
        "kind": "measured",
        "date": "2026-09-07",
        "estimator": "GJR-GARCH(1,1) by Gaussian quasi-maximum "
                     "likelihood on the tape's index log returns, whole "
                     "span, with a Bollerslev-Wooldridge sandwich "
                     "covariance; the symmetric GARCH(1,1) fit on the "
                     "same series is recorded beside it",
        "script": "the estimator module reproduced whole in "
                  "programme/garch-derive-design.md Appendix B, in its "
                  "GJR form; numpy-only Gaussian QMLE, no scipy",
        # As `market_vol_alpha`: the GJR fit's own bar for the GJR value.
        "estimate": 0.8946,
        # Was 0.0085 under the expected-information bread. This is the
        # 2.1x one and the corner where the information-matrix equality
        # fails, which is why it is the largest of the four.
        "standard_error": 0.0181,
        # THE FITTED MODEL IS NOT THE APPLIED MODEL, recorded 2026-09-14
        # (defect-16, programme/results/ceiling-and-omega.md 7 to 9).
        "applied_form": "FITTED with a FREE omega: `s2 = omega + (alpha + "
                        "gamma 1[r<0]) r^2 + beta s2`, four parameters, "
                        "omega 0.020241. APPLIED VARIANCE-TARGETED: "
                        "rust/src/market/factor_vol.rs `component_step` "
                        "sets `omega = (1 - alpha - beta - gamma/2) * "
                        "target_variance`, so there is no omega dial and "
                        "the recursion's unconditional variance is the "
                        "engine's target by construction. THE GAP: the "
                        "fitted model's own unconditional variance is "
                        "0.965629 against the tape's var(r) = 1.305298, "
                        "0.7398 of it; variance targeting sets that ratio "
                        "to one, an intercept of 0.027361 against the "
                        "fitted 0.020241, +1.461 omega bars. Re-fitting the "
                        "triple under the constraint gives alpha 0.011047, "
                        "gamma 0.170056, beta 0.888355 -- this coefficient "
                        "moves -0.006285, which is the -0.74 bars the "
                        "defect recorded against the WITHDRAWN "
                        "expected-information bar of 0.0085 and -0.35 "
                        "against the corrected 0.0181. That halving is the "
                        "whole effect of defect-15 on this defect: the "
                        "absolute gap is unchanged and its significance is "
                        "not. The restriction is REJECTED (LR 7.058 on 1 "
                        "df, p 0.0079), the triple's location is not (joint "
                        "Wald 4.111 on 3 df, p 0.25), and the constraint is "
                        "paid for in persistence: `alpha + gamma/2 + beta` "
                        "0.979038 -> 0.984431, +1.142 of its own 0.004724 "
                        "bar, fast half-life 6.23 -> about 8 sessions. That "
                        "is the direction that would read at 504 days and "
                        "is the reason this is recorded rather than "
                        "adopted",
        "presets": {"pt-v16": 0.69244622, "pt-v18": 0.69244622,
                    "pt-v19": 0.8946, "pt-v20": 0.8946},
        "identity": "the same fit as `market_vol_alpha`: beta = 0.8946, "
                    "sandwich se 0.0181, corr(beta, omega) -0.91. The "
                    "GJR persistence `alpha + gamma/2 + beta` is 0.9790; "
                    "the symmetric fit's `alpha + beta` is 0.9846 +/- "
                    "0.0046 against the pt-v14 optima's 0.9728. The "
                    "fourth-moment condition `3a^2 + 2ab + b^2` is 0.993 "
                    "at the symmetric values against the old fast "
                    "component's 1.104, so the factor gains a finite "
                    "fourth moment it did not have",
        "source": "programme/garch-derive-design.md 0 and 2, design "
                  "repository",
        "note": "3.4's option B would have corrected this value for "
                "the loop, to 0.844, and that note REJECTED the "
                "correction because it depends on `c` (an unprovenanced "
                "dial) and on `s_f` (a roster property), and a value "
                "that depends on the roster belongs in the engine as an "
                "identity rather than in a preset. "
                "`market_vol_vix_excursion` makes the correction "
                "identically zero instead of estimating it: at "
                "`c s_f` = 0 the expression is `beta_tape` exactly. The "
                "dependency is removed, not approximated",
    },
    "crash_amplifier_conditional_sigma": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "the VIX loop's stability condition. Under "
                    "`vix_level_identity` the deterministic map is "
                    "`v -> implied(v)`, the state lives on "
                    "`[10, vix_ceiling]`, and the top of that interval is "
                    "absorbing exactly when `implied(v) >= v` near it, so "
                    "the condition is `(S) implied(v) < v` for every v in "
                    "`(crisis_vix_threshold, vix_ceiling]`. `implied(v)` "
                    "carries the crash amplifier's second moment "
                    "`E[z^2 A^2]` (`market::index_var::amplifier_moments`), "
                    "which at `a = m s` and `c = T / s` grows without bound "
                    "in the regime ratio `s = sqrt(v_f) / "
                    "market_factor_sigma` -- so the map is SUPERLINEAR and "
                    "(S) fails at the ceiling for a large enough excursion, "
                    "whatever any dial is set to. Normalising the shock by "
                    "the tick's own conditional sigma sets `a = m` and "
                    "`c = T`: `E[z^2 A^2]` becomes a constant of the dials, "
                    "the amplified factor block is linear in `v_f` exactly "
                    "as the unamplified one is, `implied(v)` is "
                    "asymptotically linear in `v`, and the map cannot cross "
                    "the diagonal however far the factor variance excurses. "
                    "The value is 1.0 because the dial is a SWITCH with no "
                    "interior: `factors.rs` branches at `== 0.0` and every "
                    "other value selects the same normaliser",
        "terms": {
            "crash_amplifier_slope 0.2": "`m`, unchanged by this and not "
                                         "derived by it. It is the dial "
                                         "whose loop gain B4 exposed, and "
                                         "what this changes is the "
                                         "denomination of its argument, not "
                                         "its size",
            "crash_amplifier_threshold 2.0": "`T`, likewise unchanged. Under "
                                             "the switch it is read in "
                                             "conditional sigmas, so the "
                                             "amplifier fires on the same "
                                             "few per cent of ticks in every "
                                             "regime instead of on a share "
                                             "that rises with the regime",
            "vix_level_identity 1.0": "what makes this a stability question "
                                      "at all. With the identity off the "
                                      "amplifier's moment is not in the "
                                      "VIX's target, the map has no gain "
                                      "through it, and the baseline "
                                      "normaliser costs nothing but the "
                                      "panel rows below",
            "market_vol_alpha 0.28035, market_vol_beta 0.69245":
                "the factor variance's own persistence, which supplies the "
                "excursions the superlinearity converts into a ceiling. NOT "
                "moved: the shipped 0.65/0.35 mixture's fourth-moment "
                "condition is 0.9870, under one, and its dispersion matches "
                "the tape's own GARCH (implied factor kurtosis 13 against "
                "11.3). Recalibrating them was remedy 2 and was declined "
                "because it makes the excursions rarer without making the "
                "map stable",
        },
        "source": "rust/src/params.rs, "
                  "ModelParams::crash_amplifier_conditional_sigma; the "
                  "condition and the closed form are "
                  "rust/src/market/index_var.rs "
                  "(`amplifier_moments`, `index_conditional_variance_terms`) "
                  "and the instrument is tools/calibration/pin_ladder.py. "
                  "The read-back is asserted against "
                  "`factors::calculate_live_factors` itself by "
                  "`the_conditional_normaliser_reproduces_the_index_the_"
                  "tick_builds`, which carries a negative control at 22x "
                  "base variance so a build whose read-back had not followed "
                  "the tick cannot pass it",
        "note": "WHAT IT COSTS, AND WHY THE OLD APPRAISAL DOES NOT SURVIVE. "
                "The alternative normaliser was built and measured on "
                "2026-08-22 and cost 0.03 of volatility clustering, 0.10 of "
                "excess kurtosis and 0.006 of correlation, for 'only the "
                "constancy of the firing rate'. Those figures were measured "
                "on a preset whose VIX could not see the amplifier at all, "
                "so the PRICE was measured and the PURCHASE was not: "
                "constancy buys the loop's stability, which was not on the "
                "ledger. The re-measurement on pt-v19 is in CHANGELOG.md. "
                "What is given up is real and is given up knowingly: with a "
                "constant normaliser the amplifier no longer turns a "
                "variance regime into a correlation regime, which is what "
                "real crises do -- the crisis blend, `sector_vix_coupling` "
                "and the jump coupling carry that, and none of them has an "
                "unbounded loop gain through the read-back. THE DIAL IS A "
                "SWITCH AND BELONGS IN A TWO-LEVEL SET: `atlas.SWITCH_DIALS` "
                "does not exist, so `atlas_survey.ZERO_SHIPPED_RANGES` "
                "carries it with the defect written down -- a Latin "
                "hypercube over [0, 1] never draws zero and would survey it "
                "permanently on",
    },
    "crisis_blend_gain": {
        "kind": "derived",
        "presets": {"pt-v16": 0.8275881, "pt-v18": 0.8275881, "pt-v19": 0.0, "pt-v20": 0.0},
        "identity": "zero, the identity: the tape supports no loading lift. "
                    "Three measured facts, none needing the model. (1) The "
                    "tape's VIX has no crisis attractor: its conditional "
                    "drift by level is negative in every bin above 22.5 at "
                    "five and twenty days, the linear drift above 30 crosses "
                    "zero at 29.6, and crisis spells above 30.88 have a "
                    "median length of two sessions. (2) Its cross-sectional "
                    "correlation is a function of realised common "
                    "volatility, rho = -0.366 + 0.277 log(sigma_ann%), R^2 "
                    "0.69 on 158 21-day sub-windows of the forty-name "
                    "reference roster, and the VIX level adds nothing once "
                    "volatility is in (partial coefficient -0.35). (3) This "
                    "model's factor share of each name's variance already "
                    "gives that curve with no lift: thirty seeds on the held "
                    "roster read slope 0.283 with every populated bin within "
                    "0.03 of the tape's. A lift keyed on the VIX level feeds "
                    "the identity and the VIX target and gives the map a "
                    "stable fixed point at 33 to 36 the tape does not have; "
                    "free-running the shipped model's one-day drift was "
                    "+0.82 at a VIX of 32.5 to 35 against the tape's -0.35",
        "terms": {
            "the tape fit's slope 0.277": "year-block bootstrap sd 0.025 "
                                          "over 2,000 draws; the RESIDUAL "
                                          "of this derivation, which any "
                                          "lift large enough to move the "
                                          "model's slope by one sd exceeds",
            "the model's slope at gain 0": "0.283 on 690 sub-windows, thirty "
                                           "seeds, held roster, b4fix9; "
                                           "bins under 10 / 10-15 / 15-20 / "
                                           "20-30: 0.202 / 0.307 / 0.407 / "
                                           "0.485 against the tape's 0.210 "
                                           "/ 0.321 / 0.428 / 0.507",
            "pt-v16 and pt-v18 at 0.8275881": "pt-v13's search optimum, "
                                              "eight significant figures, "
                                              "no error bar; kept in those "
                                              "presets for bit-identity "
                                              "and not re-derived",
        },
        "source": "programme/crisis-blend-derivation.md and "
                  "programme/results/b4fix9-result.md, design repository; "
                  "rust/src/params.rs, ModelParams::pt_v19",
        "what_the_value_costs": "corr_persistence_acf1 on the held roster "
                                "at 504 days reads 0.1493 against a floor "
                                "of 0.19, and abs_return_acf1 and "
                                "vix_ar1_debiased worsen with it: three "
                                "persistence rows the old value was buying "
                                "by holding the model in a crisis regime "
                                "the tape refutes (years above 60 on 18 per "
                                "cent of runs against the tape's 5.7; a "
                                "highest VIX of 120 against 82.69; spells "
                                "with a p90 of 36 sessions against 13). "
                                "The deficit is monthly-scale volatility "
                                "and VIX persistence (VIX acf1 of 21-day "
                                "means 0.46 against the tape's 0.62) and is "
                                "derived on its own; adopted with the row "
                                "red by Simon's ruling of 2026-09-12 (R16)",
        "what_the_value_buys": "the tape's VIX distribution on every "
                               "per-year statistic (runs above 60: 3.3 per "
                               "cent against 5.7; highest 73.9 against "
                               "82.69; days above 30.88 5.1 against 7.2 "
                               "per cent), crisis spells with median 2 and "
                               "p90 13 exactly the tape's, a drift with no "
                               "second attractor, index_tail_dn3_pct 0.624 "
                               "and 0.696 in band, excess_kurtosis on the "
                               "held roster at 504 days 8.1160 against a "
                               "floor of 7.1",
    },
    "vix_target_shock_cap": {
        "kind": "derived",
        "presets": {"pt-v16": 45.0, "pt-v18": 45.0, "pt-v19": 158.8524, "pt-v20": 158.8524},
        "identity": "the SUPREMUM of the return spike over the domain the "
                    "update admits, which is the image of `vix_return_clamp` "
                    "evaluated at the VIX floor: `vix_return_gain * clamp ** "
                    "vix_return_exponent * floor ** -vix_return_level_"
                    "exponent`. At pt-v19's law that is 8.83 * 15 ** 1.4483 "
                    "* 10 ** -0.4483 = 158.85236, the literal 158.8524 "
                    "rounding it at 3.9e-5. At the LEVEL-BLIND law pt-v16 "
                    "and pt-v18 ran, `vix_return_level_exponent` is 0 and "
                    "the same expression is the product 17.0 * 15.0 = 255.0. "
                    "The driving return is clamped one step before the spike "
                    "is formed (economy/daily.rs), so a cap AT the spike's "
                    "supremum cannot bind anywhere the clamp does not and "
                    "the pair carries one binding constraint between them "
                    "instead of two",
        "terms": {
            "vix_return_gain 8.83": "the spike's slope, unchanged by this "
                                    "derivation and not derived by it. The "
                                    "17.0 the pt-v16 and pt-v18 rows carry "
                                    "was read at the wrong memory "
                                    "(`vix-dynamics.md` 11)",
            "vix_return_clamp 15.0": "the bound on the driving return, "
                                     "itself outside the 6.39 per cent the "
                                     "tape supplies a conditional median "
                                     "for, so neither dial shapes the "
                                     "response where the tape can grade it",
            "vix_return_exponent 1.4483": "the down law's curvature in the "
                                          "move. `rust/src/params.rs` writes "
                                          "pt-v19's cap as a LITERAL because "
                                          "`powf` is not available in a "
                                          "`const fn`; the identity is "
                                          "asserted in the test suite",
            "vix_return_level_exponent 0.4483": "the down law's fall with "
                                                "the level. It makes the "
                                                "spike DECREASING in the "
                                                "VIX, so the supremum is at "
                                                "the VIX floor and not at "
                                                "any level the state "
                                                "reaches",
            "the VIX floor 10.0": "a literal in the state clamp "
                                  "(economy/daily.rs, `new_state.vix = "
                                  "clamp(..., 10.0, vix_ceiling)`). The "
                                  "supremum argument rests on it: the state "
                                  "is at or above 10 from the second "
                                  "session on",
        },
        "binding": "the session at which the cap would truncate the spike is "
                   "`r_cap(x) = (cap * x ** g / gain) ** (1 / p)`, which is "
                   "15.0000 exactly at the VIX floor and rises to 17.0058 at "
                   "a VIX of 15, 18.8725 at 21, 23.0383 at 40, 36.7817 at "
                   "181.33 and 42.9846 at 300. `vix_return_clamp` at 15 "
                   "binds first everywhere above the floor, with equality "
                   "only at it. The cap also truncates the inflation and "
                   "shock adders, which the spike's supremum does not "
                   "cover -- true at 255.0 as well, and a property of the "
                   "`min` being taken over the sum",
        "ordering": "WITHDRAWN 2026-09-14, ceiling-and-omega.md 3. This "
                    "entry and `vix_ceiling` both used to assert that the "
                    "cap 'must stay above the ceiling or the cap binds "
                    "first'. On pt-v19 it does not -- 158.8524 against a "
                    "ceiling of 181.3295 -- and the claim has no mechanism "
                    "behind it: the cap truncates an ADDITIVE TERM of the "
                    "VIX target and the ceiling truncates the STATE after "
                    "the reversion step, so there is no expression in which "
                    "they are compared. A cap under the ceiling in fact "
                    "makes the ceiling LESS sticky, which is the direction "
                    "the ceiling's own derivation wants: a state at C is "
                    "held there iff `implied + min(cap, spike) >= C`, so the "
                    "read-back needed to pin the VIX to the ceiling goes "
                    "from -73.67 (none) at a cap of 255 to +22.48 at "
                    "158.8524. The ordering was a coincidence of the "
                    "level-blind law's numbers recorded as an invariant",
        "source": "rust/src/params.rs, ModelParams::vix_target_shock_cap, "
                  "section 'It was a shape parameter, and pt-v19 retires "
                  "it'; programme/results/ceiling-and-omega.md sections 2 "
                  "and 3, design repository. The guard is "
                  "rust/src/economy/daily.rs, module `fear_response_shape`, "
                  "GREEN since 2026-09-14. It was RED on the shipped "
                  "preset because `the_default_cap_is_the_clamps_own_"
                  "image` compared the cap against the LEVEL-BLIND "
                  "spelling `gain * clamp ** p` = 445.9577 and asserted "
                  "`vix_return_exponent == 1.0`; five of the module's "
                  "six tests failed for the same reason, having been "
                  "written for a one-law response. The image under the "
                  "shipped law carries the level and is evaluated at the "
                  "VIX floor of 10, `gain * clamp ** p * floor ** -g` = "
                  "8.83 * 15 ** 1.4483 * 10 ** -0.4483 = 158.85236, which "
                  "is the spike's supremum over the whole domain the "
                  "update admits and is what the shipped 158.8524 rounds "
                  "up to. Rounding UP is what makes the cap strictly "
                  "inert: the session that would truncate is 15.0000025 "
                  "per cent against a clamp of 15. The rewritten module "
                  "asserts that identity, the cap's binding session at "
                  "seven levels, and the level dependence itself "
                  "(programme/results/fear-response-shape.md)",
        "note": "WHY THE OLD VALUE WAS NOT A BOUNDARY CONDITION, which is "
                "the part a derivation alone does not say. At 45.0 against "
                "a gain of 17.0 the cap bound at 2.647 per cent of session "
                "return, so a -2.7 per cent session and a -6.4 per cent one "
                "produced identical fear -- a SHAPE parameter inside the "
                "graded range, and an undeclared one until "
                "`fear_response_shape` made every preset name it. The "
                "loop-gain run (`loopgain-report.md` section 8.2) found what "
                "it was doing there: 'vix_target_shock_cap as a brake is "
                "compensating for a read-back that omits the crisis blend', "
                "the index realising 4.0-4.9x the variance V_t priced above "
                "`crisis_vix_threshold` against 1.2-1.4x below it. "
                "`market::index_var` now prices the crash amplifier and the "
                "crisis blend, so the fear arm has a mechanism balancing it "
                "above the threshold and the brake is not load-bearing. "
                "This is charter bar B4: no shipped preset had ever met it",
    },
    "oil_supply_response": {
        "kind": "derived",
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0 / 3.0, "pt-v19": 1.0 / 3.0, "pt-v20": 0.3333333333333333},
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
        "presets": {"pt-v16": 0.008583053614, "pt-v18": 0.008583053614, "pt-v19": 0.008583053614, "pt-v20": 0.008583053614},
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
    "garch_beta": {
        # THE INVERSE DEFECT: a derivation the record HAS and the guard
        # could not see. 15.4 derives this value and the module did not
        # carry it, so the dial sat in `UNPROVENANCED` as an admitted gap
        # that was not a gap. The audit that found it also found the
        # reverse case beside it -- see `vix_mean_reversion` -- which is the
        # argument for reading the record and the table against each other
        # rather than either alone.
        "kind": "derived",
        "presets": {"pt-v19": 0.7905, "pt-v20": 0.7905},
        "identity": "`beta = rho - alpha - gamma / 2`: the GJR first-moment "
                    "persistence identity solved for beta at the SHIPPED "
                    "alpha and gamma, with rho the tape's own per-name "
                    "decay rate. 0.9416 - 0.059507211981547736 - "
                    "0.18318536187800277 / 2 = 0.7905 to four places. The "
                    "half in front of gamma is the asymmetry term's "
                    "unconditional share: the indicator is on for half the "
                    "shocks, so it contributes gamma/2 to persistence",
        "terms": {
            "rho": "0.9416, the six-window MEAN of the per-name |r| "
                   "autocorrelation decay rate, sd 0.024, half-life 11.5 "
                   "sessions [6.6, 20.6]; per window 0.9005, 0.9554, "
                   "0.9590, 0.9384 (the 2019-21 crisis window), 0.9669, "
                   "0.9293 (vix-dynamics.md 15.2)",
            "alpha": "`garch_alpha` 0.059507211981547736, which pt-v19 does "
                     "not move",
            "gamma": "`garch_gamma` 0.18318536187800277, which pt-v19 does "
                     "not move and which is itself `undetermined` above. "
                     "This derivation takes the shipped asymmetry as GIVEN "
                     "and solves only for the memory; it is not a claim "
                     "about gamma",
        },
        "source": "programme/results/vix-dynamics.md section 15.4, on the "
                  "tape curve measured in section 15.2",
        "date": "2026-09-12",
        "script": "decay-curve-504.json (design repo): the reference "
                  "roster's per-name |r| autocorrelation, median across 40 "
                  "names, per 504 window, fitted over lags 2 to 60",
        "why_one_exponential": "MEASURED, and it is what makes a single "
                               "beta the right form. Over lags 2 to 60 the "
                               "non-crisis median curve is ONE exponential, "
                               "0.0727 x 0.9468^k (SSE 0.00028), against a "
                               "power law 0.106 k^-0.50 (0.00064) and a "
                               "two-exponential fit whose second component "
                               "lands at a half-life of 0.2 sessions -- "
                               "that is, a lag-one excess of 0.038 on top "
                               "of the exponential, not a second timescale. "
                               "So at the horizons the panel grades, the "
                               "tape's per-name memory is a single "
                               "timescale of 11-13 sessions, and the "
                               "\"hyperbolic\" reading of the decay-shape "
                               "gap is the log-log slope's weighting of "
                               "that one lag-one point",
        "bar": "ONE-SIDED ABOVE, and the bound is a moment condition rather "
               "than an estimator. The median curve gives 0.7957 and -1 sd "
               "gives 0.7661; the +1 sd value, 0.9660, has a GJR "
               "fourth-moment coefficient of 1.004 and is REFUSED. At the "
               "shipped point the coefficient is 0.957 (0.967 at the median "
               "curve) -- under one, and the fragile row is watched",
        "registered_predictions_falsified": "AND THE VALUE SURVIVES THEM, "
                    "which is the distinction this field exists to keep. "
                    "`vixdyn6` ran the three candidates on the composed "
                    "blend-off vector against four predictions registered "
                    "in 15.4 before the run. Prediction 1 FALSIFIED: held "
                    "`abs_return_acf20` at 252 read 0.0076 against a "
                    "registered [0.012, 0.035] and a falsification line at "
                    "0.010, and `abs_return_acf1` missed below [0.06, 0.11] "
                    "at 0.053. Prediction 2 FALSIFIED IN THE WRONG "
                    "DIRECTION: `corr_persistence_acf1` at 504 fell to "
                    "0.069 from 0.128. Prediction 3 missed on size "
                    "(volatility +3 per cent against a registered +8 to "
                    "+12) and held on the kurtosis (7.96 to 7.46; tape 13.2 "
                    "+/- 1.3, in band). Prediction 4 HELD: every VIX row "
                    "stayed inside `mr27g0`'s IQR to the third digit, so "
                    "the two subsystems are not coupled through this dial. "
                    "What was refuted is the claim that putting the tape's "
                    "per-name memory into the engine would move the monthly "
                    "rows. The VALUE is that memory and the step to it is "
                    "arithmetic, and section 17.6 later traces the "
                    "surviving miss to the index's COMPOSITION -- a roster "
                    "of 40 with 5.3 effective names, carrying a non-factor "
                    "variance share a 500-name index does not have -- which "
                    "is not a dial",
        "note": "the cascade in `garch.rs` keeps alpha and gamma and raises "
                "beta per component, so this dial is the base memory and "
                "not the whole of it. 15.3 records that at pt-v19's "
                "per-name dials the cascade's fourth-moment coefficient "
                "passes one from its third component on (0.770, 0.959, "
                "1.032, 1.058, 1.066, 1.069) and its joint second-moment "
                "spectral radius is 1.010; that is FLAGGED there against "
                "`garch.rs` and is not a claim this entry makes about this "
                "value, which is admissible on its own",
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
        "presets": {"pt-v16": 17.0, "pt-v18": 17.0, "pt-v19": 0.049, "pt-v20": 0.049},
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
        "presets": {"pt-v16": 0.05, "pt-v18": 0.05, "pt-v19": 0.05, "pt-v20": 0.05},
        "what_would_determine_it": "a measured slow-component gain. ws-b "
                                   "withdrew this dial as undetermined "
                                   "rather than deriving it; 0.05 is a "
                                   "round number with no series behind it",
        "source": "programme/RESUME.md, ws-b's withdrawal",
    },
    "market_vol_slow_persistence": {
        "composed": "shipped on pt-v19 from 2026-09-14, returned to pt-v18's on 2026-09-20 by the 2^6 factorial (design-repo programme/results/bestof/RESULT-504.md), which measured the market variance family as one block, and returned to pt-v19 on 2026-09-21 when ptv19gjr (design-repo programme/results/ptv19gjr/RESULT.md) measured the block's three parts apart: the cost the factorial saw was the factor level's, and this value with the slow pole and the regime level on the VIX law is the first vector to pass the whole gate",
        # The factor's slow pole: the second derivation the record had and
        # this table could not see. Found by the same audit as `garch_beta`
        # above and added on the same day.
        "kind": "derived",
        "presets": {"pt-v19": 0.9913, "pt-v20": 0.9913},
        "identity": "the SLOW POLE of the tape's own variance impulse "
                    "response, read off a two-component fit and carried "
                    "into the mixture as its persistence. One exponential "
                    "is REFUSED (SSE 0.079 against 0.005): the tape is fast "
                    "0.55 at 0.918 (half-life 8 sessions) plus slow 0.45 at "
                    "0.9924 (half-life 91), and the window bootstrap puts "
                    "the slow rho at 0.9913 [0.975, 1.000]. The VIX-side "
                    "measurement of section 10 -- slow 0.64 at 0.990 "
                    "[0.975, 0.994] -- is the same component seen through "
                    "the VIX and sits inside that bar, which is the check "
                    "that the two estimators are reading one thing",
        "terms": {
            "estimator": "the coefficient of `log RV_{t+k+1..t+k+21}` on "
                         "`r_t` with `V0` controlled, pooled within the 35 "
                         "windows and taken relative to k = 0. Tape "
                         "retention 0.95 / 0.89 / 0.85 / 0.78 / 0.66 / 0.53 "
                         "/ 0.42 / 0.33 / 0.26 / 0.27 at k = 1, 2, 3, 5, "
                         "10, 20, 30, 40, 60, 80",
            "why not a likelihood": "a two-component QMLE of the engine's "
                                    "own recursion on the tape is NOT "
                                    "IDENTIFIED: every start runs the slow "
                                    "pole to the unit root (NLL 3475-3498 "
                                    "against the single GJR's 3536, all on "
                                    "the boundary), which is the known "
                                    "weakness of maximum likelihood for "
                                    "near-unit-root variance components. "
                                    "The impulse response is the estimator "
                                    "here BECAUSE the likelihood is not one",
            "market_vol_slow_weight": "0.35, UNMOVED, and this entry claims "
                                      "nothing about it. 17.4 derives 0.47 "
                                      "[0.19, 0.79] for the weight beside "
                                      "this pole and pt-v19 does not take "
                                      "it, so the weight stays in "
                                      "`UNPROVENANCED` where it belongs",
        },
        "source": "programme/results/vix-dynamics.md section 17.4, on the "
                  "tape measurement of section 17.3",
        "date": "2026-09-12",
        "script": "`t22`, the estimator of vix-dynamics.md section 10.1 "
                  "applied to forward realised variance (vix-dynamics.md "
                  "section 17.3)",
        "moment_condition": "CHECKED AND NOT BINDING, which is the opposite "
                            "of `garch_beta`'s case and worth recording as "
                            "such. The mixture's second-moment spectral "
                            "radius with the GJR indicator reads 0.9841 at "
                            "the shipped weight 0.35 and this pole, against "
                            "0.9740 at pt-v18's 0.98; at the derived pair "
                            "0.9840. The condition binds only at the bar's "
                            "upper weight (0.79) and a pole of 0.99932, and "
                            "the fitted 6-parameter mixture (radius 1.0033) "
                            "is refused by it as the per-name +1 sd value "
                            "was. The binding constraint on this derivation "
                            "is therefore NOT the moment condition but the "
                            "identification of the slow pole itself, whose "
                            "bar reaches the unit root",
        "registered_predictions_falsified": "as for `garch_beta`, and "
                    "pointing one layer further down. `vixdyn7` ran the "
                    "pole against the predictions registered in 17.5. The "
                    "index's forward-RV retention at k = 40 read 0.29 on "
                    "the pole alone against a registered [0.28, 0.45] "
                    "(marginal; 0.27 on the pair, 0.22 at weight 0.64, "
                    "FALSIFIED there). Held `abs_return_acf20` at 252 read "
                    "0.009 against [0.012, 0.030], FALSIFIED. "
                    "`corr_persistence_acf1` at 504 rose on every arm but "
                    "cleared 0.10 only at weight 0.64, so that row is not a "
                    "factor-memory row either. The VIX's own slow component "
                    "FELL as the slow weight rose -- falsified in DIRECTION "
                    "-- because the slow component's target is damped by "
                    "`market_vol_slow_vix_damp` and carries less of the "
                    "excursion loop. 17.6 then derives why the dial cannot "
                    "reach the row: the mixture's closed-form impulse "
                    "response at the SHIPPED slow dials already reads 0.43 "
                    "at k = 40 and 0.28 at k = 60, LONGER than the tape's "
                    "0.33 and 0.26, while the realised index memory reads "
                    "0.25-0.29. The factor is not short of memory; the "
                    "INDEX is, because the factor carries about 55 per cent "
                    "of this roster's index variance and the other 45 per "
                    "cent is short-memory, so the index reads 0.55 x 0.43 "
                    "plus a small term. The value is the tape's pole; what "
                    "is refuted is that moving it buys the monthly rows",
        "note": "17.7's own read, recorded so the next person does not have "
                "to find it: on the widened-plus-new tables the vector "
                "carrying this pole scores 40.8 at 252 and 46.5 at 504 "
                "against pt-v18's 39.5 and 43.0, the release bar of ahead "
                "at both horizons is NOT met, and the note does not propose "
                "its composed vector. pt-v19 ships the pole regardless. "
                "That is a derived value adopted against the score, which "
                "is the opposite direction from the compensator pattern in "
                "the module note above, and it is stated here rather than "
                "left for a reader to assume the score agreed",
    },
    "vix_variance_premium": {
        # The one MEASURED entry in the table, and the schema was built
        # around what it has to carry: a source, a date, a script, and an
        # error bar. The estimator is a choice and it is named, because
        # this quantity reads 1.076 pooled over one history and 1.252 per
        # calendar year, and the panel's own statistic is a per-window one.
        "kind": "measured",
        "presets": {"pt-v16": 0.252, "pt-v18": 0.252, "pt-v19": 0.252, "pt-v20": 0.252},
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
        #
        # RESOLVED AT 0.8.0, which is what this entry was waiting for. The
        # text above stands for pt-v16 and pt-v18, which still ship 1.0.
        # pt-v19 now ships 1.4483, an engine-shaped fit of the same law on
        # the same tape: the free down-side fit reads `|r|^p V^-g` with
        # p 1.1996 and g 0.49 +/- 0.12, and the standardised form the engine
        # runs takes `g = p - 1`, which puts the pair at 1.4483 / 0.4483
        # (vix-dynamics.md sections 2.3 and 5). The shipped value IS the
        # measured one, for the first time on this dial.
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md sections 2.3 to 2.5 (design repo)",
        "date": "2026-09-13",
        "estimator": "bucket-median regression of dVIX on |r| and the VIX "
                     "level, down sessions, whole span, refitted in the "
                     "engine's own standardised form so the exponent the "
                     "dial carries is the exponent that was fitted",
        "script": "programme/scripts/vix-updown-fit.py and the vixprobe "
                  "series (design repo); the fit and its residual are "
                  "programme/results/vix-dynamics.md sections 2.3 to 2.5",
        "estimate": 1.4483,
        "standard_error": 0.12,
        "residual": {
            "kind": "F against the shipped level-blind form on the same "
                    "bucket medians",
            "statistic": 118.0,
            "note": "the level-blind form is REFUSED at F = 118, so the "
                    "level exponent is not an optional refinement of it",
        },
        "presets": {"pt-v19": 1.4483, "pt-v20": 1.4483},
        "identity": "the down-side response is `gain * |r|^p * V^-g` with "
                    "`g = p - 1`, the one-parameter-fewer standardised "
                    "form; the free fit's g of 0.49 +/- 0.12 contains "
                    "p - 1 = 0.4483",
        "what_would_determine_it": "RESOLVED for pt-v19; for the earlier "
                                   "presets, the question asked on an arm whose "
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
    # ======================================================================
    # THE 0.8.0 VECTOR. Eleven dials that shipped inert at 0.0 and are live
    # in pt-v19 from 2026-09-13. Every figure is read off
    # programme/results/vix-dynamics.md in the design repository, which is
    # also where each falsifier and each box are recorded. They were
    # measured BEFORE they were scored: no value below was chosen because it
    # cleared a band, which is the B3 defect this module refuses.
    # ======================================================================
    "vix_return_level_exponent": {
        "kind": "derived",
        "presets": {"pt-v19": 0.4483, "pt-v20": 0.4483},
        "identity": "`p - 1` where p is `vix_return_exponent` 1.4483. The "
                    "engine runs the standardised form of the down-side "
                    "law, in which the level exponent is not free: fixing "
                    "it at `p - 1` is the one-parameter-fewer model, and "
                    "the free fit's g of 0.49 +/- 0.12 contains 0.4483",
        "terms": {"vix_return_exponent": "1.4483, the entry above"},
        "source": "programme/results/vix-dynamics.md section 2.3, and the "
                  "derived vector of section 5",
        "date": "2026-09-13",
    },
    "vix_return_exponent_up": {
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md section 2.3 (design repo)",
        "date": "2026-09-13",
        "estimator": "bucket-median regression on UP sessions, whole span, "
                     "the same estimator as the down side and fitted "
                     "separately because the tape's two sides are two laws",
        "script": "programme/scripts/vix-updown-fit.py (design repo); "
                  "vix-dynamics.md section 2.3",
        "estimate": 0.5433,
        "standard_error": 0.04,
        "residual": {
            "kind": "the free up-side fit this is shaped from",
            "p_up": 0.60,
            "note": "CONCAVE in the move, against the down side's convex "
                    "1.4483: the two sides are not one law with a sign",
        },
        "presets": {"pt-v19": 0.5433, "pt-v20": 0.5433},
        "identity": "the up-side response is `gain * |r|^p_up * V^-g_up`",
    },
    "vix_return_level_exponent_up": {
        "kind": "derived",
        "presets": {"pt-v19": -1.0, "pt-v20": -1.0},
        "identity": "the RATIO form in the level: an up-side response "
                    "proportional to the VIX is `V^-g_up` with g_up = -1. "
                    "The free fit reads -0.85 +/- 0.12, which is 1.2 "
                    "standard errors from -1, so the ratio form is the "
                    "one-parameter-fewer model the tape does not refuse",
        "terms": {"free fit": "-0.85 +/- 0.12, vix-dynamics.md section 2.3"},
        "source": "programme/results/vix-dynamics.md sections 2.3 and 5",
        "date": "2026-09-13",
    },
    "vix_innovation_return_sigma": {
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md sections 3 and 12 (design repo)",
        "date": "2026-09-13",
        "estimator": "Gaussian maximum likelihood on the within-window "
                     "residual of the fitted response law: the "
                     "return-coupled component of the VIX innovation",
        "script": "programme/scripts/vixstats.py, the form-controlled "
                  "residual estimator (design repo); vix-dynamics.md "
                  "sections 3 and 12",
        "estimate": 0.0175,
        "standard_error": 0.0015,
        "residual": {
            "kind": "form-controlled residual sd, model against tape",
            "model": 0.032,
            "tape": 0.036,
            "note": "measured on the shipped vector at 120 rosters. The "
                    "companion `vix_innovation_sigma` DERIVES TO ZERO: the "
                    "VIX's own innovation is the variance forecast's, "
                    "which is why the tape's residual persists",
        },
        "presets": {"pt-v19": 0.0175, "pt-v20": 0.0175},
        "identity": "the residual a level-aware law leaves, which a "
                    "level-blind law books as innovation and which the "
                    "`vix_dlog_innovation_sd` row of new-rows.md reads",
    },
    "vix_jump_level_scale": {
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md section 3.2 (design repo)",
        "date": "2026-09-13",
        "estimator": "cumulant inversion on the standardised residual of "
                     "the fitted response law: the jump size reproducing "
                     "the residual's third and fourth cumulants at the "
                     "derived arrival rate",
        "script": "programme/scripts/vixstats.py and t3_resid.py (design "
                  "repo); vix-dynamics.md section 3.2",
        "estimate": 1.700,
        "standard_error": 0.35,
        "residual": {
            "kind": "the VIX daily excess kurtosis it is fitted to",
            "tape": 2.66,
            "note": "the model reaches 0.72 on the shipped vector, short "
                    "for the reason section 5.5 gives: the index's own "
                    "tail is thinner than the tape's and a convex response "
                    "caps the VIX fourth moment below it. The dial is not "
                    "what is short",
        },
        "presets": {"pt-v19": 1.700, "pt-v20": 1.7},
        "identity": "the jump size in LEVEL units, which is what makes the "
                    "response scale-free; 0.0 selects the points of "
                    "`vix_jump_scale` instead",
    },
    "vix_jump_return_intensity": {
        "kind": "derived",
        "presets": {"pt-v19": 6.199, "pt-v20": 6.199},
        "identity": "the derived 2.24 arrivals a year spread over the "
                    "down-return distribution as `max(0, -r)`: a rate per "
                    "year per percentage point of down move, which "
                    "integrates back to 2.24/yr on the tape's own return "
                    "distribution",
        "terms": {"2.24/yr": "the arrival rate the cumulant inversion "
                             "behind `vix_jump_level_scale` implies, "
                             "vix-dynamics.md section 3.2",
                  "max(0, -r)": "the carrier, because the tape's VIX jumps "
                                "arrive on down sessions"},
        "source": "programme/results/vix-dynamics.md sections 3.2 and 5",
        "date": "2026-09-13",
    },
    "sector_vol_alpha": {
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md sections 19.1 and 19.7 (design repo)",
        "date": "2026-09-13",
        "estimator": "GARCH(1,1) by Gaussian quasi-maximum likelihood on "
                     "the sector factor daily return STANDARDISED by the "
                     "model's own VIX-coupled target, which is the ratio "
                     "form: the state multiplies the target rather than "
                     "adding a variance to it",
        "script": "programme/scripts/t25_sector_jump_forms.py and "
                  "t26_sector_net_of_vix.py (design repo); vix-dynamics.md "
                  "sections 19.1 and 19.7",
        "estimate": 0.067,
        "standard_error": 0.043,
        "residual": {
            "kind": "the persistence the pair implies, against the tape",
            "estimate": 0.904,
            "standard_error": 0.069,
            "note": "sd of the log scale 0.331. The ADDITIVE form of the "
                    "same state was measured on vixdyn8 and is worse at "
                    "504 by 2.68 against a paired error bar of 1.60",
        },
        "presets": {"pt-v19": 0.067, "pt-v20": 0.067},
        "identity": "the shock share of the sector variance state",
    },
    "sector_vol_beta": {
        "kind": "measured",
        "source": "programme/results/vix-dynamics.md section 19.7 (design repo)",
        "date": "2026-09-13",
        "estimator": "the same GARCH(1,1) fit as `sector_vol_alpha`, the "
                     "persistence coefficient of the same pair",
        "script": "programme/scripts/t25_sector_jump_forms.py (design "
                  "repo); vix-dynamics.md section 19.7",
        "estimate": 0.837,
        "standard_error": 0.111,
        "residual": {
            "kind": "alpha + beta against the tape's sector persistence",
            "estimate": 0.904,
            "standard_error": 0.069,
        },
        "presets": {"pt-v19": 0.837, "pt-v20": 0.837},
        "identity": "the carry-over of the sector variance state. The "
                    "state is a ratio with fixed point 1.0, so beta alone "
                    "with alpha at 0.0 leaves it there forever, which is "
                    "why the pair is jointly live and singly inert",
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
        "presets": {"pt-v18": 755.0, "pt-v19": 755.0, "pt-v20": 755.0},
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
        "presets": {"pt-v16": 0.025, "pt-v18": 0.025, "pt-v19": 0.025, "pt-v20": 0.025},
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
        "presets": {"pt-v18": 0.375, "pt-v19": 0.46, "pt-v20": 0.46},
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
        # THE BAR IS FOR pt-v18's 0.375 AND NOT FOR pt-v19's 0.46. pt-v19
        # shipped 0.375 on this measurement until its fifth composition
        # (2026-09-23), which ships 0.46 with the wire sampled on the live
        # session. Nothing above measured 0.46, so the entry names the value
        # its measurement is for and records the other as what it is.
        "estimate": 0.375,
        "fitted": "pt-v19's 0.46 is FITTED, not measured: f2sweep's value, "
                  "kept because the held-out lagged asymmetry row clears "
                  "under the live keying (`market_beta_down_asym_lag_live` "
                  "1.0; k 25 / 24 of 30 against 23 / 19 at 0.375), "
                  "programme/results/route1-blend/RESULT.md sections 8.2 and "
                  "8.4 (design repository). The ledger's rulings refuse "
                  "exactly this kind of value, and the tape identity has no "
                  "power at this keying: the tape's lagged asymmetry is flat "
                  "across lags two and three (results/corrlag2/), so it "
                  "derives no window either. What would determine it is a "
                  "tape identity for the lag under the live keying",
    },
    "vix_mean_reversion": {
        # THE SECTION 7 PATTERN, NAMED, and the dial that found the hole in
        # this module's own guard. Until 2026-09-15 it was listed in
        # `UNPROVENANCED` WHILE carrying this entry: 45 entries plus 69
        # declared names, against 113 dials in scope, collapsing to a union
        # of 113. `audit()` computes `missing` from that union, so the
        # arithmetic balanced, every completeness assertion passed, and the
        # table was asserting two contradictory things about one dial with
        # nothing able to say so. `audit()["in_both"]` now refuses it by
        # name.
        #
        # WHY THIS IS `undetermined` AND NOT `measured`. Everything that was
        # supposed to derive 0.27 has been withdrawn or falsified:
        #
        #   R13, the 2026-09-07 ruling of 0.10 off the arm D frontier, is
        #   WITHDRAWN in place (`stalemark-r13-withdrawn-in-place`), and the
        #   frontier it ruled on -- 0.10, 0.12, 0.15 -- never contained the
        #   shipped value at all.
        #
        #   `vix-dynamics.md` 10.3 derived [0.27, 0.37] from three tape
        #   constraints and two are falsified on standing entries
        #   (`stalemark-vix-dynamics-derivation-of-0.27`). Constraint 1, the
        #   fast decay, by V4: the slow weight reads 0.5472 at 0.27 and
        #   0.4739 at 0.10, both inside the tape's [0.47, 0.82] and 0.073
        #   apart against a half-width of 0.175, so the tape cannot separate
        #   the two rates at its own resolution. Constraint 2, the slow
        #   share, by V9: the read-back constant c_d is not arm-invariant --
        #   6.83 at 0.27 against about 7.95 at 0.10, an across-arm sd of
        #   0.574 where the derivation assumed 0.05 -- so every `mr x 5.07`
        #   in that item, including the figure that excluded 0.10, is
        #   computed at a constant that MOVES WITH THE RATE BEING EXCLUDED.
        #   Constraint 3, the up side's sign, is untouched.
        #
        #   `persistence-derivation.md`'s sentence "the rate stays at 0.10"
        #   is withdrawn (`stalemark-persistence-derivation-basis`); what
        #   survives it is a FORM finding -- no value of the rate carries
        #   the clustering row.
        #
        # What is left is a score and a z, and the standing ruling says so
        # in its own words: "0.27 now stands on the SCORE and the z, not on
        # its derivation ... The dial is right and its argument needs
        # re-solving jointly." A score is why a value SHIPS; it is not a
        # derivation of the dial and it is not a tape reading of it. Filing
        # it `measured` would let the score stand in for the argument, which
        # is exactly the substitution the module note opens with, and it
        # would do so under a kind whose whole promise is a tape figure with
        # a bar. `undetermined` is a PASSING state so that this can be said
        # without anyone having to claim a derivation to go green.
        #
        # NOT CLAIMED: that 0.27 is wrong, or that 0.10 should come back.
        # sigmamr1 asked that question and answered it, the ruling stands,
        # and the readings are in `ruling` below. The gap is in the
        # ARGUMENT, not in the choice.
        "kind": "undetermined",
        "presets": {"pt-v16": 0.06, "pt-v18": 0.10, "pt-v19": 0.27, "pt-v20": 0.27},
        "what_would_determine_it": "a JOINT re-solve of the three "
                                   "constraints of `vix-dynamics.md` 10.3 "
                                   "with the read-back constant c_d "
                                   "MEASURED at each rate instead of "
                                   "assumed invariant, so that "
                                   "`gain = g_tape/mr - c_d(mr)` is solved "
                                   "as the fixed point it is. V9 is both "
                                   "the measurement that showed c_d moves "
                                   "(6.83 at 0.27 against about 7.95 at "
                                   "0.10) and the estimator for the "
                                   "re-solve. Until it is done neither this "
                                   "dial nor its partner `vix_return_gain` "
                                   "has a derivation, and neither can be "
                                   "determined alone: section 11.1's own "
                                   "finding is that the pair (0.10, 17) and "
                                   "the pair (0.27, 8.83) are two points on "
                                   "ONE curve",
        "source": "programme/results/verdict-ledger.json (design repo), "
                  "entries `ruling-vix-mean-reversion-stays-at-0.27`, "
                  "`stalemark-vix-dynamics-derivation-of-0.27` and "
                  "`stalemark-r13-withdrawn-in-place`, all standing at "
                  "2026-09-15; programme/results/vix-dynamics.md sections "
                  "10.3 and 11.1",
        "ruling": "RULED 2026-09-15 on box sigmamr1 (12 arms, 120 varying "
                  "rosters plus the 30 held, both horizons): the dial STAYS "
                  "at 0.27 and the suspension is withdrawn outright rather "
                  "than left standing. The ruling's readings, on the whole "
                  "objective against arm W0: MR10A and MR10C -2.23 at 252 "
                  "and -4.19 at 504, MR10B -2.52 and -4.57; on z, 0.27 sits "
                  "nearer the tape at +2.18 / +1.54 against 0.10's +3.30 / "
                  "+2.72, the ruler being `facts.REAL_VIX_AR1`'s centre "
                  "with its bootstrap median se of 0.0120, meaned across "
                  "rosters. THE CAVEAT TRAVELS WITH THE VALUE and is why "
                  "this entry is `undetermined` rather than `measured`",
        "ruling_evidence_in_this_tree": "programme/results/sigmamr1/ (design "
                                        "repo) retains "
                                        "`per-seed-panels.json.gz` for 120 "
                                        "seeds across the twelve arms, plus "
                                        "`RETAINED.json`, "
                                        "`dials-present.txt`, "
                                        "`kat-verdict.txt`, "
                                        "`known-answer.txt` and the per-arm "
                                        "held and varying directories, so "
                                        "the figures above are recomputable "
                                        "here. The box's registration and "
                                        "result NOTES are in neither "
                                        "repository, so the prose around "
                                        "them is not, and nothing in this "
                                        "entry is cited to them",
        "superseded": "this entry was `measured` until 2026-09-15 and its "
                      "source, date, script and residual were the arm D "
                      "frontier of 2026-09-07: three non-dominated points "
                      "on the pair (S_252, S_504) -- 0.10 at 28.69 / 29.78, "
                      "0.12 at 25.65 / 32.84, 0.15 at 23.87 / 42.79 -- "
                      "spread 4.8 points at 252 and 13.0 at 504, a partial "
                      "order rather than an error bar, measured by "
                      "programme/scripts/armboth.py via ptv19armd-jobs.sh "
                      "(design repo) arm D, registered at 92463f9 before "
                      "the run and recorded at 3175a4c, median across "
                      "thirty seeds per row with "
                      "`market_beta_down_asym_lag` pinned at 0.375, the "
                      "model error each row's own across-seed spread at "
                      "`df_model` 29. THAT "
                      "RECORD IS TRUE AND IT IS EVIDENCE ABOUT OTHER "
                      "VALUES: 0.27 was not on that frontier. It is kept "
                      "here as history and is no longer what the entry "
                      "rests on -- the same defect `sector_loading` had "
                      "below, found by the same audit",
        "history": "what the withdrawn ruling was made on, kept because it "
                   "says what the objective can and cannot see: 0.10 was "
                   "the only one of the three holding `vix_ar1_debiased` "
                   "inside two standard errors of its ruler at both "
                   "horizons (+0.7 and -2.0, against -2.7 and -6.8 at "
                   "0.15), and the only one where the two horizons scored "
                   "alike rather than one being bought at the other's "
                   "expense. pt-v16's 0.06 is DOMINATED -- 0.12 beats it at "
                   "both horizons -- so it was not going to survive "
                   "whatever the ruling said. Before the persistence row "
                   "joined the rule the eighteen-row objective wanted 0.15 "
                   "at 252 and 0.20 at 504; adding the row did not close "
                   "that disagreement, it reversed which end was which. "
                   "R13's three registered reasons to re-measure are NOT "
                   "withdrawn: they are what forced sigmamr1",
    },
    "vix_return_gain": {
        # THE PARTNER OF 0.27, AND IT SHARES ITS FATE. `vix-dynamics.md`
        # 11.1 prints DERIVED over this value and the sentence that made it
        # one is withdrawn: `stalemark-fear-response-shape-solved` marks
        # `fear-response-shape.md` section 1.1's claim that 8.83 is the pair
        # SOLVED at the tape's memory and withdraws the word "solved". V9
        # measured the read-back constant c_d at 6.83 at mr 0.27 against
        # about 7.95 at 0.10. The law is `gain = g_tape/mr - c_d`, so its
        # constant is IMPLICIT in the dial it is solved for, and what looked
        # like a derivation is a fixed point solved at one arm.
        #
        # WHAT IS NOT WITHDRAWN is the measurement of what the shipped pair
        # DOES; it is untouched by the mark, it is why the value ships, and
        # it is in `measured_at_the_pair` below. It is a reading of the
        # RESPONSE, jointly, at one point. It confirms the pair and it does
        # not locate this dial -- 11.1's own finding is that
        # `vix_return_gain` "was never independent of `vix_mean_reversion`,
        # and the pair (0.10, 17) was one point on the curve
        # `gain x mr = same-day response - read-back share` at the wrong
        # memory".
        #
        # So `undetermined`, for the same reason and by the same argument as
        # its partner. A derivation claimed from a confirmation at one point
        # on a curve is the defect in the module note's first paragraph with
        # the paragraph already written.
        "kind": "undetermined",
        "presets": {"pt-v16": 17.0, "pt-v18": 17.0, "pt-v19": 8.83, "pt-v20": 8.83},
        "what_would_determine_it": "the same joint re-solve "
                                   "`vix_mean_reversion` names -- this dial "
                                   "is the other unknown in it. A tape "
                                   "measurement of c_d(mr) turns the pair "
                                   "from a fixed point solved at one arm "
                                   "into a solved curve, and then either "
                                   "determines 8.83 or moves it. NOT a "
                                   "further score comparison: the arms "
                                   "already prefer this value and that is "
                                   "not the missing thing",
        "source": "programme/results/vix-dynamics.md section 11.1 "
                  "(vixdyn3); programme/results/verdict-ledger.json entry "
                  "`stalemark-fear-response-shape-solved` (standing "
                  "2026-09-15), which marks "
                  "programme/results/fear-response-shape.md section 1.1",
        "measured_at_the_pair": "MEASURED, and explicitly untouched by the "
                                "mark. At `vix_mean_reversion` 0.27 with "
                                "this dial at 8.83 the realised same-day "
                                "response is the tape's: down slope -1.48 "
                                "[-1.69, -1.32] against the tape's -1.39 "
                                "[-1.66, -1.16]; conditional medians by "
                                "level in the 1-2 per cent bin 1.91 / 1.71 "
                                "/ 1.38 at V 17 / 20 / 29 against the "
                                "tape's 1.63 / 1.44 / 1.40, and in the 2-3 "
                                "bin 3.41 / 3.09 / 2.74 against 3.29 / 2.48 "
                                "/ 2.17; the up-side scale fitted on the "
                                "model reads 0.040 against the tape's "
                                "0.048. The dial that shipped at 17 is 8.8 "
                                "when the memory it was compensating for is "
                                "the tape's, and the response in points "
                                "does not move. That is a confirmation of "
                                "the PAIR at one arm, not a derivation of "
                                "this dial, and the difference is the whole "
                                "reason this entry is not `measured`",
        "partners": "`vix_mean_reversion` above is the other unknown. "
                    "`vix_target_shock_cap` 158.8524 is the image of the "
                    "clamp at this gain: its arithmetic stands and is "
                    "CONDITIONAL on 8.83, so a re-solve that moves this "
                    "dial moves that one with it",
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
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
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
        "stale": "THE BAND VERDICT ABOVE NO LONGER DESCRIBES THIS DIAL'S "
                 "REGIME, and the dial is not the reason. `cert4b` measured "
                 "a build whose index-variance read-back carried neither the "
                 "crash amplifier, nor the crisis blend, nor the downside "
                 "transmission tilt; all three are in `market::index_var` "
                 "now, so every pt-v19 trajectory has moved twice since and "
                 "the derived anchor with it (19.53 -> 23.72 on "
                 "Universe.random(40, seed=111)). Re-measured on "
                 "`facts.LEVEL_PROTOCOL` at the read-back as it stands, the "
                 "same vector reads 17 of 18 at 252 days: the miss is "
                 "`index_tail_dn3_pct` at 5.2590 against 0.47 to 1.96, and "
                 "it is carried by two of the thirty rosters (114 and 115), "
                 "which read 0.8680 pooled when dropped. The cause is a VIX "
                 "loop whose gain the read-back has newly exposed, not this "
                 "switch: see `market::index_var`'s module documentation and "
                 "`tools/calibration/pin_ladder.py`. `presets/pt-v19.json` "
                 "is deliberately left un-regenerated while this stands",
    },
    "vix_decay_ratio": {
        "kind": "measured",
        "presets": {"pt-v16": 0.6, "pt-v18": 0.6, "pt-v19": 1.0, "pt-v20": 1.0},
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
        # THE ENTRY DESCRIBED 0.8 AND THE PRESET SHIPS 0.60. Until
        # 2026-09-15 every measured field here was about a value pt-v19
        # stopped shipping on 2026-09-14: the source, the date, the script
        # and a residual that was the five-point grid around 0.8. `presets`
        # was correct, so `audit()` found no mismatch; the 0.60 story lived
        # in `superseded`, which no schema reads; and the entry passed with
        # its evidence pointing at another value. `vix_mean_reversion`
        # above is the same defect in the other direction -- both were
        # found by one read of the record against the table, and neither is
        # reachable by a schema rule (see the note in `validate_entry`).
        #
        # The VALUE is sound and that is the second half of the finding.
        # After R1 the derivation's target and the grading band are on ONE
        # basis, and the value was then measured directly on the composed
        # base rather than transferred. A stale entry over a sound value is
        # a documentation defect; it is recorded as loudly as a model one
        # because at read time the two are indistinguishable, which is the
        # sentence this module opens with.
        "kind": "measured",
        "presets": {"pt-v16": 0.58821442, "pt-v18": 0.58821442, "pt-v19": 0.60, "pt-v20": 0.6},
        "source": "MEASURED ON THE COMPOSED BASE. transmit1 varied the "
                  "level and the loading in ONE box for the first time: two "
                  "ladders over `sector_loading` 0.55, 0.60, 0.65 and 0.70, "
                  "one at `market_vol_level_sigma` 0.0 and one at the "
                  "shipped 0.085, on 120 varying rosters (seeds 101-220) "
                  "plus the 30 held, both horizons, one 1260-session "
                  "recording per arm so the windows are windows of one "
                  "path. F3's rule returns the centring loading L* = 0.6168 "
                  "at 252 and 0.6271 at 504 with the level ON, against "
                  "0.5958 and 0.6056 with it off. THE TARGET AND THE RULER "
                  "ARE ONE BASIS after R1: the target is the whole-tape "
                  "centre of `sector_excess_corr`, 0.1178 "
                  "(whole-tape.md's resolved table), and "
                  "`facts.RULED_BY_HORIZON` is `REAL_MARKETS_RULED`, whose "
                  "`sector_excess_corr` band is built by the same rule over "
                  "the same 1987-2025 32-name windows that centre is. The "
                  "objection that the dial was derived against one basis "
                  "and graded against another is answered by R1 and not by "
                  "this entry. pt-v16 and pt-v18 ship 0.58821442, set by an "
                  "earlier preset without provenance",
        "date": "2026-09-14 (transmit1, the measurement this entry rests "
                "on); 2026-09-13 (sector-loading.md section 6.3, the "
                "derivation it agrees with)",
        "script": "programme/results/whole-tape/scripts/transmit1_analyse.py "
                  "(design repo), applying F3's rule unchanged from "
                  "levelsec1_analyse.py; whole-tape/scripts/score_wt.py for "
                  "the objective, through `facts.aggregate_value` "
                  "(transmit1-result.md section 3)",
        "residual": "THE COMPOSITION CORRECTION, AND IT IS A TENTH OF WHAT "
                    "THE TAPE CAN SEE. Turning the level on moves L* by "
                    "+0.0210 at 252 and +0.0215 at 504 on the varying "
                    "median, +0.0080 and +0.0114 on the varying mean, "
                    "+0.0385 and +0.0402 on the held median. The tape's "
                    "centre for this row is 0.1178 +/- 0.0367, a 31 per "
                    "cent bar, which at a response exponent near 1.45 is a "
                    "loading known to about +/- 0.213. So the correction is "
                    "a tenth of the bar and 0.6168 is inside the band the "
                    "derivation already had. `defect-28` records that the "
                    "tape bar does NOT shrink by running more rosters, so "
                    "this is the answer rather than an interim one. The "
                    "objective agrees: on the whole-tape nineteen C065 "
                    "reads 13.2 at 252 and 9.7 at 504 against C060's 13.0 "
                    "and 9.2, so 0.60 is at the minimum of the ladder",
        "estimator": "median across rosters, which is the estimator "
                     "`score_wt.py` applies through `facts.aggregate_value`; "
                     "errors on the transmission statistic are a "
                     "delete-one-roster jackknife paired across windows. "
                     "The sector row's tape error is `facts.rule_row`'s "
                     "0.008954 at 252 (median of nine non-crisis windows)",
        "not_adopted": "0.6168, the measured centring loading itself. "
                       "Adopting it would move `sector_excess_corr` by "
                       "about 0.004, which is not a movement the tape can "
                       "resolve, and transmit1's own recommendation is that "
                       "0.60 STANDS and the record carry +0.021 as the "
                       "measured composition correction so nobody re-runs "
                       "the box to find out it is small. Whether to adopt "
                       "0.62 is a question THE TAPE CANNOT ANSWER, and this "
                       "field is where that is recorded rather than being "
                       "left to look like an oversight",
        "derivation_it_agrees_with": "sector-loading.md section 6.3 inverts "
                                     "`odds(E) = c L^p` onto the whole-tape "
                                     "centre from the reading the shipping "
                                     "arm produces at L = 0.8, giving L* = "
                                     "0.599 at 252 and 0.611 at 504, each "
                                     "+/- 0.12 from the target's error "
                                     "alone, judged as one number for both "
                                     "horizons at 0.60. Across three anchor "
                                     "bases the span is 0.565 to 0.678, "
                                     "which is smaller than the target's "
                                     "own bar. THE TRANSFER IT USED WAS "
                                     "REFUTED -- the fitted response "
                                     "exponent is 1.44 against a registered "
                                     "[1.5, 2.0] -- and transmit1 bypasses "
                                     "the transfer by measuring L* directly "
                                     "on the composed base, landing 0.6168 "
                                     "against the 0.6156 the registration "
                                     "derived beforehand, four thousandths "
                                     "apart. So the value is MEASURED and "
                                     "not transferred, and the derivation "
                                     "is what it agrees with rather than "
                                     "what it rests on",
        "what_the_move_from_0.8_cost": "it did not cost the weak row, it "
                                       "helped it. The exchange rate is "
                                       "-0.0105 of `cross_sectional_corr` "
                                       "per +0.1 of loading at 504, so "
                                       "moving 0.8 to 0.60 raised that row "
                                       "by about +0.021. This dial is not "
                                       "why `cross_sectional_corr` is low",
        "superseded": "pt-v19 SHIPPED 0.8 UNTIL 2026-09-14 and this whole "
                      "entry described that choice until 2026-09-15. The "
                      "0.8 record, kept because it was honest and because "
                      "it says what the surface looks like: chosen as the "
                      "third level of the 2 x 2 x 3 composition "
                      "(sectorcomp, thirty seeds, held roster 111, "
                      "2026-09-09) and located on a five-point grid of 0.7, "
                      "0.75, 0.8, 0.85, 0.9 on the four-dial base "
                      "(crosscorr screen, thirty seeds, 2026-09-10), where "
                      "S_19 at 0.75 reads -1.0 / +2.3 from 0.8 and at 0.85 "
                      "+0.4 / +2.3, inside one bootstrap sd of 5-8 per "
                      "cell, and 0.7 and 0.9 are +4 to +7 worse -- so the "
                      "surface is FLAT across 0.75-0.85 and 0.8 was the "
                      "centre of a plateau, not a resolved optimum. It "
                      "centred the row on the 2015-2025 forty-name centre "
                      "of 0.1640, and it was the whole-tape re-centring of "
                      "that target to 0.1178 that moved the value, not a "
                      "defect in the measurement. Scripts: "
                      "programme/scripts/atlas16-jobs.sh with "
                      "AXES_SET=sectorcomp on the factorial runner, cells "
                      "and effects by factorial-cells.py "
                      "(sector-corr-result.md section 3); crosscorr-jobs.sh "
                      "and crosscorr-analyse.py (crosscorr-result.md "
                      "section 3.3, prediction X5); resolve120-jobs.sh arm "
                      "C. What the record says of pt-v16 and pt-v18's "
                      "0.58821442 is the sectorcomp cell in pt-v18's own "
                      "regime (identity off, decay 0.6, thirty seeds): S_19 "
                      "32.5 / 34.2 at 0.588 against 32.9 / 31.8 at 0.7 and "
                      "43.6 / 42.5 at 0.8, flat between 0.588 and 0.7 and "
                      "worse at 0.5 and 0.85, so on pt-v18 that value sits "
                      "on a plateau whose floor is somewhere in 0.6-0.75 "
                      "and was not located (sector-corr-result.md section "
                      "6). Also on the old basis, and kept for the "
                      "mechanism rather than the figure: on the composed "
                      "base the identity and the decay ratio take "
                      "`sector_excess_corr` from -3.5 to -6.7 tape se at "
                      "252 (0.1330 to 0.1040) and the loading is what puts "
                      "the row back on centre, which is why this dial is "
                      "moved at all. The se there is against the 2015-2025 "
                      "forty-name centre of 0.1640; against the whole-tape "
                      "0.1178 the same readings are a different number of "
                      "bars, so the SIZES do not transfer and the direction "
                      "does",
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
        "presets": {"pt-v19": 0.20, "pt-v20": 0.2},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
        "presets": {"pt-v18": 0.0482, "pt-v19": 0.0482, "pt-v20": 0.0482},
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
        "presets": {"pt-v18": 1.0, "pt-v19": 1.0, "pt-v20": 1.0},
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
    # ---- pt-v19's FIFTH COMPOSITION (2026-09-23). Twenty-three dials, the
    # design programme's candidate LMN-Q25A375 (programme/results/
    # candidate-arm.txt, results/ptv19-fifth/RESULT.md section 4, design
    # repository); eighteen of them arrive here, fifteen from OUT_OF_SCOPE
    # and three from POST_BASELINE, and four more moved existing entries
    # above (`market_vol_vix_exponent`, `vix_level_sigma`,
    # `vix_level_loop_gain`, `market_beta_down_asym_lag`). Kinds as the
    # record gives them: what was FITTED against a gate or CHOSEN inside a
    # window is `undetermined`, because a gate or a window is why a value
    # ships and not a measurement of the dial -- the anchor memory, the calm
    # exponent, the lag wire's form and the news drift's timescale here, and
    # the exponent and the lag above. Switches whose identity is the value
    # are `derived` and say so.
    "vix_anchor_weight": {
        "kind": "derived",
        "presets": {"pt-v19": 0.375, "pt-v20": 0.375},
        "identity": "theta = (1 - a) k: the VIX's elasticity to realised "
                    "volatility is the anchor weight's complement times the "
                    "loop's transmission, the slow equilibrium of the memory "
                    "form. Solved for the tape's theta: a = 1 - 0.655 / 1.05 "
                    "= 0.376, shipped 0.375",
        "terms": {
            "theta 0.655 [0.61, 0.69]": "the tape's elasticity of log VIX on "
                                        "log realised volatility "
                                        "(programme/results/calm-regime/"
                                        "RESULT.md section 4, design "
                                        "repository)",
            "k 1.05": "0.578 / 0.55: the transmission measured on the Q20 "
                      "arm, whose weight is 0.45 (calm-regime section 4)",
        },
        "source": "programme/results/calm-regime/RESULT.md sections 4 and 7 "
                  "(design repository); LAW2's procedure (theta pins the "
                  "weight) re-applied on the new base, as "
                  "results/vix-law-levels/RESULT.md section 4 pinned the "
                  "earlier 0.45",
        "date": "2026-09-23",
        "note": "Measured theta on the LAW2C-based Q25A375 arm is 0.617, "
                "inside the tape's interval; theta was not re-read on "
                "pt-v19's own base (ptv19-fifth/RESULT.md section 4). Not the "
                "slow-pole closed form in `ModelParams::vix_anchor_weight` "
                "(0.6099 at exponent 4.0), a different identity no preset "
                "ships",
    },
    "vix_anchor_memory": {
        "kind": "undetermined",
        "presets": {"pt-v19": 0.05555555555555555, "pt-v20": 0.05555555555555555},
        "what_would_determine_it": "a tape reading of how long a deviation "
            "of the VIX from the level realised variance implies persists "
            "before it is pulled back. FITTED: 1/18 was read off a ladder "
            "against the certification's VIX persistence gate in route 1 "
            "(programme/results/route1-blend/RESULT.md sections 8.1, 8.3 and "
            "8.4, design repository). At 1/15 the 504-session VIX row fails "
            "by one seed, so the value sits on an edge, not in a basin",
    },
    "vix_anchor_centre": {
        "kind": "derived",
        "presets": {"pt-v19": 0.1515, "pt-v20": 0.1515},
        "identity": "c = ln(1.252 / 1.076): the anchor pulls to `L * anchor "
                    "* exp(-c)`, the VIX's MEAN level (the pooled premium on "
                    "the pooled variance) rather than the per-window premium "
                    "applied to the mean variance",
        "terms": {
            "1.252": "the tape's per-window median of VIX over realised "
                     "volatility",
            "1.076": "the same relation pooled over the whole tape",
        },
        "source": "programme/results/vix-law-levels/RESULT.md sections 3, 4 "
                  "and 7 (design repository): two tape measurements, no "
                  "model run chose it; the free-running zero-drift level "
                  "lands at 17.6 against the tape's 18.3",
        "date": "2026-09-23",
    },
    "vix_anchor_weight_level": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "eta in `1 - a(x) = (1 - a) (K / clamp(x, K, r K))^eta`: "
                    "the log-slope of the held read-back's gain g(x) between "
                    "VIX 18.5 and 30, so the loop's local gain `(1 - a(x)) "
                    "g(x)` is held at its knee value above the knee (the "
                    "tape's reversion toward its median is "
                    "level-independent)",
        "terms": {"1.06": "the measured log-slope on the engine's held-VIX "
                          "map, rounded to 1.0 (vix-law-levels section 3)"},
        "source": "programme/results/vix-law-levels/RESULT.md sections 3, 4 "
                  "and 7 (design repository): LAW's law, the knee at g = 1. "
                  "Read off the ENGINE's held map at "
                  "`market_vol_vix_exponent` 4.0, not off the tape",
        "date": "2026-09-23",
    },
    "vix_anchor_weight_level_knee": {
        "kind": "derived",
        "presets": {"pt-v19": 0.3888, "pt-v20": 0.3888},
        "identity": "k' = k + ln((1 - a') / (1 - a)) / eta: the crisis side "
                    "of the weight, (1 - a)(K / x)^eta, held while the base "
                    "weight moves from 0.45 to a' = 0.375: 0.2609 + "
                    "ln(0.625 / 0.55) / 1.0 = 0.3888",
        "terms": {
            "k 0.2609": "ln(24.015 / 18.5), LAW's knee (where the held map's "
                        "gain crosses one) as a log offset below the anchor "
                        "(vix-law-levels sections 3 and 4)",
            "0.55, 0.625": "1 - 0.45, the base weight the knee was read "
                           "at; 1 - 0.375, pt-v19's",
        },
        "source": "programme/results/calm-regime/RESULT.md section 6b "
                  "(design repository)",
        "date": "2026-09-23",
    },
    "vix_anchor_weight_level_cap": {
        "kind": "derived",
        "presets": {"pt-v19": 2.2159, "pt-v20": 2.2159},
        "identity": "r' = r (1 - a') / (1 - a): the cap held at the same VIX "
                    "as the knee moves, 1.95 * 0.625 / 0.55 = 2.2159",
        "terms": {"r 1.95": "36 / 18.5, where the held map's gain reaches 95 "
                            "per cent of its peak (vix-law-levels section 4)"},
        "source": "programme/results/calm-regime/RESULT.md section 6b "
                  "(design repository)",
        "date": "2026-09-23",
        "note": "vix-law-levels found LAW's cap inert at its settings; "
                "nobody has checked whether 2.2159 binds on pt-v19 "
                "(ptv19-fifth/RESULT.md section 4)",
    },
    "market_vol_vix_exponent_below": {
        "kind": "undetermined",
        "presets": {"pt-v19": 2.5, "pt-v20": 2.5},
        "what_would_determine_it": "a single tape target where today there "
            "is a window. CHOSEN, not derived: below the anchor the tape's "
            "shared variance scales as VIX^2.25 [1.95, 2.57] and its calm "
            "share over its mid-VIX share is 0.45 [0.38, 0.50]; on the LAW2C "
            "base both hold for exponents of about 2 to 2.6, and 2.5 is the "
            "least change from 4.0 inside that window "
            "(programme/results/calm-regime/RESULT.md sections 4 and 7, "
            "design repository). On the adopted vector the slope reads 1.94, "
            "just under the window, and the ratio 0.42, inside it "
            "(ptv19-fifth/RESULT.md section 4)",
    },
    "market_beta_down_asym_lag_live": {
        "kind": "undetermined",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "what_would_determine_it": "the diagnostic registered in "
            "programme/results/corr-asymmetry-repair.md section 8 (design "
            "repository), which would rule on the form and is unrun. A FORM "
            "dial with two admissible values and no number to derive: 1.0 "
            "samples the down-day wire on the live session, and pt-v19 takes "
            "it because that is what shows the tape's same-day "
            "`corr_asymmetry` (k 25 of 30 against 19 without it; "
            "route1-blend/RESULT.md section 8.2). CHOSEN for the mechanism "
            "it shows, not derived",
    },
    "macro_compound_days_per_year": {
        "kind": "derived",
        "presets": {"pt-v19": 252.0, "pt-v20": 252.0},
        "identity": "the economy steps once per trading session, so a year "
                    "of compounding is 252 steps; at 365 a trading year "
                    "received 252/365 of its annual GDP and CPI growth",
        "terms": {"252": "sessions in a trading year"},
        "source": "rust/src/params.rs `ModelParams::macro_compound_days_per_"
                  "year`; programme/results/longrun-drift/RESULT.md section "
                  "4 and results/macro-cycle/RESULT.md section 7 (design "
                  "repository): the long-run index return 3.83 -> 4.92 per "
                  "cent from this alone",
        "date": "2026-09-23",
    },
    "macro_calendar_days_per_year": {
        "kind": "derived",
        "presets": {"pt-v19": 252.0, "pt-v20": 252.0},
        "identity": "the session calendar: a 21-step month, a 63-step "
                    "quarter and every calendar-day span scaled by 252 / 365 "
                    "and rounded, because the economy steps once per session",
        "terms": {"252": "sessions in a trading year"},
        "source": "rust/src/params.rs `ModelParams::macro_calendar_days_per_"
                  "year`; programme/results/macro-cycle/RESULT.md sections 2 "
                  "and 7 (design repository)",
        "date": "2026-09-23",
    },
    "cycle_us_calibration": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "a switch whose identity is the value: 1.0 reads the "
                    "phase table derived from NBER recession dates and BEA "
                    "real GDP, 1990-2025, with the Weibull scales solved "
                    "from its duration means",
        "terms": {"the table": "rust/src/economy/state.rs "
                               "`us_phase_characteristics`, each number's "
                               "derivation in its docstring"},
        "source": "programme/results/macro-cycle/RESULT.md sections 3 and 7 "
                  "(design repository): 1.13 recessions a decade, 8.7 months "
                  "long, against the real 1.11 and 9.0",
        "date": "2026-09-23",
        "note": "Two choices inside the table, recorded as such by "
                "macro-cycle section 3: the hazard cap raised from 0.3 to "
                "1.0, and the 5.5 / 3.5 contraction-to-trough split taken "
                "from the model's own occupancy",
    },
    "fed_liftoff_rule": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "a switch whose identity is the value: the ladder's own "
                    "cut branch mirrored (its 50 bp trigger and 25 bp steps), "
                    "so the rate lifts off zero on its Taylor rule instead "
                    "of waiting for inflation a point over target",
        "terms": {"2.61": "the model's mean fed funds rate with it, against "
                          "1.68 without and the real 2.88"},
        "source": "programme/results/macro-cycle/RESULT.md sections 4 and 7 "
                  "(design repository)",
        "date": "2026-09-23",
    },
    "market_pe_buybacks": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "a switch whose identity is the value: market_pe divides "
                    "by the same buyback-scaled earnings the valuation "
                    "already applies, so the multiple no longer rises by the "
                    "buyback yield a year",
        "terms": {"21.3": "market_pe in year 21 with it, against 28.1 "
                          "without"},
        "source": "programme/results/macro-cycle/RESULT.md sections 4 and 7 "
                  "(design repository)",
        "date": "2026-09-23",
    },
    "cycle_stationary_opening": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "a switch whose identity is the value: 1.0 draws day "
                    "zero's phase AND age from the cycle's own stationary "
                    "law, `P(i, a) = S_i(a + 1) / sum_j E[T_j]`, every term "
                    "the engine's own (`ModelParams::cycle_stationary_"
                    "opening`), instead of opening every run in expansion at "
                    "age zero",
        "terms": {"7.83 -> 7.35": "the certified index_drift_pct from the "
                                  "boom opening and from a drawn one, "
                                  "against a band centre of 7.37 "
                                  "(programme/results/macro-cycle/RESULT.md "
                                  "section 6, design repository)"},
        "source": "the verdict ledger's standing ruling "
                  "`ruling-certification-runs-open-at-a-random-point-in-the-"
                  "business-cycle` (design repository, recorded 2026-09-23): "
                  "certification runs open at a random point in the cycle, "
                  "because a bot may start trading in any phase. The law is "
                  "derived; turning it on is the owner's ruling",
        "date": "2026-09-23",
    },
    "news_absorption_half_life": {
        "kind": "derived",
        "presets": {"pt-v19": 0.6, "pt-v20": 0.6},
        "identity": "h solving A(1) = the share of an earnings move priced "
                    "one minute after the release, with A(n) = (1 - d)(1 - "
                    "2^(-n/h)) / (1 - 2^(-390/h)) + d (1 - 2^(-n/h_d)) / (1 "
                    "- 2^(-390/h_d)): 0.64 on the 2008-2020 share, 0.60 on "
                    "2016-2020's; 0.6 puts A(1) at 0.605",
        "terms": {"1.05 of 1.80, 0.76 of 1.11": "Christensen, Timmermann and "
                                                "Veliyev, arXiv 2601.08962, "
                                                "Table 7: per cent earned at "
                                                "one minute and by the "
                                                "close, 2008-2020 and "
                                                "2016-2020"},
        "source": "programme/results/news-speed/RESULT.md section 2 and "
                  "derive.txt (design repository)",
        "date": "2026-09-23",
        "note": "Set by the one-minute share, the first point a one-minute "
                "tick can express; the least-squares fit over 1 to 5 minutes "
                "(0.8) is printed and not used. The two samples bracket it; "
                "no error bar is carried",
    },
    "news_absorption_drift_share": {
        "kind": "derived",
        "presets": {"pt-v19": 0.12, "pt-v20": 0.12},
        "identity": "d = 1 - 1.58 / 1.80 = 0.122: the share of the move "
                    "that arrives after five minutes",
        "terms": {"1.58, 1.80": "per cent earned by five minutes and by the "
                                "close, 2008-2020 (Christensen, Timmermann "
                                "and Veliyev, Table 7)"},
        "source": "programme/results/news-speed/RESULT.md section 2 and "
                  "derive.txt (design repository)",
        "date": "2026-09-23",
    },
    "news_absorption_drift_half_life": {
        "kind": "undetermined",
        "presets": {"pt-v19": 42.0, "pt-v20": 42.0},
        "what_would_determine_it": "an intraday event study that resolves "
            "the timescale of the drift after five minutes. CHOSEN: 42 ticks "
            "is a 60-minute mean life (60 ln 2 = 41.6), which lands 92 per "
            "cent of the drift in 2.5 hours; the 60 minutes is chosen to fit "
            "Patell and Wolfson (1984)'s disturbances lasting 'several "
            "hours', not measured (programme/results/news-speed/RESULT.md "
            "section 2, design repository)",
    },
    "news_quote_revision": {
        "kind": "derived",
        "presets": {"pt-v19": 1.0, "pt-v20": 1.0},
        "identity": "a switch whose identity is the value: the maker "
                    "re-quotes by the tick's news term, so the traded tape "
                    "carries the absorption profile. With it the tape holds "
                    "0.615 of the move after one tick against the profile's "
                    "0.605; without it the book walk leaves 0.086, and the "
                    "tick-1 headline edge is +123 bp against +49",
        "terms": {"0.615, 0.086": "share of the event in the printed price "
                                  "after one tick, with and without "
                                  "(news-speed section 3)"},
        "source": "programme/results/news-speed/RESULT.md section 3 (design "
                  "repository)",
        "date": "2026-09-23",
    },
    # pt-v20 (2026-09-24): the tape, the closing cross, the fair-value share,
    # the stationary opening and the stop ladder's scale. Rows, bands and
    # predictions registered before the grading box in the design
    # repository's programme/ptv20-registration.md; the desk runs behind
    # every figure below are programme/results/ptv20/desk/.
    "quote_model_weight": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a weight whose identity is the value: at 1.0 the "
                    "maker's book is centred on the efficient price (the "
                    "model price) every tick, which is what quotes that "
                    "track the efficient price means (Roll 1984), and the "
                    "print's noise around it is then the bid-ask bounce. At "
                    "0.0 the print chased the model price and the inventory "
                    "skew carried it past",
        "terms": {"-0.135 -> -0.03": "lag-one autocorrelation of 65-minute "
                                     "print returns, median name, certified "
                                     "roster (meanrev-edge section 4; ptv20 "
                                     "desk r1-r6)",
                  "5.8x -> 1.3-1.5x": "Roll spread over the quoted spread"},
        "source": "programme/meanrev-edge-ptv19-2026-09-24.md section 4 and "
                  "programme/ptv20-registration.md (design repository)",
        "date": "2026-09-24",
    },
    "closing_auction": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a switch whose identity is the value: a closing cross "
                    "clears at the efficient price, so the session's close "
                    "is the model price and the close-to-close return "
                    "carries no bid-ask bounce. Without it the 15:59 print "
                    "sat 14 bp (sd) off the model price on the certified "
                    "roster's large names and 45 bp on its small ones, the "
                    "intraday volume curve being at its peak there, and the "
                    "noise reverted the next day",
        "terms": {"+13 -> -1 bp": "daily Lo-MacKinlay one-day book on the "
                                  "roster's large names, with and without the "
                                  "cross (ptv20 desk r3, r4), against the "
                                  "certified forty's -1.7, whose closes are "
                                  "auction prices"},
        "source": "programme/ptv20-registration.md (design repository)",
        "date": "2026-09-24",
    },
    "fair_value_news_share": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "every stock- and sector-specific shock -- the "
                    "idiosyncratic and sector noise draws, the company's "
                    "own, peer and sector news, its own jump -- is news "
                    "about the company's value and not about how the market "
                    "prices it, so all of it moves fair value for good; the "
                    "mispricing keeps the market-wide part. The end point "
                    "of the dial is the statement, and it is not tuned: the "
                    "cross-sectional rows land on the real panel at it",
        "terms": {"0.59 -> 0.95": "60-session idiosyncratic variance ratio, "
                                  "median name (real 0.924; C5)",
                  "+0.40 -> 0.00": "rank IC of the value signal on "
                                   "published fundamentals against the next "
                                   "20 sessions (real +0.009; C6)",
                  "-0.17 -> -0.01": "12-1 month momentum rank IC (real "
                                    "+0.027; C7)"},
        "source": "programme/meanrev-edge-ptv19-2026-09-24.md section 8 and "
                  "programme/ptv20-registration.md; ptv20 desk r1-r6 "
                  "(design repository)",
        "date": "2026-09-24",
    },
    "opening_mispricing_sigma": {
        "kind": "measured",
        "presets": {"pt-v20": 0.016},
        "source": "programme/results/ptv20/desk/r5.json and r6b.json "
                  "(design repository)",
        "date": "2026-09-24",
        "script": "programme/results/ptv20/desk.py, row s_xsd_stationary",
        "estimator": "the cross-sectional sd of s after the cross-sectional "
                     "mean is taken out, averaged over sessions 250-2660, "
                     "certified roster, untraded, seeds 201-212, at the "
                     "shipped vector's tape, cross and fair-value share",
        "residual": "0.0155 to 0.0165 across cascade_gain 0.2 to 0.4 "
                    "(seeds 201-206), 0.012 at 0.1 (seeds 207-212); the "
                    "value moves the opening only, and C6's first-60 "
                    "reading is 0.00 to 0.015 across it",
    },
    "fair_value_market_share": {
        "kind": "undetermined",
        "presets": {"pt-v16": 0.0, "pt-v18": 0.0, "pt-v19": 0.0, "pt-v20": 0.0},
        "what_would_determine_it": "the index's annual return spread (B9, "
            "S&P 500 calendar years 1990-2024, sd 17.4) and a replayed "
            "crisis's persistence against 2008, 2020 and 2022, graded with "
            "the fifteen on the ptv20g1 box's arms at 0.5 and 1.0 (design "
            "repository programme/ptv20-registration.md section B9); a "
            "ruling by the owner, since it rewrites the index's variance "
            "structure the B rows were calibrated on",
    },
    "opening_market_sigma": {
        "kind": "measured",
        "presets": {"pt-v20": 0.1},
        "source": "programme/results/ptv20/ (desk, mkts.py; design repository)",
        "date": "2026-09-24",
        "script": "the cap-weighted mean of s over the certified roster, its "
                  "time-series sd over sessions 250-2660, pt-v20's vector",
        "estimator": "the stationary spread of the market's own mispricing: "
                     "the sd over sessions of the cap-weighted s, certified "
                     "roster, untraded, seeds 201-203",
        "residual": "0.148, 0.105 and 0.042 on the three seeds (mean 0.099): "
                    "the level is episodic, and the opening draws from the "
                    "mean spread",
    },
    # The agent-facing book (E4, feature/order-book-depth), taken by pt-v20.
    # Read only on an agent's path, so no untraded statistic moves with any
    # of them; tools/calibration/impact_curve.py measures what they buy.
    "book_depth_coefficient": {
        "kind": "measured",
        "presets": {"pt-v20": 0.75},
        "source": "tools/calibration/impact_curve.py (feature/order-book-depth; "
                  "pt-v20's run in the design repository, programme/results/ptv20/)",
        "date": "2026-09-24",
        "script": "tools/calibration/impact_curve.py --base pt-v20",
        "estimator": "the latent depth's scale at which the median average cost "
                     "of an immediate order of Q = f V, in the name's daily "
                     "sigma, meets two thirds of Toth et al.'s (2011) peak Y in "
                     "[0.5, 1]: fitted 0.469 sigma (Q/V)^0.495 from 1 per cent "
                     "of daily volume up, 3 seeds x 40 names",
        "residual": "average coefficient 0.469 against the band 0.33-0.67; "
                    "peak 0.672 against 0.5-1; matches Almgren et al. (2005) "
                    "at 10 and 30 per cent of daily volume",
    },
    "book_depth_exponent": {
        "kind": "derived",
        "presets": {"pt-v20": 0.5},
        "identity": "the square-root law's exponent: latent depth growing "
                    "linearly with distance gives a cost in sqrt(Q/V)",
        "terms": {"0.495": "fitted exponent of the average cost, pt-v20"},
        "source": "Toth et al., Physical Review X 1, 021006 (2011); "
                  "tools/calibration/impact_curve.py",
        "date": "2026-09-24",
    },
    "book_depth_reach": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "the latent book reaches a whole day's volume, so an order "
                    "up to 100 per cent of V walks it rather than being cut off",
        "terms": {"1.0": "one day's volume"},
        "source": "feature/order-book-depth hand-off (E4)",
        "date": "2026-09-24",
    },
    "book_shared": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a switch whose identity is the value: agents consume one "
                    "book, so one agent's fill is liquidity the next does not get",
        "terms": {"1.0": "on"},
        "source": "feature/order-book-depth hand-off (E4)",
        "date": "2026-09-24",
    },
    "book_resting": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a switch whose identity is the value: an agent's limit "
                    "order rests in the book with queue priority",
        "terms": {"1.0": "on"},
        "source": "feature/order-book-depth hand-off (E4)",
        "date": "2026-09-24",
    },
    "book_refill_half_life": {
        "kind": "measured",
        "presets": {"pt-v20": 27.0},
        "source": "tools/calibration/impact_curve.py refill arm (E4)",
        "date": "2026-09-24",
        "script": "tools/calibration/impact_curve.py (refill)",
        "estimator": "the half-life in ticks at which a second order's extra "
                     "temporary cost after a first decays as E4's hand-off "
                     "measured; the literature's resilience of minutes to an "
                     "hour",
        "residual": "at 10 per cent of V the second order pays 23.1 bp more "
                    "at k=0, 9.7 at 30 ticks and 1.6 at 130 (pt-v20 run)",
    },
    "fill_impact_coefficient": {
        "kind": "derived",
        "presets": {"pt-v20": 0.314},
        "identity": "Almgren, Thum, Hauptmann and Li (Risk 18(7), 2005)'s "
                    "permanent impact gamma: 0.314 sigma X / V, the linear "
                    "permanent law, attributed per agent",
        "terms": {"0.314": "gamma, US equity program trades"},
        "source": "Almgren et al. 2005; feature/order-book-depth hand-off (E4)",
        "date": "2026-09-24",
    },
    "treasury_2y_noise": {
        "kind": "measured",
        "presets": {"pt-v20": 0.022},
        "source": "programme/results/ptv20/real_rates.json and the desk curve "
                  "runs (design repository)",
        "date": "2026-09-24",
        "script": "the desk's rates.py: sd of the daily change of the 2-year, "
                  "certified roster, seeds 204-209, 1008 sessions; the tape's "
                  "from FRED DGS2 2015-2025 (real_rates.py)",
        "estimator": "the noise at which the model's 2-year daily change sd "
                     "meets the tape's 5.23 bp",
        "residual": "5.33 bp against 5.23 at 0.022; 5.73 at 0.028",
    },
    "treasury_10y_noise": {
        "kind": "measured",
        "presets": {"pt-v20": 0.025},
        "source": "programme/results/ptv20/real_rates.json and the desk curve "
                  "runs (design repository)",
        "date": "2026-09-24",
        "script": "the desk's rates.py: sd of the daily change of the 10-year, "
                  "seeds 204-209, 1008 sessions; the tape's from FRED DGS10",
        "estimator": "the noise at which the model's 10-year daily change sd, "
                     "most of it meeting-day moves toward the policy target, "
                     "meets the tape's 5.41 bp. 0.03 through pt-v19 is the "
                     "reference literal, unmeasured",
        "residual": "6.1 bp at 0.03 with the flight to quality on, against "
                    "5.41; 0.025 trims it",
    },
    "flight_to_quality_gain": {
        "kind": "measured",
        "presets": {"pt-v20": 0.008},
        "source": "programme/results/ptv20/real_rates.json and the desk curve "
                  "runs (design repository)",
        "date": "2026-09-24",
        "script": "the desk's rates.py: correlation of the roster index's "
                  "daily return with minus the 10-year's change",
        "estimator": "the gain at which that correlation meets the tape's "
                     "-0.16 (SPY against IEF, 2015-2025). 0.02 through "
                     "pt-v19 is the reference literal, which never fired",
        "residual": "-0.19 to -0.20 at 0.008 on seeds 204-209, -0.11 at "
                    "0.005, -0.60 at 0.02",
    },
    "flight_to_quality_day": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a switch whose identity is the value: the yield the "
                    "step writes after the close is that session's close, so "
                    "the move it answers is that session's return. The "
                    "shipped rule read the previous session's closing minute "
                    "behind a 0.5 per cent gate that minute never crosses",
        "terms": {"+0.02 -> -0.20": "correlation of the index's daily return "
                                    "with minus the 10-year's change (tape "
                                    "-0.16)"},
        "source": "programme/results/ptv20/ (design repository)",
        "date": "2026-09-24",
    },
    "corporate_yield_daily": {
        "kind": "derived",
        "presets": {"pt-v20": 1.0},
        "identity": "a switch whose identity is the value: the meeting "
                    "formula's own terms (the 10-year plus a spread of 2 bp a "
                    "VIX point times the cycle multiplier) applied to each "
                    "session's changes, so the level between meetings is the "
                    "formula's and the next meeting re-anchors it",
        "terms": {"+0.03 -> +0.22": "correlation of the index's daily return "
                                    "with minus the corporate yield's change "
                                    "(tape +0.27, SPY against LQD)"},
        "source": "programme/results/ptv20/ (design repository)",
        "date": "2026-09-24",
    },
    "earnings_cycle_depth": {
        "kind": "measured",
        "presets": {"pt-v20": 0.35},
        "source": "programme/results/ptv20/grid-e4.txt and box-e4/ (design "
                  "repository), box ptv20e4",
        "date": "2026-09-24",
        "script": "programme/results/ptv20/grid_table.py over longrun.py's "
                  "report, 90 pooled histories (seeds 101-130, 401-430, "
                  "701-730), 21 years each",
        "estimator": "row B9, the sd of annual index log returns over "
                     "non-overlapping 252-session blocks, years 2-21, "
                     "pooled, against the S&P 500's 17.4 (1990-2024), "
                     "among the arms that keep every long-run row in its "
                     "band. Depth 0 reads 11.7; 0.25, 0.30 and 0.35 read "
                     "14.6, 15.4 and 16.4 at pt-v19's transient shocks, "
                     "and 0.35 reads 16.8 with the market factor at 0.85 "
                     "and market jumps at half rate, the arm shipped. "
                     "Deeper moves B3 out first",
        # The bar: B9's real value carries a bootstrap se of 2.8 points over
        # calendar years, and the pooled reading moves about 20 points per
        # unit of depth, so the depth the tape pins is +/- 0.14.
        "standard_error": 0.14,
        "residual": -0.6,
        "estimate": 0.35,
    },
    "earnings_cycle_upside": {
        "kind": "derived",
        "presets": {"pt-v20": 0.09},
        "identity": "upside = q / (1 - q), where q is the share of the cycle "
                    "spent in contraction and trough, so the pull toward "
                    "-depth there and +depth * upside elsewhere averages to "
                    "zero and the cycle moves earnings around the nominal-"
                    "output path without shifting it",
        "terms": {"q = 9 / 108": "contraction plus trough, 9.0 months of a "
                                 "108-month cycle, the US phase table's "
                                 "targets (rust/src/economy/state.rs, "
                                 "us_phase_characteristics; NBER 1990-2025)",
                  "9 / 99 = 0.0909": "rounded to 0.09"},
        "source": "rust/src/economy/state.rs, us_phase_characteristics",
    },
    "earnings_cycle_half_life": {
        "kind": "undetermined",
        "presets": {"pt-v16": 60.0, "pt-v18": 60.0, "pt-v19": 60.0,
                    "pt-v20": 60.0},
        "what_would_determine_it": "the lag of S&P earnings behind the NBER "
                                   "turning points in Shiller's monthly "
                                   "series, read as the half-life of a pull "
                                   "toward the phase's level. The co-tune "
                                   "grid held it at the default; the "
                                   "calibration box ptv20e1 found the market "
                                   "mispricing's half-life at 40 or 90 did "
                                   "not help B9, and this one was not "
                                   "searched. Unread on pt-v16 to pt-v19",
    },
    "earnings_cycle_sigma": {
        "kind": "undetermined",
        "presets": {"pt-v16": 0.0, "pt-v18": 0.0, "pt-v19": 0.0,
                    "pt-v20": 0.0},
        "what_would_determine_it": "the within-phase sd of twelve-month "
                                   "earnings growth in Shiller's series "
                                   "once the phase means are taken out. "
                                   "0.0 takes no draw, so the cycle moves "
                                   "earnings only through the phase path. "
                                   "Unread on pt-v16 to pt-v19",
    },
    "cascade_gain": {
        "kind": "measured",
        "presets": {"pt-v20": 0.1},
        "source": "programme/results/ptv20/desk/r4.json, r5.json (design "
                  "repository)",
        "date": "2026-09-24",
        "script": "programme/results/ptv20/desk.py, row lm1_bps",
        "estimator": "the daily Lo-MacKinlay one-day contrarian book on the "
                     "certified roster's closes, median over histories of "
                     "2660 sessions, against the certified forty's -1.74; "
                     "the reading moves about -8.5 bp a day per unit of gain "
                     "from about -0.7 at zero, so -1.74 puts the gain at "
                     "0.12",
        # The bar of the estimate: the desk median's se over six histories
        # (about 0.95 bp) over the slope. It is as large as the value, which
        # is the honest reading: the ladder's scale is not distinguishable
        # from zero on the daily row, and 0.1 keeps a tenth of the crash
        # continuation it carries rather than deleting a mechanism on a
        # reading that cannot tell the two apart.
        "standard_error": 0.11,
        "estimate": 0.12,
    },

}

#: Dials in scope that carry NO entry.
#:
#: This list is the finding. Seventy-three names declared here against
#: ninety-eight dials in scope (one of them, `vix_mean_reversion`, beside
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
#: fifth left on 2026-09-11 with charter bar B4: `vix_target_shock_cap`,
#: which pt-v19 now sets to the image of its own clamp rather than to a
#: value that bound inside the graded range. Three more left on 2026-09-23
#: with pt-v19's fifth composition (see the note at the head of the list).
#: The list has not GROWN since,
#: which is the point worth recording:
#: `crash_amplifier_conditional_sigma` arrived on 2026-09-11 carrying its
#: own entry, so the one dial added for the stability fix never spent a
#: commit here. A
#: `measured` entry names EVERY in-scope preset's value (asserted in
#: `tests/test_dial_provenance.py`), so where pt-v16 and pt-v18 ship an
#: older value for one of these -- 0.6 and 0.58821442 -- the entry says
#: what the record measured about that value (the paired control; a
#: plateau) rather than leaving it here.
UNPROVENANCED = (
    # `rate_pe_sensitivity` became a dial at 0.8.5 at the constant that
    # stood, 1.5, which no record derives (POST_BASELINE says why it is in
    # scope); the 2022 measurement that would set it is design repo
    # programme/ptv20-scenario-size.md section 5.
    "rate_pe_sensitivity",
    # `macro_calendar_days_per_year`, `macro_compound_days_per_year` and
    # `cycle_stationary_opening` left on 2026-09-23 with pt-v19's fifth
    # composition, which moves all three and carries an entry for each: the
    # two clocks at the derived 252.0 and the opening at the stationary law,
    # by the owner's ruling. The 365.0 and 0.0 pt-v16 and pt-v18 carry are
    # pt-v1's own values, which the difference rule does not ask about.
    "crash_amplifier_slope",
    "crash_amplifier_threshold",
    "crisis_blend_cap",
    "crisis_blend_ramp",
    "crisis_blend_source",
    "crisis_vix_threshold",
    "crowd_lean_cap",
    "crowd_momentum_gain",
    "crowd_valuation_gain",
    "daily_credit_floor_gain",
    "endogenous_news_intensity",
    "endogenous_news_sigma",
    "garch_alpha",
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
    "market_vol_ceiling_multiple",
    "market_vol_floor_multiple",
    "market_vol_slow_vix_damp",
    "market_vol_slow_weight",
    "market_vol_vix_anchor",
    "market_vol_vix_coupling",
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
    "vix_cycle_amplitude",
    "vix_realised_vol_weight",
    "vix_return_clamp",
    "vix_return_source",
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


# WHAT THIS DOES NOT READ, AND WHY: `superseded`.
#
# `sector_loading` passed every rule below for a day while its source, its
# date, its script and its residual all described 0.8 and `presets` shipped
# 0.60. The only field telling the truth was `superseded`. The obvious
# repair -- teach the schema to read it -- was considered on 2026-09-15 and
# REFUSED, twice over.
#
# First, it inverts the rule. `superseded` is where an entry puts a record
# it has itself declared no longer current. A schema that accepted it as
# evidence would let an entry satisfy `source`, `date`, `script` or its
# error bar out of a record the entry says does not apply, which is a
# strictly worse state than the one being fixed: today the stale evidence
# at least sits in a field named for history.
#
# Second, it would not have caught this. The check that would is "do the
# measured fields describe the value in `presets`", and that is a human
# read. The fields are prose; `date` is free text ("2026-09-09 (sectorcomp,
# resolve120); 2026-09-10 (crosscorr, cert4b)"); and the `standard_error`
# note inside this function says why residual shapes are not enumerable
# here. A rule that could only approximate the question would have gone
# green on the entry it was written for -- a guard reporting green because
# its subject sits outside what it can ask, which is the failure the module
# note names one level up. `standard_error` IS checked, because a bar has a
# SHAPE that can be wrong; a stale narrative does not.
#
# So the repair is in the entries and in the audit, not here: history goes
# in `superseded`, the required fields describe what ships, and
# `audit()["in_both"]` closes the guard hole the same audit found. If a
# mechanical form of this is ever wanted, the honest one is a `describes`
# field carrying the value each measured field is about, checked against
# `presets` -- a new obligation on every entry, not a reading of an old
# field. Nobody has paid for that yet, and saying so is cheaper than a rule
# that reports green.
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

    # PRESENCE WAS NOT ENOUGH, and an audit found out how. On 2026-09-12
    # `market_vol_gamma` shipped with `standard_error` 0.1556 -- its own
    # POINT ESTIMATE, pasted into the bar field. The check above passed it,
    # because something was there. A bar equal to the value says the
    # coefficient is one sigma from zero, which for a term adopted at a
    # likelihood ratio of 305 is not merely wrong but backwards.
    #
    # So a numeric bar must be positive, finite, and smaller than the value
    # it qualifies on at least one shipped preset. A bar that is not
    # numeric -- a dict recording a likelihood ratio, say -- is left to the
    # entry, because the shapes an honest residual can take are not
    # enumerable here; what is refused is the shape that LOOKS like a
    # standard error and is not one.
    se = entry.get("standard_error")
    if isinstance(se, (int, float)) and not isinstance(se, bool):
        if not (se > 0.0) or se != se or se in (float("inf"), float("-inf")):
            problems.append(
                f"{dial}: standard_error is {se!r}, which is not a positive "
                "finite number"
            )
        else:
            same = sorted({v for v in (entry.get("presets") or {}).values()
                           if isinstance(v, (int, float)) and abs(v) == se})
            if same:
                problems.append(
                    f"{dial}: standard_error {se} is EXACTLY a shipped value "
                    f"{same}. That is the point estimate pasted into the bar "
                    "field, and it claims the coefficient is one sigma from "
                    "zero. Record the bar the estimator produced, or record "
                    "the residual that was actually measured and say which."
                )
            # A BAR BELONGS TO ONE ESTIMATE, and when the entry lists more
            # than one shipped value the bar does not say which. That is
            # how `market_vol_alpha` shipped the symmetric fit's 0.0093
            # (the bar for 0.1059, a value in no preset) beside the GJR
            # value 0.0066: two values in `presets`, one bar, and nothing
            # tying the bar to either. So an entry with more than one
            # distinct shipped value must name the `estimate` its bar
            # qualifies, and that estimate must be one of the values that
            # ship. A bar for a value nobody ships is a bar for a
            # different measurement.
            shipped = {v for v in (entry.get("presets") or {}).values()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if len(shipped) > 1:
                est = entry.get("estimate")
                if not isinstance(est, (int, float)) or isinstance(est, bool):
                    problems.append(
                        f"{dial}: standard_error {se} sits beside "
                        f"{len(shipped)} distinct shipped values and the entry "
                        "does not say which one it is the bar for. Add "
                        "`estimate`, the value the estimator produced."
                    )
                elif est not in shipped:
                    problems.append(
                        f"{dial}: `estimate` {est} is in no shipped preset "
                        f"({sorted(shipped)}), so standard_error {se} is the "
                        "bar for a measurement this entry does not ship."
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
    a record goes stale when a dial moves under it; `in_both` are
    dials claiming an entry and an admitted gap at once, which the union
    above cannot see; `post_baseline` are faults in the declared list
    itself; `unclassified` are settable dials
    in no bucket of the partition at all, which is what a newly added dial
    looks like before anyone has decided about it.
    """
    base = _dict(BASELINE)
    moved = moved_dials()
    required = required_dials()
    covered = set(DIAL_PROVENANCE) | set(UNPROVENANCED)

    # THE UNION IS WHERE A DOUBLE BOOKING HIDES, and it hid one until an
    # audit read the record against the table rather than either alone.
    # `missing` is computed from this union, so a dial listed in BOTH
    # sets is counted once here and twice in the two lengths `report()`
    # prints. On 2026-09-15 that dial was `vix_mean_reversion`: 45 entries
    # plus 69 declared names, 113 dials in scope, union 113. Every
    # completeness assertion balanced, `check()` was green, and the table
    # was asserting two contradictory things about one dial.
    #
    # The two sets answer opposite questions -- "where did this value come
    # from" and "nobody has said" -- so a name in both is not a redundancy
    # to be tidied, it is a claim the table is making against itself. It is
    # refused BY NAME rather than left to arithmetic, because the
    # arithmetic is what could not see it: a union has no arity. The suite
    # pins this in `test_a_dial_cannot_be_both_provenanced_and_declared_
    # unprovenanced`, and the older "in both" test next to it is about
    # `MOVED` and `OUT_OF_SCOPE`, a different pair, which is exactly why it
    # never fired here.
    in_both = [
        f"{dial}: carries a DIAL_PROVENANCE entry AND is declared in "
        "UNPROVENANCED. Those are opposite claims about the same dial, and "
        "the union `missing` is computed from cannot see the second one. "
        "Decide which it is: an entry (`undetermined` is a passing kind and "
        "costs no derivation) or an admitted gap"
        for dial in sorted(set(DIAL_PROVENANCE) & set(UNPROVENANCED))
    ]

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
        "in_both": in_both,
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
    for label in ("missing", "invalid", "mismatched", "in_both",
                  "stale_unprovenanced", "post_baseline", "unclassified"):
        for item in a[label]:
            lines.append(f"  {label.upper()}: {item}")
    return "\n".join(lines)


def check() -> None:
    """Raise unless the table describes what the presets actually ship."""
    a = audit()
    faults = (
        [f"no provenance and not declared unprovenanced: {d}" for d in a["missing"]]
        + a["invalid"] + a["mismatched"] + a["in_both"] + a["post_baseline"]
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
