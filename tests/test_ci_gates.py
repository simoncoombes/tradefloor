"""The CI gates exist, are required, and check what they say they check.

A gate that is deleted from a workflow, or left out of the job a branch
requires, stops checking without anything going red. So the shape of the
gates is asserted here, and the two scripts behind them, the known-answer
history and the skip check, are tested on cases where they must fail.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

sys.path.insert(0, str(ROOT / "tools" / "ci"))
import batches  # noqa: E402
import kat_history  # noqa: E402
import no_skips  # noqa: E402

sys.path.pop(0)


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # YAML 1.1 reads the key `on` as the boolean True.
    return doc.get("on", doc.get(True))


def _script(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


# --------------------------------------------------------------------------
# suite.yml
# --------------------------------------------------------------------------

SUITE = _workflow("suite.yml")


def test_the_suite_runs_on_pull_requests_and_merges_to_main_and_dev():
    on = _triggers(SUITE)
    assert set(on["pull_request"]["branches"]) == {"main", "dev"}
    assert set(on["push"]["branches"]) == {"main", "dev"}


def test_every_suite_job_is_part_of_the_required_check():
    """`the suite is green` is the check main and dev require. A job it
    does not need can fail with the check still green."""
    jobs = SUITE["jobs"]
    final = [name for name, job in jobs.items()
             if job.get("name") == "the suite is green"]
    assert final == ["complete"], final
    needed = set(jobs["complete"]["needs"])
    # The wheel job is needed through every job that tests the wheel.
    assert needed == set(jobs) - {"complete", "wheel"}, needed
    for name, job in jobs.items():
        if name not in ("complete", "wheel", "rust", "msrv", "known-answers"):
            assert job.get("needs") == "wheel", name


def test_the_suite_runs_every_python_test_and_the_crate_tests():
    script = _script(SUITE["jobs"]["batch"])
    assert "tools/ci/batches.py" in script and "python -m pytest" in script
    # The matrix is the batch module's list, in its order: a batch the
    # module defines and the matrix leaves out would run nowhere.
    assert SUITE["jobs"]["batch"]["strategy"]["matrix"]["batch"] == list(
        batches.BATCHES)
    rust = _script(SUITE["jobs"]["rust"])
    assert "cargo test --release" in rust
    assert "cargo clippy --all-targets --all-features -- -D warnings" in rust


def test_the_examples_job_executes_the_notebooks_and_refuses_a_skip():
    job = SUITE["jobs"]["examples"]
    steps = job["steps"]
    run = next(s for s in steps if "test_examples.py" in s.get("run", ""))
    assert run["env"]["TRADEFLOOR_SLOW_TESTS"] == "1"
    assert "tests/test_readme_snippets.py" in run["run"]
    assert "--junitxml=examples.xml" in run["run"]
    script = _script(job)
    for package in ("nbformat", "nbclient", "ipykernel", "gymnasium",
                    "anthropic"):
        assert package in script, package
    assert "python tools/ci/no_skips.py examples.xml" in script
    assert "--no-index --find-links dist tradefloor" in script


def test_the_install_job_uses_an_empty_venv_and_the_cli():
    script = _script(SUITE["jobs"]["install"])
    assert "python -m venv" in script
    for command in ("tradefloor --version", "-m tradefloor --version",
                    "tradefloor scenario list", "tradefloor mcp --help"):
        assert command in script, command


def test_the_known_answer_history_runs_with_the_tags():
    job = SUITE["jobs"]["known-answers"]
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0
    assert "python tools/ci/kat_history.py" in _script(job)


# --------------------------------------------------------------------------
# release.yml and determinism.yml
# --------------------------------------------------------------------------

def test_a_release_checks_the_tag_against_every_version_before_building():
    release = _workflow("release.yml")
    setup = release["jobs"]["setup"]
    step = next(s for s in setup["steps"]
                if "bump.py --check" in s.get("run", ""))
    assert step["if"] == "github.event_name == 'push'"
    assert '"${GITHUB_REF_NAME#v}"' in step["run"]
    assert "--tagged-on" in step["run"]
    assert release["jobs"]["wheels"]["needs"] == "setup"


def test_the_determinism_gate_checks_every_known_answer_on_every_target():
    det = _workflow("determinism.yml")
    script = _script(det["jobs"]["digest"])
    assert "tests/known_answer.py" in script
    assert "tests/test_known_answer.py" in script
    on = _triggers(det)
    assert set(on["pull_request"]["branches"]) == {"main", "dev"}


# --------------------------------------------------------------------------
# tools/ci/kat_history.py
# --------------------------------------------------------------------------

MAIN = "tests/known_answer.json"
PRESETS = "tests/known_answer_presets.json"
TRADED = "tests/known_answer_traded.json"


def _main(**changes) -> dict:
    base = {"katVersion": 28, "simulationSha256": "a", "metadataSha256": "m",
            "bondsSha256": "b", "sha256": "s", "bytes": 1, "seed": 7,
            "note": "n", "bondsNote": "bn"}
    return {**base, **changes}


def test_an_unchanged_known_answer_passes():
    assert kat_history.problems(MAIN, _main(), _main()) == []


def test_a_re_based_trajectory_without_a_bump_fails():
    found = kat_history.problems(
        MAIN, _main(), _main(simulationSha256="z", sha256="t", note="new"))
    assert len(found) == 1 and "katVersion is still 28" in found[0], found


def test_a_re_based_trajectory_without_a_new_note_fails():
    """What 0.4.0 shipped: katVersion 11 to 12 and a new digest, under a
    note still describing 0.3.0's era boundary."""
    old = _main(katVersion=11, simulationSha256="7c63282b", note="pt-v12")
    new = _main(katVersion=12, simulationSha256="63fa05e6", note="pt-v12")
    found = kat_history.problems(MAIN, old, new)
    assert len(found) == 1 and "`note` was not changed" in found[0], found


def test_a_re_based_trajectory_with_a_bump_and_a_note_passes():
    new = _main(katVersion=29, simulationSha256="z", sha256="t", note="new")
    assert kat_history.problems(MAIN, _main(), new) == []


def test_the_bonds_digest_needs_its_own_note():
    new = _main(katVersion=29, bondsSha256="z", note="new")
    found = kat_history.problems(MAIN, _main(), new)
    assert len(found) == 1 and "`bondsNote`" in found[0], found


def test_the_metadata_digest_may_move_alone_with_a_note():
    new = _main(metadataSha256="z", sha256="t", note="a reporting fix")
    assert kat_history.problems(MAIN, _main(), new) == []
    found = kat_history.problems(MAIN, _main(), _main(metadataSha256="z"))
    assert len(found) == 1 and "`note`" in found[0], found


def test_a_version_that_goes_down_fails():
    found = kat_history.problems(MAIN, _main(), _main(katVersion=27))
    assert any("went down" in line for line in found), found


def test_a_changed_run_definition_needs_a_bump():
    found = kat_history.problems(MAIN, _main(), _main(seed=8, note="new"))
    assert len(found) == 1 and "seed" in found[0], found


def _presets(rows: dict, **changes) -> dict:
    base = {"presetKatVersion": 1, "seed": 1, "sessions": 60, "ticks": 78,
            "presets": rows, "sha256": "c", "note": "n"}
    return {**base, **changes}


def test_a_new_preset_adds_its_row_freely():
    old = _presets({"pt-v1": "a"})
    new = _presets({"pt-v1": "a", "pt-v2": "b"}, sha256="d")
    assert kat_history.problems(PRESETS, old, new) == []


def test_a_released_preset_row_is_frozen():
    old = _presets({"pt-v1": "a", "pt-v2": "b"})
    moved = kat_history.problems(PRESETS, old,
                                 _presets({"pt-v1": "x", "pt-v2": "b"}))
    assert len(moved) == 2 and all("pt-v1" in line for line in moved), moved
    gone = kat_history.problems(
        PRESETS, old, _presets({"pt-v2": "b"}, presetKatVersion=2, note="m"))
    assert len(gone) == 1 and "removed: pt-v1" in gone[0], gone


def test_a_traded_re_base_needs_a_bump_and_a_note():
    old = {"tradedKatVersion": 1, "agents": {"random": {"orders": "a"}},
           "presetRow": "r", "sha256": "s", "note": "n"}
    moved = {**old, "agents": {"random": {"orders": "b"}}, "sha256": "t"}
    found = kat_history.problems(TRADED, old, moved)
    assert len(found) == 2, found
    fixed = {**moved, "tradedKatVersion": 2, "note": "random's orders moved"}
    assert kat_history.problems(TRADED, old, fixed) == []


def test_a_file_new_since_the_release_passes():
    assert kat_history.problems(TRADED, None, {"tradedKatVersion": 1}) == []


def test_the_rules_name_every_known_answer_baseline():
    on_disk = {f"tests/{p.name}" for p in (ROOT / "tests").glob(
        "known_answer*.json")}
    assert set(kat_history.RULES) == on_disk


# --------------------------------------------------------------------------
# tools/ci/no_skips.py
# --------------------------------------------------------------------------

def _report(tmp_path, cases: str) -> str:
    path = tmp_path / "report.xml"
    path.write_text(f'<testsuites><testsuite name="pytest">{cases}'
                    f"</testsuite></testsuites>", encoding="utf-8")
    return str(path)


def test_no_skips_passes_a_run_where_everything_ran(tmp_path, capsys):
    report = _report(tmp_path, '<testcase classname="t" name="a"/>'
                               '<testcase classname="t" name="b"/>')
    assert no_skips.main([report]) == 0
    assert "all 2 tests ran" in capsys.readouterr().out


def test_no_skips_fails_a_skipped_notebook_and_names_it(tmp_path, capsys):
    report = _report(
        tmp_path,
        '<testcase classname="t" name="a"/>'
        '<testcase classname="tests.test_examples" name="nb[01]">'
        '<skipped message="could not import \'nbclient\'"/></testcase>')
    assert no_skips.main([report]) == 1
    out = capsys.readouterr().out
    assert "nb[01]" in out and "nbclient" in out


def test_no_skips_fails_an_empty_run(tmp_path):
    assert no_skips.main([_report(tmp_path, "")]) == 1
