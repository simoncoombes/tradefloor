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
    assert f"{of} registered rows" in summary, summary
    assert f"{passed} of {of} met" in summary, summary
    assert f"fails 16 of the {of}" in summary, summary
    assert "28" not in summary, summary
    assert f'"{of} of {of}"' in text


def test_the_readme_counts_the_long_run_rows_the_record_holds():
    """The README said 'meets all 17, and the 23 more'."""
    record = long_run()
    text = flat(read("README.md"))
    assert f"are {record['of']} rows over 21 years" in text
    assert f"{tf.preset_record()['preset']} meets all {record['of']}," in text
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
    """The README said twelve; `mcp.py` registers thirteen. It then called
    all thirteen read-only when `start_job` was not, and the session tools
    are not either, so the row counts both. Read from the source, so this
    runs without the `mcp` extra; `test_mcp.py` checks the same row against
    the registered annotations."""
    source = read("python/tradefloor/mcp.py")
    count = source.count("@server.tool")
    read_only = source.count("annotations=_READ_ONLY,")
    words = {12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
             16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
             20: "twenty", 21: "twenty-one", 22: "twenty-two"}
    text = read("README.md")
    assert (f"| MCP server | {words[count]} tools for a coding agent, "
            f"{words[read_only]} of them read-only") in text


def test_the_examples_table_says_what_the_factors_sum_to():
    text = read("README.md")
    assert "sum to every move" not in text
    assert "The twelve factors that sum to the mispricing's move" in text


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

def limits_table() -> dict[str, str]:
    # The full table moved from the README to docs/REALISM.md, which the
    # README links for every limit.
    text = read("docs/REALISM.md")
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
    "volatility memory", "overnight gaps", "intraday",
    "slicing a large order", "agent interaction",
])
def test_every_limit_the_reviewers_measured_has_a_row(name):
    rows = limits_table()
    assert name in rows, f"no {name!r} row in the docs/REALISM.md limits table"
    assert rows[name].endswith("the next preset"), rows[name]


def test_the_opening_limit_says_pt_v21_has_none():
    """pt-v21 opens after 504 sessions of prehistory, so the opening VIX
    varies with the seed, and the row says so with the range."""
    row = limits_table()["opening state"]
    assert row.startswith("on pt-v21 the market lives 504 sessions"), row
    assert "from 10.3 to 43.2" in row, row
    assert row.endswith("| no limit on pt-v21"), row


def test_the_roster_limit_names_the_preset_it_was_measured_on():
    """The mixes were measured on pt-v19 only, as the envelope gap says."""
    assert "pt-v19 only" in limits_table()["roster"]


def test_the_readme_states_the_per_seed_pass_rate():
    """19 of 19 is a verdict on 30-seed medians, not on one seed's year."""
    text = flat(read("README.md"))
    assert "tf.envelope.intervals()" in text
    assert "all 14 were in range on 8 of the 16" in text
    assert "tf.envelope.intervals()" in flat(read("docs/STATISTICS.md"))


def test_the_clustering_shortfall_is_stated_beside_the_certificate():
    """On pt-v21 clustering is near real at lag 1 and below every real year
    at lag 5; the pages say both, with the certified figures."""
    certified = tf.envelope.certified()["statistics"]
    lag1 = certified["abs_return_acf1"]["measured"]
    lag5 = certified["abs_return_acf5"]["measured"]
    windows = tf.facts.REAL_MARKETS_WINDOWS["values"]
    assert lag5 < min(windows["abs_return_acf5"])
    readme = flat(read("README.md"))
    assert f"`abs_return_acf5` reads {lag5:.3f}" in readme
    stats = flat(read("docs/STATISTICS.md"))
    assert f"{lag1:.4f} for `abs_return_acf1`" in stats
    assert f"{min(windows['abs_return_acf5']):.3f}" in stats


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
                 "known_answer_book.py", "known_answer_traded.py"):
        assert f"`tests/{name}`" in text, name
    # A traded evaluate run has had a digest since 0.8.5. Before it, this
    # page said none did, and that sentence must not come back.
    assert "has no digest yet" not in text
    # The default preset by name since 0.10.0 (pt-v21); "on pt-v20" before.
    assert "one traded `tf.evaluate` run is pinned on the default preset" in text
    assert "does not recompute a score" in text


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------

def test_the_readme_states_cpu_time_as_the_examples_pages_do():
    """'About two seconds' and 'ten to twenty seconds' were a third of it."""
    text = flat(read("README.md"))
    for stale in ("about two seconds", "ten to twenty seconds",
                  "about two minutes", "0.7 seconds"):
        assert stale not in text, stale
    assert "The run takes under five seconds of CPU" in text
    assert "about forty seconds of CPU" in text


# ---------------------------------------------------------------------------
# The third persona round (2026-10-01)
# ---------------------------------------------------------------------------

def test_support_says_the_liquidity_crisis_run_was_recorded_again():
    """SUPPORT.md said the study's recordings stopped replaying at the first
    decision, after the canonical run had been recorded again and replayed.
    It names the preset and the call count the committed recording carries,
    read off the recording rather than restated."""
    from tradefloor.integrations.common import Transcript

    text = flat(read("docs/SUPPORT.md"))
    assert "stop replaying" not in text
    recording = Transcript.load(
        ROOT / "tests/fixtures/finrobot/liquidity-crisis.json")
    preset = recording.meta["model_preset"]
    version = recording.meta["tradefloor_version"]
    assert (f"recorded again, live, on {preset} at {version}: its canonical "
            f"run is {len(recording)} model calls, in "
            "`tests/fixtures/finrobot/liquidity-crisis.json`") in text
    assert "were not recorded again" not in text


def test_the_manifest_docs_name_the_result_block_it_writes():
    """Three pages said an edited `pnl` in the result block passes. There is
    no pnl there: the block holds the digest, the days and the draws."""
    universe = tf.Universe.random(3, seed=1)
    engine = tf.Engine(seed=1, universe=universe)
    engine.run_days(1)
    keys = set(tf.RunManifest.of(engine, seed=1, universe=universe).result)
    assert "pnl" not in keys
    for name in ("README.md", "docs/SUPPORT.md"):
        text = flat(read(name))
        assert "`pnl`" not in text, name
        assert "`result` block holds" in text, name
        for key in sorted(keys):
            assert f"`{key}`" in text, (name, key)


_COUNT_WORDS = {"nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def test_example_07_counts_the_factors_the_engine_has():
    text = flat(read("examples/07-research-workflow.py").replace("#", " "))
    said = re.findall(r"the (\w+) components sum to the change", text)
    assert said, "example 07 no longer says what the components sum to"
    for word in said:
        assert _COUNT_WORDS.get(word) == len(tf.Engine.FACTORS), word


def _callable_notebook_prose() -> str:
    import json
    nb = json.loads(read("examples/integrations/callable/five_days.ipynb"))
    return flat(" ".join("".join(cell["source"]) for cell in nb["cells"]
                         if cell["cell_type"] == "markdown"))


@pytest.mark.parametrize("name", ["docs/AGENTS.md",
                                  "examples/integrations/README.md",
                                  "callable notebook"])
def test_the_callable_docs_name_postprocess_and_the_prompt_guard(name):
    """postprocess and AdapterInfo(instructions_digest=...) were documented
    in the module docstring only, and a CI design rests on both."""
    text = (_callable_notebook_prose() if name == "callable notebook"
            else flat(read(name)))
    assert "postprocess" in text, name
    assert "instructions_digest" in text, name
    assert "AdapterInfo" in text, name


def test_no_page_says_changed_instructions_make_a_callable_key_go_missing():
    """The callable key is the payload alone. A changed prompt matches every
    key, and only an instructions_digest refuses the replay."""
    text = flat(read("examples/integrations/README.md"))
    assert "the cadence or the instructions and the key goes missing" not in text
    assert "Change the observation mapping, the mandate or the market" not in (
        _callable_notebook_prose())


def test_a_callable_replay_refuses_a_changed_prompt_only_with_a_digest():
    """What the docs above now say, held against the adapter."""
    from tradefloor.integrations.callable import callable_agent
    from tradefloor.integrations.common import AdapterInfo, Transcript, digest

    universe = tf.Universe.random(3, seed=1)

    def ask(payload):
        return {"actions": [], "rationale": "hold"}

    def info(prompt):
        return AdapterInfo(framework="callable",
                           instructions_digest=digest(prompt))

    recorder = Transcript()
    tf.evaluate({"a": callable_agent(ask, info=info("A"), recorder=recorder)},
                seed=3, universe=universe, days=1)
    with pytest.raises(tf.ValidationError, match="different instructions"):
        callable_agent(mode="replay", transcript=recorder, info=info("B"))
    callable_agent(mode="replay", transcript=recorder)


def test_world_says_every_agent_starts_with_the_same_cash():
    from tradefloor.counterfactual import World

    text = flat(World.__doc__)
    assert "Every agent starts with the same cash" in text
    assert "``cash`` and ``max_leverage`` are per agent" not in text
    with pytest.raises(tf.ValidationError, match="cash must be a number"):
        World(seed=1, universe=tf.Universe.random(3, seed=1),
              agents={"a": object(), "b": object()},
              cash={"a": 1e6, "b": 5e6})


def _close_gaps(model: str) -> list[float]:
    """|next day's first price / history close - 1| for each name and day."""
    gaps: list[float] = []

    class Reader:
        def act(self, obs):
            if obs.step_of_day == 0 and obs.day > 0:
                for bar in obs.history.bars(last=1):
                    gaps.append(abs(obs.price(bar["ticker"]) / bar["close"] - 1))
            return {}

    tf.evaluate({"r": Reader()}, seed=3, universe=tf.Universe.random(4, seed=5),
                days=3, model=model)
    return gaps


def test_history_says_its_close_is_not_where_the_next_day_starts():
    """A broker's bar closes at the official close. On pt-v20 obs.history's
    close is the last print, and the close then re-marks every name."""
    from tradefloor.harness import History

    text = flat(History.__doc__)
    assert "not the price the next session starts from" in text
    assert "On presets through pt-v19 the close re-marks nothing" in text
    assert "A bar's close is the day's last print" in flat(read("README.md"))
    assert min(_close_gaps("pt-v20")) > 0
    assert max(_close_gaps("pt-v19")) == 0


def test_the_sandbox_limits_name_the_seed_on_the_call_stack():
    """The docs called the seed 'guessed'. It is a local in the harness's own
    frames, and an engine rebuilt from it runs ahead with tampered=False."""
    from tradefloor import sandbox

    for name, text in (("sandbox", flat(sandbox.__doc__)),
                       ("README.md", flat(read("README.md"))),
                       ("CHANGELOG.md", flat(read("CHANGELOG.md")))):
        assert "guessed seed" not in text, name
        assert "sys._getframe" in text, name
