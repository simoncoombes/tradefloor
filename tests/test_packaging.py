"""What a fresh install actually contains.

The library's own tests all run against a package that is already there. That
leaves a gap the size of the first thing a new user does, and two defects lived
in it:

- `.gitignore` named `python/pretium/` after the directory had become
  `python/tradefloor/`, so `maturin develop` began writing an unignored 1.1 MB
  extension and a 1.0 MB debug database into the source tree. Both were
  committed. The committed extension then SHADOWED the real one for anyone
  importing from `python/`, and it was stale enough to raise `ImportError:
  cannot import name 'ModelParams'` on a tree that built and tested fine.
- Because maturin packages everything under `python-source`, the stray
  `pretium.pdb` was copied into every wheel built from such a tree. tradefloor
  0.5.0 on PyPI ships it: 1,019,904 bytes of Windows debug symbols, named after
  the project's old name, inside a package whose whole point is that it has no
  dependencies and nothing you did not ask for.

Neither is a simulation bug and neither could have been found by a test of the
simulation. They are checked here instead.
"""

import pathlib
import re
import subprocess
import zipfile

import pytest

import tradefloor as tf

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "python" / "tradefloor"

#: Anything a compiler produced. A source distribution that carries one is
#: shipping a binary nobody reviewed, for a platform nobody chose.
BUILD_SUFFIXES = {".pyd", ".so", ".dylib", ".pdb", ".lib", ".exp", ".obj",
                  ".dll", ".a"}


def tracked_files():
    try:
        done = subprocess.run(["git", "ls-files"], cwd=ROOT, text=True,
                              capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git is not available")
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in done.stdout.splitlines() if line]


def test_the_repository_tracks_no_compiled_artefacts():
    """A clone must contain source, and the licences, and nothing built.

    Stated over the whole tree rather than over the package directory, because
    the way this happened was a rename moving the directory out from under the
    rule that covered it. A check that also names a directory would have
    followed it into the same hole.
    """
    binaries = [name for name in tracked_files()
                if pathlib.PurePosixPath(name).suffix in BUILD_SUFFIXES]
    assert binaries == [], (
        "these compiled files are committed: "
        + ", ".join(binaries)
        + ". They are build output, they change on every rebuild, and maturin "
        "packages whatever it finds under python-source into the wheel."
    )


def test_the_ignore_rules_cover_the_package_directory_that_exists():
    """The rule has to name the directory the build writes to.

    `python/pretium/*.pyd` was correct until the rename and silently inert
    afterwards: an ignore rule for a path that does not exist matches nothing
    and reports nothing.
    """
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    package = PACKAGE.name
    for suffix in ("pyd", "so", "pdb"):
        assert f"python/{package}/*.{suffix}" in rules, (
            f"`.gitignore` does not ignore python/{package}/*.{suffix}, which "
            "is where `maturin develop` writes. The package directory is "
            f"python/{package}/; if it has been renamed again, this rule has "
            "to follow it."
        )


def test_the_version_is_available_programmatically():
    """`tf.__version__` is what a manifest records and what a bug report
    quotes, so it must exist and must agree with the packaging metadata."""
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", tf.__version__), tf.__version__

    declared = re.search(r'^version = "([^"]+)"',
                         (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                         re.MULTILINE)
    assert declared is not None, "pyproject.toml has no version"
    assert tf.__version__ == declared.group(1), (
        f"the built package reports {tf.__version__} and pyproject.toml "
        f"declares {declared.group(1)}; one of them is stale"
    )


def _wheels():
    return sorted(p for directory in ROOT.glob("dist*")
                  for p in directory.glob("*.whl"))


@pytest.mark.parametrize("wheel", _wheels(), ids=lambda p: p.name)
def test_a_built_wheel_carries_no_build_output(wheel):
    """The check that would have caught `pretium.pdb` before it was published.

    Skipped when there is no wheel to look at, which is most local runs; the
    release pipeline builds one on every target, so this runs where it counts.
    The compiled extension itself is expected and is the one thing excluded
    from the rule.
    """
    with zipfile.ZipFile(wheel) as archive:
        stowaways = [
            name for name in archive.namelist()
            if pathlib.PurePosixPath(name).suffix in BUILD_SUFFIXES
            and not pathlib.PurePosixPath(name).name.startswith("_core.")
        ]
    assert stowaways == [], (
        f"{wheel.name} carries build output that is not the extension: "
        + ", ".join(stowaways)
    )


@pytest.mark.parametrize("wheel", _wheels(), ids=lambda p: p.name)
def test_a_built_wheel_carries_its_types(wheel):
    """The stub and the PEP 561 marker have to be INSTALLED, not merely
    present in the source tree. A stub that does not ship types nothing, and
    the failure is silent: a type checker just falls back to `Any`.

    Pinned beside the exclusion above because the two pull in opposite
    directions, and an over-broad exclusion would remove these without
    anything failing.
    """
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for required in ("tradefloor/py.typed", "tradefloor/_core.pyi"):
        assert required in names, f"{wheel.name} is missing {required}"
    assert any(name.startswith("tradefloor/_core.") and
               pathlib.PurePosixPath(name).suffix in BUILD_SUFFIXES
               for name in names), f"{wheel.name} has no compiled extension"
