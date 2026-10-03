"""Where each published fact about a release lives, and every copy of it.

Each fact has one source. Everything else that states it is a copy, and a
copy is either rewritten from the source by `bump.py` or checked against it
by `tests/test_metadata_consistency.py`, which runs with the rest of the
suite. The table:

    fact                  source                                  copies
    package version       pyproject.toml [project] version        VERSION
    release date          CITATION.cff date-released              YEAR
    default preset        DEFAULT_PRESET_NAME, rust/src/params.rs  PRESET
    documentation URL     pyproject.toml [project.urls] Documentation
    homepage              pyproject.toml [project.urls] Homepage
    supported Pythons     pyproject.toml requires-python and the
                          `Programming Language :: Python :: 3.N`
                          classifiers

The version locations used to be listed in RELEASING.md and in
`check.py` separately, and the README citation was in neither list, so
the BibTeX entry still said 0.8.6 at the v0.8.6 tag after the other files
had moved on. One list here, read by the bump, the release check and the
test, is the fix: a location added to it is rewritten and checked from
then on.

This module reads files and nothing else. It does not import tradefloor, so
it works on a tree whose extension is not built.

    python tools/release/metadata.py      # print every fact and its copies
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The marketing site. A page on it that documents the library is a stale
#: link: the documentation moved to the host `[project.urls] Documentation`
#: names, and the old addresses only redirect.
MARKETING_HOST = "tradefloor.dev"


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def pyproject() -> dict:
    return tomllib.loads(read("pyproject.toml"))


# --------------------------------------------------------------------------
# The sources
# --------------------------------------------------------------------------

def package_version() -> str:
    """`[project] version` in pyproject.toml: the wheel, the sdist, PyPI."""
    return pyproject()["project"]["version"]


def release_date() -> str:
    """`date-released` in CITATION.cff: the day the version was tagged."""
    m = re.search(r'(?m)^date-released: "?([0-9]{4}-[0-9]{2}-[0-9]{2})"?\s*$',
                  read("CITATION.cff"))
    if m is None:
        raise SystemExit("CITATION.cff has no date-released")
    return m.group(1)


def default_preset() -> str:
    """`DEFAULT_PRESET_NAME` in rust/src/params.rs, which the engine runs."""
    m = re.search(r'(?m)^pub const DEFAULT_PRESET_NAME: &str = "([^"]+)";',
                  read("rust/src/params.rs"))
    if m is None:
        raise SystemExit("rust/src/params.rs has no DEFAULT_PRESET_NAME")
    return m.group(1)


def docs_url() -> str:
    """`[project.urls] Documentation`, without a trailing slash."""
    return pyproject()["project"]["urls"]["Documentation"].rstrip("/")


def homepage() -> str:
    """`[project.urls] Homepage`, without a trailing slash."""
    return pyproject()["project"]["urls"]["Homepage"].rstrip("/")


def python_floor() -> str:
    """The oldest supported Python, from `requires-python = ">=3.N"`."""
    spec = pyproject()["project"]["requires-python"]
    m = re.fullmatch(r">=\s*(3\.\d+)", spec.strip())
    if m is None:
        raise SystemExit(f"requires-python is {spec!r}; expected '>=3.N'")
    return m.group(1)


def python_versions() -> list[str]:
    """Every `Programming Language :: Python :: 3.N` classifier, oldest first."""
    prefix = "Programming Language :: Python :: "
    found = [c[len(prefix):] for c in pyproject()["project"]["classifiers"]
             if re.fullmatch(re.escape(prefix) + r"3\.\d+", c)]
    return sorted(found, key=lambda v: int(v.split(".")[1]))


# --------------------------------------------------------------------------
# The copies
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Location:
    """One place a fact is written: a file and a pattern whose one group
    is the value. `count` is how many times the pattern must match, so a
    copy that was deleted or duplicated is reported rather than missed."""

    path: str
    pattern: str
    reader: str
    count: int = 1

    def values(self, text: str | None = None) -> list[str]:
        text = read(self.path) if text is None else text
        return [m.group(1) for m in re.finditer(self.pattern, text)]

    def rewrite(self, text: str, value: str) -> str:
        found = self.values(text)
        if len(found) != self.count:
            raise SystemExit(
                f"{self.path}: expected {self.count} match(es) of "
                f"{self.pattern!r}, found {len(found)}; fix the file or the "
                f"pattern in tools/release/metadata.py")

        def put(m: re.Match) -> str:
            start, end = m.span(1)
            whole = m.group(0)
            offset = m.start(0)
            return whole[:start - offset] + value + whole[end - offset:]

        return re.sub(self.pattern, put, text)


#: Every copy of the package version. The source, pyproject.toml, is the
#: first row so that `bump.py` rewrites it with the rest.
VERSION = (
    Location("pyproject.toml", r'(?m)^version = "([^"]+)"$',
             "the wheel, the sdist, PyPI"),
    Location("rust/Cargo.toml", r'(?m)^version = "([^"]+)"$',
             "the crate, crates.io"),
    Location("CITATION.cff", r"(?m)^version: (\S+)$",
             "GitHub's citation button and anyone citing a result"),
    Location("mcpb/manifest.json", r'(?m)^  "version": "([^"]+)",$',
             "the MCP bundle for Claude Desktop and Smithery"),
    Location("mcpb/pyproject.toml", r'(?m)^version = "([^"]+)"$',
             "the MCP bundle's project"),
    Location("mcpb/pyproject.toml", r'"tradefloor\[mcp\]==([^"]+)"',
             "the version the MCP bundle installs"),
    Location("server.json", r'(?m)^ {2,6}"version": "([^"]+)",$',
             "the MCP Registry entry and the PyPI package it lists", count=2),
    Location("server.json", r'"tradefloor\[mcp\]==([^"]+)"',
             "the version `uvx --with` installs for the registry"),
    Location("README.md", r"(?m)^  version = \{([^}]+)\},$",
             "the README's BibTeX entry"),
    Location("README.md", r'"tradefloor ([0-9][^,\s]*),\s+preset ',
             "the README's example of how to cite a run in the text"),
)

#: Every copy of the release year, which is the year of `date-released`.
YEAR = (
    Location("README.md", r"(?m)^  year    = \{(\d{4})\},$",
             "the README's BibTeX entry"),
)

#: Every place that names the default preset as the one to cite.
PRESET = (
    Location("README.md", r"(?m)^  note    = \{Model preset (pt-v\d+)\}$",
             "the README's BibTeX entry"),
    Location("README.md", r'"tradefloor [^,\s]+,\s+preset (pt-v\d+),',
             "the README's example of how to cite a run in the text"),
    Location("CITATION.cff", r"for example (pt-v\d+)",
             "the citation message and its comment", count=2),
    Location(".zenodo.json", r"for example (pt-v\d+)",
             "the Zenodo record's description"),
)


#: Sentences that call a preset the default, in the two READMEs. Checked,
#: never rewritten: a release that moves the default rewrites the prose
#: around these too (RELEASING.md, step 5b), which no pattern can do.
#: "`pt-v20` became the default in 0.8.5" is history and is not here.
PRESET_PROSE = (
    r"[Oo]n (pt-v\d+), the default",
    r"default preset, `(pt-v\d+)`",
    r"the default, `(pt-v\d+)`",
    r"`(pt-v\d+)` is the default",
    r"default preset is `(pt-v\d+)`",
)
PRESET_PROSE_FILES = ("README.md", "rust/README.md")


def preset_prose() -> list[tuple[str, str]]:
    """Every (file, preset) a README sentence calls the default."""
    return [(path, m.group(1)) for path in PRESET_PROSE_FILES
            for pattern in PRESET_PROSE
            for m in re.finditer(pattern, read(path))]


def expected() -> list[tuple[Location, str]]:
    """Every copy paired with the value its source gives it."""
    version, year, preset = (package_version(), release_date()[:4],
                             default_preset())
    return ([(loc, version) for loc in VERSION]
            + [(loc, year) for loc in YEAR]
            + [(loc, preset) for loc in PRESET])


def drift() -> list[str]:
    """Every copy that disagrees with its source, as a readable line."""
    out = []
    for loc, want in expected():
        found = loc.values()
        if len(found) != loc.count:
            out.append(f"{loc.path}: {loc.reader}: expected {loc.count} "
                       f"match(es), found {len(found)}")
        elif any(v != want for v in found):
            out.append(f"{loc.path}: {loc.reader}: says "
                       f"{', '.join(sorted(set(found)))}, source says {want}")
    return out


def declared_versions() -> dict[str, str]:
    """The version as each location states it, keyed by file and reader."""
    out: dict[str, str] = {}
    for loc in VERSION:
        for i, value in enumerate(loc.values()):
            suffix = f" #{i + 1}" if loc.count > 1 else ""
            out[f"{loc.path} ({loc.reader}){suffix}"] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args()
    print(f"package version   {package_version()}")
    print(f"release date      {release_date()}")
    print(f"default preset    {default_preset()}")
    print(f"documentation     {docs_url()}")
    print(f"homepage          {homepage()}")
    print(f"python            >={python_floor()}: {', '.join(python_versions())}")
    print()
    for loc, want in expected():
        found = ", ".join(loc.values()) or "MISSING"
        mark = "ok  " if all(v == want for v in loc.values()) and \
            len(loc.values()) == loc.count else "FAIL"
        print(f"  {mark}  {loc.path:<20} {found:<12} {loc.reader}")
    return 1 if drift() else 0


if __name__ == "__main__":
    sys.exit(main())
