"""The staged screen reads, measures and verifies the right readings.

Synthetic protocols stand in for the market here, so the tests run in
milliseconds: `untraded` opens only the market channel and `traded` opens
order flow as well. A candidate that changes only an order-flow dial must
measure only `traded`, read `untraded` from its baseline, and in verify mode
catch a measurement that contradicts that claim. The last test runs the
real thing on the Mac: a two-row, two-seed, forty-session screen through
`gate_batch --staged`.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CAL = ROOT / "tools" / "calibration"
sys.path.insert(0, str(CAL))

import result_cache as rc  # noqa: E402
import staged_screen as S  # noqa: E402

CALLS: list[tuple] = []


def fake_measure(payload, name, seed, settings):
    """A deterministic stand-in: one reading per protocol and seed."""
    CALLS.append((payload["label"], name, seed))
    rng = random.Random(seed * 1000 + (1 if name == "traded" else 0))
    noise = rng.gauss(0.0, 0.1)
    if name == "untraded":
        x = payload["market_factor_sigma"] * 100 + noise
        if settings.get("leak"):
            # A dial claimed narrow that in fact moves the untraded market.
            x += payload["order_flow_coefficient"] * 1e-3
        return {"x": x}
    return {"y": payload["order_flow_coefficient"] / 50 + noise}


PROTOCOLS = [
    S.Protocol("untraded", "untraded", tuple(range(1, 31)), frozenset({"market"})),
    S.Protocol("traded", "traded", tuple(range(1, 31)), frozenset({"market", "order_flow"})),
]


def _mean(key, proto):
    return lambda rd: sum(r[key] for r in rd[proto]) / len(rd[proto])


ROWS = [
    S.RowSpec("level", ("untraded",), _mean("x", "untraded"), (0.0, 1.0)),
    S.RowSpec("cost", ("traded",), _mean("y", "traded"), (0.5, 1.5)),
]


def cand(label, sigma=0.6e-2, flow=50.0, base=None):
    values = {"market_factor_sigma": sigma, "order_flow_coefficient": flow}
    payload = {"label": label, **values}
    params = rc.params_digest(values)
    return S.Candidate(label, payload, "B" * 64, params, values, base)


def baseline():
    b = cand("base")
    b.baseline = ("B" * 64, b.params, b.values)
    return b


def test_stage_a_measures_only_what_the_change_reaches(tmp_path):
    cache = rc.ResultCache(tmp_path)
    b = baseline()
    flow = cand("flow", flow=70.0, base=b.baseline)
    CALLS.clear()
    out = S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
                stage="A", n0=10, N=30, log=lambda m: None)
    assert out[1].reachable == ["traded"]
    assert out[1].rows == ["cost"]
    assert {c[1] for c in CALLS} == {"traded"}
    assert len(CALLS) == 10
    assert out[0].rows == [], "an unchanged baseline has nothing to screen at stage A"


def test_stage_b_reads_unreachable_rows_from_the_baseline(tmp_path):
    cache = rc.ResultCache(tmp_path)
    b = baseline()
    flow = cand("flow", flow=70.0, base=b.baseline)
    CALLS.clear()
    out = S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
                stage="B", n0=10, N=30, log=lambda m: None)
    by = {o.label: o for o in out}
    assert by["flow"].counts["baseline"] == 30, "the untraded protocol was re-measured"
    assert not [c for c in CALLS if c[0] == "flow" and c[1] == "untraded"]
    # Run it again: everything is on file now.
    CALLS.clear()
    S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
          stage="B", n0=10, N=30, log=lambda m: None)
    assert CALLS == []


def test_rows_far_inside_settle_early_and_near_ones_extend(tmp_path):
    cache = rc.ResultCache(tmp_path)
    near = cand("near", sigma=1.02e-2)          # level about 1.02, just past the edge
    far = cand("far", sigma=0.5e-2)             # level 0.5, mid band
    out = S.run([near, far], PROTOCOLS, ROWS[:1], cache=cache, measure=fake_measure,
                stage="B", n0=10, N=30, block=10, log=lambda m: None)
    by = {o.label: o for o in out}
    assert by["far"].seeds["untraded"] == 10 and by["far"].state == "passes"
    assert by["near"].seeds["untraded"] > 10


def test_a_candidate_clearly_out_dies_at_stage_a(tmp_path):
    cache = rc.ResultCache(tmp_path)
    b = baseline()
    bad = cand("bad", flow=150.0, base=b.baseline)     # cost about 3 against 1.5
    out = S.run([b, bad], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
                stage="A", n0=10, N=30, log=lambda m: None)
    assert out[1].state == "dead"


def test_verify_catches_a_wrong_reach_claim(tmp_path):
    cache = rc.ResultCache(tmp_path)
    b = baseline()
    flow = cand("flow", flow=70.0, base=b.baseline)
    S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
          settings={"leak": True}, stage="B", n0=10, N=30, log=lambda m: None)
    out = S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
                settings={"leak": True}, stage="B", n0=10, N=30, verify=True,
                log=lambda m: None)
    by = {o.label: o for o in out}
    assert by["base"].disagreements == []
    bad = by["flow"].disagreements
    assert bad and all(d["protocol"] == "untraded" and d["against"] == "baseline entry"
                       for d in bad)


def test_verify_is_quiet_when_the_claim_holds(tmp_path):
    cache = rc.ResultCache(tmp_path)
    b = baseline()
    flow = cand("flow", flow=70.0, base=b.baseline)
    S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
          stage="B", n0=10, N=30, log=lambda m: None)
    out = S.run([b, flow], PROTOCOLS, ROWS, cache=cache, measure=fake_measure,
                stage="B", n0=10, N=30, verify=True, log=lambda m: None)
    assert all(o.disagreements == [] for o in out)


def test_a_tiny_gate_batch_screen_runs_end_to_end(tmp_path):
    """Two rows, two seeds, forty sessions, five names: seconds on a laptop."""
    pytest.importorskip("tradefloor")
    cands = tmp_path / "cands.json"
    cands.write_text(json.dumps([
        {"label": "base", "base": "pt-v20", "overrides": {}},
        {"label": "flow", "base": "pt-v20", "overrides": {"order_flow_coefficient": 80.0}},
        {"label": "vol", "base": "pt-v20", "overrides": {"market_factor_sigma": 0.009}}]))
    env = {**os.environ, "PT_UNIVERSE_N": "5"}
    common = [sys.executable, str(CAL / "gate_batch.py"), "--candidates", str(cands),
              "--cache", str(tmp_path / "cache"), "--kinds", "p252,vix45",
              "--rows", "p252:annualised_vol_pct,crisis_comovement", "--days", "40",
              "--seeds", "2", "--n0", "2", "--workers", "1"]

    def run(*extra):
        out = tmp_path / "out.json"
        subprocess.run(common + list(extra) + ["--out", str(out)], check=True, env=env,
                       capture_output=True, text=True, timeout=300)
        return {o["label"]: o for o in json.loads(out.read_text())}

    a = run("--staged", "A")
    assert a["flow"]["rows"] == [], "an order-flow dial reached an untraded gate row"
    assert a["vol"]["counts"]["measured"] == 4
    b = run("--staged", "B")
    assert b["flow"]["counts"] == {"cached": 0, "baseline": 4, "measured": 0}
    assert b["vol"]["counts"]["cached"] == 4
    v = run("--staged", "B", "--verify")
    assert all(o["disagreements"] == [] for o in v.values())
