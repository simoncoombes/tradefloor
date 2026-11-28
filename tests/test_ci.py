"""`tradefloor ci compare`: two versions of an agent on the same seeds.

The runs are short (two or three seeds, five days, five companies) because
what is under test is the pairing, the thresholds and the exit status, and
none of those needs a long market to show a fault.
"""

from __future__ import annotations

import json

import pytest

import tradefloor as tf
from tradefloor import ci
from tradefloor.__main__ import main

UNIVERSE = tf.Universe.random(5, seed=7)

AGENTS = '''
import tradefloor as tf

def hold():
    return tf.baselines.BuyAndHold()

def momentum():
    return tf.baselines.Momentum()

NOT_CALLABLE = 3
'''


@pytest.fixture()
def agents(tmp_path):
    path = tmp_path / "bot.py"
    path.write_text(AGENTS, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def report():
    return ci.compare(tf.baselines.BuyAndHold, tf.baselines.Momentum,
                      seeds=[1, 2, 3], universe=UNIVERSE, days=5)


def test_same_agent_ties_every_seed():
    # The property the whole comparison rests on: with the agent held fixed,
    # the two runs of a seed are the same run.
    same = ci.compare(tf.baselines.Momentum, tf.baselines.Momentum,
                      seeds=[1, 2], universe=UNIVERSE, days=5)
    for row in same.rows:
        assert row["baseline"] == row["candidate"]
    paired = same.paired()
    assert paired["ties"] == 2 and paired["p_value"] is None
    assert same.check([ci.Threshold("max_drawdown_increase", 0.0),
                       ci.Threshold("min_median_return_delta", 0.0)]) == []


def test_report_matches_evaluate(report):
    cards = tf.evaluate({"baseline": tf.baselines.BuyAndHold(),
                         "candidate": tf.baselines.Momentum()},
                        seed=2, universe=UNIVERSE, days=5)
    row = next(row for row in report.rows if row["seed"] == 2)
    assert row["candidate"]["pnl"] == cards["candidate"].pnl
    assert row["baseline"]["max_drawdown_pct"] == cards["baseline"].max_drawdown_pct


def test_repeatable(report):
    again = ci.compare(tf.baselines.BuyAndHold, tf.baselines.Momentum,
                       seeds=[1, 2, 3], universe=UNIVERSE, days=5)
    assert again.as_dict() == report.as_dict()


def test_paired_counts_add_up(report):
    paired = report.paired()
    assert (paired["candidate_wins"] + paired["baseline_wins"]
            + paired["ties"]) == 3


def test_thresholds_fail_and_pass(report):
    worst = report.summary("candidate")["worst_drawdown_pct"]
    assert report.check([ci.Threshold("max_drawdown", worst + 1.0)]) == []
    failed = report.check([ci.Threshold("max_drawdown", worst - 0.01)])
    assert len(failed) == 1 and failed[0].startswith("max_drawdown:")
    median = report.summary("candidate")["median_return_pct"]
    assert report.check([ci.Threshold("min_median_return", median - 1.0)]) == []
    assert report.check([ci.Threshold("min_median_return", median + 1.0)])


def test_unknown_threshold_is_refused():
    with pytest.raises(ValueError, match="unknown threshold"):
        ci.Threshold("max_sharpe", 1.0)


def test_repeated_or_missing_seeds_are_refused():
    with pytest.raises(ValueError, match="repeats"):
        ci.compare(tf.baselines.BuyAndHold, tf.baselines.BuyAndHold,
                   seeds=[1, 1], universe=UNIVERSE, days=5)
    with pytest.raises(ValueError, match="empty"):
        ci.compare(tf.baselines.BuyAndHold, tf.baselines.BuyAndHold,
                   seeds=[], universe=UNIVERSE, days=5)


def test_markdown_says_what_it_is(report):
    text = report.markdown()
    assert "no thresholds set" in text
    assert "| Median return, % |" in text
    assert "simulated markets" in text
    # Three seeds cannot reach 0.05, and the summary says so itself.
    assert "needs six or more" in text
    assert text.isascii()


@pytest.mark.parametrize("text,expected", [
    ("1-4", [1, 2, 3, 4]), ("3,5,8", [3, 5, 8]), ("1-2, 9", [1, 2, 9])])
def test_parse_seeds(text, expected):
    assert ci.parse_seeds(text) == expected


@pytest.mark.parametrize("text", ["", "4-1", "a"])
def test_parse_seeds_refuses(text):
    with pytest.raises(ValueError):
        ci.parse_seeds(text)


def test_cli_writes_report_and_passes(agents, tmp_path, capsys):
    out, summary = tmp_path / "r.json", tmp_path / "s.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    code = main(["ci", "compare", "--baseline", f"{agents}:hold",
                 "--candidate", f"{agents}:momentum", "--seeds", "1-2",
                 "--days", "5", "--companies", "5",
                 "--json", str(out), "--markdown", str(summary)])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_version"] == ci.REPORT_VERSION
    assert data["seeds"] == [1, 2] and data["passed"] is True
    assert len(data["per_seed"]) == 2
    # Appended, since the step summary file is shared by a job's steps.
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("earlier step\n") and "tradefloor evaluation" in written
    assert "tradefloor evaluation" in capsys.readouterr().out


def test_cli_exits_one_on_a_failed_threshold(agents):
    code = main(["ci", "compare", "--baseline", f"{agents}:hold",
                 "--candidate", f"{agents}:momentum", "--seeds", "1-2",
                 "--days", "5", "--companies", "5",
                 "--min-median-return", "1000"])
    assert code == 1


@pytest.mark.parametrize("candidate", ["{path}:missing", "{path}:NOT_CALLABLE",
                                       "{path}", "nowhere.py:hold"])
def test_cli_exits_two_on_a_bad_target(agents, candidate, capsys):
    code = main(["ci", "compare", "--baseline", f"{agents}:hold",
                 "--candidate", candidate.format(path=agents),
                 "--seeds", "1", "--days", "5", "--companies", "5"])
    assert code == 2
    assert "tradefloor ci:" in capsys.readouterr().err


def test_cli_runs_a_packaged_scenario(agents, tmp_path):
    out = tmp_path / "r.json"
    code = main(["ci", "compare", "--baseline", f"{agents}:hold",
                 "--candidate", f"{agents}:hold", "--seeds", "1",
                 "--days", "5", "--companies", "5",
                 "--scenario", "rate_shock", "--json", str(out)])
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["scenario"] == "rate_shock"


def test_the_self_test_agents_hold_one_threshold_and_breach_the_other(tmp_path):
    """The two runs `.github/workflows/evaluate-selftest.yml` makes, locally.

    `examples/ci-selftest/baseline.py` and `candidate.py` on seeds 1-3, ten
    days, eight companies: `--max-errors 0` holds (exit 0) and
    `--max-drawdown 0` fails (exit 1), which is what the workflow's `check`
    job asserts on GitHub.
    """
    import pathlib
    import warnings
    study = pathlib.Path(__file__).resolve().parent.parent / "examples" / "ci-selftest"
    common = ["ci", "compare",
              "--baseline", f"{study / 'baseline.py'}:make_agent",
              "--candidate", f"{study / 'candidate.py'}:make_agent",
              "--seeds", "1-3", "--days", "10", "--companies", "8"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert main(common + ["--max-errors", "0",
                              "--json", str(tmp_path / "holds.json")]) == 0
        assert main(common + ["--max-drawdown", "0",
                              "--json", str(tmp_path / "breaches.json")]) == 1
    breached = json.loads((tmp_path / "breaches.json").read_text(encoding="utf-8"))
    assert breached["passed"] is False
