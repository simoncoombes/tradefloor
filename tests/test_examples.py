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


# -- the Claude example, without a model ------------------------------------
#
# Every check above that touches `08-claude-agent.py` runs it with no model
# reachable. These load it as a module and drive `ClaudeTrader` with a stand-in
# client, so what it offers Claude and when it asks are checked on every run
# without a key or a bill.


def _load_claude_example(monkeypatch):
    pytest.importorskip("anthropic")
    pytest.importorskip("pydantic")
    import importlib.util
    script = EXAMPLES / "08-claude-agent.py"
    spec = importlib.util.spec_from_file_location("claude_example", script)
    module = importlib.util.module_from_spec(spec)
    # Registered first so pydantic can resolve the `Factor` annotation, which
    # `from __future__ import annotations` leaves as a string.
    monkeypatch.setitem(sys.modules, "claude_example", module)
    spec.loader.exec_module(module)
    return module


def test_the_claude_example_offers_every_factor_the_harness_scores(monkeypatch):
    """The answer Claude may give is the list `evaluate` scores against.

    It was a list typed out in the example, and it fell behind the harness
    three times. The last time it missed `fair_value_shift`, which pt-v20
    scores as the day's answer on about two days in five of the example's
    market, so Claude was marked wrong on days it could not give the right
    answer. The schema Claude is handed and the prompt it reads must both
    carry every name.
    """
    import typing
    from tradefloor.harness import FACTOR_NAMES

    ex = _load_claude_example(monkeypatch)
    assert typing.get_args(ex.Factor) == FACTOR_NAMES
    schema = ex.Decision.model_json_schema()["properties"]["driver"]
    assert schema["enum"] == list(FACTOR_NAMES)
    missing = [name for name in FACTOR_NAMES if name not in ex.SYSTEM]
    assert not missing, f"the system prompt never names {missing}"


class _StandInMessages:
    """`client.messages` for `ClaudeTrader`: one scripted answer per call.

    An answer of None raises, the way an unreachable provider does.
    """

    def __init__(self, ex, answers):
        self.ex, self.answers, self.calls = ex, answers, 0

    def parse(self, **kwargs):
        import types
        driver = self.answers[self.calls]
        self.calls += 1
        if driver is None:
            raise ConnectionError("provider unreachable")
        decision = self.ex.Decision(weights={}, driver=driver,
                                    reasoning="stand-in")
        return types.SimpleNamespace(stop_reason="end_turn",
                                     parsed_output=decision)


def test_the_claude_example_names_the_driver_of_the_day_it_is_scored_on(
        monkeypatch):
    """Asked on the day's last step, and scored on that day only.

    `evaluate` calls `explain(day)` after the day's last step and checks the
    answer against that day's attribution. The example used to ask Claude at
    the open, when all it could see was yesterday's moves, so its answer was
    about one day and scored on the next. A day whose call failed also
    reported the previous day's answer, which scored a stale guess as a new
    one. Here every call must land on a last step, each scored answer must
    be the one given that day, and the failed day must go unscored.
    """
    import types
    import tradefloor as tf

    ex = _load_claude_example(monkeypatch)
    answers = ["fair_value_shift", None, "random_noise"]
    messages = _StandInMessages(ex, answers)
    trader = ex.ClaudeTrader(client=types.SimpleNamespace(messages=messages))
    asked_at = []

    class Watched:
        def act(self, obs):
            before = messages.calls
            try:
                return trader.act(obs)
            finally:
                if messages.calls > before:
                    asked_at.append((obs.day, obs.step_of_day))

        def explain(self, day):
            return trader.explain(day)

    card = tf.evaluate({"claude": Watched()}, seed=2026,
                       universe=tf.Universe.random(4, seed=7),
                       days=len(answers))["claude"]

    last = 5                         # the harness default, six steps a day
    assert asked_at == [(day, last) for day in range(len(answers))], asked_at
    assert [claimed for claimed, _ in card.explanations] == [
        a for a in answers if a is not None], card.explanations
    assert len(card.errors) == 1 and "provider unreachable" in card.errors[0]


# -- what the pages say about the runs ---------------------------------------


def _flat(page: str) -> str:
    return " ".join((EXAMPLES.parent / page).read_text(encoding="utf-8").split())


def test_the_liquidity_crisis_study_names_the_default_it_is_not_on():
    """The study pins pt-v16, and says which preset it is NOT on.

    It said the shipped default was pt-v19 through 0.8.5, a release whose
    default is pt-v20. The sentence exists to tell a reader why the study's
    numbers differ from a fresh run, so a stale name sends them to the wrong
    preset to compare against.
    """
    import re
    import tradefloor as tf
    default = tf.ModelParams.from_preset().fingerprint
    for page, pattern in (
            ("examples/experiments/liquidity-crisis/README.md",
             r"shipped default from [0-9.]+ is `(pt-v\d+)`"),
            ("examples/experiments/liquidity-crisis/build_notebook.py",
             r"it is `(pt-v\d+)` from [0-9.]+"),
            ("examples/experiments/liquidity-crisis/notebook.ipynb",
             r"it is `(pt-v\d+)` from [0-9.]+")):
        named = re.findall(pattern, _flat(page))
        assert named == [default], (
            f"{page} names the shipped default as {named}; it is {default}")


#: What each page says a run costs: CPU time, user plus system, measured with
#: /usr/bin/time on the release venv at 0.8.5 and rounded up. The measured
#: figures on a loaded 10-core Mac were 7 to 9 s for the rate-shock demo
#: (reviewers saw 6 to 8), 85 to 114 s for 07, and about 4.5 s for the
#: FinRobot replay. About 0.7 s of each is every pt-v20 engine the run builds.
#: Before this, the pages gave the rate-shock demo "about a second" in one
#: place and "two seconds" in another, and 07 "ten to twenty seconds".
CPU_CLAIMS = {
    "examples/README.md": ("under ten seconds of CPU",
                           "07-research-workflow.py` takes about two minutes",
                           "rate_shock.py` takes about five seconds"),
    "examples/rate-shock/README.md": ("under ten seconds of CPU",),
    "examples/rate-shock/counterfactual.py": ("under ten seconds of CPU",),
    "examples/07-research-workflow.py": ("about two minutes of CPU",),
    "examples/integrations/finrobot/README.md": ("about five seconds of CPU",),
}

#: The figures those pages carried before, each of which understated the run.
STALE_TIMINGS = ("in about a second", "runs in about a second",
                 "Two seconds, no keys", "ten to twenty seconds",
                 "each run in about a second")


@pytest.mark.parametrize("page", sorted(CPU_CLAIMS))
def test_the_pages_agree_on_what_a_run_costs(page):
    """One figure per run, in CPU time, on every page that states one.

    A wall-clock figure depends on the machine and on what else it is
    doing, and the reviewers who found these ran on a machine shared with
    dozens of other jobs. CPU time is the figure a reader can check with
    /usr/bin/time wherever they are.
    """
    text = _flat(page)
    missing = [claim for claim in CPU_CLAIMS[page] if claim not in text]
    assert not missing, f"{page} no longer says {missing}"
    stale = [claim for claim in STALE_TIMINGS if claim in text]
    assert not stale, f"{page} still says {stale}"
