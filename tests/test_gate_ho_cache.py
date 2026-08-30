"""The held-out rows are measured once per model, not once per block.

Issue #82. `gate_batch` gates a candidate on several axes; two of them,
`ho_seeds` and `ho_universe`, do not depend on `--seed-start`. A campaign
that runs twenty-six blocks therefore measured them twenty-six times and got
the same answer each time.

These tests reach the cache directly rather than through a gate run, because
a gate run measures markets and takes minutes. What is checked here is the
part that can be wrong quietly: which key the rows are stored under, and
whether a second candidate's rows displace the first.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

CAL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "calibration"
sys.path.insert(0, str(CAL))

gate_batch = pytest.importorskip("gate_batch")


def test_a_missing_or_unset_cache_reads_as_empty(tmp_path):
    """Both are the first block of a campaign, and neither is an error."""
    assert gate_batch.ho_cache_read(None) == {}
    assert gate_batch.ho_cache_read(str(tmp_path / "absent.json")) == {}


def test_what_is_written_comes_back(tmp_path):
    f = tmp_path / "ho.json"
    rows = {"fp-a": {"ho_seeds": [{"seed": 1}], "ho_universe": [{"seed": 1}]}}
    gate_batch.ho_cache_write(str(f), {}, rows)
    assert gate_batch.ho_cache_read(str(f)) == rows


def test_a_second_model_is_added_rather_than_replacing_the_first(tmp_path):
    """The failure this guards.

    A campaign gates several candidates across several blocks. If the write
    replaced the file with whichever candidate ran last, every block after
    the first would re-measure everything except one model, and the saving
    the cache exists for would quietly not happen.
    """
    f = tmp_path / "ho.json"
    first = {"fp-a": {"ho_seeds": [{"seed": 1}]}}
    gate_batch.ho_cache_write(str(f), {}, first)

    cached = gate_batch.ho_cache_read(str(f))
    gate_batch.ho_cache_write(str(f), cached, {"fp-b": {"ho_seeds": [{"seed": 2}]}})

    both = gate_batch.ho_cache_read(str(f))
    assert set(both) == {"fp-a", "fp-b"}
    assert both["fp-a"] == first["fp-a"], "the first model's rows were lost"


def test_a_model_that_moved_does_not_read_the_old_rows(tmp_path):
    """Keyed by fingerprint, so a coefficient change misses the cache.

    The dangerous shape is a label reused for different coefficients: the
    rows would be reused for a model that never produced them, and the gate
    would report a held-out verdict measured on something else.
    """
    f = tmp_path / "ho.json"
    gate_batch.ho_cache_write(str(f), {}, {"fp-old": {"ho_seeds": [{"seed": 1}]}})
    cached = gate_batch.ho_cache_read(str(f))
    assert cached.get("fp-new") is None


def test_the_cached_kinds_are_the_ones_that_ignore_the_block_seed():
    """If a third axis stops depending on the block, it belongs here too.

    Equally, an axis that starts depending on it must leave, or a campaign
    reuses a number that should have moved.
    """
    assert gate_batch.HO_KINDS == ("ho_seeds", "ho_universe")
    for kind in gate_batch.HO_KINDS:
        assert kind in gate_batch.KINDS


def test_the_file_is_json_a_human_can_read(tmp_path):
    """A cache nobody can inspect is a cache nobody will believe."""
    f = tmp_path / "ho.json"
    gate_batch.ho_cache_write(str(f), {}, {"fp": {"ho_seeds": [{"seed": 1}]}})
    text = f.read_text(encoding="utf-8")
    assert "\n" in text, "written as one line; indent=1 was dropped"
    assert json.loads(text)
