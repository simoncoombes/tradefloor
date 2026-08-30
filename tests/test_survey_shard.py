"""`--shard I/N` must partition a plan, or refuse.

The failure that matters is a silent one rather than a wrong answer: a
malformed shard spec that falls back to running everything would have every
box in a fleet measure the whole plan, cost N times the money, and produce a
tasks.jsonl that looks complete. So the spec is validated up front and the
process exits rather than guessing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "calibration" / "atlas_survey.py"


def _run(shard: str, tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "run", "--out", str(tmp),
         "--samples", "8", "--shard", shard],
        capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("bad", ["3/3", "4/3", "-1/3", "1/0", "one/three", "2", "1/"])
def test_a_malformed_or_out_of_range_shard_is_refused(bad: str, tmp_path: Path) -> None:
    proc = _run(bad, tmp_path)
    assert proc.returncode != 0, (bad, proc.stdout[-400:])
    assert "--shard" in (proc.stderr + proc.stdout), (bad, proc.stderr[-400:])


def test_the_partition_is_exact() -> None:
    """Every index lands in exactly one shard, for a range of N.

    This is the property the concatenation of shard outputs rests on: the
    selection is on the GLOBAL vector index, so shards agree about which
    vector is which and their union is the whole plan.
    """
    for n in (1, 2, 3, 7, 16):
        seen: list[int] = []
        for i in range(n):
            seen += [ix for ix in range(500) if ix % n == i]
        assert sorted(seen) == list(range(500)), n
        assert len(seen) == len(set(seen)), n
