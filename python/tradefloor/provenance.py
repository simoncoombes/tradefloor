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

#: Dials a required preset moves off the baseline that carry NO entry.
#:
#: This list is the finding. Sixty-one of the sixty-four choices in the
#: shipped and candidate presets have no recorded derivation, and several
#: carry eight significant figures with no error bar anywhere --
#: `crisis_blend_gain` at 0.8275881, `crisis_vix_threshold` at 30.88325108,
#: `market_vol_vix_anchor` at 15.98426471. A search optimum with decimal
#: places is still a search optimum.
#:
#: NOT EVERY DIAL WITH A DEFECT IS IN THIS SET. `garch_omega` ships 2e-06 in
#: every preset, so no required preset moves it off the baseline and it is
#: out of scope here -- while the identity at `rust/src/market/garch.rs`
#: puts the shipped value 18.4x short. A dial every preset gets equally
#: wrong is a defect this list is not shaped to catch, because the list
#: asks "what did we choose", not "what is right". It enters the set the
#: moment a preset moves it.
#:
#: It shrinks when a workstream records a derivation and never grows without
#: someone deciding it should. `tests/test_dial_provenance.py` asserts it as
#: a SET in both directions, so a new dial fails until it is either given
#: provenance or added here on purpose.
UNPROVENANCED = (
    "cascade_symmetry",
    "crisis_blend_cap",
    "crisis_blend_gain",
    "crisis_blend_source",
    "crisis_vix_threshold",
    "cycle_hazard_per_month",
    "daily_credit_floor_gain",
    "earnings_nominal_growth",
    "endogenous_news_intensity",
    "endogenous_news_sigma",
    "garch_alpha",
    "garch_beta",
    "garch_vix_coupling",
    "idio_sigma_scale",
    "jump_intensity_idio",
    "jump_intensity_market",
    "jump_mean_compensated",
    "jump_mean_market",
    "jump_momentum_share",
    "jump_sigma_idio",
    "jump_sigma_market",
    "jump_vix_coupling",
    "macro_burn_in_days",
    "market_beta_down_asym_recentre",
    "market_factor_sigma",
    "market_vol_alpha",
    "market_vol_beta",
    "market_vol_ceiling_multiple",
    "market_vol_slow_persistence",
    "market_vol_slow_vix_damp",
    "market_vol_slow_weight",
    "market_vol_vix_anchor",
    "market_vol_vix_coupling",
    "momentum_theta",
    "neutral_discount_rate",
    "news_peer_vix_coupling",
    "news_peer_weight",
    "news_peer_weight_down",
    "oil_opec_symmetry",
    "oil_seasonality_target",
    "qe_pe_gain",
    "sector_loading",
    "sector_loading_beta_slope",
    "sector_vix_coupling",
    "vix_cycle_amplitude",
    "vix_decay_ratio",
    "vix_mean_reversion",
    "vix_realised_vol_weight",
    "vix_return_clamp",
    "vix_return_gain",
    "vix_return_source",
    "vix_target_shock_cap",
    "volume_innovation_sigma",
    "volume_move_cap",
    "volume_move_response",
    "volume_persistence",
    "volume_variance_gain",
)


def _dict(name: str) -> dict[str, float]:
    values = ModelParams.from_preset(name).to_dict()
    return {k: v for k, v in values.items() if k != "name"}


def required_dials() -> dict[str, dict[str, float]]:
    """Every dial a required preset moves off the baseline, and to what.

    Keyed by dial, then by preset, so a dial two presets set differently
    shows both values and an entry has to justify each.
    """
    base = _dict(BASELINE)
    out: dict[str, dict[str, float]] = {}
    for preset in REQUIRED_PRESETS:
        for key, value in _dict(preset).items():
            if key in base and value != base[key]:
                out.setdefault(key, {})[preset] = value
    return out


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
    a record goes stale when a dial moves under it.
    """
    required = required_dials()
    covered = set(DIAL_PROVENANCE) | set(UNPROVENANCED)

    invalid: list[str] = []
    for dial, entry in sorted(DIAL_PROVENANCE.items()):
        invalid.extend(validate_entry(dial, entry))

    mismatched: list[str] = []
    for dial, entry in sorted(DIAL_PROVENANCE.items()):
        if dial not in required:
            mismatched.append(
                f"{dial}: has provenance but no required preset moves it off "
                f"{BASELINE}"
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
        f"{len(a['required'])} dials moved off {BASELINE} by "
        f"{', '.join(REQUIRED_PRESETS)}",
        f"  {len(a['provenanced'])} with provenance "
        + ", ".join(f"{k} {len(v)}" for k, v in a["by_kind"].items()),
        f"  {len(a['unprovenanced'])} declared unprovenanced",
        f"  {len(a['missing'])} with neither",
    ]
    for label in ("missing", "invalid", "mismatched", "stale_unprovenanced"):
        for item in a[label]:
            lines.append(f"  {label.upper()}: {item}")
    return "\n".join(lines)


def check() -> None:
    """Raise unless the table describes what the presets actually ship."""
    a = audit()
    faults = (
        [f"no provenance and not declared unprovenanced: {d}" for d in a["missing"]]
        + a["invalid"] + a["mismatched"]
        + [f"declared unprovenanced but not moved by any required preset: {d}"
           for d in a["stale_unprovenanced"]]
    )
    if faults:
        raise ValidationError(
            "dial provenance does not describe the shipped presets:\n  "
            + "\n  ".join(faults))
