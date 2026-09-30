"""The scorecards `examples/integrations/README.md` prints must be real.

That page carried a table of numbers for a while with nothing running it. It
claimed all four examples printed one identical scorecard, which was true on
the day it was written and stopped being true when two of the examples grew
their own book size and one grew its own roster. Nothing said so, because
nothing executed the page.

The claim was worse than a stale figure. It told the reader that a difference
between those numbers would mean the harness was leaking into the comparison,
so a reader running the four commands would have drawn a conclusion about
this library's integrity from a table that was simply out of date. A number
that teaches somebody to misread the evidence costs more than one they can
ignore.

So the table is executed. Every row is checked against a real run, and the
set of rows is checked against the set of examples, because a table that
silently stopped covering an example would fail the same way again.

The examples take a few seconds each. That is cheap enough to run on every
pass rather than behind the slow-test flag, which is where the guard for the
notebooks lives and is why nobody sees it.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples" / "integrations"
README = EXAMPLES / "README.md"

#: The framework each example needs, or None where the adapter ships with the
#: package. Named rather than inferred, so an example added without a thought
#: about its dependency fails the coverage test below.
NEEDS: dict[str, str | None] = {
    "callable/five_days.py": None,
    "openai_agents/five_days.py": "agents",
    "pydantic_ai/rate_shock.py": "pydantic_ai",
    "langgraph/rate_shock.py": "langgraph",
}

#: FinRobot is documented by its own page and its own rate-shock study
#: rather than by the scorecard table, so it is not a row here. Named
#: rather than inferred, so a sixth integration cannot slip in unseen.
NOT_IN_THE_TABLE = {"finrobot"}

#: A row of the scorecard table: the linked file name, then trades, return
#: and impact exactly as the examples print them.
ROW = re.compile(
    r"^\|\s*\[`(?P<file>[a-z_]+/[a-z_]+\.py)`\]\([^)]*\)\s*\|"
    r"\s*(?P<trades>\d+)\s*\|"
    r"\s*(?P<ret>[-+][0-9.]+)%\s*\|"
    r"\s*(?P<impact>[-+][0-9.]+) bps\s*\|\s*$",
    re.M,
)

#: The three lines of the printed scorecard the table quotes.
PRINTED = {
    "trades": re.compile(r"^trades\s+(\d+)\s*$", re.M),
    "ret": re.compile(r"^return\s+([-+][0-9.]+)%\s*$", re.M),
    "impact": re.compile(r"^impact\s+([-+][0-9.]+) bps\s*$", re.M),
}


def table() -> dict[str, dict[str, str]]:
    text = README.read_text(encoding="utf-8")
    return {m.group("file"): {k: m.group(k)
                              for k in ("trades", "ret", "impact")}
            for m in ROW.finditer(text)}


def test_the_table_covers_every_example():
    """A row per example, and an example per row.

    An example added with no row would be undocumented, and a row naming an
    example that no longer exists would be checked against nothing. Both are
    the shape where a guard keeps reporting green over a smaller subject.
    """
    on_disk = {f"{p.parent.name}/{p.name}"
               for p in EXAMPLES.glob("*/*.py")
               if p.parent.name not in NOT_IN_THE_TABLE}
    assert on_disk == set(NEEDS), (
        f"examples/integrations holds {sorted(on_disk)}, this file expects "
        f"{sorted(NEEDS)}. Add the new example to NEEDS and to the scorecard "
        "table in examples/integrations/README.md."
    )
    assert set(table()) == on_disk, (
        f"the README scorecard table covers {sorted(table())}, the directory "
        f"holds {sorted(on_disk)}"
    )


@pytest.mark.parametrize("name", sorted(NEEDS), ids=lambda n: n[:-3])
def test_the_table_matches_a_real_run(name: str):
    """Run the example the way a reader runs it, and read its scorecard back.

    With the API keys stripped from the environment, so the page's other
    claim -- that these need no key, no provider account and no network -- is
    checked by the same run rather than asserted beside it.
    """
    framework = NEEDS[name]
    if framework:
        pytest.importorskip(
            framework, reason=f"{name} needs the {framework} extra")

    claimed = table().get(name)
    assert claimed, f"{name} has no row in the README scorecard table"

    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    done = subprocess.run([sys.executable, str(EXAMPLES / name)],
                          capture_output=True, text=True, timeout=600,
                          env=env)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]

    for field, pattern in PRINTED.items():
        found = pattern.search(done.stdout)
        assert found, (
            f"{name} printed no {field} line this could read:\n"
            f"{done.stdout[-1500:]}"
        )
        assert found.group(1) == claimed[field], (
            f"{name} printed {field} {found.group(1)}, and "
            f"examples/integrations/README.md claims {claimed[field]}. "
            "Update the table in that README, or the example, so the page "
            "states what the code does."
        )


# -- a copy outside the repository -------------------------------------------
#
# A pip user who copies an example into their own project has the library and
# not the repository. The two examples below used to look for the
# repository's `pyproject.toml` at import, so a copy raised "no pyproject.toml
# above this file" before running a line, even where the run it was asked for
# never reads a recording. The recordings live in `tests/fixtures/` and are
# not installed, so the paths that need one must say that, and every other
# path must run.
#
# `openai_agents/five_days.py`, `pydantic_ai/rate_shock.py` and
# `langgraph/rate_shock.py` still resolve their fixture at import. Adding one
# here is the check that it has been fixed.
COPY_SAFE = ("callable/five_days.py", "finrobot/rate_shock.py")


def _copied(name: str, tmp_path: pathlib.Path) -> pathlib.Path:
    """The example alone, in a folder with nothing of the repository above
    it. `tmp_path` sits under the system temp directory, which no checkout
    contains."""
    target = tmp_path / "my-project" / pathlib.Path(name).name
    if REPO in target.parents:
        pytest.skip("the temp directory is inside the checkout (--basetemp?), "
                    "so a copy there is not outside the repository")
    target.parent.mkdir(parents=True)
    target.write_bytes((EXAMPLES / name).read_bytes())
    return target


def _load(path: pathlib.Path, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", COPY_SAFE, ids=lambda n: n[:-3])
def test_a_copied_example_imports_and_names_where_its_recording_lives(
        name: str, tmp_path: pathlib.Path):
    """Importing a copy works; asking it for its recording names the place.

    The message has to send the reader somewhere: the repository path of
    the recording, and the clone that has it.
    """
    copy = _load(_copied(name, tmp_path), "copied_" + name.replace("/", "_")[:-3])
    with pytest.raises(FileNotFoundError) as refused:
        copy.fixture_path()
    assert "tests/fixtures/" in str(refused.value)
    assert "github.com/simoncoombes/tradefloor" in str(refused.value)

    # In the checkout the same lookup finds the committed file, so the
    # notebooks' `example.FIXTURE` reads what it always read.
    here = _load(EXAMPLES / name, "in_repo_" + name.replace("/", "_")[:-3])
    assert here.FIXTURE == here.fixture_path()
    assert here.FIXTURE.is_file()
    assert here.FIXTURE.relative_to(REPO).parts[:2] == ("tests", "fixtures")


def test_a_copied_callable_example_runs_whole(tmp_path: pathlib.Path):
    """The repro from the 0.8.5 user review: copy the file out and run it.

    `main()` never reads the recording, so the copy must run to the end and
    print the same scorecard the table above states.
    """
    copy = _copied("callable/five_days.py", tmp_path)
    done = subprocess.run([sys.executable, str(copy)], capture_output=True,
                          text=True, timeout=600, cwd=tmp_path)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    claimed = table()["callable/five_days.py"]
    found = PRINTED["trades"].search(done.stdout)
    assert found and found.group(1) == claimed["trades"], done.stdout[-1500:]


def test_a_copied_finrobot_study_refuses_readably_without_its_recording(
        tmp_path: pathlib.Path):
    """The default run replays the recording, which the copy does not have.

    It must stop with the message and no traceback, before any market runs.
    """
    copy = _copied("finrobot/rate_shock.py", tmp_path)
    done = subprocess.run([sys.executable, str(copy)], capture_output=True,
                          text=True, timeout=600, cwd=tmp_path)
    combined = done.stdout + done.stderr
    assert done.returncode != 0, combined[-1500:]
    assert "Traceback" not in combined, combined[-3000:]
    assert "tests/fixtures/finrobot/rate-shock.json" in combined, combined
    assert "Shared history" not in combined, "it ran the market first"


# -- the page itself ---------------------------------------------------------


def test_the_page_reaches_usage_before_its_history():
    """How to run and call the adapters first; how the table moved, last.

    The page used to follow its scorecard with about sixty lines on how each
    row had moved across presets and compositions, so a reader looking for
    how to call an adapter read a changelog first. The old rows are kept,
    under the page's last heading.
    """
    text = README.read_text(encoding="utf-8")
    headings = re.findall(r"(?m)^## (.+)$", text)
    assert headings[-1] == "History", headings
    usage = text[:text.index("## Plain Python")]
    old = re.findall(r"Re-measured|pt-v1\d|composition", usage)
    assert not old, f"the usage part of the page still carries history: {old}"


def test_the_page_states_the_order_vocabulary_and_the_exposure_unit():
    """What an agent may send, and what `gross_exposure` is measured in.

    Two questions a reader had to answer from the source. The page said
    there were no limit prices at the boundary while `tf.Limit` said it was
    valid in `act()`, and both are true of different agents. And the
    payload's `gross_exposure` is a multiple of net worth, while
    `Portfolio.gross_exposure` is dollars.
    """
    text = " ".join(README.read_text(encoding="utf-8").split())
    for needed in ("tf.Limit(quantity, price)", "tf.Cancel()",
                   "`parse_decision` refuses", "multiple of net worth"):
        assert needed in text, f"the page no longer says {needed!r}"


def test_the_payload_states_gross_exposure_as_a_multiple():
    """The unit the page states, read off a real payload.

    A rule that buys on day 0 and records what it is shown: on day 1 the
    payload's `gross_exposure` must equal the positions' value over net
    worth, computed from the same payload.
    """
    import tradefloor as tf
    from tradefloor.integrations.callable import callable_agent

    seen = []

    def buy_once(payload: dict) -> dict:
        seen.append(payload)
        if payload["day"] > 0:
            return {"actions": []}
        asset = payload["assets"][0]
        return {"actions": [{"symbol": asset["symbol"], "side": "BUY",
                             "quantity": asset["max_order_shares"]}]}

    agent = callable_agent(buy_once)
    tf.evaluate({"a": agent}, seed=4242,
                universe=tf.Universe.random(3, seed=1), days=2)
    later = seen[-1]
    book = later["portfolio"]
    held = sum(abs(a["position"]) * a["price"] for a in later["assets"])
    assert held > 0, "the rule never bought, so this checks nothing"
    assert book["gross_exposure"] == pytest.approx(held / book["net_worth"],
                                                   rel=1e-9)
