"""Inventory the numbers the learning site states in prose.

The handoff asked for two provenance fixes. The first - generating the
chart data from the goldens - is done, in `data.py`. This is groundwork for
the second: "add every prose figure on these pages to `tools/remeasure`'s
inventory, so `remeasure.py` can check them the way it already checks the
rebalance measurement".

Assigning a measurement to a claim is a judgement about which computation
reproduces it, and that judgement belongs to whoever owns the measurement.
What can be done mechanically is everything up to it, which is what this
does:

    python tools/docs/learn/figures.py

It reads every number the built pages state in prose, matches each against
`tools/remeasure/inventory.json`, and writes `figures-todo.json`: one entry
per claim that no inventory figure accounts for, with the page, the
sentence it appears in and the value, ready for a `group` and a `key`.

It reports two things worth acting on immediately:

  backed      the claim repeats a value the inventory already measures, so
              `remeasure.py` will catch it if the engine moves under it
  conflicting the claim sits in a sentence about a figure the inventory
              measures, and states a different number

Numbers inside code blocks, tables and data readouts are skipped. Those are
either generated from the goldens - where `data.py` is the guarantee - or
they are code, where the example either runs or does not.
"""

from __future__ import annotations

import argparse
import collections
import html
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INVENTORY = ROOT / "tools" / "remeasure" / "inventory.json"

#: A stated quantity. A number carrying a unit is always a claim; so is a
#: decimal, because prose does not use decimals for anything else. A bare
#: integer is not - it is usually a count of columns or a step number - and
#: is only taken when a unit follows it.
NUMBER = re.compile(
    r"(?<![\w.\-])(-?\d[\d,]*\.\d+|-?\d[\d,]*)"
    r"\s*(%|x\b|×|bps\b|basis points\b|days?\b|seeds?\b|names?\b|"
    r"ticks?\b|years?\b|sectors?\b|companies\b|instruments?\b)?")

#: Contexts where a number is never a measurement: a preset name, a version,
#: a date, a Python version, a citation year.
IGNORE_CONTEXT = re.compile(
    r"pt-v\d+|python\s*3|cpython|version\s|20\d\d-\d\d-\d\d|©|section\s+\d", re.I)

def prose(page_html: str) -> list[str]:
    """The sentences a reader reads, without the code or the tables.

    Only `<p>` and `<summary>`: a figure quoted in prose is a claim the page
    is making, while a figure in a table cell or a code block came out of
    `pt-data.js` and is already tied to a golden.
    """
    body = page_html
    start = body.find('<div id="pt-root">')
    end = body.find('<script id="pt-template"')
    if start < 0 or end < 0:
        return []
    body = body[start:end]

    out = []
    for m in re.finditer(r"<(p|summary)\b[^>]*>(.*?)</\1>", body, re.S):
        text = re.sub(r"<[^>]+>", " ", m.group(2))
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()
        if text:
            out.extend(split_sentences(text))
    return out


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    return [p.strip() for p in parts if p.strip()]


def claims(pages: dict[str, str]) -> list[dict]:
    found = []
    for slug, page_html in sorted(pages.items()):
        for sentence in prose(page_html):
            if IGNORE_CONTEXT.search(sentence):
                continue
            for m in NUMBER.finditer(sentence):
                raw, unit = m.group(1), (m.group(2) or "").strip()
                if not unit and "." not in raw:
                    continue
                value = float(raw.replace(",", ""))
                found.append({
                    "page": slug,
                    "value": value,
                    "unit": unit,
                    "text": raw + (" " + unit if unit else ""),
                    "sentence": sentence,
                })
    return found


def inventory() -> list[dict]:
    """Every measured figure, from both places that hold one.

    `tools/remeasure/inventory.json` is the claim inventory. `docs/envelope.json`
    is the envelope the package itself publishes, and it carries each of the
    fourteen statistics' measured value and the two edges of its band - all
    of which the Trust it pages quote. Reading both is what stops a figure
    being reported as unbacked when the package regenerates it on every
    release.
    """
    with INVENTORY.open(encoding="utf-8") as fh:
        figures = list(json.load(fh)["figures"])

    with (ROOT / "docs" / "envelope.json").open(encoding="utf-8") as fh:
        env = json.load(fh)
    for key, stat in env["statistics"].items():
        figures.append({
            "id": f"envelope.{key}", "file": "docs/envelope.json",
            "label": f"{key} measured on the shipped preset at the certified horizon",
            "published": stat["measured"], "source": "generated",
        })
        for edge, value in zip(("floor", "ceiling"), stat["band"]):
            figures.append({
                "id": f"envelope.{key}.{edge}", "file": "docs/envelope.json",
                "label": f"{key} real band {edge}", "published": value,
                "source": "generated",
            })
    figures.append({
        "id": "envelope.horizon", "file": "docs/envelope.json",
        "label": "the certified horizon in trading days",
        "published": env["certified_horizon_days"], "source": "generated",
    })
    return figures


def numeric(fig) -> float | None:
    v = fig.get("published")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


#: Words that carry meaning when matching a claim to a measured figure.
STOP = set("the a an of and or to in on at is are for by with from that this it "
           "its as be been than then over under per each every all any one two".split())


def keywords(label: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_]{3,}", label.lower()) if w not in STOP}


def classify(found: list[dict], figures: list[dict]) -> tuple[list, list, list]:
    """Split the site's claims into backed, conflicting and unaccounted for."""
    by_value: dict[float, list[dict]] = {}
    for fig in figures:
        v = numeric(fig)
        if v is None:
            continue
        by_value.setdefault(round(v, 6), []).append(fig)

    backed, conflicting, todo = [], [], []
    for claim in found:
        exact = by_value.get(round(claim["value"], 6))
        if exact:
            backed.append({**claim, "figure": exact[0]["id"]})
            continue

        # Not a value the inventory holds. Does the sentence talk about
        # something the inventory measures, with a different number?
        words = keywords(claim["sentence"])
        numbers_here = {round(float(t.replace(",", "")), 6)
                        for t in re.findall(r"-?\d[\d,]*(?:\.\d+)?", claim["sentence"])}
        rival = None
        best = 0
        for fig in figures:
            v = numeric(fig)
            if v is None:
                continue
            # If the measured value is already somewhere in this sentence,
            # the sentence is quoting it correctly and the other numbers
            # around it are its bounds or its inputs.
            if round(v, 6) in numbers_here:
                rival = None
                break
            overlap = keywords(fig["label"]) & words
            if len(overlap) >= 4 and len(overlap) > best:
                best = len(overlap)
                rival = (fig, sorted(overlap))
        if rival:
            conflicting.append({**claim, "figure": rival[0]["id"],
                                "published": numeric(rival[0]),
                                "label": rival[0]["label"],
                                "on": rival[1]})
        else:
            todo.append(claim)
    return backed, conflicting, todo


#: A first sort of the unbacked figures, so the worklist arrives grouped by
#: what kind of work each one is rather than as a flat list of numbers.
def kind_of(sentence: str) -> str:
    low = sentence.lower()
    if re.search(r"\braises\b|\bmeans\b|\berror\b|\brejects?\b", low):
        return "contract"          # an API promise; a test, not a measurement
    if re.search(r"\bpin\b|\bpassing\b|=\s*\d|\bfor example\b", low):
        return "example"           # a value chosen to illustrate, not measured
    if re.search(r"\bmeasured\b|\breads\b|\bagainst a real\b|\bruns\b|"
                 r"\bcloses\b|\bholds\b|\bcame back\b", low):
        return "measured"          # a claim about what the engine or the world did
    return "unsorted"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default=str(ROOT / "docs"))
    ap.add_argument("--out", default=str(HERE / "figures-todo.json"))
    args = ap.parse_args()

    site = pathlib.Path(args.site)
    # Redirect stubs carry no prose and would inflate the page count.
    pages = {}
    for path in sorted(site.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        if '<meta http-equiv="refresh"' in text:
            continue
        pages[path.stem] = text
    if not pages:
        sys.exit(f"no pages in {site}")

    found = claims(pages)
    figures = inventory()
    backed, conflicting, todo = classify(found, figures)

    print(f"{len(found)} stated figures across {len(pages)} pages")
    print(f"  {len(backed):3d} repeat a value tools/remeasure already measures")
    print(f"  {len(conflicting):3d} sit in a sentence about a measured figure and differ from it")
    print(f"  {len(todo):3d} have no measurement behind them yet")

    kinds = collections.Counter(kind_of(c["sentence"]) for c in todo)
    for kind, n in kinds.most_common():
        print(f"        {n:3d} {kind}")

    if conflicting:
        print("\nworth reading first:")
        for c in conflicting[:12]:
            print(f"  {c['page']}: says {c['text']}, {c['figure']} measures {c['published']}")
            print(f"      \"{c['sentence'][:110]}\"")

    payload = {
        "comment": (
            "Prose figures on the learning site with no entry in "
            "tools/remeasure/inventory.json. Generated by "
            "tools/docs/learn/figures.py; add `group` and `key` per figure to "
            "bring it under remeasure.py."
        ),
        "figures": [
            {"id": f"learn.{c['page']}.{i}", "file": f"docs/{c['page']}.html",
             "label": c["sentence"][:160], "published": c["value"], "unit": c["unit"],
             "kind": kind_of(c["sentence"]), "group": None, "key": None}
            for i, c in enumerate(todo)
        ],
    }
    pathlib.Path(args.out).write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
