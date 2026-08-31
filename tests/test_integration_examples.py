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
