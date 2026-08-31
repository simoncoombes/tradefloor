"""The committed PydanticAI recording, replayed WITHOUT PydanticAI.

Separate from `tests/test_pydantic_ai.py` for one reason, and it is the
reason the file exists: that module guards itself with
`pytest.importorskip("pydantic_ai")`, because a bare `from pydantic_ai
import ...` at module scope turns a missing optional extra into a collection
error and pytest then refuses to run the whole suite. Correct -- and it means
every test in that file, including the ones that replay a recording, skips on
an install without the extra.

Replaying needs no framework. `PydanticAIAdapter(mode="replay", ...)` is
constructed with no agent at all and reads recorded responses out of a
`Transcript`, so gating those tests behind an import they do not use is
stricter than the code requires, and it leaves the 44 KB committed artefact
uncovered for exactly the contributor the `importorskip` was written for --
someone running the suite on `maturin, pytest, pyarrow, numpy, pyyaml`, which
is what `CONTRIBUTING.md` documents.

`tests/test_integrations.py` covers the shared half on a bare install: every
fixture loads, its meta carries the provenance core, its digests are unique
and non-empty, and every recorded response parses. What it deliberately does
not do is re-run each adapter's world, because the seeds and rosters belong
to the examples. So the residue is the end-to-end replay -- a digest that is
well-formed but WRONG, which loads fine, parses fine, and then cannot be
found when the market asks for it. That is this file.

**Nothing here may import `pydantic_ai`.** A test at the bottom asserts it,
because the property is invisible until someone runs the suite without the
extra, and by then the failure is a collection error across ~2,200 tests.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World, agree
from tradefloor.integrations import common as ci
from tradefloor.integrations.pydantic_ai import (MANDATE, MANDATE_VERSION,
                                                 PydanticAIAdapter)

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "pydantic_ai" / "rate-shock.json"
EXAMPLE = REPO / "examples" / "integrations" / "pydantic_ai_agent.py"

needs_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="no recorded model run at tests/fixtures/pydantic_ai/")


def _load_example():
    """The shipped example, for its seed, roster and fork settings.

    Imported rather than restated: the experiment's constants live in one
    place, and a second copy here would be a second experiment wearing this
    one's name. The example's own framework imports are guarded, so this
    works with the extra absent -- which is the whole point of this file.
    """
    sys.path.insert(0, str(EXAMPLE.parent))
    try:
        return importlib.import_module("pydantic_ai_agent")
    finally:
        sys.path.remove(str(EXAMPLE.parent))


@needs_fixture
def test_the_recorded_run_replays_end_to_end():
    """The whole recorded experiment: shared history, fork, one intervention,
    both arms.

    The full fork and not just the shared days, because the recording holds
    all three phases -- five shared decisions and five per arm -- and a test
    running only the first third would leave two thirds of the artefact
    unread while reporting it covered.

    No agent, no key, no network: `mode="replay"` is constructed without one,
    so a call reaching the framework would fail on `self.agent` being None
    rather than quietly succeeding.
    """
    example = _load_example()
    transcript = ci.Transcript.load(FIXTURE)

    agent = PydanticAIAdapter(mode="replay", transcript=transcript,
                              every=example.DECISION_EVERY, arm="shared")
    assert agent.agent is None, "a replay must need no framework object"

    world = World(seed=example.SEED, universe=example.universe(),
                  agent=agent, cash=example.CASH, pins=example.PINS)
    world.run(days=example.SHARED_DAYS)
    assert len(agent.record) == example.SHARED_DAYS

    control, shock = world.fork("control", "+200bps")
    control.agent.arm, shock.agent.arm = "control", "+200bps"
    assert bool(agree(control, shock)), "the arms diverged before the shock"

    shock.intervene(federal_funds_rate=example.SHOCKED_POLICY_RATE,
                    corporate_bond_yield=example.SHOCKED_DISCOUNT_RATE)
    control.run(days=example.BRANCH_DAYS)
    shock.run(days=example.BRANCH_DAYS)

    # Every recorded interaction was consumed: five shared, five per arm.
    expected = example.SHARED_DAYS + 2 * example.BRANCH_DAYS
    assert len(transcript) == expected, len(transcript)
    for arm in (control, shock):
        assert len(arm.agent.record) == \
            example.SHARED_DAYS + example.BRANCH_DAYS
        assert not arm.rejected, arm.rejected

    # The intervention reached one arm and the decisions actually differ, or
    # the recording describes an experiment with nothing in it.
    assert control.agent.record[-1]["decision"] \
        != shock.agent.record[-1]["decision"]


@needs_fixture
def test_the_committed_fixture_still_matches_the_shipped_mandate():
    """The last mile of the replay guard, asserted against the real artefact.

    The guard refuses a replay whose recording ran under other instructions.
    This asserts the committed fixture is not that case: the mandate on disk
    today is the one that produced the recording. It catches a mandate edit
    that silently invalidated the fixture, which no amount of checking the
    observation keys would notice -- the instructions travel separately from
    the keyed input.

    Each extraction is asserted non-empty BEFORE anything is compared. The
    obvious way to write this compares two `.get()` results and passes
    vacuously when both are None, which is how the equivalent check on
    another adapter reported "unchanged: True" out of three failed lookups.
    """
    meta = ci.Transcript.load(FIXTURE).meta
    recorded = meta.get("instructions_digest")
    version = meta.get("instructions_version")
    assert recorded, f"no instructions_digest in the fixture: {sorted(meta)}"
    assert version, f"no instructions_version in the fixture: {sorted(meta)}"

    assert recorded == ci.digest(MANDATE), (
        f"the committed recording ran under instructions_digest {recorded}, "
        f"and MANDATE on disk digests to {ci.digest(MANDATE)}. The mandate "
        "was edited without re-recording, so every replay of this fixture "
        "answers the current instructions with decisions taken under the "
        "old ones. Re-record, or restore the mandate.")
    assert version == MANDATE_VERSION, (
        f"fixture records mandate version {version}, module says "
        f"{MANDATE_VERSION}")

    # The comparison must be able to fail, or its passing means nothing.
    assert ci.digest(MANDATE + " altered") != recorded


@needs_fixture
def test_the_committed_fixture_carries_no_credential():
    """The artefact is committed, so what is in it matters more than what
    `state()` publishes."""
    text = FIXTURE.read_text(encoding="utf-8")
    for secret in ("sk-ant-", "sk-proj-", "lsv2_pt_", "pylf_v1_",
                   "api_key", "Bearer ", "Authorization"):
        assert secret not in text, f"the fixture carries {secret!r}"


def test_this_module_never_imports_the_framework():
    """The property the whole file exists for, pinned.

    Checked against the SOURCE rather than `sys.modules`, because the rest
    of the suite imports PydanticAI and would make a runtime check pass for
    the wrong reason. An import added here would not fail quietly: it would
    raise a collection error and take every test in the repository with it,
    on precisely the machines this file is for.

    Parsed rather than grepped. The first version searched the text for
    "importorskip" and failed on its own docstring, which names the thing it
    forbids -- a check that cannot survive being described is a check that
    will be deleted by whoever writes the next comment. The AST sees calls
    and imports, and is blind to prose.
    """
    import ast

    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))

    imports = []
    skips = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names
                        if a.name.split(".")[0] == "pydantic_ai"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "pydantic_ai":
                imports.append(node.module)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name == "importorskip":
                skips.append(ast.unparse(node))

    assert not imports, (
        f"this module imports the framework: {imports}. Replaying needs "
        "none, and an import here re-gates the coverage behind the extra.")
    assert not skips, (
        f"an importorskip here ({skips}) would skip the coverage on exactly "
        "the bare install it was written to serve")
    assert tf.__version__, "tradefloor itself must import, obviously"
