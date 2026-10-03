"""The top-level API surface, held to its classification.

``tradefloor._api`` puts every name reachable as ``tradefloor.X`` in one tier:
stable, advanced, deprecated or internal. ``tests/api_surface.txt`` is the
sorted list of those names with their tiers. These tests fail when a name
appears at the top level, or disappears from it, without both being updated,
so every change to the public surface shows up in review as a change to
that file.

Regenerate the snapshot after editing ``_api.py`` with

    python tests/test_api_surface.py --write
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
import re
import subprocess
import sys
import warnings

import pytest

import tradefloor as tf
from tradefloor import _api

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests" / "api_surface.txt"
STUB = ROOT / "python" / "tradefloor" / "_core.pyi"

HEADER = (
    "# Every top-level name of the tradefloor package and its tier, from\n"
    "# python/tradefloor/_api.py. Regenerate with:\n"
    "#     python tests/test_api_surface.py --write\n"
)

FIX = ("Classify it in python/tradefloor/_api.py, then run "
       "`python tests/test_api_surface.py --write` and commit "
       "tests/api_surface.txt with the change.")


def snapshot_text() -> str:
    return HEADER + "".join(f"{name} {tier}\n"
                            for name, tier in _api.TIERS.items())


def reachable() -> set[str]:
    """Every name ``tradefloor.X`` answers after ``import tradefloor``.

    Read in a fresh interpreter. Importing a submodule binds it on the
    package, so in this process the answer depends on which tests ran first
    (``tradefloor.mcp`` appears once a test has imported it).
    """
    code = ("import json, tradefloor; print(json.dumps(sorted("
            "n for n in dir(tradefloor) if not n.startswith('_'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout
    return set(json.loads(out)) | {"__version__"} | set(_api.DEPRECATED)


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------

def test_every_top_level_name_is_classified():
    unlisted = sorted(reachable() - set(_api.TIERS))
    assert unlisted == [], (
        f"new top-level name(s) {unlisted} have no tier. {FIX}")


def test_every_classified_name_is_still_at_the_top_level():
    gone = sorted(set(_api.TIERS) - reachable())
    assert gone == [], (
        f"{gone} are classified but no longer reachable as tradefloor.X. "
        "Removing a stable or advanced name is a breaking change; if it is "
        f"meant, drop it from python/tradefloor/_api.py. {FIX}")


def test_no_name_is_in_two_tiers():
    listed = (list(_api.STABLE) + list(_api.ADVANCED) + list(_api.DEPRECATED)
              + list(_api.INTERNAL))
    repeated = sorted({n for n in listed if listed.count(n) > 1})
    assert repeated == []


def test_the_snapshot_matches_the_classification():
    assert SNAPSHOT.read_text(encoding="utf-8") == snapshot_text(), (
        f"tests/api_surface.txt is out of date with _api.py. {FIX}")


def test_the_tier_tuples_read_without_importing_the_package():
    """The documentation build parses ``_api.py`` as data."""
    tree = ast.parse((ROOT / "python/tradefloor/_api.py").read_text("utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"STABLE", "ADVANCED", "DEPRECATED", "INTERNAL"}:
                found[node.target.id] = ast.literal_eval(node.value)
    assert found["STABLE"] == _api.STABLE
    assert found["ADVANCED"] == _api.ADVANCED
    assert found["DEPRECATED"] == _api.DEPRECATED
    assert found["INTERNAL"] == _api.INTERNAL


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

def test_every_all_entry_resolves_without_a_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        missing = [n for n in tf.__all__ if not hasattr(tf, n)]
    assert missing == []
    assert len(tf.__all__) == len(set(tf.__all__))


def test_all_holds_only_stable_and_advanced_names():
    wrong = sorted(n for n in tf.__all__
                   if _api.TIERS.get(n) not in {"stable", "advanced"})
    assert wrong == []


def test_star_import_binds_no_deprecated_name_and_does_not_warn():
    namespace: dict = {}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        exec("from tradefloor import *", namespace)
    assert not set(_api.DEPRECATED) & set(namespace)


# ---------------------------------------------------------------------------
# Deprecated names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_api.DEPRECATED))
def test_a_deprecated_name_still_resolves_and_warns_at_the_caller(name):
    home = _api.DEPRECATED[name]
    with pytest.warns(DeprecationWarning) as caught:
        value = getattr(tf, name)
    assert value is getattr(importlib.import_module(home), name)
    assert len(caught) == 1
    message = str(caught[0].message)
    assert f"tradefloor.{name} " in message
    assert home in message and _api.REMOVAL in message
    assert caught[0].filename == __file__


@pytest.mark.parametrize("name", sorted(_api.DEPRECATED))
def test_from_import_of_a_deprecated_name_warns_once_at_the_import(name):
    source = f"from tradefloor import {name}\n"
    namespace: dict = {}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exec(compile(source, "caller.py", "exec"), namespace)
    assert namespace[name] is getattr(tf._core, name)
    assert [(w.category, w.filename, w.lineno) for w in caught] == [
        (DeprecationWarning, "caller.py", 1)]


def test_an_unknown_name_is_still_an_attribute_error():
    with pytest.raises(AttributeError, match="no attribute 'no_such_name'"):
        tf.no_such_name  # noqa: B018
    assert not hasattr(tf, "no_such_name")


def test_deprecated_names_are_engine_internals_the_stub_declares():
    """A deprecated name's home is where the stub says it is."""
    declared = {node.name for node in ast.parse(STUB.read_text("utf-8")).body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert set(_api.DEPRECATED.values()) == {"tradefloor._core"}
    assert sorted(set(_api.DEPRECATED) - declared) == []


def test_every_compiled_top_level_name_is_declared_in_the_stub():
    """What the package re-exports from the extension has a type.

    ``tests/test_stub_parity.py`` holds the stub against ``_core``; this
    holds the package's top level against the stub, so a re-export cannot
    reach users with no declaration behind it.
    """
    declared = set()
    for node in ast.parse(STUB.read_text("utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            declared.add(node.name)
    compiled = sorted(
        n for n in tf.__all__
        if getattr(getattr(tf, n), "__module__", None) in {"tradefloor._core", "_core"}
        or type(getattr(tf, n)).__name__ == "builtin_function_or_method")
    assert compiled, "found no compiled re-exports; check the probe"
    assert sorted(set(compiled) - declared) == []


# ---------------------------------------------------------------------------
# Nothing in this repository uses the deprecated spellings
# ---------------------------------------------------------------------------

SCANNED = ("python", "tests", "examples", "tools", "README.md", "docs")
ALIAS = re.compile(r"^\s*import tradefloor as (\w+)", re.M)
FROM_IMPORT = re.compile(r"from tradefloor import \(([^)]*)\)|from tradefloor import ([^\n]+)")


def _source(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".ipynb":
        cells = json.loads(text)["cells"]
        return "\n".join("".join(cell["source"]) for cell in cells)
    return text


def _files():
    for entry in SCANNED:
        root = ROOT / entry
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if (path.suffix in {".py", ".ipynb", ".md"}
                    and path.resolve() != pathlib.Path(__file__).resolve()
                    and not {".venv", "target", "__pycache__"} & set(path.parts)):
                yield path


def test_nothing_in_the_repository_reads_a_deprecated_name_from_the_top_level():
    names = "|".join(sorted(_api.DEPRECATED))
    offenders = []
    for path in _files():
        text = _source(path)
        aliases = {"tradefloor", *ALIAS.findall(text)}
        attribute = re.compile(
            rf"(?<![\w.])(?:{'|'.join(aliases)})\.(?:{names})\b")
        if attribute.search(text):
            offenders.append(str(path.relative_to(ROOT)))
            continue
        for match in FROM_IMPORT.finditer(text):
            body = re.sub(r"#.*", "", match.group(1) or match.group(2))
            imported = {part.strip().split(" as ")[0].strip()
                        for part in body.replace("\\", " ").split(",")}
            if imported & set(_api.DEPRECATED):
                offenders.append(str(path.relative_to(ROOT)))
                break
    assert offenders == [], (
        f"{offenders} read a deprecated name from the top level; import it "
        "from the module tradefloor._api.DEPRECATED names instead")


def test_importing_the_package_and_its_modules_raises_no_deprecation():
    """Run fresh, so the import is not already cached by this process."""
    code = (
        "import importlib, pkgutil, warnings\n"
        "warnings.simplefilter('error', DeprecationWarning)\n"
        "import tradefloor\n"
        "for info in pkgutil.walk_packages(tradefloor.__path__, 'tradefloor.'):\n"
        "    if info.name.endswith('__main__'):\n"
        "        continue\n"
        "    try:\n"
        "        importlib.import_module(info.name)\n"
        "    except ImportError:\n"
        "        pass\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        SNAPSHOT.write_text(snapshot_text(), encoding="utf-8")
        print(f"wrote {SNAPSHOT.relative_to(ROOT)}: {len(_api.TIERS)} names")
    else:
        sys.exit("usage: python tests/test_api_surface.py --write")
