"""The certification scoring path, checked against a second implementation of it.

`measurement-integrity.md` section 3 asked for this file by name. Its
argument, in one line: the scoring path itself was measured and found sound
-- 456 per-row fields at zero relative difference on four real certification
panels -- and every one of the eight measurement defects that campaign found
lives in a SCRIPT that bypassed it, aggregating for itself or carrying its
own copy of a ruler. A note's number is only as good as the arithmetic under
it, and the only way to know that arithmetic has not drifted is to write it
twice.

So `_independent` below is a second implementation of the whole path -- the
aggregation kind per row, the model-side error and its degrees of freedom,
the Welch combination, the t term, the sum, the blind list, the band
verdicts and their denominators, the pooled tail rate -- with no import from
`tradefloor` anywhere inside it. It is `scratchpad/indscore.py` from the
2026-09-12 audit, carried here verbatim in substance so that the check runs
on every commit instead of once.

WHAT IT DOES AND DOES NOT PROVE. The tape side -- each row's centre, error
and degrees of freedom, and the band edges -- is an INPUT, read from
`loss.rule_table` and `envelope`. The two implementations therefore share
it, and a wrong tape number would pass here. What is proved is everything
downstream of the tape: given a table and a set of per-seed panels, there is
exactly one `S`, and it is not an accident of either implementation.

THE FIXTURES ARE REAL. `tests/fixtures/scoring/b4fix7-*.json` are the four
`b4fix7` certification panels, thirty seeds each, on both roster protocols
at both horizons, trimmed to the fields the rule reads. Their `expected.S`
values are the numbers `b4fix7-result.md` published -- 25.812560 and
38.721165 on the varying roster -- so this file also pins the published
record against the library, which is the other half of what section 3 asked
for: a score in a note and a score in the code that cannot drift apart
silently.

WHY THE HELD PANELS SCORE 17 OF 18 AND THAT IS CORRECT. The row out of band
is `index_tail_dn3_pct`, at 2.19 and 2.35 against a ceiling of 1.96. That is
the protocol point rather than a failure: a crash rate is a property of the
roster's concentration as much as of the model, `facts.LEVEL_PROTOCOL`
varies the roster with the seed for exactly that reason, and a held-roster
tail never carries a verdict. The fixture keeps it because a conformance
check that only saw agreeable panels would not be one.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import statistics

import pytest

from tradefloor import envelope, facts, loss

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "scoring"
PANELS = sorted(FIXTURES.glob("b4fix7-*.json"))

#: Everything below this line is the independent implementation. It may not
#: import from `tradefloor`, and the test at the bottom of the file checks
#: that it does not.

AGGREGATE = {"index_drift_pct": "mean",
             "fear_gauge_dn3": "pooled",
             "index_tail_dn3_pct": "pooled_rate"}
SHAPE = ("annualised_vol_pct", "excess_kurtosis", "return_acf1",
         "abs_return_acf1", "abs_return_acf5", "abs_return_acf20",
         "cross_sectional_corr", "volume_abs_return_corr", "leverage_effect",
         "volume_change_acf1", "corr_asymmetry", "corr_asymmetry_lagged",
         "sector_excess_corr", "corr_persistence_acf1")
LEVEL = ("index_drift_pct",)
CRISIS = ("fear_gauge_dn1", "fear_gauge_dn3", "index_tail_dn3_pct")
PERSIST = ("vix_ar1_debiased",)
ROWS = SHAPE + LEVEL + CRISIS + PERSIST
MEDIAN_SE_FACTOR = math.sqrt(math.pi / 2)
BOOTSTRAP_DRAWS, BOOTSTRAP_SEED = 2000, 20260905


def _graded(rows, key):
    kind = AGGREGATE.get(key, "median")
    if kind == "pooled":
        pooled = [x for r in rows for x in (r.get(key + "_samples") or ())]
        return statistics.median(pooled) if pooled else None
    if kind == "pooled_rate":
        stem = key[:-4]
        hits = [r.get(stem + "_hits") for r in rows]
        sess = [r.get(stem + "_sessions") for r in rows]
        if any(h is None for h in hits) or any(s is None for s in sess) \
                or not sum(sess):
            return None
        return 100.0 * sum(hits) / sum(sess)
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return statistics.fmean(vals) if kind == "mean" else statistics.median(vals)


def _model_se(rows, key):
    kind = AGGREGATE.get(key, "median")
    if kind == "pooled":
        per_seed = [tuple(r.get(key + "_samples") or ()) for r in rows]
        per_seed = [s for s in per_seed if s]
        if len(per_seed) < 2:
            return None, None
        rng = random.Random(BOOTSTRAP_SEED)
        n, meds = len(per_seed), []
        for _ in range(BOOTSTRAP_DRAWS):
            pooled = []
            for _ in range(n):
                pooled.extend(rng.choice(per_seed))
            meds.append(statistics.median(pooled))
        return statistics.stdev(meds), len(rows) - 1
    vals = [r[key] for r in rows if r.get(key) is not None]
    if len(vals) < 2:
        return None, None
    sd = statistics.stdev(vals)
    se = (MEDIAN_SE_FACTOR * sd / math.sqrt(len(vals)) if kind == "median"
          else sd / math.sqrt(len(vals)))
    return se, len(vals) - 1


def _term(z, df):
    return z * z if math.isinf(df) else (df + 1.0) * math.log1p(z * z / df)


def _independent(panels, rule, bands):
    """`S`, the per-row detail, the blind list, the band counts, the tail."""
    out = {"rows": {}, "blind": {}, "S": 0.0, "S_gauss": 0.0}
    for key in ROWS:
        T = _graded(panels, key)
        se_m, df_m = _model_se(panels, key)
        tape = rule.get(key)
        rec = {"T": T, "se_model": se_m, "df_model": df_m,
               "band": bands.get(key)}
        if T is None:
            out["blind"][key] = "not in panels"
            out["rows"][key] = rec
            continue
        if tape is None or tape.get("centre") is None \
                or tape.get("se") is None or tape.get("df") is None:
            out["blind"][key] = "tape side incomplete"
            out["rows"][key] = rec
            continue
        if se_m is None:
            out["blind"][key] = "fewer than two seeds"
            out["rows"][key] = rec
            continue
        se_r, df_r = float(tape["se"]), float(tape["df"])
        se = math.sqrt(se_r ** 2 + se_m ** 2)
        denom = se_r ** 4 / df_r + se_m ** 4 / df_m
        df = se ** 4 / denom if denom > 0 else math.inf
        z = (T - float(tape["centre"])) / se
        rec.update({"se": se, "z": z, "df": df, "term": _term(z, df)})
        out["S"] += rec["term"]
        out["S_gauss"] += z * z
        out["rows"][key] = rec
    verdict = {k: (b[0] <= out["rows"][k]["T"] <= b[1])
               for k, b in bands.items()
               if out["rows"].get(k, {}).get("T") is not None}
    out["in_band"] = sum(verdict.values())
    out["of"] = len(verdict)
    out["shape_in_band"] = sum(v for k, v in verdict.items() if k in SHAPE)
    out["shape_of"] = sum(1 for k in verdict if k in SHAPE)
    out["scored"] = sorted(k for k in out["rows"] if k not in out["blind"])
    hits = [r.get("index_tail_dn3_hits") for r in panels]
    sess = [r.get("index_tail_dn3_sessions") for r in panels]
    if all(h is not None for h in hits) and sum(sess):
        rates = [100.0 * h / n for h, n in zip(hits, sess)]
        lo, hi = bands["index_tail_dn3_pct"]
        rate = 100.0 * sum(hits) / sum(sess)
        se_m = statistics.stdev(rates) / math.sqrt(len(rates))
        out["tail"] = {"rate": rate, "hits": sum(hits), "sessions": sum(sess),
                       "se_m": se_m, "seeds": len(rates),
                       "margin": min(rate - lo, hi - rate),
                       "in_band": lo <= rate <= hi,
                       "at_the_edge": abs(min(rate - lo, hi - rate)) < se_m}
    return out


# -- the checks ---------------------------------------------------------


def _load(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc, doc["rows"], int(doc["days"])


def _tape(horizon):
    return {k: {"centre": v.get("centre"), "se": v.get("se"), "df": v.get("df")}
            for k, v in loss.rule_table(horizon, None).items()}


def _bands(horizon):
    return {k: list(loss._band_of(k, horizon)) for k in ROWS
            if loss._band_of(k, horizon) is not None}


def _close(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


@pytest.mark.parametrize("path", PANELS, ids=lambda p: p.stem)
def test_the_independent_scorer_agrees_with_loss_scoring_rule(path):
    """Every field of every row, and `S` itself, to 1e-9.

    The three rows that are not medians are why this exists.
    `index_tail_dn3_pct` is a pooled RATE, `fear_gauge_dn3` a pooled median
    over sessions and `index_drift_pct` a MEAN; medianing all three -- which
    is what `mrarm.py` and `armboth.py` did -- moved `S` by up to six points
    and understated the tail term threefold to ninefold. Nothing in this
    test would have let that through.
    """
    doc, panels, h = _load(path)
    ind = _independent(panels, _tape(h), _bands(h))
    lib = loss.scoring_rule(panels, horizon_days=h)

    assert sorted(lib["blind"]) == sorted(ind["blind"]), (
        "the two implementations disagree about which rows can be scored, "
        "which is a bigger disagreement than any number")
    assert lib["scored"] == ind["scored"]
    assert _close(lib["S"], ind["S"]), (path.stem, lib["S"], ind["S"])
    assert _close(lib["S_gauss"], ind["S_gauss"])

    for key, row in lib["rows"].items():
        mine = ind["rows"][key]
        for field in ("T", "se_model", "df_model", "se", "z", "df", "term"):
            assert _close(row.get(field), mine.get(field)), (
                path.stem, key, field, row.get(field), mine.get(field))


@pytest.mark.parametrize("path", PANELS, ids=lambda p: p.stem)
def test_the_independent_scorer_agrees_with_envelope_score(path):
    """The band verdicts and their denominators, row by row and in total."""
    doc, panels, h = _load(path)
    ind = _independent(panels, _tape(h), _bands(h))
    sc = envelope.score(facts.aggregate_panels(panels), horizon_days=h)

    assert sc["in_band"] == ind["in_band"]
    assert sc["of"] == ind["of"]
    assert sc["shape_in_band"] == ind["shape_in_band"]
    assert sc["shape_of"] == ind["shape_of"]
    for key, stat in sc["statistics"].items():
        mine = ind["rows"][key]
        assert _close(stat["measured"], mine["T"]), (path.stem, key)
        # A ROW WITH NO BAND ON THIS BASIS IS A ROW NEITHER SIDE GRADES.
        # Under the ruled basis `fear_gauge_dn1`, `fear_gauge_dn3` and, at
        # 504, `corr_persistence_acf1` have no adopted band; the scorer must
        # say so rather than report a verdict, and the two implementations
        # must agree about WHICH rows those are, which is the assertion here.
        if stat["band"] is None:
            assert mine["band"] is None, (path.stem, key, mine["band"])
            assert stat["in_band"] is None, (path.stem, key)
            continue
        assert mine["band"] is not None, (path.stem, key, stat["band"])
        assert list(stat["band"]) == mine["band"], (path.stem, key)
        assert stat["in_band"] == (mine["band"][0] <= mine["T"] <= mine["band"][1])


@pytest.mark.parametrize("path", PANELS, ids=lambda p: p.stem)
def test_the_independent_scorer_agrees_with_the_tail_block(path):
    """The pooled rate, its model error, the margin and the edge call.

    A rate pooled as a ratio of two sums and a median of per-seed rates are
    different numbers on a zero-inflated row -- on the held-252 fixture,
    2.1912 against 1.5936 -- and the second one measures incidence rather
    than rate. This asserts the first.
    """
    doc, panels, h = _load(path)
    ind = _independent(panels, _tape(h), _bands(h))
    block = envelope.tail_block(panels, horizon_days=h,
                               stationary_opening=False)
    assert (block is None) == ("tail" not in ind)
    if block is None:
        return
    mine = ind["tail"]
    assert block["hits"] == mine["hits"]
    assert block["sessions"] == mine["sessions"]
    assert block["seeds"] == mine["seeds"]
    assert _close(block["rate"], mine["rate"])
    assert _close(block["se_m"], mine["se_m"])
    assert _close(block["margin"], mine["margin"])
    assert block["in_band"] == mine["in_band"]
    assert block["at_the_edge"] == mine["at_the_edge"]


@pytest.mark.parametrize("path", PANELS, ids=lambda p: p.stem)
def test_the_fixture_still_scores_what_the_record_published(path):
    """`b4fix7-result.md`'s published `S`, against the library, today.

    This is the half of the check that a second implementation cannot do.
    Two implementations agreeing proves the arithmetic; only a stored number
    proves the TABLE has not moved under a published verdict. If this fails
    and the conformance tests above pass, the rule table changed -- which is
    a legitimate thing to do and an illegitimate thing to do quietly, so the
    fixture's `expected` block is updated in the same commit that moves the
    table, with the note that cites it.
    """
    doc, panels, h = _load(path)
    lib = loss.scoring_rule(panels, horizon_days=h)
    assert _close(lib["S"], doc["expected"]["S"]), (
        path.stem, lib["S"], doc["expected"]["S"])
    assert lib["rule_fingerprint"] == doc["expected"]["rule_fingerprint"], (
        "the rule table moved under a published score")
    assert lib["scored"] == doc["expected"]["scored"]
    assert sorted(lib["blind"]) == doc["expected"]["blind"]


def test_the_independent_scorer_imports_nothing_from_the_library():
    """The control on the control.

    A second implementation that reached into `tradefloor` for a constant
    would agree with the library for the wrong reason, and the failure mode
    is silent. The module-level import of `envelope`, `facts` and `loss` is
    for the CHECKS; the scorer itself is the block between `AGGREGATE` and
    `_load`, and this asserts that block is self-contained.
    """
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    start = src.index("AGGREGATE = {")
    end = src.index("# -- the checks")
    body = src[start:end]
    for forbidden in ("tradefloor", "loss.", "facts.", "envelope."):
        assert forbidden not in body, (
            f"the independent scorer refers to {forbidden!r}, so it is not "
            f"independent")


def test_the_scoring_fixtures_are_present():
    """A conformance suite that silently found no fixtures is not one."""
    assert len(PANELS) == 4, [p.name for p in PANELS]
