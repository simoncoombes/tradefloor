"""The mechanical half of the release runbook, in one command.

    python tools/release/check.py
    python tools/release/check.py --version 0.7.0   # what you intend to ship

It reports. It does not fix anything, tag anything or publish anything, and
it is not a substitute for the runbook: the steps that need judgement stay in
`RELEASING.md` and are listed at the end of the output so they are visible
rather than implied.

Every check here exists because the thing it looks at was wrong once and
nothing noticed. The version locations disagreed at 0.3.0. The README went on
naming the previous default. The changelog reached 1,257 words. A preset
record described a coefficient surface the build no longer shipped. A suite
was read against a stale compiled extension and reported drift that was not
there.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "release"))
sys.path.insert(0, str(ROOT / "tools" / "ci"))

import kat_history  # noqa: E402
import metadata as md  # noqa: E402

MARKER = "<!-- release-note-ends -->"
BUDGET = 250

OK, BAD, SKIP = "ok", "FAIL", "skip"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, state: str, note: str = "") -> None:
        self.rows.append((name, state, note))

    def render(self) -> int:
        width = max(len(n) for n, _, _ in self.rows)
        for name, state, note in self.rows:
            mark = {OK: "ok  ", BAD: "FAIL", SKIP: "skip"}[state]
            print(f"  {mark}  {name:<{width}}  {note}".rstrip())
        bad = sum(1 for _, s, _ in self.rows if s == BAD)
        print(f"\n{bad} problem{'' if bad == 1 else 's'}")
        return 1 if bad else 0


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def declared_versions() -> dict[str, str]:
    """The version as each file that carries it states it. The list of
    files is `metadata.VERSION`, which `bump.py` rewrites and
    `tests/test_metadata_consistency.py` checks."""
    return md.declared_versions()


def check_versions(r: Report, intended: str | None) -> None:
    seen = declared_versions()
    distinct = set(seen.values())
    if len(distinct) != 1:
        r.add("version locations agree", BAD,
              "; ".join(f"{k} {v}" for k, v in seen.items()))
        return
    found = distinct.pop()
    if intended and found != intended:
        r.add("version locations agree", BAD,
              f"all say {found}, you asked for {intended}")
        return
    r.add("version locations agree", OK, found)

    # The date a citation carries. It went stale at 0.3.0 and at 0.8.5,
    # under a version that had moved. The release workflow fails a tag more
    # than a day from it; this shows it so it can be set before the tag.
    m = re.search(r'(?m)^date-released: "?([0-9-]+)"?', read("CITATION.cff"))
    r.add("CITATION.cff date-released", OK if m else BAD,
          m.group(1) if m else "missing")

    drift = md.drift()
    r.add("every copy agrees with its source", OK if not drift else BAD,
          "version, year, preset" if not drift
          else f"{drift[0]}; fix with tools/release/bump.py")


def check_changelog(r: Report, intended: str | None) -> None:
    text = read("CHANGELOG.md")
    parts = re.split(r"(?m)^(## .+)$", text)
    if len(parts) < 3:
        r.add("changelog has a section", BAD, "no ## heading found")
        return
    name, body = parts[1][3:].strip(), parts[2]
    if intended and name != intended:
        r.add("changelog names this release", BAD,
              f"newest section is {name}, you asked for {intended}")
    else:
        r.add("changelog names this release", OK, name)

    words = len(body.split(MARKER)[0].split())
    r.add(f"release note within {BUDGET} words",
          OK if words <= BUDGET else BAD, f"{words} words")

    for dash in ("—", "–"):
        if dash in body:
            r.add("changelog punctuation is ASCII", BAD, "an em or en dash")
            break
    else:
        r.add("changelog punctuation is ASCII", OK)


def installed():
    try:
        import tradefloor  # noqa: PLC0415
        return tradefloor
    except ImportError:
        return None


def check_build(r: Report, tf) -> str | None:
    """What the INSTALLED extension is, which is what the suite reads.

    A source tree that has moved past its compiled extension reports drift
    everywhere and none of it is real. That cost a wrong diagnosis on
    2026-08-30, so it is the first thing worth knowing.
    """
    if tf is None:
        r.add("tradefloor importable", BAD, "run `maturin develop --release`")
        return None
    declared = md.package_version()
    same = tf.version() == declared
    r.add("installed build matches the tree", OK if same else BAD,
          f"installed {tf.version()}, tree {declared}"
          + ("" if same else "; rebuild before reading any suite"))
    return tf.model_preset()["name"]


def check_readme(r: Report, default_preset: str | None) -> None:
    """The READMEs name the default preset in prose, in the sentences
    `metadata.PRESET_PROSE` matches."""
    if default_preset is None:
        r.add("README names the shipped default", SKIP, "no build")
        return
    stale = sorted({f"{path} {p}" for path, p in md.preset_prose()
                    if p != default_preset})
    r.add("README names the shipped default", OK if not stale else BAD,
          default_preset if not stale else f"still says {', '.join(stale)}")


def check_preset_record(r: Report, tf, default_preset: str | None) -> None:
    if tf is None or default_preset is None:
        r.add("preset record matches the build", SKIP, "no build")
        return
    try:
        rec = tf.preset_record()
    except (LookupError, AttributeError) as exc:
        r.add("preset record matches the build", BAD, str(exc)[:70])
        return
    sys.path.insert(0, str(ROOT / "tools" / "presets"))
    try:
        from record import coefficient_digest  # noqa: PLC0415
    except ImportError:
        r.add("preset record matches the build", SKIP, "record.py not found")
        return
    shipped = tf.ModelParams.from_preset(default_preset).to_dict()
    same = coefficient_digest(shipped) == rec["coefficient_digest"]
    r.add("preset record matches the build", OK if same else BAD,
          default_preset if same
          else "the record describes a different coefficient set")


def check_envelope(r: Report, tf) -> None:
    if tf is None:
        r.add("envelope certifies the default", SKIP, "no build")
        return
    from tradefloor import envelope  # noqa: PLC0415

    same = envelope.PRESET == tf.model_preset()["name"]
    r.add("envelope certifies the default", OK if same else BAD,
          envelope.PRESET if same
          else f"envelope says {envelope.PRESET}, engine runs "
               f"{tf.model_preset()['name']}")


def check_determinism(r: Report) -> None:
    """The digest the gate compares across five platforms."""
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import known_answer  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        r.add("determinism digest", SKIP, f"{type(exc).__name__}")
        return
    base = json.loads(read("tests/known_answer.json"))
    ver_ok = base["katVersion"] == known_answer.KAT_VERSION
    dig_ok = known_answer.simulation_digest() == base["simulationSha256"]
    if ver_ok and dig_ok:
        r.add("determinism digest", OK, f"KAT {known_answer.KAT_VERSION}")
    elif not ver_ok:
        r.add("determinism digest", BAD,
              f"KAT_VERSION {known_answer.KAT_VERSION} against baseline "
              f"{base['katVersion']}")
    else:
        r.add("determinism digest", BAD,
              "the trajectory moved; bump KAT_VERSION and regenerate")


def check_kat_history(r: Report) -> None:
    """A digest that moved since the last tag moved with a bump and a note."""
    try:
        ref = kat_history.newest_tag()
    except SystemExit as exc:
        r.add("known answers moved with a bump", BAD, str(exc).splitlines()[0])
        return
    found = [line for name in kat_history.RULES
             for line in kat_history.problems(
                 name, kat_history.at(ref, name),
                 json.loads(read(name)))]
    r.add("known answers moved with a bump", OK if not found else BAD,
          f"against {ref}" if not found else found[0])


def check_prose(r: Report) -> None:
    out = subprocess.run([sys.executable, "tools/prose/prose.py"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    last = (out.stdout.strip().splitlines() or [""])[-1]
    r.add("house style", OK if last.startswith("0 findings") else BAD, last)


#: What no script can check. Printed rather than implied, because a runbook
#: step nobody is reminded of is a step that gets skipped.
BY_HAND = [
    "step 4: run tools/remeasure/remeasure.py, on a cloud box",
    "step 5: the documentation site, in tradefloor-docs",
    "if the default moved: test expectations, example fixtures and any "
    "recorded run keyed to the market",
    "read the release note as a reader who has not seen the diff",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="the version you intend to ship")
    args = ap.parse_args()

    r = Report()
    tf = installed()
    default_preset = check_build(r, tf)
    check_versions(r, args.version)
    check_changelog(r, args.version)
    check_readme(r, default_preset)
    check_preset_record(r, tf, default_preset)
    check_envelope(r, tf)
    check_determinism(r)
    check_kat_history(r)
    check_prose(r)
    code = r.render()

    print("\nStill yours, and not checked above:")
    for line in BY_HAND:
        print(f"  - {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
