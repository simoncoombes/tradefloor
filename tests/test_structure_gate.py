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
a threshold on the verdict. pt-v19 laid its record down REFUSED on both
panels and shipped, because there was no prior record to regress from. The
tests below assert that pair of facts together, because either alone reads
as a different design.

WHAT IT GATES SINCE 2026-09-23. The owner's ruling (design repo
`programme/longrun/CRITERIA.md`, ledger `ruling-the-pass-bar-is-what-a-user-
would-notice-programme-longrun-criteria`) makes the pass bar the fifteen
long-run criteria and every ruled band: "the certification's VIX persistence
rows ... are reported and investigated but do not gate". The bar's logic is
unchanged and still tested to fire -- on a FIXED HISTORICAL RECORD, pt-v18's
and pt-v19's fourth composition's certificates as they stood that day
(`tests/fixtures/records/certificates-2026-09-23.json`), so the tests do not
move with whatever the shipped preset measures -- and the shipped preset's
own test reads its certificate as a report beside the verdict that gates.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import statistics

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


HISTORICAL = (pathlib.Path(__file__).resolve().parent / "fixtures"
              / "records" / "certificates-2026-09-23.json")

#: The long-run criteria added on 2026-09-24, after the fifteen the shipped
#: preset was adopted under: no price-only edge, on the tape and through
#: `tf.evaluate` on the published suite.
PRICE_ONLY_EDGE = ("C4a", "C4b")

#: The fifteen long-run criteria adopted on 2026-09-23, which pt-v19 was
#: adopted under (design repo `programme/longrun/CRITERIA.md`).
ADOPTED = ("A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8",
           "C1", "C2", "C3", "D1")

#: The eleven more rows pt-v20's long-run verdict grades (box ptv20g3,
#: commit 6059bfd; the record's `criteria` field names design repo
#: `programme/ptv20-registration.md`). pt-v20's record carries all
#: twenty-eight; pt-v19's carries the other seventeen.
REGISTERED_PT_V20 = ("B9", "C5", "C6", "C7", "C8", "C9", "R1", "R2", "R3",
                     "R4", "E1")


def record(name: str) -> dict:
    """A COMMITTED record: what ships, and moves with every re-measurement."""
    return json.loads((RECORDS / f"{name}.json").read_text(encoding="utf-8"))


def historical(name: str) -> dict:
    """A FROZEN record ("pt-v18" or "pt-v19-fourth"), for the bar's logic."""
    return json.loads(HISTORICAL.read_text(encoding="utf-8"))["records"][name]


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

    pt-v18's certificate PASSES `vix_ar1_debiased` on the held-out seeds;
    pt-v19's fourth composition REFUSED it there. Read one against the other
    and the bar refuses, names the row, names the panel, and quotes the `k`
    and the cut that decided it. These are two shipped defaults' real
    readings, not a construction: the regression this gate exists for
    happened once between two adjacent defaults. (Until the 2026-09-21
    composition the 252 panel carried this test. The fifth composition of
    2026-09-23 passes on both panels, so the pair is read from the frozen
    record rather than the live one.)
    """
    was = historical("pt-v18")["structure_heldout_seeds"]
    now = historical("pt-v19-fourth")["structure_heldout_seeds"]
    assert was["passed"] == [VIX_AR1_ROW]
    assert now["refused"] == [VIX_AR1_ROW]

    verdict = envelope.structure_bar(now, was, label="structure_heldout_seeds")
    assert verdict["passed"] is False
    assert verdict["lost"] == [VIX_AR1_ROW]
    assert VIX_AR1_ROW in verdict["reason"]
    assert "structure_heldout_seeds" in verdict["reason"]
    # k 23 since the third composition of 2026-09-21 (21, at the cut, on
    # the second; 25 and 22 on the two panels of the 2026-09-20 record;
    # 21 and 28 before that).
    assert "k 23 of 30" in verdict["reason"]
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
    was = historical("pt-v18")["structure_252"]
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
    # Refused above the cut, on the fourth composition's frozen record.
    was = historical("pt-v19-fourth")["structure_heldout_seeds"]
    assert was["refused"] == [VIX_AR1_ROW]
    better = block(passed=[VIX_AR1_ROW])
    verdict = envelope.structure_bar(better, was)
    assert verdict["passed"] is True
    assert verdict["gained"] == [VIX_AR1_ROW]
    assert VIX_AR1_ROW in verdict["reason"]


def test_a_preset_with_no_structural_record_is_refused_at_library_level():
    """Subset against nothing is an absence, and an absence is not a result.
    The one exception is the tool that WRITES the first record, and it is
    written at that call site rather than here."""
    now = historical("pt-v19-fourth")["structure_252"]
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
    now = historical("pt-v19-fourth")["structure_252"]
    assert REAL_VIX_AR1[252] != REAL_VIX_AR1[504]
    verdict = envelope.structure_bar(dict(now, horizon_days=504), now,
                                     horizon_days=252)
    assert verdict["passed"] is False
    assert "504" in verdict["reason"]


def test_the_bar_reads_both_panels_and_either_one_failing_is_a_failure():
    """BOTH, never one. pt-v19's fourth composition read k = 19 of 30 at 252
    and k = 23 on the held-out seeds -- one under the cut and one over it,
    on the same preset and the same build. Against pt-v18, which passes
    both, the 252 panel clears and the held-out one does not, and the bar
    fails. A bar reading either panel alone would spend the protection the
    second is there for. (Both read from the frozen record.)
    """
    assert envelope.STRUCTURE_BAR_PANELS == ("structure_252",
                                             "structure_heldout_seeds")
    v19 = historical("pt-v19-fourth")
    v18 = historical("pt-v18")
    verdict = envelope.structure_record_bar(v19, v18)
    assert verdict["passed"] is False
    assert verdict["panels"]["structure_252"]["passed"] is True
    assert verdict["panels"]["structure_heldout_seeds"]["passed"] is False
    assert "structure_heldout_seeds" in verdict["reason"]

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

    v19 = historical("pt-v19-fourth")
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
    regressed = record_tool.structure_bar(v19, historical("pt-v18"))
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
def test_the_shipped_record_reports_its_structural_certificate_beside_the_bar_that_gates():
    """THE SECOND GATE'S CERTIFICATE, REPORTED BESIDE THE SHIP BAR, BY NAME.

    This test used to hold the shipped preset to non-regression on its own
    structural certificate. The owner's ruling of 2026-09-23 (design repo
    `programme/longrun/CRITERIA.md`, ledger `ruling-the-pass-bar-is-what-a-
    user-would-notice-programme-longrun-criteria`) moved that: the pass bar
    is the fifteen long-run criteria plus every ruled band, and "the
    certification's VIX persistence rows ... are reported and investigated
    but do not gate".

    So what is asserted is what the ruling asks of the record: it CARRIES
    the row on both panels and the rise, readable by the bar and rendered in
    the line a reader sees; the reading is pinned so it cannot change in
    silence; the published table agrees with it; and beside it sit the two
    things that do gate -- the long-run criteria, which pass, and every
    ruled band in on all four protocols. The record is pt-v20's since 0.8.5.
    """
    rec = record(envelope.PRESET)
    for field in envelope.STRUCTURE_BAR_PANELS:
        b = rec[field]
        assert b["horizon_days"] == 252 and b["seeds"] == SEEDS, field
        assert sorted(b["passed"] + b["refused"]) == [VIX_AR1_ROW], field
        assert b["absent"] == [] and b["measured"]["commit"], field
    verdict = envelope.structure_record_bar(rec, rec)
    assert verdict["passed"] is True, verdict["reason"]
    line = envelope.structure_bar_line(verdict)
    assert "structure bar" in line and "PASS" in line

    # The reading the release carries, REPORTED, and asserted so it cannot
    # change in silence. RE-PINNED at 0.8.5 on pt-v20 (box ptv20g3): PASS on
    # both panels, k 15 at 252 and k 15 held out (cut 21), median 0.932337
    # against the tape's 0.929939. pt-v19's fifth composition of 2026-09-23
    # read PASS on both at k 18 and k 17, median 0.933726. The
    # fourth composition read PASS at 19 and REFUSED above at 23 held out;
    # the second 20 and 21 (at the cut), the 2026-09-20 record 25 and 22,
    # the 2026-09-14 one 21 and 28. The fifth composition's 504-session
    # certification row is refused below (k 10), which the record box
    # reports beside the verdict and which the ruling does not gate.
    row = rec["structure_252"]["rows"][VIX_AR1_ROW]
    assert rec["structure_252"]["passed"] == [VIX_AR1_ROW]
    assert (row["k"], row["cut"], row["side"]) == (15, 21, None)  # pt-v19: 18
    assert rec["structure_252"]["at_the_cut"] == []
    row = rec["structure_heldout_seeds"]["rows"][VIX_AR1_ROW]
    assert rec["structure_heldout_seeds"]["passed"] == [VIX_AR1_ROW]
    assert (row["k"], row["cut"], row["side"]) == (15, 21, None)  # pt-v19: 17
    assert rec["structure_heldout_seeds"]["at_the_cut"] == []

    # The published table agrees with the record it is written from.
    assert (round(rec["structure_252"]["rows"][VIX_AR1_ROW]["median"], 6)
            == envelope.CERTIFIED_STRUCTURE[VIX_AR1_ROW])

    # WHAT GATES, beside it: the long-run criteria, all passed. pt-v20's
    # record carries twenty-eight: the fifteen pt-v19 was adopted under on
    # 2026-09-23, C4a and C4b (added 2026-09-24, design repo
    # `programme/longrun/CRITERIA.md`, section C4), and the eleven more
    # graded for pt-v20 (`REGISTERED_PT_V20`). RE-PINNED at 0.8.5, when pt-v20
    # became the default. pt-v19's record reads 15 of 17 with the verdict
    # "fail": it passes the fifteen and fails C4a and C4b, the tape's
    # 65-minute reversal and two price-only rules on the published suite,
    # which the brief that added them gave to pt-v20 to pass.
    lr = rec["long_run"]
    ids = [r["id"] for r in lr["rows"]]
    assert sorted(ids) == sorted(ADOPTED + PRICE_ONLY_EDGE + REGISTERED_PT_V20)
    assert all(r["pass"] for r in lr["rows"]), [
        r["id"] for r in lr["rows"] if not r["pass"]]
    assert lr["of"] == 28 and lr["passed"] == 28 and lr["verdict"] == "pass"
    # ... and every ruled band in, on all four protocols.
    assert rec["misses"] == {p: [] for p in rec["misses"]}
    assert set(rec["misses"]) == {"252", "504", "heldout_universe",
                                  "heldout_seeds"}


# -- the rise, since 2026-09-21 ----------------------------------------------
#
# Simon's ruling: the second gate grades the RISE in `vix_ar1_debiased`
# from the one-year window to the two-year one, on the same seeds, against
# the tape's +0.029, as one verdict. Graded a horizon at a time the row read
# REFUSED-ABOVE at 252 and REFUSED-BELOW at 504 on one model, which was two
# verdicts on one fact: the tape's persistence has a slow component and a
# single-pole process does not rise the way it does.

def _rises(rng, delta, n=30, sd=0.03, jitter=0.005):
    a = [0.95 + rng.gauss(0, sd) for _ in range(n)]
    return a, [x + delta + rng.gauss(0, jitter) for x in a]


def test_the_tape_rise_is_the_paired_median_over_the_two_year_blocks():
    """Same estimator both sides: each two-year block's debiased reading
    minus its own first year's, the median over the 17 blocks. The
    difference of the two independent centres is about three hundredths
    and is NOT the target, because the model's statistic is paired."""
    from tradefloor.facts import (REAL_VIX_AR1, REAL_VIX_AR1_RISE, REAL_VIX_AR1_PAIRED_RISES,
                                  REAL_VIX_AR1_WINDOWS, debias_ar1, median_se, real_rise_se)
    assert len(REAL_VIX_AR1_PAIRED_RISES) == len(REAL_VIX_AR1_WINDOWS[504]) == 17
    for i, rise in enumerate(REAL_VIX_AR1_PAIRED_RISES):
        assert rise == pytest.approx(debias_ar1(REAL_VIX_AR1_WINDOWS[504][i], 504)
                                     - debias_ar1(REAL_VIX_AR1_WINDOWS[252][2 * i], 252))
    assert REAL_VIX_AR1_RISE == pytest.approx(statistics.median(REAL_VIX_AR1_PAIRED_RISES))
    assert REAL_VIX_AR1_RISE == pytest.approx(0.0120, abs=0.0005)
    assert REAL_VIX_AR1_RISE < 0.5 * (REAL_VIX_AR1[504] - REAL_VIX_AR1[252])
    assert real_rise_se() == pytest.approx(median_se(REAL_VIX_AR1_PAIRED_RISES))


def test_the_rise_verdict_is_where_the_tape_sits_against_the_seed_interval():
    import random
    from tradefloor.facts import structure_rise_verdict, REAL_VIX_AR1_RISE
    rng = random.Random(1)
    for delta, want in ((REAL_VIX_AR1_RISE, "matches"), (0.007, "below"), (0.08, "above")):
        a, b = _rises(rng, delta)
        v = structure_rise_verdict(a, b)
        assert v["verdict"] == want, (delta, v)
        assert v["ci90"][0] <= v["median_rise"] <= v["ci90"][1]
        assert v["tape_rise"] == REAL_VIX_AR1_RISE
        assert v["n"] == 30 and v["horizons"] == [252, 504]
    # the same rows twice give the same interval: the bootstrap is fixed-seed
    a, b = _rises(random.Random(2), 0.02)
    assert structure_rise_verdict(a, b) == structure_rise_verdict(a, b)
    with pytest.raises(ValidationError):
        structure_rise_verdict(a, b[:-1])


def test_the_rise_certificate_is_absent_not_passed_when_a_horizon_lacks_the_row():
    import random
    rng = random.Random(3)
    a, b = _rises(rng, 0.004)   # under the tape's paired rise, so the row reads below
    p252 = [{VIX_AR1_ROW: x} for x in a]
    block = envelope.certify_structure_rise(p252, [{VIX_AR1_ROW: y} for y in b])
    assert block["below"] == [VIX_AR1_ROW] and block["absent"] == []
    gone = envelope.certify_structure_rise(p252, [{"other": y} for y in b])
    assert gone["absent"] == [VIX_AR1_ROW]
    verdict = envelope.structure_rise_bar(gone, block)
    assert verdict["passed"] is False and verdict["absent"] == [VIX_AR1_ROW]


def test_the_rise_bar_refuses_a_row_that_stops_matching_the_tape():
    import random
    from tradefloor.facts import REAL_VIX_AR1_RISE
    rng = random.Random(4)
    mk = lambda d: envelope.certify_structure_rise(
        *[[{VIX_AR1_ROW: x} for x in xs] for xs in _rises(rng, d)])
    matched, low = mk(REAL_VIX_AR1_RISE), mk(0.007)
    assert envelope.structure_rise_bar(matched, matched)["passed"] is True
    lost = envelope.structure_rise_bar(low, matched)
    assert lost["passed"] is False and lost["lost"] == [VIX_AR1_ROW]
    assert "MATCHES the tape's rise" in lost["reason"]
    # below on both is the reading and not a regression; matching later is gained
    assert envelope.structure_rise_bar(low, low)["passed"] is True
    assert envelope.structure_rise_bar(matched, low)["gained"] == [VIX_AR1_ROW]
    assert envelope.structure_rise_bar(low, None)["passed"] is False
    assert envelope.structure_rise_bar(None, low)["passed"] is False


def test_the_record_bar_reads_the_rise_when_the_record_carries_it():
    """A record written before the block existed lays down no rise to
    regress from, and the record bar says so rather than refusing."""
    rec = record(envelope.PRESET)
    verdict = envelope.structure_record_bar(rec, rec)
    assert verdict["passed"] is True
    if rec.get(envelope.STRUCTURE_RISE_FIELD):
        assert verdict["panels"][envelope.STRUCTURE_RISE_FIELD]["passed"] is True
        row = rec[envelope.STRUCTURE_RISE_FIELD]["rows"][VIX_AR1_ROW]
        # The shipped default's PAIRED rise is the tape's since the
        # 2026-09-21 composition: +0.0116 [+0.0007, +0.0270] against the
        # tape's paired +0.0120, on the record box; +0.0071 on the fourth
        # composition and +0.0143 [+0.0064, +0.0280] on the fifth (2026-09-23),
        # reported and not gated by the owner's ruling of that day. pt-v20,
        # the default since 0.8.5, reads +0.0156 [+0.0041, +0.0270] (box
        # ptv20g3). (The
        # 2026-09-20 record read -0.0024 [-0.0058, +0.0065]: one pole. The
        # regime level on the VIX law is what makes calm years and a rise.)
        assert row["verdict"] == "matches", row
        assert row["ci90"][0] <= row["tape_rise"] <= row["ci90"][1]
        assert (round(row["median_rise"], 6)
                == envelope.CERTIFIED_STRUCTURE_RISE[VIX_AR1_ROW])
    else:
        pytest.skip("the shipped record carries no rise block yet; the "
                    "certification box lays it down")
