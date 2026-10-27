"""Write pt-v22 from an arm: the preset, its record's identity, its known answer.

    python tools/presets/arm_preset.py --arm ARM [--graded]
    python tools/presets/arm_preset.py --check

pt-v21 went in as hand-typed overrides and a record assembled after its
grade. pt-v22 is written from one file instead, `tools/presets/arms/pt-v22.txt`:
the dials it sets on its base preset, one `name value` line each, under three
header lines naming the base, the arm and whether it is the graded one. Until
the grade has run the file holds a STAND-IN, and everything written from it
says so.

`--arm` takes the arm in either form the programme writes one:
`LABEL@BASE:name=value,name=value,...` on one line, or `name value` lines
(with `@base`, `@arm` and `@status` headers, as the committed file has). It

1. writes the committed arm file;
2. writes `ModelParams::pt_v22` in `rust/src/params.rs` between its two marker
   lines, and `PT_V22_DIGEST_PREFIX`, the first eight hex digits of the digest
   the arm has as overrides on its base;
3. builds the engine (`maturin develop --release`) unless `--no-build`;
4. on the new build, checks that pt-v22 IS the arm (every value, and the
   digest prefix), and writes the identity part of
   `python/tradefloor/presets/pt-v22.json` (fingerprint, coefficients and
   their digest, mechanisms, the arm), leaving every grade slot as it is;
5. writes pt-v22's row of `tests/known_answer_presets.json`, and its `all`;
6. rewrites the pt-v22 value in each `provenance.DIAL_PROVENANCE` entry whose
   value moved, unless the entry is `measured` (a bar belongs to one
   estimate), and lists the dials whose entries need a person: a measured
   value that moved, a dial the new arm brings into scope or takes out.

`--check` writes nothing and exits 1 on any difference between the arm file,
the Rust block, the build, the record's identity and the known answer.

The grade's own numbers are not written here: `record.py`'s writers fill the
record's slots (`validation/pt-v22/README.md` lists them).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
NAME = "pt-v22"
ARM_FILE = ROOT / "tools" / "presets" / "arms" / f"{NAME}.txt"
PARAMS = ROOT / "rust" / "src" / "params.rs"
RECORD = ROOT / "python" / "tradefloor" / "presets" / f"{NAME}.json"
KAT = ROOT / "tests" / "known_answer_presets.json"
PROVENANCE = ROOT / "python" / "tradefloor" / "provenance.py"

BEGIN = "        // ARM BEGIN: written by tools/presets/arm_preset.py from tools/presets/arms/pt-v22.txt."
END = "        // ARM END"
STATUSES = ("stand-in", "graded")

ARM_HEADER = """\
# pt-v22's arm: the dials pt-v22 sets on its base preset, one `name value`
# line each. tools/presets/arm_preset.py writes this file, and writes
# `ModelParams::pt_v22`, the record's identity and the preset's known answer
# from it. `@status stand-in` means the grade has not run and these are not
# the values pt-v22 will ship.
"""


class Arm:
    def __init__(self, label: str, base: str, status: str, values: dict[str, float]):
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {STATUSES}")
        self.label, self.base, self.status, self.values = label, base, status, values

    def text(self) -> str:
        lines = [ARM_HEADER, f"@base {self.base}", f"@arm {self.label}",
                 f"@status {self.status}"]
        lines += [f"{k} {_num(v)}" for k, v in self.values.items()]
        return "\n".join(lines) + "\n"


def _num(v: float) -> str:
    """A float as Python and Rust both read it back to the same bits."""
    s = repr(float(v))
    return s if any(c in s for c in ".eEn") else s + ".0"


def parse_arm(text: str, status: str | None = None) -> Arm:
    """An arm in either form; see the module note."""
    body = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    if len(body) == 1 and "@" in body[0] and ":" in body[0] and "=" in body[0]:
        label, rest = body[0].split("@", 1)
        base, pairs = rest.split(":", 1)
        values = {}
        for pair in pairs.split(","):
            k, v = pair.split("=")
            if k in values:
                raise ValueError(f"{k} is set twice")
            values[k.strip()] = float(v)
        return Arm(label.strip(), base.strip(), status or "stand-in", values)
    head: dict[str, str] = {}
    values = {}
    for ln in body:
        k, v = ln.split()
        if k.startswith("@"):
            head[k[1:]] = v
        elif k in values:
            raise ValueError(f"{k} is set twice")
        else:
            values[k] = float(v)
    missing = [h for h in ("base", "arm") if h not in head]
    if missing:
        raise ValueError(f"the arm names no {' or '.join('@' + m for m in missing)}")
    return Arm(head["arm"], head["base"], status or head.get("status", "stand-in"), values)


def committed_arm() -> Arm:
    return parse_arm(ARM_FILE.read_text(encoding="utf-8"))


def rust_block(arm: Arm) -> str:
    """The lines between the markers in `ModelParams::pt_v22`."""
    note = ("a STAND-IN until the graded arm replaces it" if arm.status == "stand-in"
            else "the graded arm")
    lines = [BEGIN, f"        // Arm {arm.label} on {arm.base}, {note}."]
    lines += [f"        p.{k} = {_num(v)};" for k, v in arm.values.items()]
    lines.append(END)
    return "\n".join(lines)


def _base_fn(base: str) -> str:
    return "ModelParams::" + base.replace("-", "_") + "()"


def write_rust(arm: Arm, prefix: str) -> None:
    src = PARAMS.read_text(encoding="utf-8")
    block = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if len(block.findall(src)) != 1:
        raise SystemExit(f"{PARAMS}: expected one pt-v22 arm block between the markers")
    src = block.sub(lambda _: rust_block(arm), src)
    src, n = re.subn(r"(pub const fn pt_v22\(\) -> ModelParams \{\n        let mut p = )"
                     r"ModelParams::\w+\(\);",
                     lambda m: m.group(1) + _base_fn(arm.base) + ";", src)
    if n != 1:
        raise SystemExit(f"{PARAMS}: no `pt_v22` constructor to set the base in")
    src, n = re.subn(r'(const PT_V22_DIGEST_PREFIX: &str = ")[0-9a-f]{8}(";)',
                     lambda m: m.group(1) + prefix + m.group(2), src)
    if n != 1:
        raise SystemExit(f"{PARAMS}: no PT_V22_DIGEST_PREFIX to write")
    PARAMS.write_text(src, encoding="utf-8")


def rust_arm() -> dict[str, float]:
    """The values the committed Rust block sets, read back from the source."""
    src = PARAMS.read_text(encoding="utf-8")
    body = src.split(BEGIN, 1)[1].split(END, 1)[0]
    return {k: float(v) for k, v in re.findall(r"p\.(\w+) = ([^;]+);", body)}


def digest_prefix(arm: Arm) -> str:
    """The first eight hex digits of the arm's digest as overrides on its base."""
    import tradefloor

    params = tradefloor.ModelParams.from_preset(arm.base, **arm.values)
    fp = params.fingerprint
    if fp.startswith("custom-"):
        return fp[len("custom-"):]
    # The build already ships this vector under a name: read the prefix back
    # from the source, which is what the build was made from.
    m = re.search(r'const PT_V22_DIGEST_PREFIX: &str = "([0-9a-f]{8})"',
                  PARAMS.read_text(encoding="utf-8"))
    if fp == NAME and m:
        return m.group(1)
    raise SystemExit(f"the arm is the shipped preset {fp!r}, not a new vector")


def build() -> None:
    env = dict(os.environ)
    jobs = env.get("CARGO_BUILD_JOBS", "1")
    print(f"building the engine (maturin develop --release, {jobs} job(s))")
    subprocess.run([sys.executable, "-m", "maturin", "develop", "--release"],
                   cwd=ROOT, env=env, check=True)


# ---------------------------------------------------------------------------
# After the build: what the new engine says pt-v22 is.
# ---------------------------------------------------------------------------

def preset_is_arm(arm: Arm) -> list[str]:
    import tradefloor

    want = tradefloor.ModelParams.from_preset(arm.base, **arm.values).to_dict()
    have = tradefloor.ModelParams.from_preset(NAME).to_dict()
    diffs = [f"{k}: pt-v22 ships {have.get(k)!r}, the arm {want[k]!r}"
             for k in want if k != "name" and have.get(k) != want[k]]
    fp = tradefloor.ModelParams.from_preset(NAME).fingerprint
    if fp != NAME:
        diffs.append(f"pt-v22 fingerprints as {fp!r}")
    return diffs


def identity(arm: Arm) -> dict:
    """The record's fields the arm decides, and nothing the grade measures."""
    sys.path.insert(0, str(ROOT / "tools" / "presets"))
    import record  # noqa: PLC0415
    import tradefloor

    values = tradefloor.ModelParams.from_preset(NAME).to_dict()
    return {
        "preset": NAME,
        "fingerprint": NAME,
        "arm": {"name": arm.label, "base": arm.base, "status": arm.status,
                "dials": len(arm.values), "file": "tools/presets/arms/pt-v22.txt"},
        "coefficient_digest": record.coefficient_digest(values),
        "coefficients": dict(sorted(record.recorded_values(values).items())),
        "mechanisms": record.mechanism_set(values),
    }


def write_record(arm: Arm) -> None:
    import record  # noqa: PLC0415  (identity() put tools/presets on the path)

    ident = identity(arm)
    if RECORD.exists():
        rec = json.loads(RECORD.read_text(encoding="utf-8"))
        rec.update(ident)
    else:
        rec = record.scaffold(NAME, ident)
    RECORD.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"  wrote {RECORD.relative_to(ROOT)} (identity; grade slots kept)")


def kat_row() -> tuple[str, str]:
    sys.path.insert(0, str(ROOT / "tests"))
    import known_answer_presets as kp  # noqa: PLC0415

    kat = json.loads(KAT.read_text(encoding="utf-8"))
    digests = dict(kat["presets"])
    digests[NAME] = kp.preset_digest(NAME)
    import tradefloor

    ordered = {n: digests[n] for n in tradefloor.preset_names()}
    return digests[NAME], kp.combined_digest(ordered)


def write_kat() -> None:
    row, combined = kat_row()
    kat = json.loads(KAT.read_text(encoding="utf-8"))
    kat["presets"][NAME] = row
    kat["sha256"] = combined
    KAT.write_text(json.dumps(kat, indent=1) + "\n", encoding="utf-8")
    print(f"  wrote {KAT.relative_to(ROOT)}: pt-v22 {row[:8]}, all {combined[:8]}")


def provenance_values(arm: Arm, write: bool) -> list[str]:
    """Bring the pt-v22 values `DIAL_PROVENANCE` records in line with the build.

    Returns the dials that need a person rather than a rewrite.
    """
    from tradefloor import provenance as P

    have = P._dict(NAME)
    base = P._dict(P.BASELINE)
    src = PROVENANCE.read_text(encoding="utf-8")
    todo = []
    for dial, entry in P.DIAL_PROVENANCE.items():
        rec = (entry.get("presets") or {}).get(NAME)
        if rec is None or dial not in have or rec == have[dial]:
            continue
        if entry.get("kind") == "measured":
            todo.append(f"{dial}: measured entry records {rec!r}, pt-v22 ships {have[dial]!r}")
            continue
        if write:
            src = _rewrite_value(src, dial, rec, have[dial])
    moved = {k for k, v in have.items() if k in base and v != base[k]}
    for dial in sorted(moved - set(P.DIAL_PROVENANCE) - set(P.UNPROVENANCED)):
        todo.append(f"{dial}: pt-v22 now moves it and it has no entry")
    for dial in sorted(d for d, e in P.DIAL_PROVENANCE.items()
                       if NAME in (e.get("presets") or {}) and d not in moved
                       and d not in P.POST_BASELINE):
        todo.append(f"{dial}: its entry names pt-v22, which no longer moves it")
    if write:
        PROVENANCE.write_text(src, encoding="utf-8")
    return todo


def _rewrite_value(src: str, dial: str, old: float, new: float) -> str:
    start = src.index(f'\n    "{dial}": {{')
    end = src.index("\n    },", start)
    entry = src[start:end]
    fixed, n = re.subn(r'("pt-v22": )-?[0-9][0-9.eE+-]*',
                       lambda m: m.group(1) + _num(new), entry)
    if n != 1:
        raise SystemExit(f"provenance.py: could not find {dial}'s pt-v22 value {old!r}")
    fixed = fixed.replace(f"pt-v22 ships {_short(old)}", f"pt-v22 ships {_short(new)}")
    return src[:start] + fixed + src[end:]


def _short(v: float) -> str:
    return f"{v:g}" if abs(v) >= 1e-4 or v == 0 else repr(v)


# ---------------------------------------------------------------------------

def after_build() -> int:
    arm = committed_arm()
    diffs = preset_is_arm(arm)
    if diffs:
        print("pt-v22 is NOT the arm on this build:\n  " + "\n  ".join(diffs))
        return 1
    print(f"pt-v22 is {arm.label} ({arm.status}): {len(arm.values)} dials on {arm.base}")
    write_record(arm)
    write_kat()
    todo = provenance_values(arm, write=True)
    print("  provenance.py: pt-v22 values brought in line"
          + ("; needs a person:\n    " + "\n    ".join(todo) if todo else ""))
    return 0


def check() -> int:
    arm = committed_arm()
    problems = []
    if rust_arm() != arm.values:
        problems.append("the Rust block is not the arm file")
    problems += preset_is_arm(arm)
    rec = json.loads(RECORD.read_text(encoding="utf-8"))
    for key, value in identity(arm).items():
        if rec.get(key) != value:
            problems.append(f"pt-v22.json: {key} is not what the arm gives")
    row, combined = kat_row()
    kat = json.loads(KAT.read_text(encoding="utf-8"))
    if kat["presets"].get(NAME) != row or kat["sha256"] != combined:
        problems.append("known_answer_presets.json: pt-v22's row or `all` is stale")
    problems += provenance_values(arm, write=False)
    for p in problems:
        print(f"  {p}")
    print(f"{len(problems)} differences ({arm.label}, {arm.status})")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", help="the arm to write pt-v22 from")
    ap.add_argument("--graded", action="store_true",
                    help="the arm is the graded one; without it the arm is a stand-in")
    ap.add_argument("--no-build", action="store_true",
                    help="write the arm file and the Rust block and stop")
    ap.add_argument("--after-build", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--check", action="store_true",
                    help="write nothing; exit 1 on any difference")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.after_build:
        return after_build()
    if not args.arm:
        ap.error("--arm or --check is required")
    arm = parse_arm(pathlib.Path(args.arm).read_text(encoding="utf-8"),
                    status="graded" if args.graded else "stand-in")
    prefix = digest_prefix(arm)
    ARM_FILE.parent.mkdir(exist_ok=True)
    ARM_FILE.write_text(arm.text(), encoding="utf-8")
    write_rust(arm, prefix)
    print(f"wrote {ARM_FILE.relative_to(ROOT)} and ModelParams::pt_v22 "
          f"({arm.label}, {arm.status}, {len(arm.values)} dials, digest {prefix})")
    if args.no_build:
        return 0
    build()
    # A fresh interpreter: this one has the old engine loaded.
    return subprocess.run([sys.executable, __file__, "--after-build"], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
