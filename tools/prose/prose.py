"""Check prose against the house style.

    python tools/prose/prose.py                 # this repository's markdown
    python tools/prose/prose.py path [path ...] # any markdown or rendered page

Target register: reference documentation. Flat, declarative, unmemorable.
The reader is scanning for a fact, not being persuaded.

The checks here are the mechanical half of the style. Register cannot be
checked by a script, so a clean run is not a passing grade: headings still
have to be re-read for verbs and commas.

Every rule reports the file, the line and the text, so a failure is
actionable rather than a score.
"""

from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

#: The prose this repository publishes. Code comments and docstrings are
#: deliberately out of scope: they are a far larger surface and the rules
#: below were written for prose a reader meets, not for a comment beside
#: the line it explains.
DEFAULT_TARGETS = ("README.md", "CHANGELOG.md", "CONTRIBUTING.md",
                   "CONTENT.md", "PRODUCT.md", "RELEASING.md",
                   "SECURITY.md")

#: Phrases that tell the reader something is significant.
SIGNIFICANCE = [
    r"\band that is the point\b", r"\bwhich is why this matters\b",
    r"\bthat is the whole idea\b", r"\bthat is the point\b",
    r"\bwhich is the point\b", r"\bthe whole point\b",
    r"\bwhich matters\b", r"\bthis matters\b", r"\bwhat matters\b",
    r"\bis what makes\b", r"\bis the reason\b", r"\bthat is why\b",
    r"\bworth (?:saying|noting|knowing)\b",
]

#: Definition by negation.
NEGATION = [
    r"\bis not a\b", r"\bis not the\b", r"\bare not a\b", r"\bare not the\b",
    r"\bnot .{1,40}? but\b", r"\bnever .{1,30}?, only\b",
    r"\brather than a\b", r"\brather than the\b",
]

#: A heading is a noun phrase. A question word opens one; a copula or modal
#: makes one a sentence.
HEADING_OPENERS = re.compile(
    r"^(?:how|why|what|when|where|which|who|can|does|do|is|are|will|should)\b", re.I)
HEADING_COPULA = re.compile(
    r"\b(?:is|are|was|were|has|have|does|do|did|can|will|would|should|must)\b", re.I)

#: Words that are verbs in a heading unless a determiner makes them nouns:
#: "Run a market" is a verb phrase, "Citing a run" is not.
AMBIGUOUS_VERBS = ("makes", "make", "gives", "give", "takes", "take", "means",
                   "mean", "knows", "know", "says", "say", "goes", "go",
                   "comes", "come", "runs", "holds", "hold", "stops", "stop",
                   "becomes", "become", "reaches", "reach", "carries", "carry",
                   "fixes", "moves", "move", "breaks", "break", "leaves",
                   "leave", "lists", "reproduces", "reproduce", "answers",
                   "answer", "pins", "pin", "sums", "sum", "refuses", "refuse")
DETERMINED = re.compile(
    r"\b(?:a|an|the|one|two|its|their|your|our|this|that|these|those|every|each|"
    r"no|any|some)\s+(?:[a-z]+\s+){0,2}$", re.I)


#: Headings where an ambiguous word is a noun. The check cannot tell
#: "Paired runs on one seed" from "Run a market", so the exceptions are
#: listed rather than the rule loosened.
NOUN_HEADINGS = {
    "paired runs on one seed", "price move decomposition", "generator draws",
    "order impact", "trajectory inputs", "refusal points",
}


def heading_is_sentence(h: str) -> bool:
    if h.strip().lower() in NOUN_HEADINGS:
        return False
    if HEADING_OPENERS.search(h) or HEADING_COPULA.search(h):
        return True
    for m in re.finditer(r"\b([a-z]+)\b", h, re.I):
        if m.group(1).lower() in AMBIGUOUS_VERBS and not DETERMINED.search(h[:m.start()]):
            return True
    return False


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(`])", text)
    return [p.strip() for p in parts if p.strip()]


def words(sentence: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'`_./()-]*", sentence))


def check_text(label: str, blocks: list[tuple[int, str]],
               headings: list[tuple[int, str]]) -> list[str]:
    """`blocks` is (line, paragraph); `headings` is (line, heading text)."""
    out = []

    for line, h in headings:
        h = h.strip().rstrip(".")
        if not h or h.startswith("{{"):
            continue
        if "," in h:
            out.append(f"{label}:{line}: heading has a comma: {h!r}")
        if heading_is_sentence(h):
            out.append(f"{label}:{line}: heading is not a noun phrase: {h!r}")

    for line, para in blocks:
        flat = re.sub(r"\s+", " ", para).strip()
        if not flat:
            continue
        for pat in SIGNIFICANCE:
            m = re.search(pat, flat, re.I)
            if m:
                out.append(f"{label}:{line}: tells the reader it matters: "
                           f"{excerpt(flat, m.start())!r}")
        for pat in NEGATION:
            m = re.search(pat, flat, re.I)
            if m:
                out.append(f"{label}:{line}: defines by negation: "
                           f"{excerpt(flat, m.start())!r}")
        if re.search(r"\?(?:\s|$)", flat) and not flat.lstrip().startswith(">"):
            m = re.search(r"[^.?!]*\?", flat)
            out.append(f"{label}:{line}: rhetorical question: {m.group(0).strip()[:70]!r}")
        if "—" in flat:
            out.append(f"{label}:{line}: em-dash aside: "
                       f"{excerpt(flat, flat.index(chr(0x2014)))!r}")

        ss = sentences(flat)
        if ss and words(ss[-1]) < 6 and len(ss) > 1:
            out.append(f"{label}:{line}: paragraph ends on a short sentence: {ss[-1]!r}")
        for i in range(len(ss) - 2):
            if all(words(s) < 10 for s in ss[i:i + 3]):
                out.append(f"{label}:{line}: three short sentences in a row: "
                           f"{' '.join(ss[i:i + 3])[:80]!r}")
                break
    return out


def excerpt(text: str, at: int, span: int = 34) -> str:
    return text[max(0, at - 8):at + span].strip()


def from_markdown(path: pathlib.Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").split("\n")
    blocks, headings, buf, start, fence = [], [], [], 1, False
    off = False
    for n, raw in enumerate(lines, 1):
        # A passage that quotes a banned construction in order to ban it has
        # to contain one. `<!-- prose: off -->` ... `<!-- prose: on -->`
        # marks it, so the exception is visible in the file it applies to.
        if "<!-- prose: off -->" in raw:
            off = True
            continue
        if "<!-- prose: on -->" in raw:
            off = False
            continue
        if off:
            continue
        if raw.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if raw.startswith("#"):
            headings.append((n, raw.lstrip("#").strip()))
            continue
        if raw.strip():
            if not buf:
                start = n
            buf.append(raw)
        elif buf:
            blocks.append((start, " ".join(buf)))
            buf = []
    if buf:
        blocks.append((start, " ".join(buf)))
    return check_text(path.name, blocks, headings)


def from_page(path: pathlib.Path) -> list[str]:
    """The rendered page: its headings and its paragraphs."""
    src = path.read_text(encoding="utf-8").split("<template")[0]
    src = re.sub(r"<(script|style|pre|code)[^>]*>.*?</\1>", " ", src, flags=re.S)
    headings = [(0, html.unescape(re.sub(r"<[^>]+>", "", m.group(1))))
                for m in re.finditer(r"<h[1-3][^>]*>(.*?)</h[1-3]>", src, re.S)]
    blocks = [(0, html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))))
              for m in re.finditer(r"<p[^>]*>(.*?)</p>", src, re.S)]
    return check_text(path.name, blocks, headings)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--quiet", action="store_true",
                    help="counts per file, not every finding")
    args = ap.parse_args()

    targets = [pathlib.Path(p) for p in args.paths]
    if not targets:
        # The canonical copy lives here and the documentation site vendors
        # it, so the default target is whichever of the two trees it is
        # sitting in: this repository's prose, or the site's rendered pages.
        targets = [ROOT / n for n in DEFAULT_TARGETS if (ROOT / n).exists()]
        targets += sorted(p for p in (ROOT / "docs").glob("*.html")
                          if p.stat().st_size > 4096)

    findings = []
    for p in targets:
        if not p.exists():
            sys.exit(f"{p} does not exist")
        findings += from_page(p) if p.suffix == ".html" else from_markdown(p)

    if args.quiet:
        per = {}
        for f in findings:
            per[f.split(":")[0]] = per.get(f.split(":")[0], 0) + 1
        for name, n in sorted(per.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {name}")
    else:
        for f in findings:
            print(f"  {f}")
    print(f"{len(findings)} findings across {len(targets)} files")


if __name__ == "__main__":
    main()
