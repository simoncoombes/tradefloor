"""The published grades under `validation/`: pt-v20's (0.8.5) and pt-v21's (0.10.0),
and pt-v22's slots, empty until its grade has run.

The owner's decision of 2026-09-26 (item 2): publish the grading scripts and
the grade run's inputs, and point the preset record and MODEL.md at them.
Until then `tf.preset_record("pt-v20")["long_run"]` named twelve files that
were not published, the grading script `criteria.py` among them, and a reader
could check none of it.

These tests hold four things, for each grade. The scripts here are byte for
byte the ones the boxes unpacked (ptv20g6; ptv21c1 and its supplement
ptv21c1s). `criteria.py`, run on the published outputs with the
recorded command, writes the committed grade again, byte for byte. The
record's `long_run` block is that verdict with its paths pointed here, and
every path it names exists. Nothing under `validation/` carries the seeds of
a grade that has not run.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.presets import record as rec_tool  # noqa: E402

GRADE = ROOT / "validation" / "pt-v20"
RESULTS = GRADE / "programme" / "results" / "ptv20"
BOX = RESULTS / "box-g6"
RECORD = ROOT / "python" / "tradefloor" / "presets" / "pt-v20.json"


def as_run() -> list[tuple[str, str, str]]:
    rows = []
    for line in (GRADE / "scripts-as-run.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            sha, inside, published = line.split()
            rows.append((sha, inside, published))
    return rows


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_scripts_here_are_the_ones_the_box_ran():
    rows = as_run()
    # The archive held 31 files: 20 scripts, the two arms files, the bands,
    # two scenario files and six data files.
    assert len(rows) == 31
    assert len({inside for _, inside, _ in rows}) == 31
    wrong = [p for sha, _, p in rows if sha256(GRADE / p) != sha]
    assert not wrong, f"changed since the box ran them: {wrong}"
    # The archive's own hash, as the box recorded it.
    assert (BOX / "scripts-sha256.txt").read_text().startswith(
        "ecdcfe13da0da977ec683707cd4e0854b956201d815e2b2696d1b2240204568c")


def test_the_box_ran_on_the_engine_and_digest_the_record_names():
    record = json.loads(RECORD.read_text())["long_run"]["measured"]
    commit = (BOX / "commit.txt").read_text().strip()
    assert commit == "b89901979e5ab449446daf1cf8e354667e3f1cec"
    assert record["engine_commit"] == commit
    assert record["box"] == "ptv20g6"
    kat = (BOX / "known-answer.txt").read_text()
    # pt-v20's default-trajectory digest, KAT_VERSION 28. This read
    # `tests/known_answer.json` while pt-v20 was the default; from 0.10.0
    # that file holds pt-v21's, and the box graded pt-v20.
    sim = "72485a9fb16ba12d633fbc587dc63fb7293852b08219c8a84a8ed2cb40b7634e"
    assert f"sim      {sim}" in kat


def test_criteria_py_writes_the_committed_grade_again(tmp_path):
    """The desk step of the grade, as `ptv20g6-command.md` records it."""
    r, b = "programme/results/ptv20", "programme/results/ptv20/box-g6"
    cmd = [sys.executable, "programme/longrun/criteria.py",
           "--longrun", f"{b}/longrun-report.json",
           "--certgrade", f"{r}/certgrade-g6.json",
           "--edge", f"{b}/edge.json", "--c4", f"{b}/c4a.json",
           "--c4", f"{b}/c4b.json", "--xsec", f"{b}/xsec.json",
           "--driven", f"{b}/driven2020.json",
           "--driven2022", f"{b}/driven2022.json",
           "--impact", f"pt-v20={b}/impact-pt-v20.json",
           "--c10", f"{b}/c10.json", "--r7", f"{b}/r7-event.json",
           "--r7", f"{b}/r7-eval.json", "--recession", f"{b}/recession.json",
           "--v1", f"{b}/v1.json", "--arm", "pt-v19", "--arm", "pt-v20",
           "--out", str(tmp_path / "criteria-g6.txt"),
           "--json", str(tmp_path / "criteria-g6.json"),
           "--verdict", str(tmp_path / "verdict-pt-v20-g6.json"),
           "--verdict-arm", "pt-v20", "--box", "ptv20g6",
           "--date", "2026-09-26"]
    run = subprocess.run(cmd, cwd=GRADE, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    for name in ("criteria-g6.txt", "criteria-g6.json", "verdict-pt-v20-g6.json"):
        assert (tmp_path / name).read_bytes() == (RESULTS / name).read_bytes(), name
    verdict = json.loads((RESULTS / "verdict-pt-v20-g6.json").read_text())
    assert (verdict["verdict"], verdict["passed"], verdict["of"]) == ("pass", 40, 40)


def test_the_record_carries_the_published_verdict_with_public_paths():
    block = dict(json.loads(RECORD.read_text())["long_run"])
    block.pop("coefficients")
    verdict = json.loads((RESULTS / "verdict-pt-v20-g6.json").read_text())
    assert block == rec_tool.public_paths(verdict)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)


def test_every_path_the_record_names_is_in_this_repository():
    block = json.loads(RECORD.read_text())["long_run"]
    named = set()
    for s in _strings(block):
        assert not re.search(r"(?<![\w./-])programme/", s), (
            f"an unpublished path a reader cannot open: {s!r}")
        named.update(re.findall(r"validation/[\w./-]+\.(?:py|md|json)", s))
    # criteria.py, CRITERIA.md, the registration and nine files it graded.
    assert len(named) == 12, sorted(named)
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, missing


def test_public_paths_rewrites_only_a_published_box():
    published = {"criteria": "programme/longrun/CRITERIA.md (adopted)",
                 "measured": {"box": "ptv20g6",
                              "r7": ["programme/results/ptv20/box-g6/r7-event.json"],
                              "computed_by": "programme/longrun/criteria.py"}}
    out = rec_tool.public_paths(published)
    assert out["criteria"] == "validation/pt-v20/programme/longrun/CRITERIA.md (adopted)"
    assert out["measured"]["r7"] == [
        "validation/pt-v20/programme/results/ptv20/box-g6/r7-event.json"]
    assert published["measured"]["computed_by"] == "programme/longrun/criteria.py"
    # Rewriting twice changes nothing.
    assert rec_tool.public_paths(out) == out
    # A box that is not published keeps the paths it names.
    other = {"measured": {"box": "ptv19fifth",
                          "computed_by": "programme/longrun/criteria.py"}}
    assert rec_tool.public_paths(other) == other


def test_nothing_published_names_a_grade_that_has_not_run():
    """A registration's exam seeds stay private until its grade has run.

    The twelfth registration's grade ran on 2026-09-26 and the eighteenth's
    (pt-v21's, published with its certification) on 2026-10-05. A later
    registration's name would mean a pending exam went public.
    """
    later = re.compile(r"nineteenth|twentieth|registration[-_ ]?(?:19|2\d)|"
                       r"ptv2[12]g(?:19|2\d)|box-g(?:19|2\d)",
                       re.IGNORECASE)
    hits = []
    for path in sorted((ROOT / "validation").rglob("*")):
        if not path.is_file():
            continue
        if later.search(str(path.relative_to(ROOT))):
            hits.append(str(path))
            continue
        if path.suffix == ".gz":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{path.relative_to(ROOT)}: {m.group(0)}"
                 for m in later.finditer(text)]
    assert not hits, hits


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_run_box_stages_the_files_the_box_ran(tmp_path):
    env = {"PATH": f"{pathlib.Path(sys.executable).parent}:/usr/bin:/bin",
           "STAGE_ONLY": "1"}
    run = subprocess.run(["bash", str(GRADE / "run-box.sh"), str(tmp_path)],
                         capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "staged 31 files" in run.stdout
    for sha, inside, _ in as_run():
        assert sha256(tmp_path / inside) == sha, inside


def test_model_md_says_the_reference_implementation_is_unpublished():
    """Owner decision 6: say plainly that readers cannot consult it."""
    text = (ROOT / "docs" / "MODEL.md").read_text()
    section = text.split("### The reference implementation", 1)
    assert len(section) == 2, "MODEL.md has no section on the reference implementation"
    body = section[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    assert "not published" in body
    assert "until the owner names it" in body


@pytest.mark.parametrize("page", ["docs/MODEL.md", "docs/STATISTICS.md"])
def test_the_docs_cite_the_published_grade(page):
    text = (ROOT / page).read_text()
    stale = re.findall(r"(?<![\w./-])programme/(?:ptv20-registration\.md|"
                       r"longrun/criteria\.py|results/ptv20/criteria-g6\.txt)",
                       text)
    assert not stale, f"{page} still cites the grade in the private repository"
    assert "validation/pt-v20/" in text


# ---------------------------------------------------------------------------
# pt-v21, the default from 0.10.0: box ptv21c1 and its supplement ptv21c1s
# ---------------------------------------------------------------------------

GRADE21 = ROOT / "validation" / "pt-v21"
RESULTS21 = GRADE21 / "programme" / "results" / "ptv21"
BOX21 = RESULTS21 / "box-c1"
RECORD21 = ROOT / "python" / "tradefloor" / "presets" / "pt-v21.json"


def as_run21() -> list[tuple[str, str, str]]:
    rows = []
    for line in (GRADE21 / "scripts-as-run.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            sha, inside, published = line.split()
            rows.append((sha, inside, published))
    return rows


def test_pt_v21_scripts_here_are_the_ones_the_boxes_ran():
    rows = as_run21()
    # ptv21c1 unpacked 31 files and ptv21c1s 16.
    boxes = [inside.split(":", 1)[0] for _, inside, _ in rows]
    assert (boxes.count("box-c1"), boxes.count("box-c1s")) == (31, 16)
    wrong = [p for sha, _, p in rows if sha256(GRADE21 / p) != sha]
    assert not wrong, f"changed since the boxes ran them: {wrong}"
    assert (BOX21 / "scripts-sha256.txt").read_text().startswith(
        "797759e7c7a27a40d54cec49ebc273c05e6c7a6f1b4aa9636219b736f022af13")
    assert (RESULTS21 / "box-c1s" / "scripts-sha256.txt").read_text().startswith(
        "efecbac7de48362ca7dafd491eae7425b860a0ba859525971141ef7f4b0d6e49")


def test_pt_v21_boxes_ran_on_the_release_engine_the_record_names():
    record = json.loads(RECORD21.read_text())["long_run"]["measured"]
    commit = "931ed3d4fe659e4dec765673ee3e280a6b40ab2b"
    assert (BOX21 / "commit.txt").read_text().strip() == commit
    assert (RESULTS21 / "box-c1s" / "commit.txt").read_text().strip() == commit
    assert record["engine_commit"] == commit
    assert (record["box"], record["fingerprint"]) == ("ptv21c1+ptv21c1s", "pt-v21")
    kat = (BOX21 / "known-answer.txt").read_text()
    # pt-v21's default-trajectory digest, KAT_VERSION 29: the build the
    # boxes ran is the 0.10.0 engine. A literal, since known_answer.json
    # reads the default's, which moves with it.
    assert "package  0.10.0" in kat
    assert ("sha256   4ea1d56340a3357db730764c3e24c131"
            "c5e0883efa4cd6f7f56c83ea9674033c") in kat
    sim = "3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c"
    assert f"sim      {sim}" in kat


def _criteria21(tmp_path, definitions):
    r, b = "programme/results/ptv21", "programme/results/ptv21/box-c1"
    cmd = [sys.executable, "programme/longrun/criteria.py",
           "--longrun", f"{b}/lr270/longrun-report.json",
           "--certgrade", f"{r}/certgrade-c1.json",
           "--edge", f"{b}/edge.json", "--c4", f"{b}/c4a.json",
           "--c4", f"{b}/c4b.json", "--xsec", f"{b}/lr270/xsec.json",
           "--driven", f"{b}/driven2020.json",
           "--driven2022", f"{b}/driven2022.json",
           "--impact", f"pt-v21={b}/impact-pt-v21.json",
           "--c10", f"{b}/c10.json", "--r7", f"{b}/r7-event.json",
           "--r7", f"{b}/r7-eval.json", "--recession", f"{b}/recession.json",
           "--v1", f"{b}/lr270/v1.json", "--arm", "pt-v20", "--arm", "pt-v21"]
    if definitions == "reg18":
        cmd += ["--definitions", "reg18", "--reg18", f"{r}/reg18-c1.json",
                "--box", "ptv21c1+ptv21c1s"]
    else:
        cmd += ["--box", "ptv21c1"]
    cmd += ["--out", str(tmp_path / "out.txt"), "--json", str(tmp_path / "out.json"),
            "--verdict", str(tmp_path / "verdict.json"), "--verdict-arm", "pt-v21",
            "--date", "2026-10-05"]
    run = subprocess.run(cmd, cwd=GRADE21, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return {name: (tmp_path / name).read_bytes()
            for name in ("out.txt", "out.json", "verdict.json")}


def test_pt_v21_criteria_py_writes_the_committed_grade_again(tmp_path):
    """The desk step as `ptv21c1-command.md` records it, on the eighteenth
    registration's definitions, and on the twelfth's, which it reports."""
    got = _criteria21(tmp_path, "reg18")
    assert got["out.txt"] == (RESULTS21 / "criteria-c1-reg18.txt").read_bytes()
    assert got["out.json"] == (RESULTS21 / "criteria-c1-reg18.json").read_bytes()
    assert got["verdict.json"] == (RESULTS21 / "verdict-pt-v21-c1.json").read_bytes()
    verdict = json.loads(got["verdict.json"])
    assert (verdict["verdict"], verdict["passed"], verdict["of"]) == ("pass", 40, 40)
    assert verdict["definitions"]["version"] == "reg18"
    twelfth = _criteria21(tmp_path, "reg12")
    assert twelfth["out.txt"] == (RESULTS21 / "criteria-c1.txt").read_bytes()
    assert twelfth["verdict.json"] == (
        RESULTS21 / "verdict-pt-v21-c1-reg12.json").read_bytes()


def test_pt_v21_record_carries_the_published_verdict_with_public_paths():
    block = dict(json.loads(RECORD21.read_text())["long_run"])
    block.pop("coefficients")
    verdict = json.loads((RESULTS21 / "verdict-pt-v21-c1.json").read_text())
    assert block == rec_tool.public_paths(verdict)


def test_every_path_the_pt_v21_record_names_is_in_this_repository():
    block = json.loads(RECORD21.read_text())["long_run"]
    named = set()
    for s in _strings(block):
        assert not re.search(r"(?<![\w./-])programme/", s), (
            f"an unpublished path a reader cannot open: {s!r}")
        named.update(re.findall(r"validation/[\w./-]+\.(?:py|md|json)", s))
    # criteria.py, CRITERIA.md, both registrations, the reg18 inputs and the
    # nine files it graded.
    assert len(named) == 14, sorted(named)
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, missing


def test_public_paths_rewrites_a_verdict_read_from_two_published_boxes():
    both = {"measured": {"box": "ptv21c1+ptv21c1s",
                         "computed_by": "programme/longrun/criteria.py"}}
    assert rec_tool.public_paths(both)["measured"]["computed_by"] == (
        "validation/pt-v21/programme/longrun/criteria.py")
    # One unpublished box keeps every path as it was.
    half = {"measured": {"box": "ptv21c1+ptv21x",
                         "computed_by": "programme/longrun/criteria.py"}}
    assert rec_tool.public_paths(half) == half


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_pt_v21_run_box_stages_the_files_the_boxes_ran(tmp_path):
    env = {"PATH": f"{pathlib.Path(sys.executable).parent}:/usr/bin:/bin",
           "STAGE_ONLY": "1"}
    run = subprocess.run(["bash", str(GRADE21 / "run-box.sh"), str(tmp_path)],
                         capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "staged 47 files" in run.stdout
    for sha, inside, _ in as_run21():
        box, _, path = inside.partition(":")
        assert sha256(tmp_path / box.replace("box-", "") / path) == sha, inside


def test_the_files_the_pt_v21_registration_cites_are_here():
    """`ptv21-registration-18.md` is published as registered, and the files
    it cites by name are published beside it at the paths it names, each
    with the hash it had at the commit it was read from."""
    rows = [line.split(None, 3)
            for line in (GRADE21 / "registration-files.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")]
    assert len(rows) == 37
    wrong = [r[2] for r in rows if sha256(GRADE21 / r[2]) != r[0]]
    assert not wrong, wrong
    cited = (GRADE21 / "programme" / "ptv21-registration-18.md").read_text()
    for name in ("r13reg/grade-seeds.json", "r13reg/grade_all.py",
                 "r13reg/box/digests.py", "r13reg/arms/arm-R21E1.txt",
                 "programme/screen/box.sh", "programme/screen/box/screen_box.py",
                 "programme/screen/box/merge_out.py", "screen/stages.json",
                 "programme/longrun/CRITERIA-pt-v21.md"):
        assert name in cited, name
        assert (GRADE21 / "programme" / name.removeprefix("programme/")).is_file(), name


# ---------------------------------------------------------------------------
# pt-v22: in the engine before its grade, written from a stand-in arm
# ---------------------------------------------------------------------------

GRADE22_DIR = ROOT / "validation" / "pt-v22"
RECORD22 = ROOT / "python" / "tradefloor" / "presets" / "pt-v22.json"

#: What pt-v22's grade gives, as named slots, each None until the grade has
#: run (`validation/pt-v22/README.md` says what fills each one). The tests
#: below that read the grade run once every slot is filled; the test after
#: this table fails while some are filled and others are not, so a release
#: cannot ship half a grade.
GRADE22 = {
    # The boxes that ran the grade, as `record.py`'s PT_V22_GRADE_BOXES
    # names them, joined by "+" in the verdict's `measured.box`.
    "boxes": None,
    # The engine commit the boxes ran, and the package version it builds.
    "engine_commit": None,
    "package": None,
    # Per box: (files unpacked, sha256 of the archive), as the box recorded.
    "scripts": None,
    # The verdict the record carries: its file under validation/pt-v22/,
    # its result, the rows passed and the rows graded.
    "verdict": None,
}


def _arm_tool():
    sys.path.insert(0, str(ROOT / "tools" / "presets"))
    import arm_preset  # noqa: PLC0415

    return arm_preset


def test_pt_v22_grade_is_either_all_pending_or_all_filled():
    from tradefloor.records import open_slots  # noqa: PLC0415

    record = json.loads(RECORD22.read_text())
    filled = {
        "record.py PT_V22_GRADE_BOXES": bool(rec_tool.PT_V22_GRADE_BOXES),
        "pt-v22.json's slots": not open_slots(record),
        **{f"GRADE22[{k!r}]": v is not None for k, v in GRADE22.items()},
    }
    assert len(set(filled.values())) == 1, (
        "pt-v22's grade is half filled: "
        + ", ".join(f"{k} {'filled' if v else 'empty'}" for k, v in filled.items()))
    if not all(filled.values()):
        # Pending: the folder holds its README and nothing a grade wrote.
        assert sorted(p.name for p in GRADE22_DIR.iterdir()) == ["README.md"]


def test_pt_v22_record_refuses_to_load_while_a_slot_is_empty():
    import tradefloor as tf  # noqa: PLC0415
    from tradefloor import records  # noqa: PLC0415

    record = json.loads(RECORD22.read_text())
    empty = records.open_slots(record)
    if not empty:
        assert "pt-v22" in records.available()
        return
    # Every slot is a block the grade measures, named after itself, with the
    # writer that fills it.
    assert empty == [k for k in rec_tool.GRADE_SLOTS if k in empty]
    assert set(empty) == set(rec_tool.GRADE_SLOTS)
    for field in empty:
        assert record[field] == {"slot": field, "filled_by": rec_tool.GRADE_SLOTS[field]}
    with pytest.raises(LookupError, match="not measured") as raised:
        tf.preset_record("pt-v22")
    assert all(field in str(raised.value) for field in empty)
    assert "pt-v22" in records.pending()
    assert "pt-v22" not in records.available()


def test_pt_v22_is_the_arm_its_file_names():
    """The arm file, the Rust block written from it, the build and the
    record's identity are one vector, and the arm says what it is."""
    import tradefloor as tf  # noqa: PLC0415

    tool = _arm_tool()
    arm = tool.committed_arm()
    assert arm.base == "pt-v20" and arm.status in tool.STATUSES
    assert tool.rust_arm() == arm.values
    assert tool.preset_is_arm(arm) == []
    record = json.loads(RECORD22.read_text())
    assert {k: record.get(k) for k in tool.identity(arm)} == tool.identity(arm)
    assert record["arm"]["status"] == arm.status


def test_the_arm_file_reads_both_forms_the_programme_writes():
    tool = _arm_tool()
    one = tool.parse_arm("X1@pt-v20:surface_ssvi=1.0,basis_sd=3.753")
    lines = tool.parse_arm(one.text())
    assert (lines.label, lines.base, lines.status) == ("X1", "pt-v20", "stand-in")
    assert lines.values == one.values == {"surface_ssvi": 1.0, "basis_sd": 3.753}
    assert tool.parse_arm(one.text(), status="graded").status == "graded"
    with pytest.raises(ValueError, match="twice"):
        tool.parse_arm("X1@pt-v20:basis_sd=1.0,basis_sd=2.0")
    with pytest.raises(ValueError, match="@base"):
        tool.parse_arm("@arm X1\nbasis_sd 1.0\n")


def test_a_pt_v22_session_lists_the_contracts():
    """Index, rate and oil futures and options on every name; no index
    options and no VIX futures. All but the index futures list from the
    first close."""
    import tradefloor as tf  # noqa: PLC0415

    engine = tf.Engine(seed=7, universe=tf.Universe.random(4, seed=1), model="pt-v22")
    engine.open_market()
    engine.run_session(9, 30, 3, 78, volatility=1.0)
    engine.close_market()
    engine.open_market()
    listed = {(c["root"], c["kind"]) for c in engine.contracts()}
    futures = {root for root, kind in listed if kind == "future"}
    assert futures == {"IDX", "FF", "TR3", "OIL"}
    names = {root for root, kind in listed if kind == "option"}
    assert names == set(engine.name_option_roots()) and len(names) == 4
    assert engine.lists_name_options and not engine.lists_options


_PENDING22 = [k for k, v in GRADE22.items() if v is None]


@pytest.mark.skipif(bool(_PENDING22),
                    reason=f"pt-v22's grade has not run: GRADE22 {_PENDING22} empty")
def test_pt_v22_record_carries_the_published_verdict_with_public_paths():
    verdict_file, result, passed, of = GRADE22["verdict"]
    verdict = json.loads((GRADE22_DIR / verdict_file).read_text())
    assert (verdict["verdict"], verdict["passed"], verdict["of"]) == (result, passed, of)
    block = dict(json.loads(RECORD22.read_text())["long_run"])
    block.pop("coefficients")
    assert block == rec_tool.public_paths(verdict)
    measured = block["measured"]
    assert measured["box"] == "+".join(GRADE22["boxes"])
    assert measured["engine_commit"] == GRADE22["engine_commit"]
    assert tuple(GRADE22["boxes"]) == tuple(rec_tool.PT_V22_GRADE_BOXES)


@pytest.mark.skipif(bool(_PENDING22),
                    reason=f"pt-v22's grade has not run: GRADE22 {_PENDING22} empty")
def test_pt_v22_scripts_here_are_the_ones_the_boxes_ran():
    rows = []
    for line in (GRADE22_DIR / "scripts-as-run.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append(line.split())
    boxes = [inside.split(":", 1)[0] for _, inside, _ in rows]
    for box, (count, _archive) in GRADE22["scripts"].items():
        assert boxes.count(box) == count, box
    wrong = [p for sha, _, p in rows if sha256(GRADE22_DIR / p) != sha]
    assert not wrong, f"changed since the boxes ran them: {wrong}"


@pytest.mark.skipif(bool(_PENDING22),
                    reason=f"pt-v22's grade has not run: GRADE22 {_PENDING22} empty")
def test_every_path_the_pt_v22_record_names_is_in_this_repository():
    block = json.loads(RECORD22.read_text())["long_run"]
    named = set()
    for s in _strings(block):
        assert not re.search(r"(?<![\w./-])programme/", s), (
            f"an unpublished path a reader cannot open: {s!r}")
        named.update(re.findall(r"validation/[\w./-]+\.(?:py|md|json)", s))
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert named and not missing, missing


def test_swapping_the_arm_rewrites_only_pt_v22s_value_in_an_entry():
    """What `arm_preset.py` does to a `provenance.py` entry whose value the
    graded arm moves: the pt-v22 literal and the sentence that quotes it, and
    nothing else, so another preset's value or a bar is never touched."""
    tool = _arm_tool()
    src = ('X\n    "fed_put_gain": {\n        "kind": "undetermined",\n'
           '        "presets": {"pt-v21": 3.0, "pt-v22": 2.5},\n'
           '        "what_would_determine_it": "pt-v22 ships 2.5, pt-v21 3",\n'
           '    },\n    "other": {\n        "presets": {"pt-v22": 2.5},\n    },\n')
    out = tool._rewrite_value(src, "fed_put_gain", 2.5, 2.75)
    assert '"presets": {"pt-v21": 3.0, "pt-v22": 2.75}' in out
    assert "pt-v22 ships 2.75, pt-v21 3" in out
    assert out.count('"pt-v22": 2.5}') == 1  # the other entry's


def test_swapping_the_arm_writes_a_pending_entry_out_of_out_of_scope():
    """A dial the graded arm moves that no entry covers: the swap takes its
    entry from `arms/pt-v22-provenance.json`, removes the dial's
    `OUT_OF_SCOPE` note and adds the entry to `DIAL_PROVENANCE` in key order,
    so release day applies no patch by hand (the rehearsal's blocker 1)."""
    tool = _arm_tool()
    src = ('X\nOUT_OF_SCOPE = {\n    "a_dial":\n        "inert at 0.0 "\n        "as it stood",\n'
           '    "new_dial":\n        "inert at 0.0: off, as it stood; "\n        "left out of the digest",\n'
           '    "z_dial":\n        "inert",\n}\n'
           'DIAL_PROVENANCE: dict[str, dict[str, Any]] = {\n'
           '    "m_dial": {\n        "kind": "derived",\n    },\n'
           '    "q_dial": {\n        "kind": "derived",\n    },\n}\n')
    entry = {"kind": "measured", "presets": {"pt-v22": 0.005375}, "estimate": 0.005375,
             "standard_error": 0.000249, "estimator": "least squares " * 12}
    out = tool._insert_entry(tool._drop_out_of_scope(src, "new_dial"), "new_dial", entry)
    ns = {"Any": object}
    exec(out.split("\n", 1)[1], ns)
    assert list(ns["OUT_OF_SCOPE"]) == ["a_dial", "z_dial"]
    assert list(ns["DIAL_PROVENANCE"]) == ["m_dial", "new_dial", "q_dial"]
    assert ns["DIAL_PROVENANCE"]["new_dial"] == entry
    assert max(len(line) for line in out.splitlines()) < 100


def test_each_pending_provenance_entry_is_a_valid_entry():
    """The entries the swap will write are held to provenance.py's own schema
    now, before the dials they cover reach this branch."""
    from tradefloor import provenance as pv

    pending = json.loads((ROOT / "tools" / "presets" / "arms" / "pt-v22-provenance.json").read_text())
    assert pending, "no pending entries"
    for dial, entry in pending.items():
        assert pv.validate_entry(dial, entry) == [], dial
        assert set(entry["presets"]) == {"pt-v22"}, dial
        assert "TO CONFIRM" not in json.dumps(entry), dial


# ---------------------------------------------------------------------------
# Rows a registration reports without gating, and accepted exceptions
# ---------------------------------------------------------------------------

def _verdict22(**passes):
    rows = [{"id": rid, "words": rid, "value": 0.0, "real": 0.0, "rule": "band", "pass": ok}
            for rid, ok in passes.items()]
    n = sum(1 for ok in passes.values() if ok)
    return {"verdict": "pass" if n == len(rows) else "fail", "passed": n, "of": len(rows), "rows": rows,
            "measured": {"fingerprint": "pt-v22", "engine_commit": "x", "box": "b"}}


def test_pt_v22_reports_of4_and_r6_and_records_on1s_and_e1s_exceptions():
    """OF4 (draft item 21) and R6 (item 25(i)) are read and reported, not
    gated; ON1 and E1 are gated and carry the owner's exceptions (items 25(h)
    and 25(j)). `pass` stays the plain fact of whether a row sits inside its
    mark."""
    assert set(rec_tool.REPORTED_ROWS["pt-v22"]) == {"OF4", "R6"}
    assert set(rec_tool.ROW_EXCEPTIONS["pt-v22"]) == {"ON1", "E1"}
    doc = _verdict22(A1=True, ON1=True, E1=True, OF4=False, R6=False)
    block, problems = rec_tool.mark_rows("pt-v22", doc)
    assert problems == []
    rows = {r["id"]: r for r in block["rows"]}
    assert [rows[k]["gated"] for k in ("A1", "ON1", "E1", "OF4", "R6")] == [True, True, True, False, False]
    assert rows["OF4"]["pass"] is False and "item 21" in rows["OF4"]["reported_because"]
    assert "item 25(i)" in rows["R6"]["reported_because"]
    assert "item 25(h)" in rows["ON1"]["exception"] and rows["ON1"]["pass"] is True
    assert "item 25(j)" in rows["E1"]["exception"] and rows["E1"]["pass"] is True
    assert (block["verdict"], block["passed"], block["of"]) == ("pass", 3, 3)
    assert block["all_rows"] == {"passed": 3, "of": 5}
    # Every other preset gates every row, as pt-v20's and pt-v21's grades did.
    plain, _ = rec_tool.mark_rows("pt-v21", doc)
    assert all(r["gated"] for r in plain["rows"]) and plain["verdict"] == "fail"


def test_a_gated_row_outside_its_mark_fails_and_a_missing_named_row_is_refused():
    block, problems = rec_tool.mark_rows("pt-v22", _verdict22(A1=True, ON1=False, E1=True, OF4=True, R6=True))
    assert problems == [] and block["verdict"] == "fail"
    assert (block["passed"], block["of"]) == (2, 3)
    _, problems = rec_tool.mark_rows("pt-v22", _verdict22(A1=True, ON1=True, E1=True, OF4=True))
    assert any("R6" in p and "no such row" in p for p in problems)


def test_write_long_run_writes_the_marks_and_a_failing_gated_row_cannot_load(tmp_path, monkeypatch):
    from tradefloor import records  # noqa: PLC0415

    out = tmp_path / "presets"
    out.mkdir()
    (out / "pt-v22.json").write_text(RECORD22.read_text())
    monkeypatch.setattr(rec_tool, "OUT", out)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_verdict22(A1=True, ON1=True, E1=True, OF4=False, R6=True)))
    assert rec_tool.write_long_run(str(ok)) == 0
    block = json.loads((out / "pt-v22.json").read_text())["long_run"]
    assert (block["verdict"], block["passed"], block["of"], block["all_rows"]) == (
        "pass", 3, 3, {"passed": 4, "of": 5})
    assert records.gated_failures({"long_run": block}) == []
    # A gated row outside its mark is written as the grade said, verdict
    # "fail", and the record holding it does not load.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_verdict22(A1=False, ON1=True, E1=True, OF4=True, R6=True)))
    assert rec_tool.write_long_run(str(bad)) == 0
    block = json.loads((out / "pt-v22.json").read_text())["long_run"]
    assert block["verdict"] == "fail" and records.gated_failures({"long_run": block}) == ["A1"]
    # A verdict that names no R6 is refused outright.
    short = tmp_path / "short.json"
    short.write_text(json.dumps(_verdict22(A1=True, ON1=True, E1=True, OF4=True)))
    assert rec_tool.write_long_run(str(short)) == 1


def test_a_record_that_gates_a_failing_row_does_not_load(tmp_path, monkeypatch):
    from tradefloor import records  # noqa: PLC0415

    def record(gated):
        return {"schema": 1, "preset": "pt-x", "long_run": {
            "verdict": "pass", "passed": 1, "of": 1 + gated, "all_rows": {"passed": 1, "of": 2},
            "rows": [{"id": "A1", "pass": True, "gated": True},
                     {"id": "OF4", "pass": False, "gated": gated}]}}
    (tmp_path / "presets").mkdir()
    monkeypatch.setattr(records, "_dir", lambda: tmp_path / "presets")
    (tmp_path / "presets" / "pt-x.json").write_text(json.dumps(record(False)))
    assert records.preset_record("pt-x")["long_run"]["rows"][1]["pass"] is False
    (tmp_path / "presets" / "pt-x.json").write_text(json.dumps(record(True)))
    with pytest.raises(LookupError, match="outside their mark"):
        records.preset_record("pt-x")
    # Written with the marks, a failing gated row is refused whatever the
    # verdict says.
    marked = record(True)
    marked["long_run"]["verdict"] = "fail"
    (tmp_path / "presets" / "pt-x.json").write_text(json.dumps(marked))
    with pytest.raises(LookupError, match="outside their mark"):
        records.preset_record("pt-x")
    monkeypatch.undo()
    # Every shipped record loads. pt-v19's carries the grade it failed as
    # pt-v20's control (C4a, C4b), written before the marks, and loads as it did.
    for name in records.available():
        assert records.refused_rows(json.loads(
            (ROOT / "python" / "tradefloor" / "presets" / f"{name}.json").read_text())) == [], name
    assert records.gated_failures(records.preset_record("pt-v19")) == ["C4a", "C4b"]


# ---------------------------------------------------------------------------
# tools/presets/fill_grade22.py, the release-day fill, on its own mock grade
# ---------------------------------------------------------------------------

def _fill_tool():
    sys.path.insert(0, str(ROOT / "tools" / "presets"))
    import fill_grade22  # noqa: PLC0415

    return fill_grade22


def _fill_tree(tmp_path):
    """A copy of the files the fill writes, with a mock grade beside it."""
    if GRADE22["boxes"] is not None:
        # The release tree: the fill has run, and these tests of it on an
        # unfilled tree retire with it (the release rehearsal's finding).
        pytest.skip("the fill has run on this tree")
    tool = _fill_tool()
    tree = tmp_path / "tree"
    for rel in tool.TOUCHED[:3] + ("rust/src/params.rs",):
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tree / rel)
    tool.mock(tmp_path / "mock")
    shutil.copy2(tmp_path / "mock" / "README.md", tree / "README.md")
    m = tmp_path / "mock"
    args = type("A", (), dict(desk=str(m / "desk"), box=[f"ptv22g1={m / 'ptv22g1'}", f"ptv22g2={m / 'ptv22g2'}"],
                              panel=None, level_rows=None, pooled=None, fill=["README.md"], values=None,
                              graded_fingerprint=f"custom-{tool.digest_prefix()}"))()
    return tool, tree, args


def test_the_fill_writes_the_verdict_the_named_values_and_the_placeholders(tmp_path, monkeypatch):
    tool, tree, args = _fill_tree(tmp_path)
    monkeypatch.setattr(tool, "ROOT", tree)
    monkeypatch.setattr(rec_tool, "OUT", rec_tool.OUT)
    monkeypatch.setattr(rec_tool, "PUBLISHED_GRADES", dict(rec_tool.PUBLISHED_GRADES))
    assert tool.run(tree, args) == 0
    verdict = json.loads((tree / "validation/pt-v22/verdict-pt-v22.json").read_text())
    rows = {r["id"]: r for r in verdict["rows"]}
    assert (verdict["verdict"], verdict["passed"], verdict["of"]) == ("pass", 14, 14)
    assert verdict["all_rows"] == {"passed": 14, "of": 16}
    assert rows["OF4"]["gated"] is False and rows["R6"]["gated"] is False
    assert "exception" in rows["ON1"] and "exception" in rows["E1"]
    assert rows["B3"]["group"] == "amended (registration 19)" and verdict["replaced_by_amendments"] == ["B3"]
    assert verdict["measured"]["fingerprint"] == "pt-v22"
    assert verdict["measured"]["graded_fingerprint"] == f"custom-{tool.digest_prefix()}"
    block = dict(json.loads((tree / "python/tradefloor/presets/pt-v22.json").read_text())["long_run"])
    block.pop("coefficients")
    assert block == rec_tool.public_paths(verdict)
    assert (tree / "README.md").read_text() == "pt-v22 passes 14 of 14 rows.\n"
    tests = (tree / "tests/test_validation.py").read_text()
    assert '"verdict": (\'verdict-pt-v22.json\', \'pass\', 14, 14),' in tests
    assert '\n    "boxes": None,' not in tests and '"package": \'0.11.0\'' in tests
    assert 'PT_V22_GRADE_BOXES: tuple[str, ...] = ("ptv22g1", "ptv22g2",)' in (
        tree / "tools/presets/record.py").read_text()


def test_the_fill_refuses_another_arm_and_an_unfilled_placeholder(tmp_path, monkeypatch):
    tool, tree, args = _fill_tree(tmp_path)
    monkeypatch.setattr(rec_tool, "OUT", rec_tool.OUT)
    monkeypatch.setattr(rec_tool, "PUBLISHED_GRADES", dict(rec_tool.PUBLISHED_GRADES))
    v = pathlib.Path(args.desk) / "verdict-R22-fixd2RWP.json"
    doc = json.loads(v.read_text())
    v.write_text(json.dumps(dict(doc, fingerprint="custom-00000000")))
    with pytest.raises(SystemExit, match="not pt-v22's digest"):
        tool.run(tree, args)
    v.write_text(json.dumps(doc))
    args.graded_fingerprint = None
    with pytest.raises(SystemExit, match="record no fingerprint"):
        tool.run(tree, args)
    args.graded_fingerprint = f"custom-{tool.digest_prefix()}"
    (tree / "README.md").write_text("{{G19:SS1}} and {{G19:long_run_of}}\n")
    with pytest.raises(SystemExit, match="no value for .*SS1"):
        tool.run(tree, args)


def test_the_fill_records_the_pooled_read_with_a_public_source(tmp_path, monkeypatch):
    """`--pooled` hands `level_pool.py measure`'s file to `level_pool.py
    record` after the level rows, writing the tree's record, and names a
    public source rather than the file's local path."""
    tool, tree, args = _fill_tree(tmp_path)
    monkeypatch.setattr(rec_tool, "OUT", rec_tool.OUT)
    monkeypatch.setattr(rec_tool, "PUBLISHED_GRADES", dict(rec_tool.PUBLISHED_GRADES))
    import level_pool  # noqa: PLC0415

    seen = {}
    monkeypatch.setattr(level_pool, "record", lambda ns: seen.update(vars(ns), records=level_pool.RECORDS) or 0)
    monkeypatch.setattr(level_pool, "RECORDS", level_pool.RECORDS)
    args.pooled = str(tmp_path / "box" / "pt-v22.json")
    assert tool.run(tree, args) == 0
    assert seen["file"] == args.pooled and seen["records"] == tree / "python/tradefloor/presets"
    assert seen["source"] == "tools/presets/level_pool.py measure pt-v22 (pt-v22.json)"


def test_with_no_fill_named_the_fill_finds_every_page_with_a_placeholder(tmp_path):
    tool = _fill_tool()
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("{{G19:long_run_of}}\n")
    (tmp_path / "docs" / "REALISM.md").write_text("{{G19:one_year_in}} in\n")
    (tmp_path / "docs" / "MODEL.md").write_text("no placeholder\n")
    assert tool.placeholder_files(tmp_path) == ["README.md", "docs/REALISM.md"]
