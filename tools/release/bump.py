"""Set the release version everywhere it is written, or check that it is.

    python tools/release/bump.py 0.8.9 --date 2026-10-10   # rewrite
    python tools/release/bump.py --check                   # report drift
    python tools/release/bump.py --check 0.8.9 --tagged-on 2026-10-10

The rewrite sets pyproject.toml's version and CITATION.cff's date-released,
then rewrites every copy `metadata.py` lists from its source: the crate, the
citation file, the MCP bundle and registry entry, and the README's BibTeX
entry and citation example, including the year and the default preset. It
touches nothing else, refuses a file where a copy is missing or doubled,
and gives the same files for the same arguments. `--date` defaults to
today.

`--check` changes nothing and exits 1 if any copy disagrees with its
source. Given a version, it also fails unless the source says that version.
Given `--tagged-on`, it fails unless date-released is within a day of that
date, which allows for the tag being made in another time zone. The release
workflow runs it against the tag before it builds anything, because at
v0.5.0 and v0.7.1 CITATION.cff named the previous version, and v0.8.5
shipped a date-released a week before its tag.

Dev may carry a version ahead of the newest tag. Nothing here compares
against git, so that is allowed until the tag is pushed.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import metadata as md  # noqa: E402

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def rewrite(version: str, date: str) -> list[str]:
    """Write `version` and `date` to their sources, then every copy. Returns
    the files that changed."""
    texts: dict[str, str] = {}

    def text(path: str) -> str:
        if path not in texts:
            texts[path] = md.read(path)
        return texts[path]

    # The two sources first, through the same Location machinery as the
    # copies, so a malformed source stops the run before anything is written.
    cff = md.Location("CITATION.cff",
                      r'(?m)^date-released: "([0-9]{4}-[0-9]{2}-[0-9]{2})"$',
                      "the citation's release date")
    texts["CITATION.cff"] = cff.rewrite(text("CITATION.cff"), date)
    preset = md.default_preset()
    for loc, value in ([(loc, version) for loc in md.VERSION]
                       + [(loc, date[:4]) for loc in md.YEAR]
                       + [(loc, preset) for loc in md.PRESET]):
        texts[loc.path] = loc.rewrite(text(loc.path), value)

    changed = []
    for path, new in texts.items():
        target = md.ROOT / path
        if target.read_text(encoding="utf-8") != new:
            target.write_text(new, encoding="utf-8", newline="")
            changed.append(path)
    return changed


def check(version: str | None, tagged_on: str | None) -> list[str]:
    problems = md.drift()
    source = md.package_version()
    if version is not None and source != version:
        problems.append(f"pyproject.toml says {source}, expected {version}")
    if tagged_on is not None:
        released = datetime.date.fromisoformat(md.release_date())
        tagged = datetime.date.fromisoformat(tagged_on)
        if abs((released - tagged).days) > 1:
            problems.append(f"CITATION.cff date-released is {released}, "
                            f"the tag is dated {tagged}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="X.Y.Z")
    ap.add_argument("--date", help="the day you will tag, YYYY-MM-DD "
                                   "(default today)")
    ap.add_argument("--check", action="store_true",
                    help="change nothing; exit 1 on any disagreement")
    ap.add_argument("--tagged-on", help="with --check: the tag's date")
    args = ap.parse_args(argv)

    if args.version is not None and not VERSION_RE.fullmatch(args.version):
        ap.error(f"{args.version!r} is not X.Y.Z")
    for name in ("date", "tagged_on"):
        value = getattr(args, name)
        if value is not None:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                ap.error(f"--{name.replace('_', '-')} {value!r} is not "
                         f"YYYY-MM-DD")

    if args.check:
        problems = check(args.version, args.tagged_on)
        for line in problems:
            print(f"FAIL  {line}")
        if not problems:
            print(f"ok    every copy agrees: {md.package_version()}, "
                  f"released {md.release_date()}, preset "
                  f"{md.default_preset()}")
        return 1 if problems else 0

    if args.version is None:
        ap.error("give a version to set, or --check")
    date = args.date or datetime.date.today().isoformat()
    changed = rewrite(args.version, date)
    print(f"set {args.version}, released {date}, preset "
          f"{md.default_preset()}")
    for path in changed:
        print(f"  wrote {path}")
    print("then: cd rust && cargo update -p tradefloor, and "
          "maturin develop --release so the installed package reports it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
