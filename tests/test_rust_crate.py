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


def test_ci_builds_the_docs_with_warnings_as_errors():
    """`cargo doc` runs in CI and a rustdoc warning fails it.

    0.8.5 shipped 24 rustdoc warnings: links to items that did not resolve
    from where they were written, and public docs linking private items. On
    docs.rs those render as plain text or dead links.
    """
    job = rust_job()
    runs = [line.strip() for line in job.splitlines()
            if line.strip().startswith("run: cargo doc")]
    assert runs, "the rust job does not build the docs"
    assert "--no-deps" in runs[0]
    assert re.search(r"RUSTDOCFLAGS:\s*-D warnings", job), (
        "cargo doc runs without RUSTDOCFLAGS=-D warnings")
    docs_rs = re.search(r"^\[package\.metadata\.docs\.rs\]\n(.*?)(?=^\[)",
                        manifest(), re.M | re.S)
    assert docs_rs, "Cargo.toml does not tell docs.rs which features to build"
    assert '"wasm"' in docs_rs.group(1)
    assert "--features wasm" in runs[0], (
        "CI must build the docs with the features docs.rs builds")


def readme_rust_blocks() -> list[str]:
    readme = (RUST / "README.md").read_text(encoding="utf-8")
    return re.findall(r"^```rust\n(.*?)^```", readme, re.M | re.S)


def test_the_readme_example_runs_as_a_doctest_and_moves_prices():
    """The README's example is compiled and run by `cargo test`, and trades.

    0.8.5's example built an engine and called `close_day(0)`. That settles a
    day and steps the economy but runs no ticks, so every price stayed where
    it started. Nothing ran the snippet, so nothing noticed. The crate root
    now includes the README, which makes each rust block a doctest, and the
    example asserts that the prices moved.
    """
    lib = (RUST / "src" / "lib.rs").read_text(encoding="utf-8")
    assert '#![doc = include_str!("../README.md")]' in lib, (
        "lib.rs does not include the README, so its example is not a doctest")
    blocks = readme_rust_blocks()
    assert blocks, "the README has no rust example"
    example = blocks[0]
    for call in ("open_market()", "run_session(", "close_day("):
        assert call in example, f"the README example never calls {call}"
    assert example.index("open_market()") < example.index("run_session(") \
        < example.index("close_day("), "the day loop is out of order"
    assert re.search(r"assert_ne!\(engine\.prices\(\), \w+\)", example), (
        "the README example does not check that the prices moved")

    readme = (RUST / "README.md").read_text(encoding="utf-8")
    bare = re.findall(r"^```\n", readme, re.M)
    fences = re.findall(r"^```", readme, re.M)
    # Every opening fence names a language. rustdoc compiles an unlabelled
    # block as Rust, so `pip install tradefloor` in a bare fence would fail
    # the doctest run. Closing fences are the bare ones, one per block.
    assert len(bare) == len(fences) // 2, (
        "a README code block has no language, and rustdoc would compile it")


def excluded_tests() -> set[str]:
    return set(re.findall(r'"tests/(\w+)\.rs"', manifest()))


def test_the_package_ships_what_it_says_and_no_dev_scripts():
    """The README names every integration test that ships, and nothing else.

    0.8.5's README and manifest said three integration tests ship. Four did
    (depth_counterfactual was missing from the list), and sync-goldens.py, a
    script that reads the excluded goldens/, shipped too.
    """
    shipped = {p.stem for p in (RUST / "tests").glob("*.rs")} - excluded_tests()
    assert shipped, "every integration test is excluded"
    readme = (RUST / "README.md").read_text(encoding="utf-8")
    scope = readme[readme.index("## Scope of this crate"):]
    named = set(re.findall(r"`(\w+)`", scope))
    assert shipped <= named, (
        f"README does not name shipped tests: {sorted(shipped - named)}")
    for name in named & {p.stem for p in (RUST / "tests").glob("*.rs")}:
        assert name in shipped, f"README names {name}, which does not ship"
    assert re.search(r'^\s*"sync-goldens\.py",', manifest(), re.M), (
        "sync-goldens.py is not excluded from the package")
    assert "tradefloor-design" not in manifest(), (
        "Cargo.toml ships a comment pointing into a private repository")


def test_cargo_test_is_optimised():
    """`cargo test` builds optimised, so the unit tests finish in minutes.

    At opt-level 0 the unit tests took about 17 minutes on a shared machine.
    Optimising cannot move a result, because Rust does not reassociate or
    fuse floating point at any opt-level.
    """
    profile = re.search(r"^\[profile\.test\]\n(.*?)(?=^\[|\Z)", manifest(),
                        re.M | re.S)
    assert profile, "Cargo.toml has no [profile.test]"
    assert re.search(r"^opt-level\s*=\s*[123]\s*$", profile.group(1), re.M)


def changelog_085() -> str:
    text = "\n" + (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("\n## 0.8.5\n")
    return text[start:text.index("\n## ", start + 1)]


def test_the_rust_api_breaks_since_0_8_1_are_listed_and_the_growing_structs_are_closed():
    """0.8.5 breaks Rust code written for 0.8.1, and the release says so.

    crates.io's newest crate before this release was 0.8.1, and Cargo treats
    0.8.5 as a compatible update, so `tradefloor = "0.8"` moves to it on
    `cargo update`. Seeds went from u32 to u64, `tick_components` rows from
    eight entries to nine, a dozen public structs gained fields and the
    default preset moved. The 0.8.5 changelog covered only the Python
    surface. It now has a section that names each Rust change (below the
    release-note marker, since the note is at its 250-word budget), the
    crate README says how to stay on 0.8.1, and the two structs
    a user is told to build are `#[non_exhaustive]` so the next added field
    breaks nothing. The compile_fail doctests on those structs prove a
    literal is refused; this test keeps the attribute and the notes in place.
    """
    section = changelog_085()
    assert "### The Rust crate since 0.8.1" in section, (
        "the 0.8.5 changelog has no section on the Rust API changes")
    rust = section[section.index("### The Rust crate since 0.8.1"):]
    for item in ("Engine::new", "universe::random_universe", "GameRng::from_seed",
                 "GameRng::substream", "GameRng::surgery", "Pcg32::new",
                 "fixed_simulation_digest", "Engine::tick_components",
                 "COMPONENT_COUNT", "state_hash_with_pending", "SessionRequest",
                 "TickInputs", "TickStock", "LiveFactors", "DailyInputs",
                 "EconomyState", "OrderBook", "ModelParams", "DEFAULT_PRESET_NAME",
                 "pt-v20", '"=0.8.1"'):
        assert item in rust, f"'The Rust crate since 0.8.1' does not name {item}"

    readme = (RUST / "README.md").read_text(encoding="utf-8")
    assert "## Upgrading from 0.8.1" in readme
    assert '"=0.8.1"' in readme

    src = RUST / "src"
    for path, header in ((src / "engine.rs", "pub struct SessionRequest<'a> {"),
                         (src / "params.rs", "pub struct ModelParams {")):
        text = path.read_text(encoding="utf-8")
        before = text[:text.index(header)].rstrip().splitlines()[-3:]
        assert "#[non_exhaustive]" in before, (
            f"{header} is not #[non_exhaustive], so a new field breaks users")
        assert re.search(r"^/// ```compile_fail", text, re.M), (
            f"{path.name} has no compile_fail doctest refusing a struct literal")

    releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
    assert "cargo semver-checks check-release" in releasing, (
        "RELEASING.md does not check the Rust API against crates.io")
