"""Write pt-v22's grade onto the release tree: the record's long run, the grade's named values and the
`{{G19:...}}` placeholders.

    python tools/presets/fill_grade22.py --desk OUT --box NAME=DIR [--box ...] [--dry] \\
        [--panel PANEL.json] [--level-rows ROWS.json] [--pooled POOLED.json] [--fut PIECES_DIR] [--fill FILE ...] \\
        [--values VALUES.json]

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
4. **`OF4_bias126`**, the oil forecast's mean error at 126 sessions in dollars a barrel, which no grade output
   carries: read with `--fut` from the grade's merged `ptv22fut/ARM` pieces, as the registered OF4 reader reads
   them (each history's mean realised less forecast, `futrows`' `of4`, averaged over the histories; OF4 is that
   mean over its standard error). The page states the direction, so the value is its size, and the fill prints
   the sign: the forecast runs high when realised less forecast is negative.
5. **The `{{G19:KEY}}` placeholders** in each `--fill` file. The keys are `long_run_passed` and `long_run_of`
   (the record's gated counts), `long_run_all_passed` and `long_run_all_of`, and any in `--values` (a JSON
   object, for the docs' row placeholders). A placeholder with no value stops the script.

`--dry` writes everything into a temporary copy of the files it touches and prints the diff against the tree,
changing nothing. `--mock DIR` writes a mock grade (a desk and two boxes) for a dry run.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
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
#: Where runbook step 1.9 publishes the desk's grade outputs; the verdict names the three files it was assembled from.
GRADE_DIR = "validation/pt-v22/programme/results/ptv22/grade"
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
        out[name] = {"commit": commit, "archive": sha, "package": _package(d), "files": _files(d)}
    return out


def _package(d: pathlib.Path) -> str | None:
    """The package version the box ran: its known-answer report's `package` line, or, for a box that wrote only
    `kat.txt` (most of the grade's boxes do), the wheel it installed, as `wheel.txt` names it."""
    for name, pattern in (("known-answer.txt", r"package\s+(\S+)"), ("wheel.txt", r"\btradefloor-(\d[^\s/]*?)(?:-cp|\.whl|\s|$)")):
        f = d / name
        m = re.search(pattern, f.read_text(), re.MULTILINE) if f.exists() else None
        if m:
            return m.group(1)
    return None


def _files(d: pathlib.Path) -> int | None:
    """How many files the box unpacked: `scripts-files.txt`, or the files in the archive it kept,
    `scripts-as-run.tgz`, whose sha256 is the one `scripts-sha256.txt` records."""
    listed = d / "scripts-files.txt"
    if listed.exists():
        return len([x for x in listed.read_text().splitlines() if x.strip()])
    kept = d / "scripts-as-run.tgz"
    if kept.exists():
        import tarfile  # noqa: PLC0415

        with tarfile.open(kept) as t:
            return sum(1 for m in t.getmembers() if m.isfile())
    return None


#: The criteria the long-run rows are graded under, as grade 23's grade_all names them, at their published paths.
CRITERIA = ("validation/pt-v21/programme/longrun/CRITERIA.md (adopted 2026-09-23; C4a and C4b added 2026-09-24; "
            "C5-C8 registered 2026-09-24); the pt-v22 rows registered in "
            "validation/pt-v22/programme/ptv22-registration-23-DRAFT.md (the twenty-third registration)")
#: The long-run criteria's script, as published with pt-v21; its `_row` is unchanged at grade 23's JOBS_REF.
CRITERIA_SCRIPT = "validation/pt-v21/programme/longrun/criteria.py"
#: The day grade 23 ran. Its desk files carry no date; the verdict and ptv22grade were written that day.
GRADE_DATE = "2026-11-01"


def criteria_module():
    """pt-v21's published criteria.py, whose `_row`, WORDS and RULE are unchanged at the grade's JOBS_REF."""
    spec = importlib.util.spec_from_file_location("criteria", ROOT / CRITERIA_SCRIPT)
    criteria = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(criteria)
    return criteria


def registered_numbers(desk: pathlib.Path, arm: str, rows: dict[str, dict]) -> None:
    """The registered rows' value and real as numbers, the types pt-v21's rows carry.

    verdict16 shows each registered row as grade_all prints it: `value` a display string ("0.44 / 0.34") and, for a
    ratio or "within" rule, the real/target column under `band` ("0.57 / 0.34"). The numbers are grade_all's own
    (`ga-ARM.json`'s `existing` block, the one verdict16 reads, R4 on the held close), turned into a row's value and
    real by the long-run criteria's own `_row`, the function pt-v21's rows were written by (unchanged at the grade's
    JOBS_REF). The display strings are kept as `value_text` and `real_text`; a numeric band stays `band`.
    """
    criteria = criteria_module()
    existing = json.loads((desk / f"ga-{arm}.json").read_text())[arm]["existing"]
    for rid, r in rows.items():
        if r.get("group") != "registered" or rid not in existing:
            continue
        x = criteria._row(rid, existing[rid])
        if x["pass"] != r["pass"]:
            raise SystemExit(f"row {rid}: grade_all reads pass {x['pass']} and verdict16 {r['pass']}")
        r["value_text"] = r["value"]
        if isinstance(r.get("band"), str):
            r["real_text"] = r.pop("band")
        r["value"], r["real"] = x["value"], x["real"]
        # The criterion's words and rule, verbatim from the same script: the grade's own texts.
        r["words"], r["rule"] = x["words"], x["rule"]
        applied = rule_applied(rid, existing[rid])
        if applied:
            r["rule_applied"] = applied


def amended_reals(desk: pathlib.Path, arm: str, a19: dict, rows: dict[str, dict]) -> None:
    """The amended rows' real value, in pt-v21's shape (a one-item list): amend19's own where it gives one (E1, the
    TTM reported EPS's fall), and otherwise the long-run criteria's, which the amendment re-reads the model side of
    and leaves the real side of (R3, `criteria-MERGED.json`)."""
    merged = json.loads((desk / "criteria-MERGED.json").read_text())["arms"][arm]
    criteria = criteria_module()
    for rid, r in rows.items():
        if r.get("group") != "amended (registration 19)":
            continue
        # The criterion's words and rule, verbatim, as for the registered rows; the amendment's own reading is in `note`.
        if rid in criteria.WORDS:
            r["words"], r["rule"] = criteria.WORDS[rid], criteria.RULE[rid]
        if "real" in r:
            continue
        real = (a19.get("rows") or {}).get(rid, {}).get("real")
        if real is None:
            real = (merged.get(rid) or {}).get("real")
        if real is not None:
            r["real"] = real if isinstance(real, list) else [real]


def rule_applied(rid: str, c: dict) -> str | None:
    """The mark a rule the grade scales by history count is applied at, where it differs from the rule as written.

    C2's rule is a rate, at most one history in thirty, and the grade allows `max(1, n // 30)` of its n histories
    (criteria.py at the grade's JOBS_REF). R7b's "ahead in at most 20 of 30" scales the same way, as 20n/30 of n.
    """
    if rid == "C2" and c.get("histories"):
        n = int(c["histories"])
        return f"at most {max(1, n // 30)} of {n} (1 per 30)" if n != 30 else None
    if rid == "R6" and c.get("bands"):
        # R6's rule is written for pt-v21's 2022 reading; grade 23 applies the band it registered.
        lo, hi = c["bands"][0]
        return f"{lo:g} to {hi:g} per cent"
    if rid == "R7b":
        m = re.search(r"(\d+) of (\d+)", c.get("edge_note") or "")
        if m and int(m.group(2)) != 30:
            n = int(m.group(2))
            return f"at most 0 points a year, ahead in at most {20 * n / 30:g} of {n} (20 per 30)"
    return None


def long_run_size(desk: pathlib.Path, arm: str) -> tuple[int, int]:
    """The long run's histories and years for the arm, from the longrun report grade_all names."""
    line = next(l for l in (desk / "grade_all.txt").read_text().splitlines() if l.startswith("long run"))
    report = json.loads(pathlib.Path(line.split(None, 2)[2].strip()).read_text())
    a = report["arms"][arm]
    return int(a["n"]), int(a["years"])


#: The four Cboe option rules' rows: each rule's return, volatility and drawdown.
OPTION_RULE_ROW = re.compile(r"OB[1-4][rvd]")
OPTION_RULES_NOT_READ = ("not read: the grade's chain histories carry no option quotes (the index option stage, "
                         "ptv22idx, is off), so no rule opened a position")


def assemble(desk: pathlib.Path, boxes: dict[str, dict], graded_fp: str | None = None) -> dict:
    """The verdict the record carries: see the module note, step 1."""
    arm, v16 = _one(desk, "verdict")
    published = {key: f"{GRADE_DIR}/{stem}-{arm}.json" for key, stem in
                 (("verdict16", "verdict"), ("amend19", "amend19"), ("ptv22grade", "ptv22"))}
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
    # The four Cboe option rules (OB1..OB4) trade only on rows that carry an option chain. When the
    # grade's chain histories carried none, ptv22grade reports no ATM entry, every rule's account stays
    # at 1.0, and its r, v and d are the index's own return and two zeros, not readings of the rule. They
    # are written unread, as OB5 and OB6 are.
    chainless = "ATM" not in (p22.get("reported") or {})
    registered_numbers(desk, arm, rows)
    amended_reals(desk, arm, a19, rows)
    for rid, r in (p22.get("rows") or {}).items():
        if rid in rows:
            raise SystemExit(f"row {rid} is in ptv22-{arm}.json and in verdict16 or amend19: say which is the grade's")
        row = {"group": "pt-v22", "id": rid, "value": r.get("value"), "band": [r.get("low"), r.get("high")],
               "rule": r.get("rule"), "pass": bool(r.get("pass"))}
        if r.get("gated") is False:
            # ptv22grade's "reported, not graded" rows: the grade's own gating, and an unread one stays unread.
            row.update(gated=False, **({"pass": r.get("pass")} if r.get("pass") is None else {}))
        if chainless and OPTION_RULE_ROW.fullmatch(rid):
            if r.get("gated") is not False:
                raise SystemExit(f"row {rid} is gated, and the chain histories carried no option quotes to read it on")
            row.update(value=None, gated=False, note=OPTION_RULES_NOT_READ)
            row["pass"] = None
        rows[rid] = row
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
    seeds, years = long_run_size(desk, arm)
    doc = {"grade": "pt-v22, twenty-third registration", "arm": arm, "criteria": CRITERIA, "rows": list(rows.values()),
           "superseded": superseded, "replaced_by_amendments": sorted(replaced),
           "measured": {"fingerprint": NAME, "graded_fingerprint": fp, "box": "+".join(boxes),
                        "engine_commit": commits.pop(), "desk": "validation/pt-v22/",
                        "seeds": seeds, "years": years, "date": GRADE_DATE,
                        # The stages read other counts than the long run's: each one's, as ptv22grade gives it.
                        "histories": dict(p22.get("histories") or {}),
                        **{k: v for k, v in published.items() if (ROOT / v).is_file()}}}
    reported = rec.reported_rows(NAME, doc["rows"])
    gated = [r for r in doc["rows"] if r["id"] not in reported]
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
    if getattr(a, "fut", None):
        bias, n = of4_bias126(pathlib.Path(a.fut))
        values["OF4_bias126"] = f"{abs(bias):.2f}"
        print(f"OF4_bias126: realised less forecast {bias:+.4f} dollars a barrel over {n} histories, so the "
              f"oil forecast runs {'high' if bias < 0 else 'low'} at 126 sessions")
    if a.values:
        values.update(json.loads(pathlib.Path(a.values).read_text()))
    fill_placeholders([root / f for f in (a.fill or placeholder_files(root))], values)
    print(f"verdict {verdict['verdict']}: {verdict['passed']} of {verdict['of']} gated rows "
          f"({verdict['all_rows']['passed']} of {verdict['all_rows']['of']} rows); "
          f"amend19 replaced {verdict['replaced_by_amendments']}; boxes {'+'.join(boxes)}")
    return 0


def of4_bias126(pieces: pathlib.Path) -> tuple[float, int]:
    """The mean, over the histories, of each history's mean error at 126 sessions (`of4["126"]` in futrows'
    pieces: realised less forecast), and how many histories carry one."""
    errs = []
    for f in sorted(pieces.glob("pieces-*.json")):
        v = json.loads(f.read_text()).get("of4", {}).get("126")
        if v is not None and v == v:
            errs.append(float(v))
    if len(errs) < 2:
        raise SystemExit(f"{pieces}: {len(errs)} histories carry OF4's 126-session error; the grade's merged "
                         "ptv22fut/ARM folder holds one pieces-N.json per history")
    return sum(errs) / len(errs), len(errs)


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
        "E1": {"value": -0.09, "band": [-0.3, -0.046], "pass": True, "real": -0.17, "source": "TTM reported EPS with v"},
        "B3": {"value": 1.2, "band": [0.58, 2.31], "pass": True},
        "DV1": {"value": 0.1, "superseded_by": "DV1c (item 7)", "pass": None}}}))
    (desk / f"ptv22-{arm}.json").write_text(json.dumps({"arm": arm, "rows": {
        "OF4": {"value": 0.87, "low": 1.0, "high": None, "rule": "ratio >= 1", "pass": False, "gated": False},
        "IF1": {"value": 3.7, "low": 1.2, "high": 6.3, "rule": "band", "pass": True, "gated": True}}}))
    # grade_all's own numbers for the registered rows, and the long-run report it names.
    (desk / f"ga-{arm}.json").write_text(json.dumps({arm: {"existing": {
        f"B{i}": {"pass": True, "model": 1.0, "real": 1.0} for i in (1, 2, 4, 5, 6, 7, 8)}}}))
    (desk / "criteria-MERGED.json").write_text(json.dumps({"arms": {arm: {"B3": {"real": 1.156}}}}))
    (d / "longrun-report.json").write_text(json.dumps({"arms": {arm: {"n": 270, "years": 21}}}))
    (desk / "grade_all.txt").write_text(f"PASS CRITERIA\nlong run  {d / 'longrun-report.json'}\n")
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
    ap.add_argument("--fut", help="the grade's merged ptv22fut/ARM pieces, for OF4_bias126")
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
