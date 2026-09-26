"""The Rust crate as a crates.io user meets it.

The Python suite imports the extension and never sees the crate's manifest,
README or rustdoc, and `cargo test` checks behaviour, not packaging. The 0.8.5
review of the published crate found defects in exactly that gap: no declared
minimum Rust, a README example that simulated nothing, clippy failing, and
broken doc links. None of them can move a market, so none of them is caught by
a known-answer digest. These tests read the files that ship and the workflow
that checks them, so each fix stays fixed.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUST = ROOT / "rust"
SUITE = ROOT / ".github" / "workflows" / "suite.yml"


def manifest() -> str:
    return (RUST / "Cargo.toml").read_text(encoding="utf-8")


def suite() -> str:
    return SUITE.read_text(encoding="utf-8")


def test_the_crate_declares_the_rust_it_needs_and_ci_builds_on_it():
    """`rust-version` is set, and the MSRV job checks that exact toolchain.

    Without `rust-version`, crates.io shows "unknown" and a user on an old
    toolchain gets "`from_bits` is not yet stable as a const fn" from deep in
    market/tick.rs instead of one line naming the Rust they need. A declared
    floor that nothing builds on is a guess, so the suite's `msrv` job must
    install the same version.
    """
    found = re.search(r'^rust-version\s*=\s*"([0-9.]+)"', manifest(), re.M)
    assert found, "rust/Cargo.toml declares no rust-version"
    declared = found.group(1)

    job = re.search(r"^  msrv:\n(.*?)(?=^  \S)", suite(), re.M | re.S)
    assert job, "suite.yml has no msrv job"
    toolchain = re.search(r"dtolnay/rust-toolchain@([0-9.]+)", job.group(1))
    assert toolchain, "the msrv job does not pin a numbered toolchain"
    assert toolchain.group(1) == declared, (
        f"rust-version is {declared} but the msrv job builds on "
        f"{toolchain.group(1)}")
    assert "--all-features" in job.group(1), (
        "the msrv job must build the python and wasm features too")
    assert "msrv" in re.search(r"^  complete:\n.*?needs: \[(.*?)\]",
                               suite(), re.M | re.S).group(1), (
        "`the suite is green` does not wait for the msrv job")
