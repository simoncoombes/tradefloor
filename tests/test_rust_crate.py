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


def rust_job() -> str:
    job = re.search(r"^  rust:\n(.*?)(?=^  \S)", suite(), re.M | re.S)
    assert job, "suite.yml has no rust job"
    return job.group(1)


def test_ci_runs_clippy_on_everything_with_warnings_as_errors():
    """Clippy runs, on every target and feature, and a warning fails it.

    0.8.5 shipped with plain `cargo clippy --all-targets` exiting 101 on a
    deny-by-default lint, and 26 more warnings under `-D warnings`, because
    no workflow ran clippy at all.
    """
    runs = [line.strip() for line in rust_job().splitlines()
            if line.strip().startswith("run: cargo clippy")]
    assert runs, "the rust job does not run clippy"
    run = runs[0]
    for flag in ("--all-targets", "--all-features", "-D warnings"):
        assert flag in run, f"clippy runs without {flag}: {run}"
