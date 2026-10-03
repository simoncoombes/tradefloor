"""Every copy of a release fact agrees with the one place it comes from.

`tools/release/metadata.py` names the source of each fact (the version, the
release date, the default preset, the documentation and home URLs, the
supported Pythons) and lists the copies `tools/release/bump.py` rewrites.
These tests check every copy against its source, so a copy that a release
forgot fails the suite on the pull request rather than reaching PyPI, the
crate page or somebody's bibliography. Two releases shipped that way:
v0.5.0 and v0.7.1 each carried a CITATION.cff naming the previous version,
and v0.8.5's date-released was a week before its tag.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

import tradefloor as tf
from tradefloor.__main__ import main as cli_main

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "release"))
import bump  # noqa: E402
import metadata as md  # noqa: E402

sys.path.pop(0)


def _json(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _toml(name: str) -> dict:
    return tomllib.loads((ROOT / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Version, citation and default preset
# --------------------------------------------------------------------------

def test_every_copy_agrees_with_its_source():
    """The version, the release year and the cited preset, everywhere.

    The fix for a failure here is `python tools/release/bump.py X.Y.Z --date
    YYYY-MM-DD`, which rewrites every copy from its source. A copy that is
    missing or doubled is reported too, because a reworded README sentence
    would otherwise drop out of the check without anyone noticing.
    """
    drift = md.drift()
    assert not drift, (
        "these copies disagree with their source; run tools/release/bump.py "
        "rather than editing them by hand:\n  " + "\n  ".join(drift))


def test_the_bump_rewrites_every_copy_and_nothing_else(tmp_path):
    """`bump.py` and the check read one list, so whatever the check
    reports, the bump fixes. Run on a copy of the files: setting a new
    version and date moves every copy, and setting them back restores the
    files byte for byte."""
    files = sorted({loc.path for loc in (*md.VERSION, *md.YEAR, *md.PRESET)}
                   | {"CITATION.cff", "rust/src/params.rs"})
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, tmp_path / name)
    original = {n: (tmp_path / n).read_bytes() for n in files}
    before = md.ROOT
    md.ROOT = tmp_path
    try:
        version, date = md.package_version(), md.release_date()
        bump.rewrite("99.0.1", "2031-02-03")
        assert {v for loc in md.VERSION for v in loc.values()} == {"99.0.1"}
        assert {v for loc in md.YEAR for v in loc.values()} == {"2031"}
        assert md.release_date() == "2031-02-03"
        assert md.drift() == []
        bump.rewrite(version, date)
    finally:
        md.ROOT = before
    changed = [n for n in files if (tmp_path / n).read_bytes() != original[n]]
    assert not changed, changed


def test_the_package_reports_the_source_version():
    """`tf.__version__` and `tf.version()` come from the crate's version,
    which the extension is compiled with, so a stale build or a crate
    that was not bumped shows here."""
    assert tf.__version__ == tf.version() == md.package_version()


def test_the_cli_reports_the_source_version(capsys):
    """`tradefloor --version` and `python -m tradefloor --version`.

    The console script is what `[project.scripts]` installs, and it runs
    `tradefloor.__main__:main`, which is checked here in process. Where the
    script is installed beside this interpreter (a wheel or `maturin
    develop`), it is run as well, and so is `python -m tradefloor`.
    """
    scripts = md.pyproject()["project"]["scripts"]
    assert scripts["tradefloor"] == "tradefloor.__main__:main"
    want = f"tradefloor {md.package_version()}"

    with pytest.raises(SystemExit) as stop:
        cli_main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == want

    done = subprocess.run([sys.executable, "-m", "tradefloor", "--version"],
                          capture_output=True, text=True, timeout=120)
    assert (done.returncode, done.stdout.strip()) == (0, want), done.stderr

    script = shutil.which("tradefloor",
                          path=str(pathlib.Path(sys.executable).parent))
    if script is not None:
        done = subprocess.run([script, "--version"], capture_output=True,
                              text=True, timeout=120)
        assert (done.returncode, done.stdout.strip()) == (0, want), done.stderr


def test_the_default_preset_is_the_one_the_engine_runs():
    """`DEFAULT_PRESET_NAME` is the source. The engine, the envelope and
    every README sentence that calls a preset the default must agree."""
    preset = md.default_preset()
    assert tf.model_preset()["name"] == preset
    assert tf.ModelParams.from_preset().fingerprint == preset
    from tradefloor import envelope

    assert envelope.PRESET == preset
    named = md.preset_prose()
    assert len(named) >= 3, (
        f"only {len(named)} README sentences call a preset the default; the "
        "patterns in tools/release/metadata.py no longer match the prose")
    stale = sorted({f"{path} says {p}" for path, p in named if p != preset})
    assert not stale, f"the default is {preset}: {stale}"


# --------------------------------------------------------------------------
# Documentation and home URLs
# --------------------------------------------------------------------------

DOCS_HOST = "docs.tradefloor.dev"

#: The pages of the documentation site, from its sitemap. A link to any
#: other path on the docs host is a typo or a page that was renamed; the
#: site answers the old names with a redirect today, and nothing promises it
#: always will. Add a page here when the site gains one.
DOCS_PAGES = {
    "", "install.html", "reproducibility.html", "troubleshooting.html",
    "glossary.html", "guide-agent.html", "guide-compare.html",
    "guide-fork.html", "guide-replay.html", "llm-adapters.html",
    "mcp-local.html", "hosted.html", "api.html", "core-types.html",
    "evaluate.html", "api-counterfactual.html", "api-scenario.html",
    "api-integrations.html", "rl-environment.html", "parameters.html",
    "how-prices-are-made.html", "why-pt-v20.html", "how-its-measured.html",
    "release-notes.html", "support.html", "cite.html",
}

#: What the marketing site may be linked for besides its home page: files,
#: not pages.
ASSET = re.compile(r"\.(png|svg|ico|jpg|jpeg|webp|gif)$")

URL = re.compile(
    r"https?://((?:www\.|docs\.)?tradefloor\.dev)(/[^\s)\]>\"'`,;\\]*)?")

#: Where a stale link is history rather than a link: the changelog records
#: what each release said, and a remeasure report records the site as it
#: was when the report ran.
HISTORY = ("CHANGELOG.md", "tools/remeasure/out-")


def _tracked_text() -> list[pathlib.Path]:
    done = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                          text=True, timeout=60)
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    out = []
    for name in done.stdout.splitlines():
        if name.startswith(HISTORY) or name == "tests/test_metadata_consistency.py":
            continue
        path = ROOT / name
        if path.suffix in {".so", ".pyd", ".png", ".parquet", ".arrow",
                           ".gz", ".zip", ".pdf"} or not path.is_file():
            continue
        out.append(path)
    return out


def _links() -> list[tuple[str, str, str]]:
    """Every (file, host, path) linking to a tradefloor.dev host."""
    found = []
    for path in _tracked_text():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in URL.finditer(text):
            found.append((path.relative_to(ROOT).as_posix(), m.group(1),
                          (m.group(2) or "/").rstrip(".")))
    return found


def test_no_link_sends_a_reader_to_a_doc_page_on_the_marketing_site():
    """Documentation is at docs.tradefloor.dev. tradefloor.dev is the
    product site, and its old documentation addresses (`/mcp.html`,
    `/realism-envelope.html`, `/scenarios.html`) only redirect, to pages
    that have since been renamed. A link to the marketing host may name its
    home page or a file on it, never a page."""
    links = _links()
    assert any(host == DOCS_HOST for _, host, _ in links), (
        "found no link to the documentation site at all; the URL pattern "
        "in this test has stopped matching")
    stale = sorted({f"{name}: https://{host}{path}" for name, host, path in links
                    if host != DOCS_HOST and path not in ("/", "")
                    and not ASSET.search(path)})
    assert not stale, (
        "these link to a documentation page on the marketing site. Link to "
        f"the page on https://{DOCS_HOST} instead:\n  " + "\n  ".join(stale))


def test_every_docs_link_names_a_page_the_site_has():
    unknown = sorted({f"{name}: https://{host}{path}"
                      for name, host, path in _links()
                      if host == DOCS_HOST
                      and path.lstrip("/").split("#")[0] not in DOCS_PAGES
                      and not ASSET.search(path)})
    assert not unknown, (
        "these name a path the documentation site's sitemap does not list; "
        "check the page name (the old ones redirect) or add the page to "
        "DOCS_PAGES:\n  " + "\n  ".join(unknown))


def test_every_documentation_field_names_the_docs_site():
    """`[project.urls] Documentation` is the source. Each field that a
    registry or a form shows as "documentation" uses it, and each field
    that means the home page uses `Homepage`."""
    docs, home = md.docs_url(), md.homepage()
    assert docs == f"https://{DOCS_HOST}"
    assert home == "https://tradefloor.dev"

    manifest = _json("mcpb/manifest.json")
    assert manifest["documentation"].startswith(docs + "/")
    assert manifest["homepage"].rstrip("/") == home
    assert _json("server.json")["websiteUrl"].startswith(docs + "/")
    zenodo = _json(".zenodo.json")["related_identifiers"]
    assert [r["identifier"].rstrip("/") for r in zenodo
            if r["relation"] == "isDocumentedBy"] == [docs]
    template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(
        encoding="utf-8")
    m = re.search(r"- name: Documentation\n\s+url: (\S+)", template)
    assert m and m.group(1).rstrip("/") == docs, template

    assert _toml("rust/Cargo.toml")["package"]["homepage"].rstrip("/") == home
    cff = re.search(r'(?m)^url: "?([^"\s]+)"?$',
                    (ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    assert cff and cff.group(1).rstrip("/") == home


# --------------------------------------------------------------------------
# Supported Pythons
# --------------------------------------------------------------------------

def test_the_supported_pythons_are_one_list():
    """`requires-python` sets the floor and the classifiers list the
    versions CI runs. Every other statement of either is checked against
    them: the abi3 floor the extension is compiled for, the MCP bundle,
    the README badge, the interpreter each CI job pins, the `versions`
    matrix and the docstring of the test that compares them."""
    floor, versions = md.python_floor(), md.python_versions()
    minors = [int(v.split(".")[1]) for v in versions]
    assert versions and versions[0] == floor, (floor, versions)
    assert minors == list(range(minors[0], minors[0] + len(minors))), versions

    cargo = (ROOT / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    assert re.findall(r'"abi3-py3(\d+)"', cargo) == [floor.split(".")[1]]

    bundle = _toml("mcpb/pyproject.toml")["project"]["requires-python"]
    runtime = _json("mcpb/manifest.json")["compatibility"]["runtimes"]["python"]
    assert bundle == runtime == f">={floor}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    badge = re.findall(r"badge/python-(3\.\d+)%2B", readme)
    assert badge == [floor], badge
    stated = re.findall(r"CPython (3\.\d+)\+", readme)
    assert set(stated) <= {floor}, stated

    workflows = ROOT / ".github" / "workflows"
    pinned = {f"{p.name}: {v}" for p in workflows.glob("*.yml")
              for v in re.findall(r'python-version: "(3\.\d+)"',
                                  p.read_text(encoding="utf-8"))
              if v != floor}
    assert not pinned, (
        f"a CI job pins a Python other than the floor, {floor}; the other "
        f"versions belong in the `versions` matrix: {sorted(pinned)}")
    suite = (workflows / "suite.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python: \[([^\]]+)\]", suite)
    assert matrix is not None
    assert [v.strip().strip('"') for v in matrix.group(1).split(",")] == \
        versions[1:]

    doc = (ROOT / "tests" / "test_python_versions.py").read_text(
        encoding="utf-8")
    served = re.search(r"serves ([0-9., and]+?) from one build", doc)
    assert served is not None
    assert re.findall(r"3\.\d+", served.group(1)) == versions
