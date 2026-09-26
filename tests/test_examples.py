"""The example notebooks must still run.

A committed notebook carries its output, which makes it readable
without a kernel -- and also what lets it rot silently. The output says the
code worked on the day it was written; nothing says it works now. A reader
who trusts a stale notebook loses an afternoon to an API that moved.

`depth()` gaining a required `side` argument is the concrete case: the
notebook read correctly, the output looked right, and the code raised
`TypeError`. It was caught because the notebooks are executed rather than
written and hoped over.

Opt-in because executing every notebook takes several minutes and needs
`jupyter`, which the library does not depend on. Set `TRADEFLOOR_SLOW_TESTS=1`
to run it; the release check does.

What is checked is everything under `examples/`, both tiers of it: the
numbered curriculum and the per-study directories `CONTRIBUTING.md`
describes. It used to be `0*`, and the first unnumbered example
landed with nothing checking it at all.
"""

import os
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

#: Everything under `examples/`, rather than a glob. A glob has now missed
#: two examples in two different ways: `0*` left `10-forking-a-market.py`
#: unchecked because the tenth example is not `0`-prefixed, and widening it to
#: `[0-9]*` still leaves out every example that is not numbered at all. Both
#: are the same failure, which is that a glob matching nothing new fails
#: silently by design. `CONTRIBUTING.md` sets out the two tiers -- the
#: numbered curriculum and one directory per study -- and this walk covers
#: both, so adding to either is enough to be checked.
_IGNORED = ("__pycache__", ".ipynb_checkpoints", "artifacts", "data")


def _examples(suffix: str) -> list[Path]:
    return sorted(
        path for path in EXAMPLES.rglob(f"*{suffix}")
        if not any(part in _IGNORED for part in path.relative_to(
            EXAMPLES).parts)
    )


NOTEBOOKS = _examples(".ipynb")
SCRIPTS = _examples(".py")

#: Executing notebooks is slow and needs jupyter, so those tests are opt-in.
#: The syntax check on the scripts is not -- a rename that missed a
#: reference should fail on every run, not only when someone remembers the
#: flag.
#: Both spellings. The flag was named before the library was, and someone
#: typing the current name at the current project should not silently get the
#: skip -- which is the same "a guard that quietly stops guarding" shape the
#: rename left in four other places. The old name keeps working because it is
#: in CONTRIBUTING.md, in RELEASING.md, and in people's shell history.
SLOW = pytest.mark.skipif(
    not (os.environ.get("TRADEFLOOR_SLOW_TESTS")
         or os.environ.get("PRETIUM_SLOW_TESTS")),
    reason=("executing the examples is slow; set TRADEFLOOR_SLOW_TESTS=1 "
            "to run (PRETIUM_SLOW_TESTS is still honoured)"),
)


@SLOW
def test_there_are_notebooks_to_check():
    """Guards the guard. A glob that matched nothing would make every test
    below pass by vacuum, and the suite would report the notebooks healthy
    while checking none of them."""
    assert len(NOTEBOOKS) >= 4, f"found {[p.name for p in NOTEBOOKS]}"


#: Notebooks whose recorded agent runs were made under a harness this build
#: no longer runs, and which have not been re-recorded, with the reason. A
#: replay is keyed to the exact text the agent was sent, so from the first
#: trade on the prompts differ and the replay misses. Each entry is a
#: follow-up, not a pass: it names what has to be re-recorded to remove it.
STALE_RECORDINGS = {
    EXAMPLES / "experiments" / "liquidity-crisis" / "notebook.ipynb": (
        "the liquidity-crisis study replays FinRobot runs recorded under the "
        "0.8.x harness, which counted an agent's fills on every tick of a "
        "step; since 0.8.5 they reach the market once, every price after the "
        "first trade moves, and the recorded decisions stop matching. "
        "Re-record tests/fixtures/finrobot/liquidity-crisis.json (60 calls) "
        "and the four replications in data/ to remove this skip."),
}


def test_a_stale_study_says_so_where_it_says_how_to_re_execute():
    """A study skipped above must not tell its reader the rebuild works.

    At 0.8.5 the liquidity-crisis README carried a banner saying its
    recordings no longer replay, and further down, under "Reading it", the
    old instructions: run `build_notebook.py`, no model call, every decision
    replayed from the fixture. The same page also still named pt-v19 as the
    shipped default. A reader following the instructions got a traceback.
    """
    import re

    import tradefloor as tf
    default = tf.ModelParams.from_preset().fingerprint
    for notebook in STALE_RECORDINGS:
        readme = (notebook.parent / "README.md").read_text(encoding="utf-8")
        assert "## Reading it" in readme, notebook.parent
        section = readme.split("## Reading it", 1)[1].split("\n## ", 1)[0]
        assert "fails" in section, (
            f"{notebook.parent.name}/README.md tells the reader how to "
            f"re-execute a notebook this suite skips as stale, and does not "
            f"say that it fails")
        for preset in re.findall(r"shipped default from \S+ is\s+`(pt-v\d+)`",
                                 readme):
            assert preset == default, (
                f"{notebook.parent.name}/README.md names {preset} as the "
                f"shipped default; it is {default}")


@SLOW
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_notebook_executes_without_error(path):
    if path in STALE_RECORDINGS:
        pytest.skip(STALE_RECORDINGS[path])
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("nbclient")
    from nbclient import NotebookClient

    nb = nbformat.read(path, as_version=4)
    # Run in the notebook's own directory, as a reader would, and never write
    # back: a test that rewrote the committed output would hide the drift it
    # exists to find. `path.parent` rather than `EXAMPLES`, because a study
    # notebook lives one level down and its relative paths are written from
    # where it sits.
    client = NotebookClient(nb, timeout=900, kernel_name="python3",
                            resources={"metadata": {"path": str(path.parent)}})
    client.execute()


@SLOW
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_committed_notebook_carries_its_output(path):
    """A notebook committed with empty cells is a listing, not an example.

    The point of shipping the output is that the page is readable on GitHub
    without a kernel, so an unexecuted commit defeats the reason these exist.
    """
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(path, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert code, f"{path.name} has no code cells"

    empty = [i for i, c in enumerate(code) if not c.get("outputs")]
    assert not empty, (
        f"{path.name}: code cells {empty} carry no output. Regenerate with "
        f"`jupyter nbconvert --to notebook --execute --inplace "
        f"{path.relative_to(EXAMPLES.parent).as_posix()}`"
    )

    errors = [o for c in code for o in c.get("outputs", [])
              if o.get("output_type") == "error"]
    assert not errors, (
        f"{path.name}: committed output contains "
        f"{errors[0].get('ename')} -- the notebook was committed broken"
    )


# -- the scripts -----------------------------------------------------------


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_the_script_at_least_compiles(path):
    """Cheap, and runs by default rather than behind the slow-test flag.

    A rename that missed a reference, or an API that moved under an example,
    shows up here in milliseconds. It is not a claim that the script works
    -- that is the test below -- only that it is still valid Python.
    """
    import ast
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@SLOW
def test_the_research_workflow_runs_end_to_end():
    """The example the README points at, run whole.

    It asserts its own structural gates as it goes -- the TCA fill
    saturation and the pinned-scenario ripple among them -- so a silent
    behavioural change fails here rather than in a reader's terminal.
    """
    import subprocess
    script = EXAMPLES / "07-research-workflow.py"
    if not script.exists():                     # renamed? say so clearly
        pytest.fail(f"{script.name} is missing; examples/ has "
                    f"{[p.name for p in SCRIPTS]}")
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "total" in done.stdout.lower()
    # On pt-v20 the evaluation and the ranking each carry the note that no
    # capture ratio is reported, and until 0.8.5 the script printed both,
    # so the same paragraph appeared twice six lines apart.
    assert done.stdout.count("No capture ratio on") <= 1, done.stdout


def test_the_forking_demo_runs_end_to_end():
    """The forking demo, run whole, and NOT behind the slow flag.

    It takes about two seconds, and what it checks -- that a fork starts where
    its source stood, carries its source's history, does not reach its source
    or its siblings, and replays from the checkpoint -- is the guarantee the
    library makes about experiments. Something that central should be checked
    on every run rather than when someone remembers a flag.

    It asserts its own gates and exits non-zero if any fails, so this reads
    the return code and then confirms the summary line, because a script that
    printed FAIL and exited zero would be the more dangerous failure.
    """
    import subprocess
    script = EXAMPLES / "10-forking-a-market.py"
    if not script.exists():
        pytest.fail(f"{script.name} is missing; examples/ has "
                    f"{[p.name for p in SCRIPTS]}")
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "fork test          PASS" in done.stdout, done.stdout[-2000:]
    assert "FAIL" not in done.stdout


def test_the_scenario_fork_runs_end_to_end():
    """Example 11, run whole, and NOT behind the slow flag.

    It takes about two seconds. At 0.8.5 it had stopped working with
    nothing noticing: `liquidity_crisis.yml` gained an earnings ramp that
    runs to day 175, the script took its reading days from the last day of
    ANY intervention, both fell past the end of its eighty-day loop, and
    it died on a TypeError after printing half its report. The only check
    on it was the syntax check above, which that passes.

    Same reading as the forking demo: the return code, then the summary
    line, then no FAIL anywhere.
    """
    import subprocess
    script = EXAMPLES / "11-scenario-fork.py"
    if not script.exists():
        pytest.fail(f"{script.name} is missing; examples/ has "
                    f"{[p.name for p in SCRIPTS]}")
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "scenario fork      PASS" in done.stdout, done.stdout[-2000:]
    assert "FAIL" not in done.stdout


#: Who runs each script under `examples/`, as `file::test`, or why nothing
#: does. The README says the test suite runs the examples, and until 0.8.5
#: the syntax check above was all that touched `11-scenario-fork.py`, which
#: is how it could crash on every run and still pass. Every script has an
#: entry here, and the test below fails on one that arrives without one.
RUN_BY: dict[str, str] = {
    "07-research-workflow.py":
        "test_examples.py::test_the_research_workflow_runs_end_to_end",
    "08-claude-agent.py":
        "test_examples.py::"
        "test_the_claude_example_refuses_when_every_decision_fails",
    "10-forking-a-market.py":
        "test_examples.py::test_the_forking_demo_runs_end_to_end",
    "11-scenario-fork.py":
        "test_examples.py::test_the_scenario_fork_runs_end_to_end",
    "experiments/liquidity-crisis/experiment.py":
        "test_examples.py::"
        "test_the_liquidity_crisis_study_replays_its_recording",
    "integrations/callable/five_days.py":
        "test_integration_examples.py::test_the_table_matches_a_real_run",
    "integrations/finrobot/rate_shock.py":
        "test_finrobot.py::test_the_recorded_run_replays_end_to_end",
    "integrations/langgraph/rate_shock.py":
        "test_integration_examples.py::test_the_table_matches_a_real_run",
    "integrations/openai_agents/five_days.py":
        "test_integration_examples.py::test_the_table_matches_a_real_run",
    "integrations/pydantic_ai/rate_shock.py":
        "test_integration_examples.py::test_the_table_matches_a_real_run",
    "rate-shock/counterfactual.py":
        "test_rate_shock_demo.py::"
        "test_the_demo_runs_end_to_end_and_writes_readable_artifacts",
}

#: Scripts nothing executes on purpose, with the reason.
NOT_RUN: dict[str, str] = {
    "experiments/liquidity-crisis/build_notebook.py":
        "rewrites the committed notebook; the notebook test executes the "
        "same cells without writing",
    "experiments/liquidity-crisis/charts.py":
        "a module of figures the study notebook imports",
    "rate-shock/agent.py":
        "the agent counterfactual.py imports and runs",
}


def test_every_example_script_is_run_by_a_named_test():
    """The guard on the claim that the suite runs the examples.

    A new script with no entry fails here, and so does an entry whose test
    has been renamed away or no longer mentions the script, because either
    would leave the claim true on paper and the script unchecked.
    """
    on_disk = {p.relative_to(EXAMPLES).as_posix() for p in SCRIPTS}
    assert on_disk == set(RUN_BY) | set(NOT_RUN), (
        f"no entry for {sorted(on_disk - set(RUN_BY) - set(NOT_RUN))}; "
        f"stale entries {sorted((set(RUN_BY) | set(NOT_RUN)) - on_disk)}. "
        "Name the test that runs the script in RUN_BY, or say in NOT_RUN "
        "why nothing does.")
    assert not set(RUN_BY) & set(NOT_RUN)
    tests = Path(__file__).resolve().parent
    for script, where in RUN_BY.items():
        module, name = where.split("::")
        source = (tests / module).read_text(encoding="utf-8")
        assert f"def {name}(" in source, f"{script}: no {where}"
        assert Path(script).name in source, (
            f"{script}: {module} never names {Path(script).name}")


def test_the_claude_example_refuses_when_every_decision_fails():
    """A run that never happened must not be presented as a result.

    When no model is reachable every decision fails, and the leaderboard
    would report claude at zero pnl and no explanation accuracy -- which a
    reader cannot tell apart from a genuinely flat agent. What is checked is
    that the example says so instead, in a sentence naming the fix rather
    than a traceback, and that it prints no table at all.

    The child environment is pinned so this can never reach a provider:
    both credential variables are removed AND the base URL points at a
    closed local port. Removing the variables alone is not enough -- the
    SDK also resolves a stored `ant auth login` profile, which on a
    developer machine would turn this test into twenty billed calls.
    """
    import os
    import subprocess
    script = EXAMPLES / "08-claude-agent.py"
    if not script.exists():
        pytest.skip(f"{script.name} not present")
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=300, env=env)
    combined = done.stdout + done.stderr
    tail = combined[-1500:]
    assert done.returncode != 0, f"expected a non-zero exit, got 0. Output: {tail}"
    assert "tradefloor[claude]" in combined or "ANTHROPIC_API_KEY" in combined, (
        f"expected a refusal naming the extra or the variable. Output: {tail}")
    assert "Traceback" not in combined, f"refused with a traceback. Output: {tail}"
    assert "why-right" not in combined, (
        f"printed the leaderboard for a run Claude was never reached in. Output: {tail}")


def test_the_liquidity_crisis_study_replays_its_recording():
    """The study's first shared day, replayed from its fixture, NOT behind
    the slow flag.

    A replay is keyed to the exact text the agent was sent, so anything that
    moves a day-zero price makes every recorded decision miss. Under
    `on_refusal="skip"` a miss is counted and the run goes on, so the market
    runs, the agent never trades, and nothing fails until the notebook looks
    for a decision at the fork. That is how the study stopped replaying at
    0.7.0 without anyone seeing it: `edgar.to_instruments` began pricing
    under the shipped default rather than under the preset the study pins,
    and only the opt-in notebook run could notice.

    One day is one decision and a fraction of a second, and it is enough:
    a mispriced universe misses on the first one.
    """
    import importlib.util
    study = EXAMPLES / "experiments" / "liquidity-crisis"
    spec = importlib.util.spec_from_file_location(
        "liquidity_crisis_experiment", study / "experiment.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)

    from tradefloor.counterfactual import World
    from tradefloor.integrations.finrobot import FinRobotAdapter, Transcript

    small = ex.subset(ex.load_snapshot())
    agent = FinRobotAdapter(
        mode="replay", transcript=Transcript.load(ex.FIXTURE),
        fundamentals=ex.fundamentals(small), objective=ex.OBJECTIVE,
        every=ex.DECISION_EVERY, arm="shared")
    world = World(seed=ex.SEED, universe=ex.universe(small), agent=agent,
                  pins=ex.BASE_PINS, cash=ex.CASH,
                  steps_per_day=ex.STEPS_PER_DAY,
                  ticks_per_step=ex.TICKS_PER_STEP,
                  model=ex.PRESET, label="shared", on_refusal="skip")
    world.run(days=1)

    missed = [row["unusable"] for row in world.trace if row.get("unusable")]
    assert not missed, missed[0][:300]
    assert [e["step"] for e in agent.record] == list(
        range(0, ex.STEPS_PER_DAY, ex.DECISION_EVERY))
