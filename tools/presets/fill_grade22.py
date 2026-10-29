"""Write pt-v22's grade onto the release tree: the record's long run, the grade's named values and the
`{{G19:...}}` placeholders.

    python tools/presets/fill_grade22.py --desk OUT --box NAME=DIR [--box ...] [--dry] \\
        [--panel PANEL.json] [--level-rows ROWS.json] [--pooled POOLED.json] [--fill FILE ...] [--values VALUES.json]

The release runbook's step 1.7, 1.8 and 1.10 from the grade's outputs. `--desk` is grade19.sh's OUT folder:
`verdict-ARM.json` (verdict16.py --registration19), `amend19-ARM.json` and `ptv22-ARM.json`. Each `--box` is one
grading box's output folder, holding `commit.txt`, `scripts-sha256.txt` and `known-answer.txt`.

1. **The verdict the record carries**, assembled from the three files and written to
   `validation/pt-v22/verdict-pt-v22.json`:
   - verdict16's rows;
   - amend19's graded rows in place of the rows of the same id; its superseded rows, which carry no pass, are
     listed under `superseded`;
   - R6 back from verdict16's superseded list as a row (`record.py`'s REPORTED_ROWS marks it reported);
   - ptv22grade's rows, OF4 among them.
   The rows are then marked by `record.py`'s `mark_rows` (OF4 and R6 reported, ON1 and E1 excepted).
   `measured` names the boxes, the engine commit and the fingerprint. The grade ran the arm as
   `custom-XXXXXXXX`, read from the grade's files or given as `--graded-fingerprint`; that is pt-v22 only if
   XXXXXXXX is `PT_V22_DIGEST_PREFIX`, the swapped preset's own digest, and the script refuses otherwise.
2. **The record's slots**, through `record.py`'s writers: `--long-run` on that verdict, and `--panel` and
   `--level-rows` when their artefacts are given. Without them those slots stay empty and the record won't load.
   `--pooled` publishes the pooled level read (`level_pool.py measure pt-v22`'s file, 360 seeds) at
   `validation/pt-v22/level-pool/pt-v22.json` and records it from there after the level rows (`level_pool.py
   record`), as pt-v21's record names its own per-seed file.
3. **The named values:** `record.py`'s `PT_V22_GRADE_BOXES` and `tests/test_validation.py`'s `GRADE22`.
4. **The `{{G19:KEY}}` placeholders** in each `--fill` file. The keys are `long_run_passed` and `long_run_of`
   (the record's gated counts), `long_run_all_passed` and `long_run_all_of`, and any in `--values` (a JSON
   object, for the docs' row placeholders). A placeholder with no value stops the script.

`--dry` writes everything into a temporary copy of the files it touches and prints the diff against the tree,
changing nothing. `--mock DIR` writes a mock grade (a desk and two boxes) for a dry run.
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "presets"))
import record as rec  # noqa: E402

NAME = "pt-v22"
VERDICT_FILE = "verdict-pt-v22.json"
TOUCHED = ("python/tradefloor/presets/pt-v22.json", "tools/presets/record.py", "tests/test_validation.py",
           f"validation/pt-v22/{VERDICT_FILE}")
PLACEHOLDER = re.compile(r"\{\{G19:([A-Za-z0-9_\-]+)\}\}")


def _one(desk: pathlib.Path, stem: str) -> tuple[str, dict]:
    hits = sorted(desk.glob(f"{stem}-*.json"))
    if len(hits) != 1:
        raise SystemExit(f"{desk}: expected one {stem}-ARM.json, found {[h.name for h in hits]}")
    arm = hits[0].stem[len(stem) + 1:]
    return arm, json.loads(hits[0].read_text())


def digest_prefix() -> str:
    m = re.search(r'const PT_V22_DIGEST_PREFIX: &str = "([0-9a-f]{8})"',
                  (ROOT / "rust" / "src" / "params.rs").read_text())
    if not m:
        raise SystemExit("rust/src/params.rs: no PT_V22_DIGEST_PREFIX")
    return m.group(1)


def boxes_of(specs: list[str]) -> dict[str, dict]:
    out = {}
    for spec in specs:
        name, _, d = spec.partition("=")
        d = pathlib.Path(d)
        commit = (d / "commit.txt").read_text().strip()
        sha = (d / "scripts-sha256.txt").read_text().split()[0]
        kat = (d / "known-answer.txt").read_text() if (d / "known-answer.txt").exists() else ""
        m = re.search(r"package\s+(\S+)", kat)
        files = d / "scripts-files.txt"
        out[name] = {"commit": commit, "archive": sha, "package": m.group(1) if m else None,
                     "files": len([x for x in files.read_text().splitlines() if x.strip()]) if files.exists() else None}
    return out


def assemble(desk: pathlib.Path, boxes: dict[str, dict], graded_fp: str | None = None) -> dict:
    """The verdict the record carries: see the module note, step 1."""
    arm, v16 = _one(desk, "verdict")
    _, a19 = _one(desk, "amend19")
    _, p22 = _one(desk, "ptv22")
    rows = {r["id"]: dict(r) for r in v16["rows"]}
    replaced, superseded = [], list(v16.get("superseded") or [])
    for rid, r in (a19.get("rows") or {}).items():
        if r.get("pass") is None:
            superseded.append(dict(r, id=rid))
            continue
        if rid in rows:
            replaced.append(rid)
        rows[rid] = {"group": "amended (registration 19)", "id": rid, "value": r.get("value"),
                     "band": r.get("band"), "pass": bool(r["pass"]), **({"note": r["source"]} if r.get("source") else {})}
    for s in v16.get("superseded") or []:
        if s.get("id") in rec.REPORTED_ROWS.get(NAME, {}) and s["id"] not in rows:
            rows[s["id"]] = {k: v for k, v in s.items() if k != "superseded_by"}
    for rid, r in (p22.get("rows") or {}).items():
        if rid in rows:
            raise SystemExit(f"row {rid} is in ptv22-{arm}.json and in verdict16 or amend19: say which is the grade's")
        rows[rid] = {"group": "pt-v22", "id": rid, "value": r.get("value"), "band": [r.get("low"), r.get("high")],
                     "rule": r.get("rule"), "pass": bool(r.get("pass"))}
    commits = {b["commit"] for b in boxes.values()}
    if len(commits) != 1:
        raise SystemExit(f"the boxes ran different engine commits: {sorted(commits)}")
    fp = v16.get("fingerprint") or p22.get("fingerprint") or graded_fp
    if not fp:
        raise SystemExit("the grade's files record no fingerprint: pass --graded-fingerprint, the arm's fingerprint "
                         "as the grade's boxes recorded it (runbook 1.5's custom-XXXXXXXX)")
    want = f"custom-{digest_prefix()}"
    if fp not in (want, NAME):
        raise SystemExit(f"the grade ran {fp}, which is not pt-v22's digest {want}: the swapped preset is not the graded arm")
    doc = {"grade": "pt-v22, nineteenth registration", "arm": arm, "rows": list(rows.values()),
           "superseded": superseded, "replaced_by_amendments": sorted(replaced),
           "measured": {"fingerprint": NAME, "graded_fingerprint": fp, "box": "+".join(boxes),
                        "engine_commit": commits.pop(), "desk": "validation/pt-v22/"}}
    gated = [r for r in doc["rows"] if r["id"] not in rec.REPORTED_ROWS.get(NAME, {})]
    doc["passed"] = sum(1 for r in gated if r["pass"])
    doc["of"] = len(gated)
    doc["verdict"] = "pass" if doc["passed"] == doc["of"] else "fail"
    marked, problems = rec.mark_rows(NAME, doc)
    if problems:
        raise SystemExit("REFUSED: " + "; ".join(problems))
    return marked


def set_named(root: pathlib.Path, boxes: dict[str, dict], verdict: dict) -> None:
    rp = root / "tools/presets/record.py"
    s = rp.read_text()
    s, n = re.subn(r"PT_V22_GRADE_BOXES: tuple\[str, \.\.\.\] = \([^)]*\)",
                   "PT_V22_GRADE_BOXES: tuple[str, ...] = (" + ", ".join(f'"{b}"' for b in boxes) + ",)", s)
    if n != 1:
        raise SystemExit("record.py: no PT_V22_GRADE_BOXES to set")
    rp.write_text(s)
    b0 = next(iter(boxes.values()))
    vals = {"boxes": tuple(boxes), "engine_commit": b0["commit"], "package": b0["package"],
            "scripts": {k: (b["files"], b["archive"]) for k, b in boxes.items()},
            "verdict": (VERDICT_FILE, verdict["verdict"], verdict["passed"], verdict["of"])}
    tp = root / "tests/test_validation.py"
    t = tp.read_text()
    for key, val in vals.items():
        t, n = re.subn(rf'(\n    "{key}": )None,', lambda m: m.group(1) + repr(val) + ",", t, count=1)
        if n != 1:
            raise SystemExit(f"test_validation.py: GRADE22[{key!r}] is not an empty slot")
    tp.write_text(t)


def fill_placeholders(files: list[pathlib.Path], values: dict[str, object]) -> None:
    for f in files:
        text = f.read_text()
        missing = sorted({k for k in PLACEHOLDER.findall(text) if k not in values})
        if missing:
            raise SystemExit(f"{f}: no value for {', '.join('{{G19:' + k + '}}' for k in missing)}")
        f.write_text(PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text))


#: Where the pooled level read's per-seed file is published, and what the record names as its source.
POOLED_FILE = "validation/pt-v22/level-pool/pt-v22.json"


def run(root: pathlib.Path, a) -> int:
    boxes = boxes_of(a.box)
    verdict = assemble(pathlib.Path(a.desk), boxes, a.graded_fingerprint)
    vf = root / "validation/pt-v22" / VERDICT_FILE
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps(verdict, indent=1) + "\n")
    rec.OUT = root / "python/tradefloor/presets"
    rec.PUBLISHED_GRADES.update({b: "validation/pt-v22" for b in boxes})
    if a.panel:
        argv, sys.argv = sys.argv, ["record.py", "--panel", a.panel]
        try:
            if rec.main() != 0:
                return 1
        finally:
            sys.argv = argv
    if a.level_rows and rec.write_level_protocol(a.level_rows) != 0:
        return 1
    if getattr(a, "pooled", None):
        import level_pool  # noqa: PLC0415
        level_pool.RECORDS = root / "python/tradefloor/presets"
        # The per-seed file is published beside the grade and the record names
        # it there, as pt-v21's names its own: `test_preset_records` rebuilds
        # the pooled read from it.
        dest = root / POOLED_FILE
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(a.pooled, dest)
        ns = argparse.Namespace(file=str(dest), commit=None, package_version=None, date=None, check=False,
                                source=POOLED_FILE)
        if level_pool.record(ns) != 0:
            return 1
    if rec.write_long_run(str(vf)) != 0:
        return 1
    set_named(root, boxes, verdict)
    lr = json.loads((root / "python/tradefloor/presets/pt-v22.json").read_text())["long_run"]
    values = {"long_run_passed": lr["passed"], "long_run_of": lr["of"],
              "long_run_all_passed": lr["all_rows"]["passed"], "long_run_all_of": lr["all_rows"]["of"]}
    if a.values:
        values.update(json.loads(pathlib.Path(a.values).read_text()))
    fill_placeholders([root / f for f in (a.fill or placeholder_files(root))], values)
    print(f"verdict {verdict['verdict']}: {verdict['passed']} of {verdict['of']} gated rows "
          f"({verdict['all_rows']['passed']} of {verdict['all_rows']['of']} rows); "
          f"amend19 replaced {verdict['replaced_by_amendments']}; boxes {'+'.join(boxes)}")
    return 0


def placeholder_files(root: pathlib.Path) -> list[str]:
    """With no --fill: README.md and every docs page that holds a {{G19:...}} placeholder."""
    pages = [root / "README.md", *sorted((root / "docs").glob("*.md"))]
    return [str(p.relative_to(root)) for p in pages if p.exists() and "{{G19:" in p.read_text(encoding="utf-8")]


def _rel(f: str) -> str:
    """A --fill path as one under the tree: an outside file is copied in under `fill/`."""
    p = pathlib.Path(f)
    return f"fill/{p.name}" if p.is_absolute() and not p.is_relative_to(ROOT) else str(p.relative_to(ROOT) if p.is_absolute() else p)


def dry(a) -> int:
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        src_of = {_rel(f): (pathlib.Path(f) if pathlib.Path(f).is_absolute() else ROOT / f) for f in a.fill}
        for rel in list(TOUCHED[:3]):
            src_of[rel] = ROOT / rel
        for rel, src in src_of.items():
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, tmp / rel)
        (tmp / "validation/pt-v22").mkdir(parents=True, exist_ok=True)
        (tmp / "rust/src").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "rust/src/params.rs", tmp / "rust/src/params.rs")
        a.fill = [_rel(f) for f in a.fill]
        rc = run(tmp, a)
        src_of.setdefault(TOUCHED[3], ROOT / TOUCHED[3])
        for rel, src in src_of.items():
            old = src.read_text().splitlines(keepends=True) if src.exists() else []
            new = (tmp / rel).read_text().splitlines(keepends=True) if (tmp / rel).exists() else []
            d = list(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", n=1))
            sys.stdout.writelines(d[:200] if len(d) > 200 else d)
            if len(d) > 200:
                print(f"... {len(d) - 200} more diff lines in {rel}")
        print("DRY RUN: nothing in the tree changed")
        return rc


def mock(d: pathlib.Path) -> None:
    """A mock grade: a desk with the three files and two boxes, every row in its mark but OF4 and R6."""
    desk = d / "desk"
    desk.mkdir(parents=True, exist_ok=True)
    arm = "R22-fixd2RWP"
    row = lambda g, i, ok=True: {"group": g, "id": i, "value": 1.0, "band": [0.0, 2.0], "pass": ok, "usage": 0.5}
    v16 = [row("registered", f"B{i}") for i in range(1, 12)] + [row("pt-v21", "ON1")]
    sup = [dict(row("registered", "R6", False), superseded_by="draft item 25(i): reported, not gated"),
           dict(row("registered", "E1"), superseded_by="draft item 25: E1 on the TTM reported EPS with v")]
    (desk / f"verdict-{arm}.json").write_text(json.dumps(
        {"arm": arm, "rows": v16, "superseded": sup, "verdict": "pass", "passed": len(v16), "of": len(v16)}))
    (desk / f"amend19-{arm}.json").write_text(json.dumps({"arm": arm, "rows": {
        "E1": {"value": -0.09, "band": [-0.3, -0.046], "pass": True, "source": "TTM reported EPS with v"},
        "B3": {"value": 1.2, "band": [0.58, 2.31], "pass": True},
        "DV1": {"value": 0.1, "superseded_by": "DV1c (item 7)", "pass": None}}}))
    (desk / f"ptv22-{arm}.json").write_text(json.dumps({"arm": arm, "rows": {
        "OF4": {"value": 0.87, "low": 1.0, "high": None, "rule": "ratio >= 1", "pass": False, "gated": False},
        "IF1": {"value": 3.7, "low": 1.2, "high": 6.3, "rule": "band", "pass": True, "gated": True}}}))
    for b in ("ptv22g1", "ptv22g2"):
        bd = d / b
        bd.mkdir(exist_ok=True)
        (bd / "commit.txt").write_text("0baefaf44fdf4b751db7e8d8bd825032365b2712\n")
        (bd / "scripts-sha256.txt").write_text(f"{'ab' * 32}  scripts.tgz\n")
        (bd / "known-answer.txt").write_text("tradefloor known-answer test v29\n  package  0.11.0\n")
        (bd / "scripts-files.txt").write_text("a.py\nb.py\n")
    (d / "README.md").write_text("pt-v22 passes {{G19:long_run_passed}} of {{G19:long_run_of}} rows.\n")
    print(f"mock grade in {d}: --desk {desk} --box ptv22g1={d / 'ptv22g1'} --box ptv22g2={d / 'ptv22g2'} "
          f"--fill {d / 'README.md'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desk"); ap.add_argument("--box", action="append", default=[])
    ap.add_argument("--panel"); ap.add_argument("--level-rows", dest="level_rows"); ap.add_argument("--pooled")
    ap.add_argument("--fill", action="append", default=[]); ap.add_argument("--values")
    ap.add_argument("--graded-fingerprint", dest="graded_fingerprint",
                    help="the arm's fingerprint as the grade recorded it, when its files don't carry one")
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--mock")
    a = ap.parse_args()
    if a.mock:
        mock(pathlib.Path(a.mock)); return 0
    if not a.desk or not a.box:
        ap.error("--desk and at least one --box are required")
    a.fill = a.fill or placeholder_files(ROOT)
    if not a.dry:
        a.fill = [_rel(f) for f in a.fill]
        if any(f.startswith("fill/") for f in a.fill):
            ap.error("--fill outside the tree is for --dry only")
    return dry(a) if a.dry else run(ROOT, a)


if __name__ == "__main__":
    sys.exit(main())
