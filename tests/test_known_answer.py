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
    # THREE digests, in a fixed order: combined, simulation, metadata. The CI
    # gate greps all of them and compares the SET across platforms, so the
    # count and the order are both contractual -- .github/workflows/
    # determinism.yml hashes each target's file and requires one unique hash.
    #
    # It used to be exactly one, and the count was asserted for the same
    # reason it is asserted now: a gate that greps an ambiguous number of
    # digests is comparing something nobody specified. When the digest split
    # landed, this test and that workflow had to move together -- leaving the
    # workflow alone would have made it count three digests as three
    # disagreements and fail every green run.
    assert len(digests) == 3, result.stdout
    assert digests[0] == known_answer.known_answer_digest()
    assert digests[1] == known_answer.simulation_digest()
    assert digests[2] == known_answer.metadata_digest()
