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
    description           pyproject.toml [project] description    PURPOSE
    scenario count        python/tradefloor/scenarios/*.yml       COUNTS
    preset count          preset_names(), rust/src/params.rs      COUNTS
    MCP tool counts       `@server.tool` in tradefloor/mcp.py     COUNTS
    latest release line   pyproject.toml [project] version        LATEST_LINE

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


def description() -> str:
    """`[project] description` in pyproject.toml: PyPI's one-line summary."""
    return pyproject()["project"]["description"]


def purpose() -> str:
    """What the description says the project is for, as a phrase a longer
    sentence can carry: the description without its article and full stop."""
    return re.sub(r"^(?:An?|The) ", "", description()).rstrip(".")


def scenario_names() -> list[str]:
    """The packaged scenarios: one `.yml` file each, shipped in the wheel."""
    return sorted(p.stem for p in
                  (ROOT / "python" / "tradefloor" / "scenarios").glob("*.yml"))


def preset_names() -> list[str]:
    """The shipped presets, from `preset_names()` in rust/src/params.rs."""
    m = re.search(r"pub fn preset_names\(\)[^{]*\{\s*&\[([^\]]+)\]",
                  read("rust/src/params.rs"))
    if m is None:
        raise SystemExit("rust/src/params.rs has no preset_names() list")
    return re.findall(r'"([^"]+)"', m.group(1))


def mcp_tools() -> dict[str, bool]:
    """Every tool the MCP server registers, in order, and whether its
    annotations mark it read-only. Read from the `@server.tool(...)`
    decorators in python/tradefloor/mcp.py without importing it, because
    the import needs the `mcp` extra and a built extension."""
    blocks = re.findall(r"(?ms)^@server\.tool\((.*?)^(?:async )?def (\w+)\(",
                        read("python/tradefloor/mcp.py"))
    return {name: bool(re.search(r"annotations=_READ_ONLY\b"
                                 r"|readOnlyHint=True", head))
            for head, name in blocks}


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


#: Files that state what the project is for. Each carries `purpose()` word
#: for word, whatever else its sentence says. Checked, never rewritten: the
#: sentence around the phrase differs in every file.
PURPOSE = (
    "CITATION.cff", ".zenodo.json", "rust/README.md", "server.json",
    "mcpb/manifest.json",
)

#: The registry's limit on `description` in server.json, in characters.
REGISTRY_DESCRIPTION_LIMIT = 100


def purpose_drift() -> list[str]:
    """Every description that has stopped saying what pyproject.toml says."""
    want = purpose()
    out = []
    crate = re.findall(r'(?m)^description = "([^"]+)"$',
                       read("rust/Cargo.toml"))
    if crate != [description()]:
        out.append(f"rust/Cargo.toml: description is {crate}, pyproject.toml "
                   f"says {description()!r}")
    for path in PURPOSE:
        if want not in " ".join(read(path).split()):
            out.append(f"{path}: does not say {want!r}")
    m = re.search(r'(?m)^  "description": "([^"]*)",$', read("server.json"))
    if m is None:
        out.append("server.json: no top-level description")
    elif len(m.group(1)) > REGISTRY_DESCRIPTION_LIMIT:
        out.append(f"server.json: description is {len(m.group(1))} "
                   f"characters, the registry allows "
                   f"{REGISTRY_DESCRIPTION_LIMIT}")
    return out


_UNITS = ("zero one two three four five six seven eight nine ten eleven "
          "twelve thirteen fourteen fifteen sixteen seventeen eighteen "
          "nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def number(text: str) -> int | None:
    """A count as prose writes it, `22` or `twenty-two`, below a hundred."""
    text = text.lower()
    if text.isdigit():
        return int(text)
    if text in _UNITS:
        return _UNITS.index(text)
    tens, _, unit = text.partition("-")
    if tens in _TENS and (not unit or unit in _UNITS[1:10]):
        return (20 + 10 * _TENS.index(tens)
                + (_UNITS.index(unit) if unit else 0))
    return None


#: Every sentence that states a count the code decides, by fact. The one
#: group is the number, in digits or words. Checked, never rewritten: a
#: count moves when a scenario, a preset or a tool is added, which is no
#: release step. The README and docs/MODEL.md said seven scenarios for a
#: release after the eighth shipped.
COUNTS = {
    "scenarios": (
        Location("README.md", r"\b([\w-]+) packaged\s+(?:shocks|scenarios)\b",
                 "the README's table of what the package holds, and its "
                 "scenario section", count=2),
        Location("docs/MODEL.md", r"(?m)^([\w-]+) scenarios ship:",
                 "the model document's list of packaged scenarios"),
    ),
    "presets": (
        Location("rust/README.md", r"\bship as ([\w-]+) presets\b",
                 "the crate page"),
    ),
    "MCP tools": (
        Location("README.md", r"\b([\w-]+) tools for a coding agent\b",
                 "the README's table of what the package holds"),
    ),
    "read-only MCP tools": (
        Location("README.md", r"\b([\w-]+) of them read-only\b",
                 "the README's table of what the package holds"),
    ),
}


def counts() -> dict[str, int]:
    """Each counted fact, from the code."""
    tools = mcp_tools()
    return {"scenarios": len(scenario_names()),
            "presets": len(preset_names()),
            "MCP tools": len(tools),
            "read-only MCP tools": sum(tools.values())}


def count_drift() -> list[str]:
    """Every sentence whose count disagrees with the code, and every
    pattern that has stopped matching its sentence."""
    out = []
    for fact, want in counts().items():
        for loc in COUNTS[fact]:
            found = loc.values()
            if len(found) != loc.count:
                out.append(f"{loc.path}: {loc.reader}: expected {loc.count} "
                           f"sentence(s) counting the {fact}, found "
                           f"{len(found)}")
            for said in found:
                if number(said) != want:
                    out.append(f"{loc.path}: {loc.reader}: says {said} "
                               f"{fact}, the code has {want}")
    return out


#: The row of SECURITY.md's table that names the latest release line, as
#: `0.11.x`. Checked, never rewritten: a minor release also moves the line
#: it replaces into the rows below, which no pattern can do. The row said
#: 0.9.x through 0.10.0, 0.10.1, 0.10.2 and 0.11.0.
LATEST_LINE = Location(
    "SECURITY.md",
    r"(?m)^\| (\d+\.\d+)\.x \| yes, the latest release line \|$",
    "the security policy's table of supported versions")


def support_drift() -> list[str]:
    """The security policy's latest line, if it is another release's."""
    want = ".".join(package_version().split(".")[:2])
    found = LATEST_LINE.values()
    if found != [want]:
        return [f"{LATEST_LINE.path}: {LATEST_LINE.reader}: names "
                f"{found or 'no line'} as the latest, the version is on "
                f"{want}.x"]
    return []


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
    print()
    print(f"description       {description()}")
    for fact, want in counts().items():
        print(f"{fact:<22}{want}")
    problems = purpose_drift() + count_drift() + support_drift()
    for line in problems:
        print(f"  FAIL  {line}")
    return 1 if drift() or problems else 0


if __name__ == "__main__":
    sys.exit(main())
