"""`examples/mcp-client/client.py` runs against the real server and prints what its README says."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib

import pytest

pytest.importorskip("mcp")

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent
          / "examples" / "mcp-client" / "client.py")


def test_the_mcp_client_example_reads_two_branches_of_one_fork():
    spec = importlib.util.spec_from_file_location("mcp_client_example", SCRIPT)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    out = asyncio.run(example.main())
    readme = (SCRIPT.parent / "README.md").read_text(encoding="utf-8")
    assert f"{out['tools']} tools:" in readme
    assert f"{out['control']:.4f}" in readme
    assert f"{out['branch']:.4f}" in readme
    assert out["branch"] != out["control"]
