"""Re-measure every published figure and report what moved.

One command:

    .venv/bin/python tools/remeasure/remeasure.py

runs every measurement group in tools/remeasure/measures.py against the
installed `pretium` package, joins the results to the claim inventory in
tools/remeasure/inventory.json, and writes

    tools/remeasure/out/figures.json     machine-readable results
    tools/remeasure/out/REPORT.md        the delta report, grouped by document

Statuses:

    reproduced             measured value matches the published one at the
                           precision the document printed it
    within_seed_variation  off the published median but inside the published
                           seed-to-seed range (range-kind rows only)
    MOVED                  outside tolerance: after an engine change this row
                           is a doc edit; on unchanged main it means the
                           document was already stale (see the row's note)
    machine_bound          a wall-clock absolute; reported, never failed --
                           the corresponding ratio rows are the portable claim
    structural_*           boolean claims
    method_unknown         nobody can reconstruct how the number was produced;
                           listed so the gap is visible rather than silent
    not_harnessable        needs a rebuilt engine, another platform, or a paid
                           external service
    covered_by_tests       exercised by the test suite, not duplicated here

Options:
    --only GROUP[,GROUP..]  run a subset of measurement groups
    --workers N             thread pool width for seed-parallel groups
    --out DIR               output directory (default tools/remeasure/out)
    --list                  print the inventory and exit
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

sys.path.insert(0, str(HERE))
import register  # noqa: E402
from measures import GROUPS, TIMED_GROUPS, Ctx  # noqa: E402


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def _round_ok(measured, published, decimals) -> bool:
    return abs(measured - published) <= 0.5 * 10 ** (-decimals) + 1e-12


def judge(fig: dict, measured) -> tuple[str, float | None]:
    """Return (status, delta) for one inventory row."""
    kind = fig["compare"]["kind"]
    pub = fig.get("published")

    if kind == "none":
        return fig.get("status_override", "info"), None

    if measured is None:
        return "measurement_failed", None

    if kind == "exact":
        if isinstance(pub, bool):
            return ("structural_ok" if measured == pub else "structural_fail"), None
        if isinstance(pub, str):
            return ("reproduced" if measured == pub else "MOVED"), None
        delta = float(measured) - float(pub)
        return ("reproduced" if measured == pub else "MOVED"), delta

    delta = float(measured) - float(pub)

    if kind == "round":
        return ("reproduced" if _round_ok(measured, pub, fig["compare"]["decimals"])
                else "MOVED"), delta
    if kind == "range":
        if _round_ok(measured, pub, fig["compare"]["decimals"]):
            return "reproduced", delta
        lo, hi = fig["compare"]["lo"], fig["compare"]["hi"]
        return ("within_seed_variation" if lo <= measured <= hi else "MOVED"), delta
    if kind == "band":
        tol = fig["compare"].get("abs")
        if tol is None:
            tol = abs(pub) * fig["compare"]["rel"]
        return ("reproduced" if abs(delta) <= tol else "MOVED"), delta
    if kind == "less_than":
        return ("reproduced" if measured < pub else "MOVED"), delta
    if kind == "order":
        import math
        if measured <= 0:
            return "reproduced", None  # a zero residual beats "around 1e-16"
        exp = math.log10(measured)
        ok = abs(exp - fig["compare"]["exponent"]) <= fig["compare"]["tol"]
        return ("reproduced" if ok else "MOVED"), None
    if kind == "timing":
        return "machine_bound", delta
    raise ValueError(f"unknown compare kind {kind!r} on {fig['id']}")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v != 0 and abs(v) < 1e-3:
            return f"{v:.3g}"
        if abs(v) >= 10000:
            return f"{v:,.0f}"  # P&L-scale: 90,250, never 9.025e+04
        return f"{v:,.4g}" if abs(v) >= 1000 else f"{v:.4g}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _scope_line(meta: dict) -> str:
    """Say whether this was the whole inventory or a slice of it.

    This line used to read "Full run" unconditionally. On 2026-08-27 a
    three-group smoke test (`--only arith,tca_example,tca_ripple`, 3s wall)
    overwrote `out/REPORT.md` with 54 of 355 figures and a header claiming a
    full run, and no MOVED rows -- because the groups that move were never
    measured. RELEASING.md step 4 tells the releaser to read that file, so a
    partial that presents as complete is the one failure mode this report
    must not have.
    """
    ran, total = len(meta.get("groups_run", [])), meta.get("groups_total", 0)
    wall = f"{meta['wall_s']:.0f}s wall with {meta['workers']} workers"
    if total and ran < total:
        return (
            f"**PARTIAL RUN: {ran} of {total} measurement groups.** {wall}. "
            f"Groups run: {', '.join(meta['groups_run'])}. "
            f"This is not the release gate; re-run without `--only`."
        )
    return f"Full run: {wall}."


def write_report(rows: list[dict], meta: dict, path: Path) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines = [
        "# Published-figure re-measurement",
        "",
        f"Commit `{meta['commit']}`, {meta['date']}, pretium {meta['pretium_version']}. "
        f"{_scope_line(meta)}",
        "",
        "| status | figures |",
        "|---|---|",
    ]
    order = ["reproduced", "within_seed_variation", "MOVED", "machine_bound",
             "structural_ok", "structural_fail", "method_unknown",
             "not_harnessable", "covered_by_tests", "measurement_failed"]
    for s in order:
        if s in counts:
            lines.append(f"| {s} | {counts[s]} |")
    lines.append("")

    moved = [r for r in rows if r["status"] in ("MOVED", "structural_fail")]
    if moved:
        lines += [
            "## Doc edits needed",
            "",
            "Every row here is a published number the stated (or reconstructed)",
            "method no longer produces. On unchanged main these are documents that",
            "were already stale; after an engine change, this section IS the edit",
            "list.",
            "",
            "| where | figure | published | measured |",
            "|---|---|---|---|",
        ]
        for r in moved:
            lines.append(f"| {r['file']}:{r['line']} | {r['label']} | "
                         f"{_fmt(r['published'])} | {_fmt(r['measured'])} |")
        lines.append("")

    unknown = [r for r in rows if r["status"] == "method_unknown"]
    if unknown:
        lines += [
            "## Published numbers nobody can re-measure",
            "",
            "| where | figure | why |",
            "|---|---|---|",
        ]
        for r in unknown:
            lines.append(f"| {r['file']}:{r['line']} | {r['label']} | "
                         f"{r.get('note', '')} |")
        lines.append("")

    lines += ["## Every figure, by document", ""]
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)
    for file in sorted(by_file):
        lines += [f"### {file}", "",
                  "| line | figure | published | measured | delta | status |",
                  "|---|---|---|---|---|---|"]
        for r in sorted(by_file[file], key=lambda x: (x["line"], x["id"])):
            lines.append(
                f"| {r['line']} | {r['label']} | {_fmt(r['published'])} | "
                f"{_fmt(r['measured'])} | {_fmt(r.get('delta'))} | {r['status']} |")
        lines.append("")

    notes = [r for r in rows if r.get("note")]
    if notes:
        lines += ["## Notes", ""]
        for r in notes:
            lines.append(f"- **{r['id']}** ({r['file']}:{r['line']}): {r['note']}")
        lines.append("")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="comma-separated group names")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--inventory", default=None,
                    help="the claim register; overrides TRADEFLOOR_DOCS")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    inventory_path, how = register.resolve(args.inventory)
    print(f"register: {inventory_path}  (via {how})")
    inventory = json.loads(
        inventory_path.read_text(encoding="utf-8"))["figures"]
    if args.list:
        for fig in inventory:
            print(f"{fig['id']:34s} {fig['file']}:{fig['line']:<4} "
                  f"[{fig.get('group') or '-'}] {fig['label']}")
        return 0

    wanted = set(args.only.split(",")) if args.only else set(GROUPS)
    unknown = wanted - set(GROUPS)
    if unknown:
        ap.error(f"unknown groups: {sorted(unknown)}; know {sorted(GROUPS)}")

    ctx = Ctx(root=ROOT, workers=args.workers)
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    started = time.time()

    def run_group(name: str):
        t0 = time.time()
        try:
            out = GROUPS[name](ctx)
            print(f"  {name:22s} {time.time() - t0:6.1f}s")
            return name, out, None
        except Exception:
            print(f"  {name:22s} FAILED")
            return name, {}, traceback.format_exc()

    concurrent = [g for g in wanted if g not in TIMED_GROUPS]
    timed = [g for g in TIMED_GROUPS if g in wanted]
    print(f"running {len(concurrent)} concurrent groups "
          f"({args.workers} workers), then {len(timed)} timed groups serially")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name, out, err in pool.map(run_group, concurrent):
            results[name] = out
            if err:
                errors[name] = err
    for name in timed:  # wall-clock groups: machine otherwise quiet
        name, out, err = run_group(name)
        results[name] = out
        if err:
            errors[name] = err

    import tradefloor as pt
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    meta = {
        "commit": commit or "unknown",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "pretium_version": pt.version(),
        "wall_s": time.time() - started,
        "workers": args.workers,
        "groups_run": sorted(results),
        "groups_total": len(GROUPS),
        "errors": errors,
    }

    rows = []
    for fig in inventory:
        group, key = fig.get("group"), fig.get("key")
        if group is not None and group not in wanted:
            continue
        measured = results.get(group, {}).get(key) if group else None
        status, delta = judge(fig, measured)
        rows.append({**{k: fig.get(k) for k in
                        ("id", "file", "line", "label", "published",
                         "method", "source", "note")},
                     "measured": measured, "delta": delta, "status": status})

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "figures.json").write_text(json.dumps(
        {"meta": meta, "figures": rows, "raw": results}, indent=1, default=str))
    write_report(rows, meta, out_dir / "REPORT.md")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n{len(rows)} figures in {meta['wall_s']:.0f}s -> "
          + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    print(f"wrote {out_dir / 'figures.json'} and {out_dir / 'REPORT.md'}")
    if errors:
        print(f"\nGROUP FAILURES: {sorted(errors)}", file=sys.stderr)
        for name, tb in errors.items():
            print(f"\n--- {name} ---\n{tb}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
