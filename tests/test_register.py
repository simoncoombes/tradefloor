"""The claim register must be found deliberately, or not at all.

The register describes the documentation, and the documentation left this
repository at 0.5.0, so the register lives there now. That makes "which
register did this run read" a question with more than one answer, and a gate
that reads a different one than its operator believes is no better than a
gate that reads none.

Two failures are guarded here. The quiet fallback: TRADEFLOOR_DOCS set, the
register not present under it, and the tool reaching for some other copy.
And the copy itself: this repository kept one until 0.8.1, it stayed behind
when the pages moved, and for four releases every coordinate in it named a
page nothing held.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "remeasure"))

import register  # noqa: E402


def _register_at(root: Path, figures=None) -> Path:
    path = root / register.IN_DOCS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"figures": figures or []}), encoding="utf-8")
    return path


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    """--inventory beats the environment, so a one-off run needs no exporting."""
    mine = _register_at(tmp_path / "somewhere")
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(tmp_path / "elsewhere"))
    path, how = register.resolve(str(mine))
    assert path == mine.resolve()
    assert how == "--inventory"


def test_an_explicit_path_that_is_absent_is_refused(tmp_path):
    """A named file that is not there is an error, never a fallback."""
    with pytest.raises(SystemExit) as caught:
        register.resolve(str(tmp_path / "nope.json"))
    assert "no such file" in str(caught.value)


def test_docs_root_is_used_when_it_holds_the_register(tmp_path, monkeypatch):
    docs = tmp_path / "docs-repo"
    expected = _register_at(docs)
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(docs))
    path, how = register.resolve()
    assert path == expected.resolve()
    assert how == "TRADEFLOOR_DOCS"


def test_docs_root_without_a_register_does_not_fall_back_quietly(tmp_path,
                                                                monkeypatch):
    """Asked for the documentation's register and not given one, this must
    stop rather than substitute anything else.

    A silent substitution reports another register's figures under this
    one's name, and the run looks entirely normal.
    """
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(tmp_path / "empty"))
    with pytest.raises(SystemExit) as caught:
        register.resolve()
    message = str(caught.value)
    assert "holds no" in message
    assert "tools/remeasure/inventory.json" in message


def test_this_repository_keeps_no_register():
    """The copy that went stale is gone, and must not come back.

    Two copies of one register drift, and the one beside the code is the one
    nobody edits when a page changes.
    """
    assert not (REPO / "tools" / "remeasure" / "inventory.json").exists(), (
        "tools/remeasure/inventory.json is back in this repository. The "
        "register lives in tradefloor-docs, beside the pages it describes.")


def test_nothing_set_is_refused_with_a_pointer(monkeypatch):
    """A clone of this repository alone stops, and says where to look."""
    monkeypatch.delenv("TRADEFLOOR_DOCS", raising=False)
    with pytest.raises(SystemExit) as caught:
        register.resolve()
    message = str(caught.value)
    assert "tradefloor-docs" in message
    assert "TRADEFLOOR_DOCS" in message
    assert "--inventory" in message


@pytest.mark.parametrize("tool", ["remeasure.py", "resync.py"])
def test_each_tool_says_which_register_it_read(tool, tmp_path):
    """Naming it is the difference between a gate and a rumour."""
    mine = _register_at(tmp_path / "docs-repo")
    done = subprocess.run(
        [sys.executable, str(REPO / "tools" / "remeasure" / tool),
         "--inventory", str(mine)]
        + (["--list"] if tool == "remeasure.py" else
           # A stored run, not `out/`, which is not committed: on a fresh
           # clone the default figures file does not exist, and a test that
           # depends on one being there passes only on a machine that has
           # already run the gate.
           ["--report", "--figures", "tools/remeasure/out-0.6.1/figures.json"]),
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    assert "register:" in done.stdout, (
        f"{tool} did not name the register it read. stdout:\n"
        f"{done.stdout[-800:]}\nstderr:\n{done.stderr[-800:]}"
    )


@pytest.mark.parametrize("tool", ["remeasure.py", "resync.py"])
def test_each_tool_without_a_register_points_at_it(tool):
    env = {k: v for k, v in os.environ.items() if k != "TRADEFLOOR_DOCS"}
    done = subprocess.run(
        [sys.executable, str(REPO / "tools" / "remeasure" / tool)]
        + (["--list"] if tool == "remeasure.py" else ["--lines"]),
        capture_output=True, text=True, cwd=REPO, timeout=300, env=env,
    )
    assert done.returncode != 0
    assert "Traceback" not in done.stderr, done.stderr[-800:]
    assert "tradefloor-docs" in done.stderr, done.stderr[-800:]


def test_the_code_repository_is_searched_before_the_documentation(tmp_path,
                                                                  monkeypatch):
    """Both repositories carry a README.md, and a few rows mean the code one.

    `readme.residual` is a claim about the factor decomposition, stated in the
    code README and nowhere on the site. Searched documentation-first it would
    find the site's README, fail to locate its value, and be reported as a
    figure that moved when nothing had moved at all.
    """
    docs = tmp_path / "docs-repo"
    docs.mkdir()
    (docs / "README.md").write_text("a different README", encoding="utf-8")
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(docs))

    roots = register.page_roots()
    assert roots[0] == register.CODE_ROOT, (
        f"code repository must be searched first, got {roots}")
    assert docs.resolve() in roots

    first = next(r / "README.md" for r in roots if (r / "README.md").is_file())
    assert first == (register.CODE_ROOT / "README.md"), (
        "a bare README.md resolved to the documentation site's, which is not "
        "the file those rows were written about")


def test_the_documentation_root_is_searched_at_all(tmp_path, monkeypatch):
    """The point of the move: pages living in the other repository resolve."""
    docs = tmp_path / "docs-repo"
    (docs / "docs").mkdir(parents=True)
    page = docs / "docs" / "scenarios.html"
    page.write_text("<p>0.052</p>", encoding="utf-8")
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(docs))

    found = [r / "docs" / "scenarios.html" for r in register.page_roots()
             if (r / "docs" / "scenarios.html").is_file()]
    assert found == [page], f"documentation page did not resolve: {found}"


def test_a_missing_measurement_run_is_explained(tmp_path):
    """`out/` is not committed, so this is what a fresh clone hits.

    A bare FileNotFoundError names a path and not the thing to do about it,
    and it arrived before the register line, so the run said nothing at all
    about what it had been asked to read.
    """
    mine = _register_at(tmp_path / "docs-repo")
    done = subprocess.run(
        [sys.executable, str(REPO / "tools" / "remeasure" / "resync.py"),
         "--inventory", str(mine),
         "--report", "--figures", "tools/remeasure/out/definitely-absent.json"],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    combined = done.stdout + done.stderr
    assert "Traceback" not in combined, combined[-800:]
    assert "no measurement run at" in combined, combined[-800:]
    assert "remeasure.py" in combined, combined[-800:]
    assert "register:" in done.stdout, (
        "the run did not say which register it was going to read before "
        f"stopping. stdout: {done.stdout[-800:]}")


# ---------------------------------------------------------------------------
# bound figures
# ---------------------------------------------------------------------------

def _docs_with_fixture(tmp_path: Path) -> Path:
    docs = tmp_path / "docs-repo"
    fixture = docs / "tools" / "docs" / "learn" / "experiments.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(json.dumps({
        "grid": {
            "agents_table": [
                {"name": "mean_reversion", "pooled_capture": 0.9466,
                 "capture_range": [0.33, 1.58]},
                {"name": "momentum", "pooled_capture": -0.1031,
                 "capture_range": [-0.53, 0.39]},
            ],
            "separation": {"wins_a": 12, "wins_b": 0},
        },
        "rebalance": {"rows": [{"steps": 3, "return_pct": 38.6}]},
    }), encoding="utf-8")
    return docs


def test_a_register_knows_the_checkout_it_sits_in(tmp_path):
    docs = tmp_path / "docs-repo"
    path = _register_at(docs)
    assert register.docs_root_of(path) == docs.resolve()
    assert register.docs_root_of(tmp_path / "loose.json") is None


@pytest.mark.parametrize("path, expected", [
    ("grid.agents_table[name=momentum].pooled_capture", -0.1031),
    ("grid.agents_table[name=mean_reversion].capture_range[1]", 1.58),
    ("rebalance.rows[steps=3].return_pct", 38.6),
    (["grid.separation.wins_a", "grid.separation.wins_b"], "12-0"),
])
def test_a_bound_value_is_read_from_the_data_file(tmp_path, path, expected):
    """The page states what the build wrote from the file, so the file is
    the published value, whatever the row last typed."""
    docs = _docs_with_fixture(tmp_path)
    row = {"id": "x", "published": 0.0,
           "bound": {"file": "tools/docs/learn/experiments.json", "path": path}}
    assert register.bound_value(row, docs) == expected


@pytest.mark.parametrize("path", [
    "grid.agents_table[name=nobody].pooled_capture",
    "grid.nothing_here",
])
def test_a_bound_path_that_is_not_there_is_named(tmp_path, path):
    docs = _docs_with_fixture(tmp_path)
    row = {"id": "x", "bound": {"file": "tools/docs/learn/experiments.json",
                                "path": path}}
    with pytest.raises(LookupError) as caught:
        register.bound_value(row, docs)
    assert "x" in str(caught.value)


def test_remeasure_refuses_bound_rows_it_cannot_read_before_measuring(tmp_path):
    """A register copied somewhere without the data files its bound rows
    read must stop the run at once, not after minutes of measurement end in
    rows with no published value."""
    loose = tmp_path / "loose" / "inventory.json"
    loose.parent.mkdir()
    loose.write_text(json.dumps({"figures": [{
        "id": "agents.pooled_mr", "file": "docs/agents.html", "line": 1,
        "label": "x", "published": 0.947, "group": "arith",
        "key": "clean_sweep_p", "compare": {"kind": "round", "decimals": 3},
        "bound": {"file": "tools/docs/learn/experiments.json",
                  "path": "grid.agents_table[name=mean_reversion].pooled_capture"},
    }]}), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(REPO / "tools" / "remeasure" / "remeasure.py"),
         "--inventory", str(loose), "--only", "arith",
         "--out", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    assert done.returncode != 0
    assert "bound rows cannot read" in done.stderr, done.stderr[-800:]
    assert not (tmp_path / "out" / "figures.json").exists()
