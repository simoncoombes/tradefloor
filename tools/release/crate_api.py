"""Check the Rust crate's public API against the newest published crate.

    python tools/release/crate_api.py              # the version Cargo.toml states
    python tools/release/crate_api.py --version 0.10.1
    python tools/release/crate_api.py --plan       # say what it would run

The crate follows Cargo's semver rules from 0.10.0. While it is 0.x, a
minor bump (0.10 to 0.11) may break the API and a patch bump (0.10.0 to
0.10.1) may not. This reads the newest version on crates.io, works out
which kind of release the new version is against it, and runs
`cargo semver-checks check-release` with that release type, so a patch
release that breaks the API fails and a minor release that breaks it
passes and has to list each break in the CHANGELOG.

The release type is passed explicitly rather than left to cargo-semver-checks
to infer, because on dev the version can equal the published one (nothing
is bumped until the release), and the tool's guess for that case is not
written down anywhere this repository controls. cargo-semver-checks calls
a Cargo-compatible release `minor`, which is what a 0.x patch release is,
and equal versions are checked the same way: a break on dev fails until the
version moves to the next 0.x minor.

The release workflow runs this before anything is published, and
`check.py` runs it as one of its rows. It needs network access and
cargo-semver-checks (`cargo install cargo-semver-checks --locked`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
CRATE = ROOT / "rust"
#: The crates.io sparse index entry for `tradefloor`: one JSON line per
#: published version, which needs no API token and no crate download.
INDEX = "https://index.crates.io/tr/ad/tradefloor"

Version = tuple[int, int, int]


def parse(text: str) -> Version:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ValueError(f"not a release version: {text!r}")
    return int(m[1]), int(m[2]), int(m[3])


def show(v: Version) -> str:
    return ".".join(map(str, v))


def cargo_version() -> Version:
    text = (CRATE / "Cargo.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("rust/Cargo.toml has no version line")
    return parse(m[1])


def published_versions(url: str = INDEX) -> list[Version]:
    """Every version on crates.io that is not yanked."""
    with urllib.request.urlopen(url, timeout=30) as reply:  # noqa: S310
        lines = reply.read().decode("utf-8").splitlines()
    out = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("yanked"):
            continue
        try:
            out.append(parse(entry["vers"]))
        except ValueError:
            continue  # a pre-release: never a baseline
    return out


def release_type(new: Version, old: Version) -> str:
    """The cargo-semver-checks release type `new` is against `old`.

    Cargo's rule: the leftmost non-zero component is the breaking one, so
    0.10.0 to 0.11.0 is a major release and 0.10.0 to 0.10.1 a minor one.
    Equal versions are checked as a minor release, which allows additions
    and refuses breaks.
    """
    if new < old:
        raise ValueError(f"{show(new)} is older than the published {show(old)}")
    if new[0] != old[0]:
        return "major"
    if new[0] == 0 and new[1] != old[1]:
        return "major"
    return "minor"


def installed() -> bool:
    """Whether `cargo semver-checks` runs. Cargo finds a subcommand in its
    own bin directory, which need not be on PATH, so ask cargo."""
    if shutil.which("cargo") is None:
        return False
    out = subprocess.run(["cargo", "semver-checks", "--version"],
                         capture_output=True, check=False)
    return out.returncode == 0


def command(baseline: Version, kind: str) -> list[str]:
    return ["cargo", "semver-checks", "check-release",
            "--manifest-path", str(CRATE / "Cargo.toml"),
            "--baseline-version", show(baseline),
            "--release-type", kind]


def plan(version: Version | None = None) -> tuple[Version, Version, str]:
    """(new, baseline, release type) for `version`, or for Cargo.toml's."""
    new = version or cargo_version()
    older = [v for v in published_versions() if v <= new]
    if not older:
        raise SystemExit(f"nothing on crates.io at or below {show(new)}")
    baseline = max(older)
    return new, baseline, release_type(new, baseline)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", help="the version being released; "
                    "defaults to the one rust/Cargo.toml states")
    ap.add_argument("--plan", action="store_true",
                    help="print the baseline and release type and run nothing")
    args = ap.parse_args()

    new, baseline, kind = plan(parse(args.version) if args.version else None)
    meaning = ("breaks allowed, each listed in the CHANGELOG" if kind == "major"
               else "no breaks allowed")
    print(f"tradefloor {show(new)} against published {show(baseline)}: "
          f"{kind} release type, {meaning}")
    cmd = command(baseline, kind)
    print("$ " + " ".join(cmd), flush=True)
    if args.plan:
        return 0
    if not installed():
        print("cargo-semver-checks is not installed: "
              "cargo install cargo-semver-checks --locked", file=sys.stderr)
        return 2
    return subprocess.run(cmd, cwd=CRATE, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
