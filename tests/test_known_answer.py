"""The cross-platform determinism gate.

Deliberately a SEPARATE file from the parity suite, and the reason matters:
`test_parity.py` carries a module-level skipif on the golden corpus being
present. A CI runner building a fresh wheel does not have that corpus -- by
design, it is 135 MB and lives with the reference implementation -- so had
these tests stayed in that file they would have SILENTLY SKIPPED on exactly
the machines the gate exists to check, and the release would have gone green
without ever running it.

This file needs nothing but the installed package.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import known_answer  # noqa: E402
import known_answer_book  # noqa: E402



def test_known_answer_digest_matches_the_committed_baseline():
    """The gate behind the library's headline claim.

    Every wheel target runs this and must produce the same digest. A mismatch
    without a katVersion bump means one platform's build disagrees with the
    others -- which is the failure the libm-only design exists to
    prevent, and which nothing else in the suite would notice, since every
    other test compares this build against itself or against a reference the
    release machine may not have.

    Unlike the parity corpus, this needs nothing but the installed package, so
    it can run inside a fresh wheel on a machine that has never seen the
    reference implementation.
    """
    baseline = json.loads(
        (HERE / "known_answer.json").read_text(encoding="utf-8")
    )
    assert baseline["katVersion"] == known_answer.KAT_VERSION, (
        "KAT_VERSION changed without regenerating known_answer.json. If the "
        "simulation was intentionally changed, regenerate the baseline; if not, "
        "this is the drift the gate exists to catch."
    )
    # The SIMULATION digest is the gate. It covers the trajectory and nothing
    # else, so a mismatch here means either an intended era boundary (which
    # bumps KAT_VERSION) or a platform that disagrees about arithmetic --
    # never a change in what the library reports about itself.
    assert known_answer.simulation_digest() == baseline["simulationSha256"], (
        "the SIMULATION digest moved. Either a trajectory changed and "
        "KAT_VERSION must bump, or a platform disagrees -- which is the "
        "failure this gate exists to catch."
    )
    # The METADATA digest covers `model_preset()`'s report, which runs
    # nothing. It is asserted so a silent change is still caught, but it can
    # be re-based on its own when a reporting bug is fixed, without anyone
    # having to claim the market changed. Before the split, those two cases
    # were indistinguishable and the choice was between two false statements.
    assert known_answer.metadata_digest() == baseline["metadataSha256"], (
        "the reported model preset changed. If that was a deliberate "
        "reporting fix, re-base metadataSha256 alone and leave KAT_VERSION "
        "and simulationSha256 untouched."
    )
    assert known_answer.known_answer_digest() == baseline["sha256"]


def test_a_session_with_the_rate_indices_matches_its_baseline():
    """The simulated rate indices' own digest, beside the three above.

    A separate digest so that adding UST2Y, UST10Y and IGCORP moved none of
    the others: a roster without them is the market it always was. This is
    the claim that a roster with them is one market on every platform too,
    which the determinism workflow checks on each wheel target through this
    file, and across targets through the line `known_answer.py` prints.
    """
    baseline = json.loads(
        (HERE / "known_answer.json").read_text(encoding="utf-8")
    )
    assert known_answer.bonds_digest() == baseline["bondsSha256"], (
        "the rate indices' digest moved. Either their pricing, books or tape "
        "changed and bondsSha256 must be regenerated with the change, or a "
        "platform disagrees."
    )


def test_each_named_preset_matches_its_own_baseline():
    """A preset that is not the default gets its own known answer
    (RELEASING.md), so a change that moved it is caught while the default's
    digest stands still. The same simulation as the default's, under the
    named preset."""
    baseline = json.loads(
        (HERE / "known_answer.json").read_text(encoding="utf-8")
    )
    recorded = baseline.get("presetDigests", {})
    assert sorted(recorded) == sorted(known_answer.PRESET_DIGESTS)
    for name in known_answer.PRESET_DIGESTS:
        assert known_answer.preset_digest(name) == recorded[name], (
            f"{name}'s simulation digest moved: either the preset changed "
            "and its baseline must be regenerated with it, or a platform "
            "disagrees.")


def test_the_book_known_answer_matches_its_baseline():
    """The agent-facing book's gate, beside the market's.

    `known_answer.py`'s digests are set by a market nobody trades, which the
    book's dials cannot move, so that gate is blind to the book. This one
    runs a fixed market with every book dial on and several agents in it
    (`known_answer_book.py` says what it covers) and must agree on every
    platform, for the same reason the other must. It lives in this file so
    the determinism workflow, which runs this file on every wheel target,
    runs it too.
    """
    baseline = json.loads(
        (HERE / "known_answer_book.json").read_text(encoding="utf-8")
    )
    assert baseline["bookKatVersion"] == known_answer_book.BOOK_KAT_VERSION, (
        "BOOK_KAT_VERSION changed without regenerating known_answer_book.json.")
    assert baseline["dials"] == known_answer_book.DIALS
    assert known_answer_book.book_digest() == baseline["sha256"], (
        "the book digest moved. Either the book's behaviour changed and "
        "BOOK_KAT_VERSION must bump, or a platform disagrees.")


def test_known_answer_is_stable_within_a_process():
    assert known_answer.known_answer_digest() == known_answer.known_answer_digest()


def test_known_answer_actually_covers_something():
    """Guard against a gate that passes because it measures nothing.

    A KAT that hashed an empty buffer would be perfectly stable across every
    platform and would prove nothing at all.
    """
    buf = known_answer.known_answer_buffer()
    assert len(buf) % 8 == 0, "buffer is not a whole number of f64 values"
    assert len(buf) > 4000, f"buffer only {len(buf)} bytes - coverage shrank"
    # Not all one value: a buffer of repeated zeros would also be stable.
    assert len(set(buf[i:i + 8] for i in range(0, len(buf), 8))) > 100


def test_the_script_runs_as_the_gate_runs_it(tmp_path):
    """Execute known_answer.py the way CI does: as a script, from elsewhere.

    Everything above imports the module and calls into it. That left the
    ``__main__`` path -- the only path the determinism gate ever takes --
    completely uncovered, and broken: it called ``tradefloor.version()``,
    which the package had never re-exported. Every test passed and the gate
    failed on all five platforms at once.

    Run from tmp_path rather than the repo root, for the same reason CI does:
    from the repo root an importable source tree can satisfy ``import
    pretium`` and the gate would be measuring the checkout instead of the
    wheel it is supposed to certify.
    """
    import re
    import subprocess

    script = Path(__file__).parent / "known_answer.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    digests = re.findall(r"\b[0-9a-f]{64}\b", result.stdout)
    # FOUR digests, in a fixed order: combined, simulation, metadata, and
    # the session with the simulated rate indices. The CI gate greps all of
    # them and compares the SET across platforms, so the count and the order
    # are both contractual -- .github/workflows/determinism.yml hashes each
    # target's file and requires one unique hash, which holds for four as it
    # did for three.
    #
    # It used to be exactly one, and the count was asserted for the same
    # reason it is asserted now: a gate that greps an ambiguous number of
    # digests is comparing something nobody specified. When the digest split
    # landed, this test and that workflow had to move together -- leaving the
    # workflow alone would have made it count three digests as three
    # disagreements and fail every green run.
    # FIVE since pt-v20: the named presets' own simulation digests follow,
    # one line each (known_answer.PRESET_DIGESTS).
    assert len(digests) == 4 + len(known_answer.PRESET_DIGESTS), result.stdout
    assert digests[0] == known_answer.known_answer_digest()
    assert digests[1] == known_answer.simulation_digest()
    assert digests[2] == known_answer.metadata_digest()
    assert digests[3] == known_answer.bonds_digest()
    for k, name in enumerate(known_answer.PRESET_DIGESTS):
        assert digests[4 + k] == known_answer.preset_digest(name)
