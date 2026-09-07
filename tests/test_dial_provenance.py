"""Every shipped dial value carries its derivation, or says it has none.

The defect these tests exist for is in `tradefloor.provenance`'s module note:
a dial whose justification is another mechanism's defect looks exactly like a
dial derived off an identity, and nothing in the repository could tell them
apart. These assert the three rules that make the difference machine-readable
-- a measured value carries an error bar, a solve carries its tolerance, and a
gap is declared rather than absent -- and, for each, that the rule actually
FIRES rather than being satisfied by construction.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import tradefloor
from tradefloor import provenance as pv


def test_the_table_describes_the_presets_that_actually_ship():
    """The whole audit, as one refusal with every fault named."""
    pv.check()


def test_every_choice_is_either_derived_measured_or_declared_unknown():
    """Completeness, asserted as a SET in both directions.

    A new dial fails until someone either records where its value came from
    or adds it to `UNPROVENANCED` on purpose, and provenance cannot be
    written without the list shrinking. Seventy-seven of the ninety-five
    dials in scope have no recorded derivation today; that number is the
    finding, and this is what stops it growing quietly.
    """
    a = pv.audit()
    assert not a["missing"], (
        "dials with no provenance and no place in UNPROVENANCED: "
        f"{a['missing']}"
    )
    assert not a["stale_unprovenanced"], (
        "names in UNPROVENANCED that no required preset moves off the "
        f"baseline: {a['stale_unprovenanced']}"
    )
    assert set(a["required"]) == set(a["provenanced"]) | set(a["unprovenanced"])


def test_an_entry_that_goes_stale_when_its_dial_moves_is_caught():
    """The binding, not just the listing.

    An entry records the VALUE it justifies, so a dial that moves under it
    stops matching. This is the pt-v18 failure one level down: a figure
    survived a coefficient change because nothing tied the two together.
    """
    dial = "market_beta_down_asym"
    shipped = tradefloor.ModelParams.from_preset("pt-v16").to_dict()[dial]
    assert pv.DIAL_PROVENANCE[dial]["presets"]["pt-v16"] == shipped

    moved = dict(pv.DIAL_PROVENANCE[dial],
                 presets={"pt-v16": shipped + 0.01})
    with_moved = dict(pv.DIAL_PROVENANCE, **{dial: moved})
    a = _audit_with(with_moved, pv.UNPROVENANCED)
    assert any("ships" in m for m in a["mismatched"]), a["mismatched"]


def test_the_settable_surface_is_a_partition():
    """Every settable dial in exactly one bucket, and none left over.

    This is the test the whole module was missing. Scope used to be a
    COMPUTATION -- "differs from pt-v1" -- and a computation can quietly
    stop covering things: on 2026-09-06 it had stopped covering a tape
    measurement, an exponent whose shipped value is not the measured one,
    and a live per-name floor, and every test here passed. A partition
    cannot do that, because the leftovers are named.
    """
    part = pv.partition()
    assert part["surface"] == len(set(pv.settable_dials()))
    assert not part["faults"], part["faults"]
    assert not part["overlapping"], part["overlapping"]
    assert not part["unclassified"], (
        "settable dials in no bucket -- classify them rather than letting "
        f"the audit not ask about them: {part['unclassified']}")

    counted = (len(part["moved"]) + len(part["post_baseline"])
               + len(part["out_of_scope"]))
    assert counted == part["surface"], (counted, part["surface"])


def test_a_dial_added_tomorrow_fails_until_somebody_classifies_it():
    """The property the partition exists for, made to fire.

    A new dial lands in no bucket. It has to FAIL, not be absorbed, or the
    partition is a description of today rather than a guard on tomorrow.
    """
    real = pv.settable_dials
    pv.settable_dials = lambda: tuple(real()) + ("a_dial_nobody_classified",)
    try:
        part = pv.partition()
        assert part["unclassified"] == ["a_dial_nobody_classified"], part
        with pytest.raises(tradefloor.ValidationError,
                           match="no bucket of the partition"):
            pv.check()
    finally:
        pv.settable_dials = real


def test_a_dial_in_two_buckets_is_refused():
    """The other direction, and it is the one that would hide a choice.

    A dial filed out of scope that a preset then moves would read as
    settled in one place and be a live choice in the other. `vix_return_gain`
    is moved by both required presets, so declaring it out of scope has to
    be refused.
    """
    real = pv.OUT_OF_SCOPE
    pv.OUT_OF_SCOPE = dict(real, vix_return_gain="filed away")
    try:
        assert any("in both MOVED and OUT_OF_SCOPE" in f
                   for f in pv.partition()["overlapping"]), pv.partition()
        with pytest.raises(tradefloor.ValidationError, match="in both"):
            pv.check()
    finally:
        pv.OUT_OF_SCOPE = real


ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Dials whose setter writes a field other than their own, so the value
#: reaches the engine under a different name. Pinned, not discovered at
#: import: a parser that silently found nothing would make the test below
#: pass by returning an empty set, which is the shape of a broken instrument.
DERIVED_PARTNERS = {
    "mispricing_half_life_days": {"mispricing_phi", "s_phi_tick"},
    "price_breaker_fraction": {"breaker_up", "breaker_down"},
}


def _setter_arms():
    """Every by-name setter arm in `params.rs`, and the fields it assigns.

    Read from the Rust source because the derived fields are not in
    `to_dict()` -- `breaker_up` and `breaker_down` are invisible from
    Python, which is part of why the dial looked dead.
    """
    lines = (ROOT / "rust" / "src" / "params.rs").read_text(
        encoding="utf-8").split("\n")
    starts = [i for i, l in enumerate(lines)
              if re.search(r'^\s*"[a-z0-9_]+" => (out\.|\{)', l)]
    assert starts, "no setter arms parsed; the instrument is broken"
    region = lines[min(starts):max(starts) + 40]

    arms, i = {}, 0
    while i < len(region):
        m = re.match(r'\s*"([a-z0-9_]+)" => (.*)$', region[i])
        if not m:
            i += 1
            continue
        dial, rest = m.group(1), m.group(2)
        body = [rest]
        if rest.strip().startswith("{"):
            depth = rest.count("{") - rest.count("}")
            j = i + 1
            while j < len(region) and depth > 0:
                body.append(region[j])
                depth += region[j].count("{") - region[j].count("}")
                j += 1
            i = j
        else:
            i += 1
        arms[dial] = set(re.findall(r"out\.([a-z0-9_]+)\s*=", "\n".join(body)))
    return arms


def test_a_dial_that_reaches_the_engine_under_another_name_is_never_out_of_scope():
    """The mistake this test exists for was made here, and it was mine.

    `price_breaker_fraction` was recorded as never read, because nothing
    outside `params.rs` mentions it. Its setter derives `breaker_up` and
    `breaker_down`, the engine reads those, and
    `the_breaker_band_is_derived_once_at_construction` pins it. Searching
    the consumer for a dial's own name is the wrong instrument for a dial
    with a derived partner, and a negative result from the wrong instrument
    is not evidence.

    So a dial with a derived partner may not be declared out of scope at
    all: whatever is true of it cannot have been established that way.
    """
    arms = _setter_arms()
    assert len(arms) == len(set(pv.settable_dials())), (
        f"parsed {len(arms)} setter arms for "
        f"{len(set(pv.settable_dials()))} settable dials; the parser has "
        "drifted and would under-report derived partners")

    found = {d: extra for d, a in arms.items() if (extra := a - {d})}
    assert found == DERIVED_PARTNERS, (
        "the set of dials writing a field other than their own has changed. "
        "Each one needs its out-of-scope status re-decided, because its "
        f"value reaches the engine under another name: {found}")

    for dial in DERIVED_PARTNERS:
        assert dial not in pv.OUT_OF_SCOPE, (
            f"{dial} writes {sorted(DERIVED_PARTNERS[dial])} and cannot be "
            "declared out of scope on evidence about where its own name "
            "appears")


def test_out_of_scope_claims_inertness_and_never_ignorance():
    """`OUT_OF_SCOPE` may say "this cannot be a choice", never "we did not look".

    The distinction is the module's whole point one level up. A dial that is
    LIVE at its shipped value and moved by nobody is a chosen constant and
    belongs in `POST_BASELINE` with no entry -- which is `UNPROVENANCED`,
    the state that records an admitted gap. Filing it here would hide it.

    Enforced on the text, because the text is the claim: every reason names
    a gate, a partner dial, or the fact that nothing reads the dial.
    """
    # "never read" is deliberately NOT here: see the test above.
    grounds = ("inert", "unread")
    for dial, why in pv.OUT_OF_SCOPE.items():
        assert why and isinstance(why, str), dial
        assert any(g in why for g in grounds), (
            f"{dial}: an out-of-scope reason claims the dial cannot be a "
            f"choice, and this one does not say so: {why!r}")
        assert "nobody has looked" not in why, dial


def test_post_baseline_names_dials_the_difference_rule_cannot_reach():
    """The declared list is real, and it is not doing the other rule's job.

    Every name is a dial of the baseline, and none of them is one a
    required preset moves -- a dial that IS moved is in scope already, and
    listing it here as well would hide that somebody chose it. Both halves
    are asserted to FIRE below, because a membership rule nothing can
    violate is not a rule.
    """
    base = tradefloor.ModelParams.from_preset(pv.BASELINE).to_dict()
    moved = pv.moved_dials()
    for dial, why in pv.POST_BASELINE.items():
        assert dial in base, f"{dial} is not a dial of {pv.BASELINE}"
        assert dial not in moved, (
            f"{dial} is moved off the baseline by "
            f"{sorted(moved[dial])} and does not need declaring")
        assert why and isinstance(why, str), dial

    assert not pv.audit()["post_baseline"]

    # It fires on a name that is not a dial at all.
    a = _audit_with_post_baseline({"no_such_dial": "invented"})
    assert any("is not a dial" in f for f in a["post_baseline"]), a

    # And on a dial the difference rule already covers. `vix_return_gain`
    # is 25.0 at pt-v1 and 17.0 in both required presets.
    a = _audit_with_post_baseline({"vix_return_gain": "already a choice"})
    assert any("moves it off" in f for f in a["post_baseline"]), a


def test_a_dial_added_after_the_baseline_is_in_scope_at_what_it_ships():
    """The hole `POST_BASELINE` exists for, on the dial that opened it.

    `vix_variance_premium` is 0.252 in `pt-v1`, `pt-v16` and `pt-v18`
    alike, because the dial did not exist when `pt-v1` was frozen and the
    baseline therefore carries this era's own measurement. The
    difference-from-baseline rule cannot see such a value however chosen it
    is; the scope rule that replaces it records what each preset SHIPS, so
    the staleness binding still works.
    """
    dial = "vix_variance_premium"
    values = {p: tradefloor.ModelParams.from_preset(p).to_dict()[dial]
              for p in (pv.BASELINE,) + tuple(pv.REQUIRED_PRESETS)}
    assert len(set(values.values())) == 1, values

    assert dial not in pv.moved_dials()
    required = pv.required_dials()
    assert dial in required
    for preset in pv.REQUIRED_PRESETS:
        assert required[dial][preset] == values[preset]

    # And it still goes stale when the dial moves under the entry, which is
    # the whole point of recording the value rather than only the name.
    moved = dict(pv.DIAL_PROVENANCE[dial],
                 presets={p: v + 0.1 for p, v in required[dial].items()})
    a = _audit_with(dict(pv.DIAL_PROVENANCE, **{dial: moved}),
                    pv.UNPROVENANCED)
    assert any(dial in m and "ships" in m for m in a["mismatched"]), a


def test_the_measured_entry_carries_its_error_bar_and_names_its_estimator():
    """The one `measured` entry, held to the rule the kind exists for.

    A source and a date are not a measurement. This one carries the per-year
    IQR as its residual, and it names the estimator, because the same tape
    reads 1.076 pooled over one history and 1.252 per calendar year and the
    two have been read side by side before.
    """
    measured = pv.audit()["by_kind"]["measured"]
    assert measured, "the schema's measured branch is exercised by no entry"
    for dial in measured:
        entry = pv.DIAL_PROVENANCE[dial]
        assert not pv.validate_entry(dial, entry)
        assert any(entry.get(f) for f in pv.MEASURED_ERROR_FIELDS), dial
        assert entry.get("estimator"), (
            f"{dial} is measured and does not say by which estimator. Two "
            "correct readings of one quantity were read as one number for "
            "months, which is why this is asserted and not merely advised")
        shipped = pv.required_dials()[dial]
        assert entry["presets"] == shipped, (dial, entry["presets"], shipped)


def test_a_measured_value_without_an_error_bar_is_refused():
    """A figure shipped without an error bar is a chosen constant.

    The rule that stops `measured` from becoming the easy way to a green
    suite: a source and a date are not a measurement.
    """
    bare = {"kind": "measured", "presets": {"pt-v16": 1.0},
            "source": "the tape", "date": "2026-09-05", "script": "x.py"}
    problems = pv.validate_entry("some_dial", bare)
    assert any("error bar" in p for p in problems), problems

    for field in pv.MEASURED_ERROR_FIELDS:
        ok = dict(bare, **{field: 0.01})
        assert not pv.validate_entry("some_dial", ok), (field, ok)


def test_a_solve_that_did_not_converge_produces_no_entry():
    """`converged_offset`, as a rule.

    A field name asserted a condition nothing checked and its value was read
    as settled for hours. So a solve-backed entry carries the tolerance AND
    whether it was met, and an unmet one is refused rather than shipping the
    last iterate under a name that claims convergence.
    """
    base = {"kind": "derived", "presets": {"pt-v16": 1.0},
            "identity": "f(x) = 0", "terms": {"x": "the root"},
            "source": "rust/src/params.rs"}

    met = dict(base, solve={"tolerance": 1e-9, "met": True})
    assert not pv.validate_entry("some_dial", met), met

    unmet = dict(base, solve={"tolerance": 1e-9, "met": False})
    assert any("did not meet its tolerance" in p
               for p in pv.validate_entry("some_dial", unmet))

    silent = dict(base, solve={"tolerance": 1e-9})
    assert any("whether it was met" in p
               for p in pv.validate_entry("some_dial", silent))


def test_undetermined_is_a_passing_state_and_has_to_say_what_is_missing():
    """If claiming a derivation were the only way to green, people would.

    An admitted gap is worth more than a claimed derivation, so this state
    passes -- but it carries what would close it, or it is just an absence
    with a label.
    """
    good = {"kind": "undetermined", "presets": {"pt-v16": 1.0},
            "what_would_determine_it": "a tape measurement of the response"}
    assert not pv.validate_entry("some_dial", good)

    empty = {"kind": "undetermined", "presets": {"pt-v16": 1.0}}
    assert any("what_would_determine_it" in p
               for p in pv.validate_entry("some_dial", empty))

    # And it is genuinely in use, so the state is exercised by real data
    # rather than only by this test.
    assert pv.audit()["by_kind"]["undetermined"]


def test_a_kind_outside_the_three_is_refused():
    for kind in (None, "", "assumed", "obvious", 3):
        problems = pv.validate_entry("some_dial", {"kind": kind})
        assert any("is not one of" in p for p in problems), kind


def test_the_report_names_every_dial_it_counts():
    text = pv.report()
    a = pv.audit()
    assert str(len(a["required"])) in text
    assert pv.BASELINE in text
    for preset in pv.REQUIRED_PRESETS:
        assert preset in text


def _audit_with_post_baseline(mapping):
    """`audit()` over a substituted POST_BASELINE, for the fires-when-it-should
    half of the membership rule."""
    real = pv.POST_BASELINE
    pv.POST_BASELINE = mapping
    try:
        return pv.audit()
    finally:
        pv.POST_BASELINE = real


def _audit_with(table, unprovenanced):
    """`audit()` over a substituted table, for the fires-when-it-should tests."""
    real_table, real_list = pv.DIAL_PROVENANCE, pv.UNPROVENANCED
    pv.DIAL_PROVENANCE, pv.UNPROVENANCED = table, unprovenanced
    try:
        return pv.audit()
    finally:
        pv.DIAL_PROVENANCE, pv.UNPROVENANCED = real_table, real_list


def test_the_completeness_check_fires_when_a_dial_has_neither():
    """Would a broken instrument pass? Not this one.

    A dial dropped from both the table and the list must fail, or the
    completeness assertion is satisfied by its own construction.
    """
    dropped = pv.UNPROVENANCED[0]
    thinner = tuple(d for d in pv.UNPROVENANCED if d != dropped)
    a = _audit_with(pv.DIAL_PROVENANCE, thinner)
    assert a["missing"] == [dropped], a["missing"]

    with pytest.raises(tradefloor.ValidationError, match="not declared"):
        real = pv.UNPROVENANCED
        pv.UNPROVENANCED = thinner
        try:
            pv.check()
        finally:
            pv.UNPROVENANCED = real
