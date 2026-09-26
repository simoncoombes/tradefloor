"""The top-level documents agree with the package and with each other.

Five outside reviewers read the 0.8.5 pre-release and found the README,
docs/STATISTICS.md, docs/MODEL.md and docs/SUPPORT.md disagreeing with the
shipped record and with each other: a summary row that counted 28 long-run
rows where the record holds 40, a C9 figure in MODEL.md that matched no
record, "twelve" MCP tools where the server registers thirteen, a scenario
limit that read like sampling noise when the envelope states a bias, a
replay promise wider than any digest checks, and demo timings a third of
what the demo costs. Each test below reads the figure from where it lives,
the record, the envelope or the source, and checks the page against it, so
the page moves when the figure does.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import tradefloor as tf

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def flat(text: str) -> str:
    """Hard-wrapped markdown as one line, so a phrase can span a wrap."""
    return re.sub(r"\s+", " ", text)


def long_run() -> dict:
    return tf.preset_record()["long_run"]


def row(record: dict, row_id: str) -> dict:
    return next(r for r in record["rows"] if r["id"] == row_id)


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_the_statistics_summary_row_counts_what_the_record_holds():
    """The "sets at a glance" row said 28 rows, 28 of 28 and fails 10 of 28."""
    record = long_run()
    of, passed = record["of"], record["passed"]
    text = read("docs/STATISTICS.md")
    summary = next(line for line in text.splitlines()
                   if line.startswith("| [The long-run criteria]"))
    assert f"{of} registered rows for pt-v20" in summary, summary
    assert f"{passed} of {of} met" in summary, summary
    assert f"fails 16 of the {of}" in summary, summary
    assert "28" not in summary, summary
    assert f'"{of} of {of}"' in text


def test_the_readme_counts_the_long_run_rows_the_record_holds():
    """The README said 'meets all 17, and the 23 more'."""
    record = long_run()
    text = flat(read("README.md"))
    assert f"are {record['of']} rows over 21 years for pt-v20" in text
    assert f"pt-v20 meets all {record['of']}." in text
    assert "meets all 17" not in text


def test_model_md_quotes_c9_as_the_record_reads_it():
    """MODEL.md gave C9 as 0.487 and 0.468; the record reads 0.4836, 0.4239."""
    exponent, coefficient = row(long_run(), "C9")["value"]
    text = flat(read("docs/MODEL.md"))
    quoted = (f"row C9 reads exponent {exponent:.3f} and coefficient "
              f"{coefficient:.3f}")
    assert quoted in text, f"MODEL.md should say {quoted!r}"
    stats = read("docs/STATISTICS.md")
    assert f"| {exponent:.3f}; {coefficient:.3f} |" in stats


def test_c9_says_it_measures_one_immediate_order():
    """Nothing said which order type or size range the fit describes."""
    for page in ("docs/STATISTICS.md", "docs/MODEL.md"):
        text = flat(read(page))
        assert "1% to 100% of a day's volume" in text, page
        assert "impact_curve.py" in text, page
    assert "one immediate order" in read("docs/STATISTICS.md")


def test_the_readme_counts_the_mcp_tools_the_server_registers():
    """The README said twelve; `mcp.py` registers thirteen."""
    source = read("python/tradefloor/mcp.py")
    count = source.count("@server.tool")
    words = {12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen"}
    text = read("README.md")
    assert f"| MCP server | {words[count]} read-only tools" in text


def test_the_examples_table_says_what_the_factors_sum_to():
    text = read("README.md")
    assert "sum to every move" not in text
    assert "The eleven factors that sum to the mispricing's move" in text


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def limits_table() -> dict[str, str]:
    text = read("README.md")
    start = text.index("| limit | what it means |")
    rows = {}
    for line in text[start:].splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[cells[0]] = " | ".join(cells[1:])
    return rows


def test_the_scenario_limit_states_the_envelope_s_bias():
    """It read 'the response has the right sign, but one run cannot size it'."""
    gap = next(g for g in tf.envelope.GAPS if g.id == "scenario-magnitude")
    rows = limits_table()
    assert gap.summary in rows["scenario size"], (gap.summary, rows)
    assert "cannot size it" not in rows["scenario size"]


@pytest.mark.parametrize("name", [
    "volatility memory", "opening state", "overnight gaps", "intraday",
    "slicing a large order", "agent interaction",
])
def test_every_limit_the_reviewers_measured_has_a_row(name):
    rows = limits_table()
    assert name in rows, f"no {name!r} row in the README limits table"
    assert rows[name].endswith("the next preset"), rows[name]


def test_the_roster_limit_names_the_preset_it_was_measured_on():
    """The mixes were measured on pt-v19 only, as the envelope gap says."""
    assert "pt-v19 only" in limits_table()["roster"]


def test_the_readme_states_the_per_seed_pass_rate():
    """19 of 19 is a verdict on 30-seed medians, not on one seed's year."""
    text = flat(read("README.md"))
    assert "tf.envelope.intervals()" in text
    assert "all 14 were in range on 5 of the 16" in text
    assert "tf.envelope.intervals()" in flat(read("docs/STATISTICS.md"))


def test_the_clustering_shortfall_is_stated_beside_the_certificate():
    certified = tf.envelope.certified()["statistics"]
    lag1 = certified["abs_return_acf1"]["measured"]
    lag5 = certified["abs_return_acf5"]["measured"]
    windows = tf.facts.REAL_MARKETS_WINDOWS["values"]
    assert lag1 < min(windows["abs_return_acf1"])
    assert lag5 < min(windows["abs_return_acf5"])
    readme = flat(read("README.md"))
    assert f"`abs_return_acf1` reads {lag1:.3f}" in readme
    stats = flat(read("docs/STATISTICS.md"))
    assert f"{lag1:.4f} for `abs_return_acf1`" in stats
    assert f"{min(windows['abs_return_acf1']):.3f}" in stats


# ---------------------------------------------------------------------------
# Replay promise
# ---------------------------------------------------------------------------

def test_the_readme_promises_only_what_a_digest_checks():
    """'Results will not [change]' covered traded runs no digest pins."""
    text = flat(read("README.md"))
    assert "Results will not" not in text
    assert "replays the same way this month" not in text
    assert "a market with no agent orders in it replays exactly" in text
    assert ("a traded run recorded before 0.8.5 matches up to its first "
            "trade and differs after it") in text


def test_support_says_what_each_digest_covers():
    text = flat(read("docs/SUPPORT.md"))
    for name in ("known_answer.py", "known_answer_presets.py",
                 "known_answer_book.py"):
        assert f"`tests/{name}`" in text, name
    assert "has no digest yet" in text
    assert "does not recompute a score" in text


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------

def test_the_readme_states_cpu_time_as_the_examples_pages_do():
    """'About two seconds' and 'ten to twenty seconds' were a third of it."""
    text = flat(read("README.md"))
    for stale in ("about two seconds", "ten to twenty seconds"):
        assert stale not in text, stale
    assert "The run takes under ten seconds of CPU" in text
    assert "about two minutes of CPU" in text
    assert "about 0.7 seconds of CPU to build" in text
