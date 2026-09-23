"""The `long_run` block of a preset record: written, refused, carried, kept.

A record's `long_run` block is the owner's adopted long-run pass bar (design
repo `programme/longrun/CRITERIA.md`), graded by
`programme/longrun/criteria.py --verdict` and written by
`tools/presets/record.py --long-run`. The trading server reads it to say
whether the preset passes. These tests run against copies of a committed
record in a temporary directory; no committed file is touched.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.presets import record as rec_tool  # noqa: E402
from tools.presets import restamp as restamp_tool  # noqa: E402

RECORDS = ROOT / "python" / "tradefloor" / "presets"


def verdict(fingerprint="pt-v19", passes=(True,) * 15):
    rows = [{"id": f"R{i}", "words": "a criterion", "value": 1.0, "real": 1.0,
             "rule": "a rule", "pass": p} for i, p in enumerate(passes)]
    n = sum(passes)
    return {"criteria": "programme/longrun/CRITERIA.md (adopted 2026-09-23)",
            "verdict": "pass" if n == len(rows) else "fail", "passed": n, "of": len(rows),
            "rows": rows,
            "measured": {"engine_commit": "abc", "seeds": 30, "years": 21, "box": "test",
                         "date": "2026-09-23", "fingerprint": fingerprint}}


@pytest.fixture
def out(tmp_path, monkeypatch):
    for name in ("pt-v18", "pt-v19"):
        (tmp_path / f"{name}.json").write_bytes((RECORDS / f"{name}.json").read_bytes())
    monkeypatch.setattr(rec_tool, "OUT", tmp_path)
    return tmp_path


def _write(tmp_path, doc):
    # Beside the records, not among them: restamp.py reads every *.json there.
    (tmp_path / "in").mkdir(exist_ok=True)
    p = tmp_path / "in" / "verdict.json"
    p.write_text(json.dumps(doc))
    return str(p)


def test_the_verdict_lands_on_the_record_it_names_and_nothing_else_moves(out):
    before = json.loads((out / "pt-v19.json").read_text())
    assert rec_tool.write_long_run(_write(out, verdict())) == 0
    after = json.loads((out / "pt-v19.json").read_text())
    block = after.pop("long_run")
    assert after == before
    assert block["verdict"] == "pass" and block["passed"] == block["of"] == 15
    assert block["coefficients"] == before["coefficients"]
    assert "long_run" not in json.loads((out / "pt-v18.json").read_text())


@pytest.mark.parametrize("doc, why", [
    (verdict(fingerprint="custom-992de165"), "not a named"),
    (dict(verdict(), passed=14), "do not add up"),
    (dict(verdict(), verdict="fail"), "where the rows say"),
    (verdict(fingerprint="pt-v99"), "no committed record"),
])
def test_a_verdict_that_does_not_describe_a_named_record_is_refused(out, capsys, doc, why):
    was = (out / "pt-v19.json").read_text()
    assert rec_tool.write_long_run(_write(out, doc)) == 1
    assert why in capsys.readouterr().err
    assert (out / "pt-v19.json").read_text() == was


def test_a_rebuild_carries_the_block_while_the_values_stand_and_drops_it_when_they_move(out):
    assert rec_tool.write_long_run(_write(out, verdict(passes=(True,) * 7 + (False,) * 8))) == 0
    path = out / "pt-v19.json"
    rebuilt = json.loads(path.read_text())
    rebuilt.pop("long_run")
    assert "carried" in rec_tool.carry_long_run(rebuilt, path)
    assert rebuilt["long_run"]["verdict"] == "fail"

    moved = json.loads(path.read_text())
    moved.pop("long_run")
    moved["coefficients"] = dict(moved["coefficients"], vix_anchor_weight=0.375)
    assert "DROPPED" in rec_tool.carry_long_run(moved, path)
    assert "long_run" not in moved


def test_a_restamp_keeps_the_block(out, monkeypatch):
    """restamp.py rewrites the coefficient vector for a dial ADDED inert and
    nothing else; the long-run verdict is a measurement it must keep."""
    assert rec_tool.write_long_run(_write(out, verdict())) == 0
    path = out / "pt-v19.json"
    doc = json.loads(path.read_text())
    # Pretend the build added a dial since the record was written.
    dropped = sorted(doc["coefficients"])[0]
    del doc["coefficients"][dropped]
    doc["coefficient_digest"] = "stale"
    path.write_text(json.dumps(doc, indent=2))
    (out / "pt-v18.json").unlink()
    monkeypatch.setattr(restamp_tool, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["restamp.py"])
    assert restamp_tool.main() == 0
    again = json.loads(path.read_text())
    assert dropped in again["coefficients"]
    assert again["long_run"] == doc["long_run"]
