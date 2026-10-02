"""The Python-surface findings of the third persona round on 0.8.5.

Each test failed on the release candidate (35cbc135) and names the
reviewer's repro it stands for. None of them needs a network, an API key or
a framework installed.
"""

from __future__ import annotations

import math
import re
import statistics
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor import envelope as env
from tradefloor.baselines import BuyAndHold, reference_agents
from tradefloor.integrations.callable import callable_agent

UNIVERSE = tf.Universe.random(6, seed=1)


class Flat:
    """Never trades."""

    def act(self, obs):
        return {}


class Broken:
    """Raises on every step."""

    def act(self, obs):
        raise KeyError("my bug")


def _hallucinating(payload):
    """One action on a ticker the roster does not have, one good buy."""
    real = payload["assets"][0]["symbol"]
    return {"actions": [{"symbol": "NVDA", "side": "BUY", "quantity": 10},
                        {"symbol": real, "side": "BUY", "quantity": 100}]}


# -- an adapter's refusal is a rejected action, not a raise ------------------


def test_an_adapter_refusal_is_counted_in_rejected():
    """Priya's n1: a hallucinated ticker went to `errors` and not to
    `rejected`, so `rank` read the step as one where act() raised."""
    card = tf.evaluate({"v2": callable_agent(_hallucinating, every=6)},
                       seed=100, universe=UNIVERSE, days=2)["v2"]
    refused = [e for e in card.errors if "refused action" in e]
    assert len(refused) == 2, card.errors
    assert card.rejected == len(card.errors) == 2
    assert card.trades == 2


def test_rank_labels_a_refusal_as_a_refusal_and_not_as_raised():
    ranking = tf.rank(
        lambda: {"v2": callable_agent(_hallucinating, every=6),
                 "buy_and_hold": BuyAndHold()},
        seeds=[100], universe=UNIVERSE, days=2)
    record = ranking.records["v2"]
    assert record.errors == [0]
    assert record.rejected == [2]
    report = ranking.report()
    assert "RAISED" not in report, report
    assert "act() raised" not in report, report
    line = next(x for x in report.splitlines() if "REFUSED v2" in x)
    assert "2 refused" in line and "seed 100, step 0: refused action" in line


def test_a_refused_line_ends_on_one_full_stop():
    """The refusal's own text ends in a full stop, and the report put a
    second one after it: "AAE, AAF.. Its adapter"."""
    ranking = tf.rank(
        lambda: {"v2": callable_agent(_hallucinating, every=6),
                 "buy_and_hold": BuyAndHold()},
        seeds=[100], universe=UNIVERSE, days=2)
    line = next(x for x in ranking.report().splitlines() if "REFUSED v2" in x)
    assert ".." not in line, line
    assert "AAF. Its adapter" in line, line


class Lister:
    """Answers with a list of pairs, which is not an order mapping."""

    def act(self, obs):
        return [("AAA", 100)]


def test_a_list_answer_is_reported_as_unusable_and_not_as_raised():
    """A persona: an agent returning a list read 'RAISED lister: ... where
    act() raised', with no first line, though nothing raised."""
    with pytest.warns(UserWarning, match="failed on all"):
        ranking = tf.rank(lambda: {"lister": Lister(),
                                   "buy_and_hold": BuyAndHold()},
                          seeds=[1, 2], universe=UNIVERSE, days=1)
    record = ranking.records["lister"]
    assert record.errors == [0, 0]
    assert record.unusable == [6, 6]
    assert record.first_unusable.startswith(
        "seed 1, step 0: act() must return a mapping"), record.first_unusable
    assert record.as_dict()["unusable"] == [6, 6]
    report = ranking.report()
    assert "RAISED" not in report and "act() raised" not in report, report
    assert "[unusable answers: see below]" in report, report
    line = next(x for x in report.splitlines() if "UNUSABLE lister" in x)
    assert line.startswith("  UNUSABLE lister: 12 unusable answers on 2 of 2 "
                           "seeds, the first on seed 1, step 0: "), line
    assert "It returned a list" in line and ".." not in line, line


def test_a_real_exception_is_still_reported_as_raised():
    ranking = tf.rank(lambda: {"broken": Broken(),
                               "buy_and_hold": BuyAndHold()},
                      seeds=[1], universe=UNIVERSE, days=1)
    report = ranking.report()
    assert "RAISED broken: 6 errors" in report, report
    assert "KeyError" in report
    assert "REFUSED" not in report


# -- the benchmark is named as the one used ----------------------------------


def test_the_report_names_the_benchmark_it_used():
    """Elena's score.py: benchmark='flat' printed 'vs buy-and-hold'."""
    ranking = tf.rank(lambda: {"flat": Flat(), "hold": BuyAndHold()},
                      seeds=range(2), universe=UNIVERSE, days=2,
                      benchmark="flat")
    report = ranking.report()
    assert ranking.capture_withheld is not None
    assert "buy-and-hold" not in report, report
    assert re.search(r"hold\s+vs flat ", report), report


def test_an_agent_that_raised_on_every_step_reads_no_score_not_ahead():
    """Elena's raising.py: a broken agent printed 'ahead 2/3'."""
    ranking = tf.rank(lambda: {"broken": Broken(),
                               "buy_and_hold": BuyAndHold()},
                      seeds=range(3), universe=UNIVERSE, days=2)
    row = next(x for x in ranking.report().splitlines()
               if x.strip().startswith("broken"))
    assert "ahead" not in row, row
    assert "failed on every step of 3 of 3 seeds" in row, row
    assert ranking.records["broken"].mean_excess_pnl is None
    assert ranking.records["broken"].seeds_ahead is None


# -- the oracle is named when pt-v20 leaves it out ---------------------------


def test_rank_on_pt_v20_says_the_oracle_ran_and_was_left_out():
    """Jordan: the oracle dropped out of the table without a word."""
    ranking = tf.rank(lambda: reference_agents(seed=3), seeds=range(2),
                      universe=UNIVERSE, days=2)
    assert "oracle" not in ranking.records
    report = ranking.report()
    line = next(x for x in report.splitlines() if "'oracle' ran" in x)
    excess = statistics.fmean(
        o - b for o, b in zip(ranking.reference_pnls,
                              ranking.records["buy_and_hold"].pnls))
    assert f"{excess:+,.0f}" in line, (line, excess)


# -- Sharpe, volatility, time in market and gross exposure -------------------


def _cards():
    return tf.evaluate({"hold": BuyAndHold(), "flat": Flat()}, seed=3,
                       universe=UNIVERSE, days=5)


def test_the_scorecard_reports_sharpe_volatility_and_exposure():
    """Elena and Marcus: no Sharpe, volatility or time-in-market."""
    hold = _cards()["hold"]
    start = hold.final_net_worth - hold.pnl
    curve = [start, *hold.equity_curve]
    daily = [b / a - 1.0 for a, b in zip(curve, curve[1:])]
    sd = statistics.stdev(daily)
    assert hold.volatility_pct == pytest.approx(sd * math.sqrt(252) * 100)
    assert hold.sharpe == pytest.approx(
        statistics.fmean(daily) / sd * math.sqrt(252))
    assert hold.time_in_market == 1.0
    assert 0.9 < hold.avg_gross_exposure < 1.01
    shown = repr(hold)
    # Five days is under Scorecard.SHARPE_MIN_DAYS, so the repr holds the
    # figure back; the property still has it.
    assert "sharpe=n/a (short run)" in shown, shown
    assert f"vol={hold.volatility_pct:.1f}%" in shown, shown
    assert "in_market=100%" in shown and "exposure=" in shown, shown


def test_the_repr_prints_no_sharpe_for_a_short_run():
    """A persona: a 10-day run printed sharpe=+1.99, a figure whose
    standard error at ten days is about five."""
    assert tf.Scorecard.SHARPE_MIN_DAYS == 20
    cards = tf.evaluate({"hold": BuyAndHold(), "flat": Flat()}, seed=3,
                        universe=UNIVERSE, days=19)
    hold = cards["hold"]
    assert hold.sharpe is not None
    assert "sharpe=n/a (short run)" in repr(hold), repr(hold)
    assert "sharpe=n/a (short run)" in repr(cards["flat"])
    long = tf.evaluate({"hold": BuyAndHold()}, seed=3, universe=UNIVERSE,
                       days=20)["hold"]
    assert f"sharpe={long.sharpe:+.2f}" in repr(long), repr(long)


def test_a_flat_agent_has_no_sharpe_and_no_exposure():
    flat = _cards()["flat"]
    assert flat.sharpe is None
    assert flat.volatility_pct == 0.0
    assert flat.time_in_market == 0.0
    assert flat.avg_gross_exposure == 0.0
    assert "sharpe=n/a" in repr(flat)


def test_the_new_figures_stay_out_of_as_dict():
    card = _cards()["hold"]
    keys = set(card.as_dict())
    for name in ("sharpe", "volatility_pct", "time_in_market",
                 "avg_gross_exposure", "exposure_curve"):
        assert name not in keys, name
    with pytest.raises(AttributeError):
        card.sharpe = 1.0


# -- Transcript from the subpackage ------------------------------------------


def test_transcript_imports_from_the_integrations_package():
    from tradefloor.integrations import Transcript
    from tradefloor.integrations.common import Transcript as Common
    assert Transcript is Common


def test_the_integrations_package_still_imports_nothing_until_asked():
    code = ("import sys, tradefloor.integrations as i; "
            "print('tradefloor.integrations.common' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "False"


# -- tca.analyse takes history_days ------------------------------------------


def test_tca_analyse_takes_history_days():
    """Marcus: analyse(..., history_days=21) raised TypeError."""
    seen = []

    class Looks:
        def act(self, obs):
            seen.append(len(obs.history) if obs.step == 0 else None)
            return {}

    run = tf.tca.analyse(Looks(), seed=3, universe=UNIVERSE, days=1,
                         history_days=21)
    assert seen[0] == 21
    assert run.history_days == 21
    plain = tf.tca.analyse(Looks(), seed=3, universe=UNIVERSE, days=1)
    assert plain.history_days == 0
    with pytest.raises(tf.ValidationError):
        tf.tca.analyse(Looks(), seed=3, universe=UNIVERSE, days=1,
                       history_days=-1)


def test_tca_history_days_runs_both_worlds_from_the_warmed_market():
    """A shortfall priced against an untraded world that skipped the
    warm-up would measure the warm-up. With no orders the two paths are
    the same market."""
    run = tf.tca.analyse(Flat(), seed=3, universe=UNIVERSE, days=1,
                         history_days=5)
    assert run.actual_path == run.baseline_path
    cold = tf.tca.analyse(Flat(), seed=3, universe=UNIVERSE, days=1)
    assert cold.actual_path[0] != run.actual_path[0]


# -- the envelope ------------------------------------------------------------


def test_a_short_lag_clustering_question_is_outside():
    """Elena: check(252, [acf1, acf5]) read 'inside the envelope'."""
    gap = {g.id: g for g in env.GAPS}["decay-shape"]
    assert {"abs_return_acf1", "abs_return_acf5",
            "abs_return_acf20"} <= set(gap.statistics)
    for stats in (["abs_return_acf1"], ["abs_return_acf5"],
                  ["abs_return_acf1", "abs_return_acf5"]):
        v = env.check(horizon_days=252, statistics=stats)
        assert not v.inside, (stats, str(v))
        assert [g.id for g in v.gaps] == ["decay-shape"], stats


_HISTORY = re.compile(
    r"\b(UPDATED|CORRECTED|SHARPENED|RECOMPOSED|NARROWED|REPLACED|"
    r"WITHDRAWN)\b|envgaps|docs080|ptv19panel|\b20\d\d-\d\d-\d\d\b|"
    r"\buntil 20\d\d")


@pytest.mark.parametrize("gap", env.GAPS, ids=lambda g: g.id)
def test_a_gap_states_its_finding_without_a_changelog(gap):
    found = _HISTORY.findall(gap.detail)
    assert not found, (gap.id, found)


def test_a_long_horizon_reason_opens_with_a_plain_sentence():
    """Marcus: check(horizon_days=450) opened on panel statistics."""
    reason = env.check(horizon_days=450).reasons[0]
    first = reason.split(". ")[0]
    assert first.startswith("A 450-day run is longer than the 252 days"), \
        reason
    assert not re.search(r"\d\.\d|band|seed", first), first
