"""The SECOND gate: structural rows, signed against the real tape's centre.

`test_mechanism_gate.py` is the first gate and this is its sibling, kept
apart on Simon's ruling -- *"second gate beside it, don't mix structural
rows in"*. The two ask different questions of different nulls:

  mechanism   per-seed readings against the model WITHOUT the mechanism
              (`facts.NULLS`). "Is a model without it excluded."
  structure   per-seed readings against THE TAPE ITSELF
              (`facts.REAL_VIX_AR1`). "Is the model centred on the real
              reading at all."

WHY THE SECOND GATE HAS TO EXIST SEPARATELY. `vix_ar1_debiased` has no band
in this library -- `facts.RULED_UNREADABLE` holds it at both horizons -- so
`envelope.score` cannot grade it and it is in no `in_band` count. It is not a
mechanism row either, so `mechanism_verdict` refuses it by name. It has been
MEASURED on every seed of every run and reported ungraded, which is a row
that justified a decision and then existed in no scorer: the `vixlaw-ruling`
shape, one storey over from the one the mechanism bar closed this morning.

WHAT THE BAR IS AND IS NOT. Subset / non-regression, both panels: a preset
may not read REFUSED on a row its own committed record reads PASS. It is NOT
a threshold on the verdict. pt-v19 lays its record down REFUSED on both
panels and ships, because there is no prior record to regress from. The
tests below assert that pair of facts together, because either alone reads
as a different design.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from tradefloor import envelope
from tradefloor.facts import (
    REAL_VIX_AR1,
    REAL_VIX_AR1_WINDOWS,
    STRUCTURE,
    VIX_AR1_ROW,
    band_rule_tolerance,
    band_windows,
    real_centre,
    real_centre_se,
    sign_cut,
    structure_verdict,
)
from tradefloor._core import ValidationError

SEEDS = 30
RECORDS = (pathlib.Path(__file__).resolve().parent.parent
           / "python" / "tradefloor" / "presets")


def record(name: str) -> dict:
    return json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))


def panels(values, row: str = VIX_AR1_ROW) -> list[dict]:
    """Per-seed panels carrying one row, which is all this gate reads."""
    return [{row: v} for v in values]


def block(passed=(), refused=(), *, horizon_days=252, rows=None) -> dict:
    """A structural certificate with the two lists `structure_bar` reads."""
    return {
        "horizon_days": horizon_days,
        "seeds": SEEDS,
        "counts": {"structure_pass": len(passed),
                   "structure_of": len(passed) + len(refused)},
        "passed": sorted(passed),
        "refused": sorted(refused),
        "absent": [],
        "at_the_cut": [],
        "rows": rows or {},
    }


# --------------------------------------------------------------------------
# THE TEST ITSELF
# --------------------------------------------------------------------------

def test_the_sign_test_is_two_sided_and_the_cut_is_derived_not_chosen():
    """Nothing about the instrument is picked: the tolerance is `BAND_RULE`'s
    own false-alarm rate at the row's window count, and the cut falls out of
    the binomial. Above the cut and below its mirror are BOTH refusals,
    because a model above the tape and one below it are both off it."""
    assert band_windows(VIX_AR1_ROW, 252) == 9
    assert band_rule_tolerance(9) == 0.06486
    assert sign_cut(SEEDS, 0.06486) == 21

    high = structure_verdict([0.99] * 21 + [0.80] * 9, VIX_AR1_ROW)
    low = structure_verdict([0.80] * 21 + [0.99] * 9, VIX_AR1_ROW)
    straddle = structure_verdict([0.99] * 15 + [0.80] * 15, VIX_AR1_ROW)
    assert (high["verdict"], high["side"]) == ("refused", "above")
    assert (low["verdict"], low["side"]) == ("refused", "below")
    assert (straddle["verdict"], straddle["side"]) == ("pass", None)
    assert high["cut"] == low["cut"] == 21
    assert high["at_the_cut"] and low["at_the_cut"]


def test_the_reference_is_the_tape_and_it_is_the_debiased_median():
    """The point signed against is `REAL_VIX_AR1`, derived from the 35
    committed windows by the same two functions the model's row goes
    through -- not a constant typed anywhere."""
    assert len(REAL_VIX_AR1_WINDOWS[252]) == 35
    assert real_centre(VIX_AR1_ROW) == REAL_VIX_AR1[252]
    assert round(REAL_VIX_AR1[252], 6) == 0.929939
    # The tape's own error is carried beside the verdict and gates nothing.
    assert round(real_centre_se(VIX_AR1_ROW), 6) == 0.010420
    v = structure_verdict([0.9] * 30, VIX_AR1_ROW)
    assert v["real_centre"] == REAL_VIX_AR1[252] and v["tape_windows"] == 35


def test_a_row_that_is_not_structural_is_refused_by_name():
    """The roster is closed, and the refusal says which test the row's own
    kind gets instead."""
    with pytest.raises(ValidationError, match="not a structural row"):
        structure_verdict([0.1] * 30, "leverage_effect")
    with pytest.raises(ValidationError, match="at least two"):
        structure_verdict([0.9], VIX_AR1_ROW)
    assert STRUCTURE == (VIX_AR1_ROW,)


def test_certify_carries_the_structural_block_and_mixes_it_into_no_count():
    """`certify` computes both certificates off ONE set of per-seed panels,
    and the structural row enters none of the three counts. The ruling is
    that it sits BESIDE the panel."""
    rows = panels([0.99] * 30)
    for p, extra in zip(rows, range(30)):
        p.update({k: v for k, v in envelope.CERTIFIED.items()})
    result = envelope.certify(rows)
    assert result["structure"]["refused"] == [VIX_AR1_ROW]
    assert VIX_AR1_ROW not in result["counts"]
    assert VIX_AR1_ROW not in result["fidelity"]["statistics"]
    assert VIX_AR1_ROW not in result["mechanism"]
    # And it does NOT reach the mechanism block a record already carries:
    # folding it in there is the mixing the ruling forbids.
    assert "structure" not in envelope.certification_record(result)


def test_a_panel_that_stopped_measuring_the_row_is_absent_not_passed():
    """`refused` shrinks when a row vanishes, so an empty `refused` list
    would read as a clean certificate. The block names it absent."""
    b = envelope.certify_structure(panels([]) or [{"annualised_vol_pct": 1.0}] * 30)
    assert b["absent"] == [VIX_AR1_ROW]
    assert b["passed"] == [] and b["refused"] == []


# --------------------------------------------------------------------------
# THE BAR. Subset, both panels, non-regression.
# --------------------------------------------------------------------------

def test_the_bar_refuses_a_preset_whose_structural_row_went_PASS_to_REFUSED():
    """THE TEST THAT PROVES THE CHANGE DOES ANYTHING AT ALL.

    pt-v18's committed certificate PASSES `vix_ar1_debiased` on the 252
    panel; pt-v19's REFUSES it. Read one against the other and the bar
    refuses, names the row, names the panel, and quotes the `k` and the cut
    that decided it. These are the two shipped presets' real readings, not a
    construction: the regression this gate exists for has already happened
    once between two adjacent defaults.
    """
    was = record("pt-v18")["structure_252"]
    now = record("pt-v19")["structure_252"]
    assert was["passed"] == [VIX_AR1_ROW]
    assert now["refused"] == [VIX_AR1_ROW]

    verdict = envelope.structure_bar(now, was, label="structure_252")
    assert verdict["passed"] is False
    assert verdict["lost"] == [VIX_AR1_ROW]
    assert VIX_AR1_ROW in verdict["reason"]
    assert "structure_252" in verdict["reason"]
    # k 25 since the 2026-09-20 recomposition (k 21, at the cut, before it).
    assert "k 25 of 30" in verdict["reason"]
    # And each against itself is the pass, so the refusal above is the
    # regression and not the comparison.
    assert envelope.structure_bar(was, was)["passed"] is True
    assert envelope.structure_bar(now, now)["passed"] is True


def test_a_row_that_leaves_the_certificate_is_a_loss_and_not_a_shorter_list():
    """The subset is taken on the PASSING side, and this is why.

    Delete the row from the certificate and `refused` gets SMALLER, so
    `refused <= recorded_refused` passes on exactly the change that removed
    the row from the gate.
    """
    was = record("pt-v18")["structure_252"]
    gone = block(passed=(), refused=())
    assert set(gone["refused"]) <= set(was["refused"])   # the naive test passes
    verdict = envelope.structure_bar(gone, was)
    assert verdict["passed"] is False
    assert verdict["absent"] == [VIX_AR1_ROW]
    assert "has left the gate rather than failed it" in verdict["reason"]


def test_passing_more_than_the_record_clears_the_bar_and_is_named():
    """The direction that is NOT a regression. A model that repairs the row
    locks the PASS in for every model after it, so the gain is named rather
    than silent."""
    was = record("pt-v19")["structure_252"]
    better = block(passed=[VIX_AR1_ROW])
    verdict = envelope.structure_bar(better, was)
    assert verdict["passed"] is True
    assert verdict["gained"] == [VIX_AR1_ROW]
    assert VIX_AR1_ROW in verdict["reason"]


def test_a_preset_with_no_structural_record_is_refused_at_library_level():
    """Subset against nothing is an absence, and an absence is not a result.
    The one exception is the tool that WRITES the first record, and it is
    written at that call site rather than here."""
    now = record("pt-v19")["structure_252"]
    verdict = envelope.structure_bar(now, None)
    assert verdict["passed"] is False
    assert "no committed structural certificate" in verdict["reason"]
    assert "record.py --structure-rows" in verdict["reason"]
    stopped = envelope.structure_bar(None, now)
    assert stopped["passed"] is False
    assert "stopped being run" in stopped["reason"]


def test_the_bar_refuses_a_certificate_read_at_another_horizon():
    """A structural row's tape centre is per horizon -- 0.929939 at 252 and
    0.959348 at 504 -- so a subset taken across two of them signs against
    two different points."""
    now = record("pt-v19")["structure_252"]
    assert REAL_VIX_AR1[252] != REAL_VIX_AR1[504]
    verdict = envelope.structure_bar(dict(now, horizon_days=504), now,
                                     horizon_days=252)
    assert verdict["passed"] is False
    assert "504" in verdict["reason"]


def test_the_bar_reads_both_panels_and_either_one_failing_is_a_failure():
    """BOTH, never one. The shipped default reads k = 21 of 30 at 252 and
    k = 28 of 30 on the held-out seeds -- one exactly at the cut and one
    nowhere near it, on the same preset and the same build. A bar reading
    either panel alone would spend the protection the second is there for.
    """
    assert envelope.STRUCTURE_BAR_PANELS == ("structure_252",
                                             "structure_heldout_seeds")
    v19 = record("pt-v19")
    v18 = record("pt-v18")
    verdict = envelope.structure_record_bar(v19, v18)
    assert verdict["passed"] is False
    assert verdict["panels"]["structure_252"]["passed"] is False
    assert verdict["panels"]["structure_heldout_seeds"]["passed"] is False
    for field in envelope.STRUCTURE_BAR_PANELS:
        assert field in verdict["reason"]

    # One panel only is refused ON THAT PANEL, by name, and the other is
    # still read -- so the message says which of the two is missing.
    half = {k: v for k, v in v19.items() if k != "structure_heldout_seeds"}
    one = envelope.structure_record_bar(v19, half)
    assert one["passed"] is False
    assert one["panels"]["structure_252"]["passed"] is True
    assert "structure_heldout_seeds: no committed structural certificate" \
        in one["reason"]


def test_the_record_tool_treats_a_first_lay_down_as_the_one_exception():
    """The exception lives at the call site that needs it and nowhere else.
    The library rule is unchanged, which is what keeps the exception from
    being inherited by a reader of the rule."""
    import sys

    sys.path.insert(0, str(RECORDS.parent.parent.parent / "tools" / "presets"))
    record_tool = pytest.importorskip("record")

    v19 = record("pt-v19")
    first = record_tool.structure_bar(v19, None)
    assert first["passed"] is True and first["first"] is True
    assert "LAYS ONE DOWN" in first["reason"]
    # A record that carries no structural block at all is the same case:
    # that is what every preset looked like before this gate landed.
    bare = {k: v for k, v in v19.items()
            if k not in envelope.STRUCTURE_BAR_PANELS}
    assert record_tool.structure_bar(v19, bare)["first"] is True
    # And against a committed record it is the library rule, unmodified.
    assert record_tool.structure_bar(v19, v19)["passed"] is True
    regressed = record_tool.structure_bar(v19, record("pt-v18"))
    assert regressed["passed"] is False and regressed["first"] is False
    assert regressed["lost"] == [VIX_AR1_ROW]


# --------------------------------------------------------------------------
# THE RECORDS, AND THE SHIP BAR
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(RECORDS.glob("*.json")),
                         ids=lambda p: p.stem)
def test_every_committed_record_carries_a_structural_certificate(path):
    """All eighteen, on both panels. A preset with no block is refused by
    the bar, so an unlaid record is not a quiet pass -- but it would be a
    release nobody could ship, which is worse news arriving later."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    for field in envelope.STRUCTURE_BAR_PANELS:
        b = rec[field]
        assert b["horizon_days"] == 252 and b["seeds"] == SEEDS
        assert sorted(b["passed"] + b["refused"]) == [VIX_AR1_ROW]
        assert b["absent"] == []
        assert b["rows"][VIX_AR1_ROW]["cut"] == 21
        assert b["measured"]["commit"]
    # Every record is a subset of itself, which is what makes the gate free
    # to land: nothing that ships today newly fails.
    assert envelope.structure_record_bar(rec, rec)["passed"] is True


@pytest.mark.ship_bar
def test_the_shipped_preset_holds_its_structural_certificate_on_both_panels():
    """THE SECOND GATE, IN THE SHIP BAR, BY NAME.

    This one is RED-BY-READING and green as a bar, and the two halves have
    to be read together. The shipped preset REFUSES `vix_ar1_debiased` on
    both panels -- that is on its record, by name, with its `k` and its cut
    -- and it still clears the bar, because the bar is non-regression and
    pt-v19 has no earlier structural record to regress from.

    That is deliberate and it is Simon's "you can't fix what you can't
    see": the row is visible on every record and gated against LOSS, not
    blocked today. The first model that repairs it lays down a PASS, and
    from that record on no model may lose it again -- at which point this
    test starts refusing releases.
    """
    rec = record(envelope.PRESET)
    verdict = envelope.structure_record_bar(rec, rec)
    assert verdict["passed"] is True, verdict["reason"]

    # The reading the release carries, asserted so it cannot change in
    # silence: REFUSED on both panels, above the tape. Re-pinned at the
    # 2026-09-20 recomposition: k 25 and 22 where the 2026-09-14 vector read
    # 21 (at the cut) and 28. Neither panel sits at the cut now.
    for field, k in (("structure_252", 25), ("structure_heldout_seeds", 22)):
        row = rec[field]["rows"][VIX_AR1_ROW]
        assert rec[field]["refused"] == [VIX_AR1_ROW]
        assert (row["k"], row["cut"], row["side"]) == (k, 21, "above")
    assert rec["structure_252"]["at_the_cut"] == []
    assert rec["structure_heldout_seeds"]["at_the_cut"] == []

    # The published table agrees with the record it is written from.
    assert (round(rec["structure_252"]["rows"][VIX_AR1_ROW]["median"], 6)
            == envelope.CERTIFIED_STRUCTURE[VIX_AR1_ROW])

    # And the line a reader sees, which is the other half of "by name".
    line = envelope.structure_bar_line(verdict)
    assert "structure bar" in line and "PASS" in line
