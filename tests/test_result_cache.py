"""The result cache keeps one reading per (build, model, protocol, seed).

What can go wrong quietly: a key that does not change when the model does
(a candidate reads another model's rows), a key that changes when it should
not (a new switch at zero empties the cache), a second measurement that
disagrees with the first being written over it, and a baseline read for a
protocol the candidate's changes do reach.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

CAL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "calibration"
sys.path.insert(0, str(CAL))

import result_cache as rc  # noqa: E402

tradefloor = pytest.importorskip("tradefloor")


def test_the_model_digest_is_the_rust_digest():
    """Its first eight hex characters are a custom model's fingerprint."""
    m = tradefloor.ModelParams.from_preset("pt-v20", market_factor_sigma=0.007)
    assert m.fingerprint == "custom-" + rc.params_digest(m)[:8]


def test_a_silent_switch_at_zero_keeps_the_key():
    """A new switch shipped at zero must not empty the cache of the model before it."""
    silent = tradefloor.ModelParams.digest_silent_at_zero()
    assert silent, "no silent switch to test with"
    plain = tradefloor.ModelParams.from_preset("pt-v20")
    zero = tradefloor.ModelParams.from_preset("pt-v20", **{silent[0]: 0.0})
    on = tradefloor.ModelParams.from_preset("pt-v20", **{silent[0]: 1.0})
    assert rc.params_digest(plain) == rc.params_digest(zero)
    assert rc.params_digest(plain) != rc.params_digest(on)


def test_any_coefficient_change_moves_the_key():
    a = tradefloor.ModelParams.from_preset("pt-v20")
    b = tradefloor.ModelParams.from_preset("pt-v20", market_factor_sigma=0.0065)
    assert rc.params_digest(a) != rc.params_digest(b)


def _tree(root: pathlib.Path) -> None:
    (root / "rust" / "src").mkdir(parents=True)
    (root / "rust" / "src" / "lib.rs").write_text("fn a() {}\n")
    (root / "python" / "tradefloor").mkdir(parents=True)
    (root / "python" / "tradefloor" / "facts.py").write_text("X = 1\n")


def test_the_build_digest_follows_the_sources_and_nothing_else(tmp_path):
    _tree(tmp_path)
    first = rc.build_digest(tmp_path)
    (tmp_path / "python" / "tradefloor" / "__pycache__").mkdir()
    (tmp_path / "python" / "tradefloor" / "__pycache__" / "facts.cpython-311.pyc").write_bytes(b"x")
    assert rc.build_digest(tmp_path) == first, "bytecode moved the build digest"
    (tmp_path / "rust" / "src" / "lib.rs").write_text("fn a() { 1; }\n")
    assert rc.build_digest(tmp_path) != first


def test_the_engine_build_digest_is_stable():
    assert rc.build_digest() == rc.build_digest()


def test_protocol_ids_carry_their_settings():
    assert rc.protocol_id("p252") == "p252"
    a = rc.protocol_id("p252", universe_n=40, universe_seed=111)
    b = rc.protocol_id("p252", universe_seed=111, universe_n=40)
    assert a == b == "p252[universe_n=40,universe_seed=111]"
    assert rc.protocol_id("p252", days=40) != rc.protocol_id("p252")


def test_a_reading_comes_back_with_its_non_finite_values(tmp_path):
    cache = rc.ResultCache(tmp_path)
    key = rc.Key("b" * 64, "p" * 64, "p252", 101)
    value = {"x": 1.5, "nan": math.nan, "inf": -math.inf, "list": [1, 2.25], "none": None}
    cache.put(key, value)
    back = cache.get(key)
    assert back["x"] == 1.5 and math.isnan(back["nan"]) and back["inf"] == -math.inf
    assert back["list"] == [1, 2.25] and back["none"] is None
    assert rc.differences(value, back) == []


def test_a_disagreeing_second_measurement_is_refused(tmp_path):
    """Two runs of one deterministic measurement that disagree are a failure."""
    cache = rc.ResultCache(tmp_path)
    key = rc.Key("b" * 64, "p" * 64, "p252", 101)
    cache.put(key, {"x": 1.0})
    cache.put(key, {"x": 1.0})                  # the same reading is fine
    with pytest.raises(rc.CacheConflict):
        cache.put(key, {"x": 1.0 + 1e-15})


def test_differences_are_exact_unless_told_otherwise():
    assert rc.differences({"a": [1.0, 2.0]}, {"a": [1.0, 2.0 + 1e-12]})
    assert not rc.differences({"a": 1.0}, {"a": 1.0 + 1e-12}, rel_tol=1e-9)
    assert rc.differences({"a": 1}, {"b": 1})


def test_resolve_reads_the_baseline_only_where_nothing_reaches(tmp_path):
    cache = rc.ResultCache(tmp_path)
    B, base, cand = "b" * 64, "0" * 64, "1" * 64
    for proto in ("untraded", "traded"):
        for s in (1, 2):
            cache.put(rc.Key(B, base, proto, s), {"v": s})
    tasks = rc.resolve(cache, build=B, params=cand, baseline_build=B, baseline_params=base,
                       protocols=["untraded", "traded"], seeds=[1, 2], reachable={"traded"})
    src = {(t.protocol, t.seed): t.source for t in tasks}
    assert src == {("untraded", 1): "baseline", ("untraded", 2): "baseline",
                   ("traded", 1): "measure", ("traded", 2): "measure"}
    cache.put(rc.Key(B, cand, "traded", 1), {"v": 9})
    tasks = rc.resolve(cache, build=B, params=cand, baseline_build=B, baseline_params=base,
                       protocols=["traded"], seeds=[1, 2], reachable={"traded"})
    assert [t.source for t in tasks] == ["cached", "measure"]
    assert rc.summary(tasks) == {"cached": 1, "baseline": 0, "measure": 1}


def test_verify_measures_everything_and_keeps_what_to_compare(tmp_path):
    cache = rc.ResultCache(tmp_path)
    B, base, cand = "b" * 64, "0" * 64, "1" * 64
    cache.put(rc.Key(B, base, "untraded", 1), {"v": 1})
    tasks = rc.resolve(cache, build=B, params=cand, baseline_build=B, baseline_params=base,
                       protocols=["untraded"], seeds=[1, 2], reachable=set(), verify=True)
    assert [t.source for t in tasks] == ["measure", "measure"]
    assert tasks[0].expect_from == rc.Key(B, base, "untraded", 1)
    assert tasks[1].expect_from is None


def _git(root, *args):
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(root), "PATH": "/usr/bin:/bin"})


def test_a_build_product_beside_the_sources_keeps_the_build_digest(tmp_path):
    """A box that built the engine wrote rust/Cargo.lock, which git ignores; a
    box that installed the cached wheel did not. One commit must give one digest."""
    _tree(tmp_path)
    (tmp_path / ".gitignore").write_text("Cargo.lock\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "x")
    clean = rc.build_digest(tmp_path)
    (tmp_path / "rust" / "Cargo.lock").write_text("[[package]]\nname = \"x\"\n")
    (tmp_path / "python" / "tradefloor" / "_core.abi3.so").write_bytes(b"\0")
    assert rc.build_digest(tmp_path) == clean
    (tmp_path / "python" / "tradefloor" / "facts.py").write_text("X = 2\n")
    assert rc.build_digest(tmp_path) != clean, "an edit to a tracked source must move it"
