"""The release type `tools/release/crate_api.py` checks the crate under.

The crate follows Cargo's semver rules from 0.10.0, and the release
workflow refuses a patch release that breaks the API. That refusal is only
as good as the mapping from a version bump to a cargo-semver-checks release
type, so the mapping is pinned here without touching the network.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "crate_api", ROOT / "tools" / "release" / "crate_api.py")
crate_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crate_api)


@pytest.mark.parametrize(("new", "old", "kind"), [
    # A 0.x minor bump is Cargo-incompatible: breaks allowed.
    ("0.10.0", "0.9.1", "major"),
    ("0.11.0", "0.10.3", "major"),
    # A 0.x patch bump is Cargo-compatible: no breaks.
    ("0.10.1", "0.10.0", "minor"),
    # The 0.8.1 to 0.8.5 release that broke the API would have failed.
    ("0.8.5", "0.8.1", "minor"),
    # Dev before a bump is checked as the next compatible release.
    ("0.9.1", "0.9.1", "minor"),
    ("1.0.0", "0.10.4", "major"),
    ("1.1.0", "1.0.2", "minor"),
    ("1.0.1", "1.0.0", "minor"),
])
def test_release_type_follows_cargo(new: str, old: str, kind: str) -> None:
    assert crate_api.release_type(crate_api.parse(new), crate_api.parse(old)) == kind


def test_an_older_version_is_refused() -> None:
    with pytest.raises(ValueError):
        crate_api.release_type(crate_api.parse("0.9.0"), crate_api.parse("0.9.1"))


def test_the_command_names_the_baseline_and_the_type() -> None:
    cmd = crate_api.command((0, 9, 1), "minor")
    assert cmd[:3] == ["cargo", "semver-checks", "check-release"]
    assert cmd[cmd.index("--baseline-version") + 1] == "0.9.1"
    assert cmd[cmd.index("--release-type") + 1] == "minor"


def test_cargo_toml_states_a_release_version() -> None:
    assert len(crate_api.cargo_version()) == 3
