"""Which tests the box gate is allowed to deselect, pinned by name.

`fleet.skip_gate_is_earned` refuses a launch when no full-suite run is
recorded green at the pin. That is the right refusal and it is why every box
from `wtcomp1` onward ran on a tree nobody had measured. It also deadlocks,
because the suite cannot go green: two tests assert the shipped preset
clears its release bar, the preset does not clear it, and they will stay red
until it does. A guard that must be bypassed to do ordinary work gets
bypassed, and then it guards nothing.

So the box gate runs `pytest -m "not ship_bar and not needs_live_model"`
and the release gate runs the whole suite. `tests/conftest.py` says what
each marker means. This file says which tests carry them, and it is the
half that stops the scheme from rotting.

WHY A NAMED SET RATHER THAN A COUNT, AND WHY NOT AN ALLOWLIST. The first
design on the table was "green except a named, justified set": a file of
test names with prose beside each one. It was refused because it makes
every legitimate red look like the same kind of thing, and because adding a
row to it is a diff nobody can judge without opening three other files.
Adding a marker to a test is a diff a reader can judge against the test's
own docstring. The set below is pinned rather than counted because a count
of two cannot tell you that a marker moved from one test to another.

WHAT IS DELIBERATELY NOT ASSERTED. A defect allowlist needs a rule that a
listed entry which starts passing fails the guard, or the list becomes a
place to hide regressions. That rule does not transfer here and asserting
it would be perverse: a `ship_bar` test passing is the outcome the whole
project is working toward, and a `needs_live_model` test passing means
somebody re-recorded the fixture. Both are good news. What these markers
rot by is sprawl, so sprawl is what is guarded.

THE HALF THAT LIVES ELSEWHERE, recorded here because the two are useless
apart. A green reading that silently deselected tests is the
`SKIP_GATE_IF_KAT` habit in a new coat, so a recorded run has to carry the
selection it ran under. `guards.py gate-green-at-pin` takes the expression,
refuses an entry that does not name its selection, and refuses a pin green
under one expression when asked about another.
"""
from __future__ import annotations

import pathlib
import re

TESTS = pathlib.Path(__file__).resolve().parent

#: Every test carrying a gate marker, as (file, test name), with the reason
#: the marker is the truthful one for it.
MARKED = {
    "ship_bar": {
        # Both assert 13 == 14. `pt-v19.json` records `in_band 13` with
        # `misses ["sector_excess_corr"]` at all four protocols against
        # `len(envelope.CERTIFIED) == 14`, so both read the same fact by
        # different routes. The tests are right and the preset is wrong.
        # Turning either green needs the record edited to say the preset
        # misses nothing, or the assertion relaxed to permit one miss, and
        # both are a value chosen because it clears a band.
        ("test_envelope.py",
         "test_all_fourteen_are_in_band_at_the_certified_horizon"),
        ("test_preset_records.py",
         "test_the_envelope_and_the_record_agree_on_the_band_count"),
        # THE MECHANISM AND STRUCTURAL CERTIFICATES, REPORTED BESIDE THE
        # BAR. Until 2026-09-23 these two were the mechanism half and the
        # second gate's half of the ship bar: non-regression on the shipped
        # preset's own certificates. The owner's ruling of that day (design
        # repo `programme/longrun/CRITERIA.md`, ledger `ruling-the-pass-bar-
        # is-what-a-user-would-notice-programme-longrun-criteria`) makes the
        # pass bar the fifteen long-run criteria plus every ruled band, and
        # says the mechanism certificate and the VIX persistence rows "are
        # reported and investigated but do not gate".
        #
        # Why they still carry the marker. Each reads the SHIPPED preset's
        # committed record and asserts, beside the certificate it reports,
        # the two things that DO gate: the long-run verdict passes and every
        # ruled band is in on all four protocols. A red here is therefore a
        # fact about the preset and not about the tree, which is what the
        # marker means, and a box gate must deselect them for the reason it
        # deselects the band count -- the box is being asked whether the
        # tree runs, not whether this preset may ship. The bars' own logic
        # is tested elsewhere in both files, unmarked, on a frozen record.
        ("test_mechanism_gate.py",
         "test_the_shipped_record_reports_its_mechanism_certificate_beside_the_bar_that_gates"),
        ("test_structure_gate.py",
         "test_the_shipped_record_reports_its_structural_certificate_beside_the_bar_that_gates"),
    },
    "needs_live_model": {
        # Both replay `tests/fixtures/openai_agents/five-days.json` and both
        # miss all five recorded digests. MEASURED 2026-09-14 by diffing the
        # live day-zero prompt against the recorded one: the instructions
        # are byte-identical, every asset field is identical (day-zero
        # prices are the same on every shipped preset), and the only
        # difference is the macro opening -- `federal_funds_rate` 0.03 to
        # 0.025, `vix` 24.3276 to 22.5296, `corporate_bond_yield` 0.055290
        # to 0.049268, `inflation_rate` 0.0282215 to 0.0280943. A recording
        # is keyed on a digest of the exact observation the model was sent,
        # so a moved macro opening invalidates all five keys and no edit in
        # this repository can put them back.
        ("test_openai_agents.py",
         "test_the_committed_recording_replays_end_to_end"),
        ("test_render.py",
         "test_openai_agents_default_renderer_replays_the_shipped_fixture"),
        # THE 0.8.5 CONTRACT FREEZE. Every test below replays one of the
        # seven LLM recordings in `tests/fixtures/` (callable/five-days,
        # finrobot/{liquidity-crisis,rate-ladder,rate-shock},
        # langgraph/rate-shock, openai_agents/five-days,
        # pydantic_ai/rate-shock), and all seven miss at step 0 by
        # construction. Decision schema 2 and observation payload 1 changed
        # the text every model is sent: the payload gained
        # `portfolio.open_orders`, renamed `portfolio.gross_exposure` to
        # `leverage`, and widened `return_5d` from 29 to 30 step intervals,
        # and the FinRobot, LangGraph and PydanticAI instructions now
        # describe limit orders and CANCEL. A replay key is a digest of that
        # text, and the instructions digest is checked at construction, so
        # only a live re-record restores them. The two FinRobot mandate and
        # PydanticAI mandate checks compare a recording's stamped
        # instructions digest with the shipped text, which moved on purpose.
        ("test_boundary.py",
         "test_the_map_runs_against_the_recorded_finrobot_agent_without_a_provider"),
        ("test_boundary.py",
         "test_the_runner_replays_the_recording_and_writes_the_map"),
        ("test_callable.py", "test_the_recorded_run_replays_end_to_end"),
        ("test_examples.py",
         "test_the_liquidity_crisis_study_replays_its_recording"),
        ("test_fingerprint.py",
         "test_the_recorded_finrobot_fixture_matches_its_own_transcript"),
        ("test_finrobot.py", "test_the_recorded_run_replays_end_to_end"),
        ("test_finrobot.py",
         "test_the_shipped_fixture_carries_the_digest_of_the_mandate_that_ran_it"),
        ("test_langgraph.py", "test_the_recorded_run_replays_end_to_end"),
        ("test_langgraph.py", "test_the_recorded_arms_diverge_at_the_shock"),
        ("test_langgraph.py",
         "test_replay_needs_neither_the_framework_nor_a_key"),
        ("test_pydantic_ai_replay.py",
         "test_the_committed_fixture_still_matches_the_shipped_mandate"),
        ("test_pydantic_ai_replay.py",
         "test_the_recorded_run_replays_end_to_end"),
        ("test_render.py",
         "test_finrobot_default_renderer_replays_the_shipped_fixture"),
        ("test_render.py",
         "test_langgraph_default_renderer_replays_the_shipped_fixture"),
        ("test_render.py",
         "test_pydantic_ai_default_renderer_replays_the_shipped_fixture"),
        ("test_render.py",
         "test_two_identical_renderers_give_identical_decisions_on_the_fixture"),
        ("test_render.py",
         "test_invariance_reports_a_non_matching_renderer_as_unrecorded"),
        ("test_render.py",
         "test_invariance_asked_for_more_days_than_the_fixture_covers_stops_early"),
        # EXAMPLE 08's RECORDING (owner decision 10). Both read
        # `tests/fixtures/claude/example-08.json`, a live Claude run of
        # `examples/08-claude-agent.py`. Each answer is keyed by a digest of
        # the day's prompt, and the prompt carries every price, so a moved
        # market or prompt misses at day 0 and only a live re-record
        # restores it. Until the first recording is committed both skip and
        # name the command that makes it.
        ("test_examples.py",
         "test_the_claude_example_replays_its_committed_recording"),
        ("test_examples.py",
         "test_the_claude_example_recording_says_what_made_it"),
    },
}

#: The expression `fleet.BOX_GATE_SELECTION` carries. Written out here so
#: that a marker added without the gate being told fails in the suite rather
#: than silently widening what a box may skip.
BOX_GATE_SELECTION = "not ship_bar and not needs_live_model"


def _marked(name: str) -> set[tuple[str, str]]:
    """Every `(file, test)` carrying `@pytest.mark.<name>`, read from source.

    Read from SOURCE rather than from collected items, and the reason is
    the whole point of the file. Under the box gate's own selection the
    marked tests are deselected, so a check built on `session.items` would
    count zero of them and pass by vacuum in exactly the run where it
    matters most.
    """
    pattern = re.compile(
        r"^@pytest\.mark\." + re.escape(name) + r"\s*$"
        r"(?:\n^@.*$)*"          # any further decorators
        r"\n^def (test_\w+)", re.M)
    found = set()
    for path in sorted(TESTS.glob("test_*.py")):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            found.add((path.name, match.group(1)))
    return found


def test_the_gate_markers_are_on_the_tests_this_file_names():
    """A marker that moved, arrived or left fails here and says which."""
    for name, expected in MARKED.items():
        found = _marked(name)
        assert found == expected, (
            f"@pytest.mark.{name} is on {sorted(found)} and this file names "
            f"{sorted(expected)}. A gate marker says a red is a fact about "
            "the preset or about an artefact rather than about the tree, "
            "which is a claim somebody has to make on purpose. Add the test "
            "to MARKED above with the measurement behind it, or take the "
            "marker off.")


def test_no_test_carries_both_gate_markers():
    """They answer different questions and a test that claimed both would
    be deselected by a gate that only meant to skip one of them."""
    both = _marked("ship_bar") & _marked("needs_live_model")
    assert not both, both


def test_the_box_gate_selection_covers_every_marker_this_file_knows():
    """The expression and the marker set have to move together.

    A third marker introduced without `fleet.BOX_GATE_SELECTION` being told
    would be deselected by nothing, so the box gate would go on refusing
    the pin while the suite reported the red as accounted for. That is the
    worst of both."""
    for name in MARKED:
        assert f"not {name}" in BOX_GATE_SELECTION, (
            f"{name} is a gate marker and BOX_GATE_SELECTION does not "
            f"deselect it: {BOX_GATE_SELECTION!r}")
    named = set(re.findall(r"not (\w+)", BOX_GATE_SELECTION))
    assert named == set(MARKED), (
        f"BOX_GATE_SELECTION deselects {sorted(named)} and this file knows "
        f"{sorted(MARKED)}. A marker the suite does not know about is one "
        "nobody is pinning.")


def test_this_file_is_not_itself_deselected():
    """The control on the control.

    Every check above is worthless if it does not run in the box gate's own
    selection, which is the one run where a widened marker set would
    otherwise pass unseen. Nothing in this module carries a gate marker, so
    nothing here can be deselected by the expression that deselects them."""
    mine = {t for marks in (_marked(n) for n in MARKED) for t in marks}
    assert not any(f == pathlib.Path(__file__).name for f, _ in mine), mine
