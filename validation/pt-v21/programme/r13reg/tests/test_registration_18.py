"""The eighteenth registration (pt-v21, R21E1, PH5 in the Bonferroni form) holds together.

    python -m pytest programme/r13reg/tests/test_registration_18.py -q

grade-seeds.json is layout.py's output; the arm, its fingerprint and the digest gate agree with the registration
text; the tools the box ships are pinned to commits that exist; the grade plan, once written, gives every shard
exactly the registered variables and the registration commit.
"""
import glob
import json
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
R13 = os.path.dirname(HERE)
PROG = os.path.dirname(R13)
ROOT = os.path.dirname(PROG)
REG = os.path.join(PROG, "ptv21-registration-18.md")
GS = os.path.join(R13, "grade-seeds.json")
sys.path[:0] = [R13, os.path.join(R13, "box"), os.path.join(R13, "freshseeds")]

import digests as DG  # noqa: E402
import layout  # noqa: E402
import seedplan as S  # noqa: E402


def test_grade_seeds_json_is_layout_py_output():
    prot, var = layout.layout()
    gs = json.load(open(GS))
    assert gs["protocols"] == prot and gs["variables"] == var
    assert gs["offset"] == 260000 and gs["span"] == "260201-264400"


def test_every_protocol_has_exam_seeds_and_none_was_an_earlier_exam():
    gs = json.load(open(GS))
    assert set(gs["protocols"]) == {p for p, *_ in S.PROTOCOLS}
    assert "ptv21" in gs["protocols"] and gs["variables"]["PTV21_SEEDS"] == "260201-260290"
    seeds = {s for d in gs["protocols"].values() for s in S.parse(d["seeds"])}
    for lo, hi in ((13201, 16030), (33201, 37400), (85201, 89400), (200001, 201500), (220201, 224400),
                   (230201, 233830), (240201, 244400)):
        assert not [s for s in seeds if lo <= s <= hi]
    assert not S.old_exam(sorted(seeds))


def test_the_arm_and_the_digest_gate_match_the_text():
    reg = open(REG).read()
    assert DG.ARM_FINGERPRINTS == {"R21E1": "custom-2dc32068"}
    assert "custom-2dc32068" in reg and DG.PRESETS_DIGEST in reg
    assert "2024f633101be2668b4fd48c14a19a9ba31ef9e6" in reg
    line = open(os.path.join(R13, "arms", "arm-R21E1.txt")).read().strip()
    import hashlib
    assert hashlib.sha256(line.split(":", 1)[1].encode()).hexdigest() in reg


def test_the_arm_fingerprints_as_registered_on_the_engine():
    tf = pytest.importorskip("tradefloor")
    line = open(os.path.join(R13, "arms", "arm-R21E1.txt")).read().strip()
    dials = {k: float(v) for k, v in (x.split("=") for x in line.split(":", 1)[1].split(",") if x)}
    try:
        fp = tf.ModelParams.from_preset("pt-v20", **dials).fingerprint
    except Exception:
        pytest.skip("this engine is not the registered pin")
    assert fp == "custom-2dc32068" and dials["cycle_equity_hazard_opening"] == 0.011


def test_the_shipped_tools_are_pinned_to_commits_that_exist():
    files = open(os.path.join(PROG, "screen", "box", "files.txt")).read()
    pins = re.findall(r"^(\S+) @ (\S+)$", files, re.M)
    assert ("screen/box/ph_rows_port.py", "53597c44b82a52f31d8f55590de3321e668ba608") in pins
    assert ("longrun/ptv21.py", "3fdb518c4575510da3c7499180e98c5ced674a6a") in pins
    for path, ref in pins:
        subprocess.run(["git", "-C", ROOT, "cat-file", "-e", f"{ref}:programme/{path}"], check=True)
    gs = open(os.path.join(PROG, "screen", "box", "graded-scenarios.txt")).read()
    assert "ENGINE_REF caaa4c6e36e49791ad230379bafad4c1dbbeddb4" in gs


def test_the_job_keeps_its_line_numbers_and_exports_the_ptv21_seeds():
    job = open(os.path.join(R13, "box", "r15-grade-jobs.sh")).read().split("\n")
    assert job[77].startswith('export PTV21_SEEDS="${PTV21_SEEDS:-201-290}"')
    assert job[133].strip() == 'done < "$L/arms.txt"'          # the preamble still ends on line 134
    assert len(job) == 384 or len(job) == 385


@pytest.mark.skipif(not glob.glob(os.path.join(R13, "grade18", "plan-g18*.json")), reason="no grade plan yet")
def test_every_grade_shard_carries_exactly_the_registered_variables():
    gs = json.load(open(GS))["variables"]
    plans = sorted(glob.glob(os.path.join(R13, "grade18", "plan-g18-box*.json")))
    shards = [sh for p in plans for sh in json.load(open(p))["shards"]]
    stages = {sh["stage"] for sh in shards}
    assert {"edgeaudit", "regr", "ptv21", "box", "longrun_pool"} <= stages and len(shards) == 22
    shas = {sh["env"].pop("REGISTERED_GRADE") for sh in shards}
    assert len(shas) == 1 and re.fullmatch(r"[0-9a-f]{40}", shas.pop())
    assert all(sh["env"] == gs for sh in shards)
    assert {sh["arm"] for sh in shards} == {"R21E1"}


def test_ph5_is_registered_in_the_bonferroni_form():
    reg = open(REG).read()
    assert re.findall(r"^PH5_VOL_Z = (\S+)$", open(os.path.join(R13, "grade_all.py")).read(), re.M) == ["2.69"]
    assert "2.69 sqrt(se_0^2 + se_y^2)" in reg and "<!-- PH5 CLAUSE -->" not in reg
    assert "TO FILL" not in reg and "DRAFT" not in reg


def test_the_noisy_rows_run_on_three_times_the_seeds():
    """The owner's decision after grade 17: the true-phase histories, the recession rows, the SF desk's six files, the
    L-rate probe and the pt-v21 rows on three times the seeds; the long-run rows on all 270 (lr270.py)."""
    gs = json.load(open(GS))["protocols"]
    assert {p: gs[p]["n"] for p in ("truephase", "recession", "sf", "probe", "ptv21")} == {
        "truephase": 270, "recession": 90, "sf": 36, "probe": 36, "ptv21": 90}
    assert gs["longrun"]["n"] == 90 and gs["longrunpool"]["n"] == 180
    reg = open(REG).read()
    assert "lr270.py" in reg and "1.08" in reg


def test_lr270_links_the_long_run_and_the_pool(tmp_path, monkeypatch):
    import numpy as np
    import lr270
    monkeypatch.delenv("REGISTERED_GRADE", raising=False)
    monkeypatch.setattr(S, "grade_seeds_path", lambda env=None: None)
    shift = lambda k: S.parse(",".join(f"{a + k}-{b + k}" for a, b in ((201, 230), (501, 530), (801, 830))))
    box = tmp_path / "box"
    for sub, seeds in (("longrun", shift(0)), ("longrun-pool", shift(50000) + shift(60000))):
        d = box / sub / "ARM"; d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"arm": "ARM", "base": "pt-v20", "dials": {"x": 1}, "years": 21}))
        for s in seeds:
            for part in ("free", "gfc", "covid"):
                np.savez(d / f"{s}-{part}.npz", level=np.ones(3)); (d / f"{s}-{part}.json").write_text("{}")
    d, note = lr270.build(str(box), "ARM")
    assert d and note.startswith("270 histories") and len(os.listdir(d)) == 270 * 6 + 1
    os.remove(box / "longrun-pool" / "ARM" / f"{shift(60000)[-1]}-free.npz")
    d, note = lr270.build(str(box), "ARM")
    assert d is None and note.startswith("not built")
