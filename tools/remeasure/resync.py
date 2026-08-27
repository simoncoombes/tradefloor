"""Re-point the claim inventory at the prose as it is now.

Why this exists. The 0.3.0 documentation was rewritten twice -- once to move
it from the pt-v10 era to pt-v12, once to fix 333 audit findings -- and
`inventory.json` was not re-read from the result. So the gate compared
today's engine against yesterday's prose and reported 106 MOVED rows, none of
which was a documentation defect. Fifty described content the rewrite had
removed outright; three were reading the wrong column of a table the page
gets right; the rest recorded a value the page no longer prints.

That is worse than no gate. A release checklist step that cries wolf 106
times teaches the releaser to skim it, which is exactly how the three-group
smoke test passed for "Full run".

What this does, per MOVED row: look for the freshly measured value in the
page, rendered the way a page renders numbers. If it is there, the page is
already right and the row is re-pointed at it -- new line, new published
value at the page's own precision. If it is not there, and the row's old
value is not there either, the claim is gone and the row goes with it.

What this does NOT do: decide anything it is unsure about. A row that could
match in more than one place, or whose page still carries the OLD value, is
left alone and listed for a human. Guessing here would write a wrong number
into the gate that guards the numbers.

    python tools/remeasure/resync.py --report        # say what it would do
    python tools/remeasure/resync.py --apply         # do it
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "tools" / "remeasure" / "inventory.json"


def renderings(v: float) -> list[str]:
    """How a page might print this number.

    Every precision is tried and ALL matches are returned, not the first
    precision that hits. Stopping at the most precise match looked right and
    was not: `scen.mom_seedband_lo` measures -1.87, the page prints the seed
    band as "-1.9" in one place and an unrelated delta column as "-1.87" in
    another, and first-match-wins re-pointed the row at the delta. A wrong
    number written into the gate that guards the numbers is the one outcome
    this tool must not produce, so any row matching in more than one place
    goes to a human instead.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return [str(v)]
    out = []
    for p in (4, 3, 2, 1, 0):
        out.append(f"{v:.{p}f}")
        out.append(f"{v:+.{p}f}")
        if abs(v) >= 1000:
            out.append(f"{v:,.{p}f}")
    if float(v).is_integer():
        out.append(str(int(v)))
    # a percentage page prints 13.22, not 0.1322
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


#: Words that say nothing about which claim a row is, so they cannot be used
#: to tell one line of a table from another.
STOPWORDS = {
    "the", "a", "an", "of", "in", "at", "on", "to", "and", "or", "is", "it",
    "its", "by", "for", "with", "over", "per", "from", "as", "that", "this",
    "value", "values", "figure", "number", "median", "mean", "run", "runs",
    "day", "days", "seed", "seeds", "page", "report", "sample",
}


def find(text: str, value) -> list[tuple[int, str, str]]:
    """(line, rendering, text) for the most precise UNIQUE rendering.

    Word-boundary matched, so 0.626 does not match inside 0.6261 and 118 does
    not match inside 1180.

    "Most precise that is unique" rather than "most precise that matches
    anywhere": the low-precision renderings of a small number are digits like
    "0" and "5" that appear on every other line, and gathering all of them
    made 66 of 106 rows unresolvable for no reason. Returning [] when no
    precision is unique is the honest answer -- it sends the row to a human.
    """
    lines = text.splitlines()
    for rendering in renderings(value):
        if len(rendering.lstrip("+-").replace(".", "").lstrip("0")) < 2:
            continue          # "0", "5", "-1": too generic to locate anything
        pat = re.compile(r"(?<![\d.])" + re.escape(rendering) + r"(?![\d])")
        hits = [(i, rendering, l.strip())
                for i, l in enumerate(lines, 1) if pat.search(l)]
        if len(hits) == 1:
            return hits
    return []


def locatable(value) -> bool:
    """Can this value be searched for at all?

    A measurement of 0, 5 or -1 renders as one or two characters that appear
    all over a page, so absence of a match says nothing about whether the
    claim is still there. Without this, `agents.beat12_momentum` -- which the
    page states correctly as "momentum | 0 of 12" -- was classified as
    retired and would have been deleted from the gate for measuring zero.
    """
    return any(len(r.lstrip("+-").replace(".", "").lstrip("0")) >= 2
               for r in renderings(value))


def occurs(text: str, value) -> bool:
    """Is `value` printed anywhere on the page, uniquely or not?

    Separate from `find` because they answer different questions and
    conflating them deletes data: `find` returning [] means "cannot locate it
    exactly", which for a moment here was being read as "the claim is gone"
    and would have retired 67 rows, 31 of them for the crime of appearing in
    a table twice.
    """
    lines = text.splitlines()
    for rendering in renderings(value):
        if len(rendering.lstrip("+-").replace(".", "").lstrip("0")) < 2:
            continue
        pat = re.compile(r"(?<![\d.])" + re.escape(rendering) + r"(?![\d])")
        if any(pat.search(l) for l in lines):
            return True
    return False


def label_fits(label: str, text: str, line_no: int, lines: list[str]) -> bool:
    """Does the matched line look like it is about what the row is about?

    A guard against attaching a row to a number that happens to be equal
    somewhere else on the page. Cheap and imperfect -- two rows from the same
    table share every word -- but it catches the case where a seed-band row
    lands on an unrelated paragraph.
    """
    words = {w for w in re.findall(r"[a-z_]{3,}", label.lower())
             if w not in STOPWORDS}
    if not words:
        return True
    window = " ".join(lines[max(0, line_no - 4):line_no + 3]).lower()
    return any(w in window for w in words)


#: Rows the automatic match gets wrong, verified by hand.
#:
#: The seed-band rows describe a spread ACROSS seeds -- "buy-and-hold gives up
#: 3.4 to 4.7 points" -- and the value they measure happens to equal a number
#: in the calm/hiked/delta table on another line. The label guard cannot
#: separate them because both mention buy-and-hold, so the matcher confidently
#: re-points a seed band at a delta. Listed rather than special-cased, because
#: the general fix is reading the sentence.
NEEDS_READING = {
    "scen.bh_seedband_lo", "scen.bh_seedband_hi",
    "scen.mom_seedband_lo", "scen.mom_seedband_hi",
}


def classify(row: dict, measured, text: str) -> tuple[str, dict]:
    """One of: repoint, retire, review."""
    pub = row.get("published")

    if row["id"] in NEEDS_READING:
        return "review", {"why": "on the hand-verified list: the automatic "
                                 "match attaches this to the wrong number"}

    if isinstance(pub, bool) or isinstance(measured, bool):
        return "review", {"why": "boolean row, needs the sentence read"}
    if measured is None:
        return "review", {"why": "no measurement"}

    on_page_new = find(text, measured)
    on_page_old = (occurs(text, pub) if isinstance(pub, (int, float))
                   and not isinstance(pub, bool) else False)

    if on_page_old and not on_page_new and not occurs(text, measured):
        return "review", {
            "why": "the page still prints the OLD value, so this may be a real "
                   "documentation defect rather than a stale row",
        }
    if not on_page_new:
        if occurs(text, measured):
            return "review", {
                "why": "the new value is on the page but in more than one "
                       "place; which one is the claim needs reading",
            }
        if not locatable(measured):
            return "review", {
                "why": f"measures {measured!r}, too generic to find by value; "
                       "the claim has to be located by reading",
            }
        words = {w for w in re.findall(r"[a-z_]{4,}", (row.get("label") or "").lower())
                 if w not in STOPWORDS}
        low = text.lower()
        present = [w for w in words if w in low]
        if len(present) >= 2:
            return "review", {
                "why": "the value is gone but the page still discusses "
                       f"{', '.join(present[:3])}, so the claim is probably "
                       "still there with a different number",
            }
        return "retire", {"why": "neither the value nor the subject is on the page"}
    line_no, rendering, line = on_page_new[0]
    if not label_fits(row.get("label", ""), text, line_no, text.splitlines()):
        return "review", {
            "why": f"prints once, at line {line_no}, but nothing near it "
                   f"mentions what this row measures",
            "at": line_no, "line": line,
        }
    return "repoint", {"line": line_no, "rendering": rendering, "text": line}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figures", default="tools/remeasure/out-0.3.0/figures.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    figures = json.loads((ROOT / args.figures).read_text(encoding="utf-8"))
    measured_by_id = {r["id"]: r.get("measured") for r in figures["figures"]}
    moved = {r["id"] for r in figures["figures"] if r["status"] == "MOVED"}

    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    rows = inv["figures"]

    cache: dict[str, str] = {}

    def text_of(path: str) -> str:
        if path not in cache:
            cache[path] = (ROOT / path).read_text(encoding="utf-8")
        return cache[path]

    buckets: dict[str, list] = {"repoint": [], "retire": [], "review": []}
    for row in rows:
        if row["id"] not in moved:
            continue
        verdict, info = classify(row, measured_by_id.get(row["id"]),
                                 text_of(row["file"]))
        buckets[verdict].append((row, info))

    for name in ("repoint", "retire", "review"):
        print(f"\n=== {name.upper()}  ({len(buckets[name])}) ===")
        for row, info in buckets[name]:
            print(f"  {row['id']:<34} {row['file']}")
            if name == "repoint":
                print(f"      published {row['published']} -> {info['rendering']}"
                      f"   line {row.get('line')} -> {info['line']}")
                print(f"      {info['text'][:100]}")
            else:
                print(f"      {info['why']}")
                if info.get("line"):
                    print(f"      L{info['at']}: {str(info['line'])[:96]}")

    if not args.apply:
        print("\n(report only; pass --apply to write inventory.json)")
        return

    keep, retired = [], {r["id"] for r, _ in buckets["retire"]}
    fixes = {r["id"]: i for r, i in buckets["repoint"]}
    for row in rows:
        if row["id"] in retired:
            continue
        if row["id"] in fixes:
            info = fixes[row["id"]]
            row["line"] = info["line"]
            rendered = info["rendering"]
            # A string row stays a string. `repro.preset` publishes "pt-v12",
            # which float() rejects -- and the first --apply died on it, which
            # is the only reason the inventory survived that run intact.
            if isinstance(row.get("published"), str):
                row["published"] = rendered
            elif re.fullmatch(r"[+-]?\d+", rendered):
                row["published"] = int(rendered)
            else:
                row["published"] = float(rendered.replace(",", ""))
        keep.append(row)
    inv["figures"] = keep
    INVENTORY.write_text(json.dumps(inv, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {INVENTORY.relative_to(ROOT)}: "
          f"{len(keep)} figures ({len(retired)} retired, {len(fixes)} re-pointed)")


if __name__ == "__main__":
    main()
