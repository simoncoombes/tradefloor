"""The claim register must be found deliberately, or not at all.

The register describes the documentation, and the documentation left this
repository at 0.5.0, so the register follows it. That makes "which register
did this run read" a question with more than one answer, and a gate that
reads a different one than its operator believes is no better than a gate
that reads none.

The failure guarded hardest here is the quiet fallback: TRADEFLOOR_DOCS set,
the register not present under it, and the tool reaching for the copy still
committed in this repository. That would report figures measured against the
old register while its operator believed the documentation's own was in use,
and nothing in the output would say so.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "remeasure"))

import register  # noqa: E402


def _register_at(root: Path) -> Path:
    path = root / register.IN_DOCS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"figures": []}), encoding="utf-8")
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
    """The whole point. Asked for the documentation's register and not given
    one, this must stop rather than substitute the copy committed here.

    A silent substitution reports the old register's figures under the new
    register's name, and the run looks entirely normal.
    """
    monkeypatch.setenv("TRADEFLOOR_DOCS", str(tmp_path / "empty"))
    with pytest.raises(SystemExit) as caught:
        register.resolve()
    message = str(caught.value)
    assert "holds no" in message
    assert "tools/remeasure/inventory.json" in message


def test_the_local_copy_answers_when_nothing_is_set(monkeypatch):
    """A clone of this repository alone still runs the gate.

    This is the transition fallback and not the destination: it exists so the
    move can land in the two repositories in either order.
    """
    monkeypatch.delenv("TRADEFLOOR_DOCS", raising=False)
    path, how = register.resolve()
    assert path == (REPO / "tools" / "remeasure" / "inventory.json").resolve()
    assert how == "the copy in this repository"


@pytest.mark.parametrize("tool", ["remeasure.py", "resync.py"])
def test_each_tool_says_which_register_it_read(tool):
    """Naming it is the difference between a gate and a rumour."""
    done = subprocess.run(
        [sys.executable, str(REPO / "tools" / "remeasure" / tool),
         "--list" if tool == "remeasure.py" else "--report"],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    assert "register:" in done.stdout, (
        f"{tool} did not name the register it read. stdout:\n"
        f"{done.stdout[-800:]}\nstderr:\n{done.stderr[-800:]}"
    )


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
