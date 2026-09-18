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
        # THE MECHANISM HALF OF THE SAME BAR, and the first entry here that
        # is GREEN. That is not an oversight and this file already says so
        # above: a `ship_bar` test passing is the outcome the project works
        # toward, and the rule against asserting that a listed entry fails
        # is stated in WHAT IS DELIBERATELY NOT ASSERTED.
        #
        # Why it carries the marker at all. It reads the SHIPPED preset's
        # committed certificate and refuses a release that shows fewer
        # mechanisms than that record does, on either the 252 panel or the
        # held-out seeds. A red here is therefore a fact about the preset --
        # a model that lost a mechanism -- and not about the tree, which is
        # exactly what the marker means. A box gate must deselect it for the
        # same reason it deselects the band count: the box is being asked
        # whether the tree runs, not whether this preset may ship.
        ("test_mechanism_gate.py",
         "test_the_shipped_preset_clears_the_mechanism_bar_on_both_panels"),
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
