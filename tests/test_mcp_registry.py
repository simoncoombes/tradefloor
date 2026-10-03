"""The MCP Registry entry (`server.json`) agrees with the package it lists.

The registry launches the server as `uvx --with "tradefloor[mcp]==X"
tradefloor mcp`, and it checks ownership by finding `mcp-name: <name>` in
the README PyPI shows. Each of those is a string in a different file, so a
release that moved one and not the others would publish a listing that
installs the wrong version or fails its ownership check.
"""
import json
import pathlib
import re

import tradefloor.__main__ as cli

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
VERSION = re.search(r'(?m)^version = "([^"]+)"',
                    (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)


def test_the_readme_names_the_server_the_registry_lists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"mcp-name: " + re.escape(SERVER["name"]) + r"(\s|-->|<)", readme)


def test_every_version_in_the_entry_is_the_package_version():
    assert SERVER["version"] == VERSION
    (pkg,) = SERVER["packages"]
    assert (pkg["registryType"], pkg["identifier"]) == ("pypi", "tradefloor")
    assert pkg["version"] == VERSION
    (extra,) = pkg["runtimeArguments"]
    assert (extra["name"], extra["value"]) == ("--with", f"tradefloor[mcp]=={VERSION}")


def test_the_entry_launches_the_mcp_subcommand(monkeypatch):
    (pkg,) = SERVER["packages"]
    assert [a["value"] for a in pkg["packageArguments"]] == ["mcp"]
    called = []
    monkeypatch.setattr(cli, "mcp_main", lambda: called.append(True))
    assert cli.main(["mcp"]) == 0
    assert called == [True]
