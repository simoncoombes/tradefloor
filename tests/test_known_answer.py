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
    others -- which is exactly the failure the libm-only design exists to
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
    completely uncovered, and it was broken: it called ``pretium.version()``,
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
    # The gate greps exactly one digest out of this output and compares it
    # across platforms. Two would make the comparison ambiguous; none would
    # make it vacuous.
    assert len(digests) == 1, result.stdout
    assert digests[0] == known_answer.known_answer_digest()
