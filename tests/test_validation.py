"""The published grade under `validation/` is the one pt-v20 shipped on.

The owner's decision of 2026-09-26 (item 2): publish the grading scripts and
the grade run's inputs, and point the preset record and MODEL.md at them.
Until then `tf.preset_record("pt-v20")["long_run"]` named twelve files that
were not published, the grading script `criteria.py` among them, and a reader
could check none of it.

These tests hold four things. The scripts here are byte for byte the ones
box ptv20g6 unpacked. `criteria.py`, run on the published outputs with the
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
    """Registration 15's exam seeds stay private until its grade has run.

    Only the twelfth registration's grade is here, and it ran on 2026-09-26.
    A later registration's name or its seed file would mean a pending exam
    went public.
    """
    later = re.compile(r"thirteenth|fourteenth|fifteenth|grade-seeds|"
                       r"registration[-_ ]?1[3-9]|ptv20g1[3-9]|box-g1[3-9]",
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
