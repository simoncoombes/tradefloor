"""Pages authored as markdown and rendered into the built site.

`docs/*.md` and the design bundle are two independently maintained sources for
the same claims, which is the mechanism behind the class-6 findings of the
0.3.0 audit, where `trust.html` and `realism-envelope.md` disagreed about the
band count at 504 days. Pages added through this module have one source: the
markdown. The site page is generated from it, so the two cannot drift.

Wiring a page into the built site takes four insertions, all with unique
anchors in the canvas script and template:

  1. the slug into `known()`, which is what `readPage` checks a hash against
  2. `pgSlug: d("slug")` into the values object the template resolves
     `style="display:{{ pgSlug }}"` against
  3. the page div itself, `<div data-screen-label=... data-page=...>`
  4. a nav link, `<a href="#/slug" data-nav="slug">`

Search needs no fifth edit: `buildSearchIndex` reads `#sbar a[data-nav]` and
`#main [data-screen-label]` from the DOM, so a page with a nav link and a page
div is indexed by existing code. The sitemap, `llms.txt` and the static pages
all derive from `split_pages`, which reads the same page divs.
"""

from __future__ import annotations

import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

#: (slug, markdown file, sidebar label, sidebar section, rack kicker).
#: The section is the sidebar heading the nav link is inserted after, which is
#: how the page lands in the right group rather than at the end of the list.
#: The sidebar anchor each nav link is inserted AFTER. The sidebar has four
#: section headings -- Guide, Connect, Reference, API -- and the two links
#: above the first one are the getting-started pair, so a page that belongs
#: with them anchors on the install link rather than on a heading.
NAV_AFTER = {
    "install": '<a href="#/install" data-nav="install">Install &amp; Versioning</a>',
    "Reference": ('<div style="font:650 13px/1 var(--font-ui);letter-spacing:0.01em;'
                  'color:var(--fg);padding:0 10px;margin:22px 0 8px">Reference</div>'),
}

NEW_PAGES = [
    ("two-loops", "two-loops.md", "The Two Loops", "install", "GETTING STARTED"),
    ("glossary", "glossary.md", "Glossary", "Reference", "REFERENCE"),
    ("principles", "principles.md", "The Principles", "Reference", "REFERENCE"),
]

#: Markdown filename to site slug, for links between the two surfaces. Only
#: pairs where the markdown page and the site page are the same page: several
#: markdown files have no site page and several site pages have no markdown,
#: and guessing a pairing would be exactly the invented connection this
#: project has a recorded case of. Anything not listed is rendered as a link
#: to the markdown file, which GitHub Pages serves.
MD_TO_SLUG = {
    "atlas.md": "atlas",
    "mcp.md": "mcp",
    "scenarios.md": "scenarios",
    "an-llm-agent.md": "llm-agent",
    "agents-and-evaluation.md": "agents",
    "forking-a-simulation.md": "forking",
    "running-a-simulation.md": "simulate",
    "rng-streams.md": "internals",
    "real-fundamentals-from-sec-edgar.md": "edgar",
    "glossary.md": "glossary",
    "two-loops.md": "two-loops",
    "principles.md": "principles",
}

_MUT = 'style="color:var(--mut);font-size:14px;margin:0 0 12px"'
_CODE = 'style="font-size:12.5px"'
_LINK = 'style="color:var(--accent)"'


def _inline(text: str) -> str:
    """Markdown inline markup to the design's inline-styled HTML."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code {_CODE}>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def link(m):
        label, target = m.group(1), m.group(2)
        file, _, anchor = target.partition("#")
        slug = MD_TO_SLUG.get(file)
        if slug:
            href = f"#/{slug}" + (f"/{anchor}" if anchor else "")
        else:
            href = target
        return f'<a href="{href}" {_LINK}>{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    body = [r for r in cells[1:] if not all(set(c) <= set("-: ") for c in r)]
    head = "".join(
        f'<th style="text-align:left;padding:6px 10px;border-bottom:1px solid '
        f'var(--line);font:600 12px var(--font-ui)">{_inline(c)}</th>'
        for c in cells[0])
    trs = "".join(
        "<tr>" + "".join(
            f'<td style="padding:6px 10px;border-bottom:1px solid var(--linesoft);'
            f'font-size:13px;color:var(--mut);vertical-align:top">{_inline(c)}</td>'
            for c in r) + "</tr>"
        for r in body)
    return ('<div style="overflow-x:auto"><table style="border-collapse:collapse;'
            f'margin:12px 0;min-width:100%"><tr>{head}</tr>{trs}</table></div>')


def render(md: str) -> tuple[str, str]:
    """(h1, body html) for one markdown page.

    Handles what these pages use: headings, paragraphs, bullet lists, tables,
    fenced code, and raw HTML blocks passed through verbatim so a hand-authored
    `<figure>` survives intact.
    """
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":            # frontmatter
        end = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
        lines = lines[end + 1:]

    out: list[str] = []
    para: list[str] = []
    bullets: list[str] = []
    table: list[str] = []
    fence: list[str] | None = None
    rawhtml: list[str] | None = None
    h1 = ""

    def flush() -> None:
        nonlocal para, bullets, table
        if table:
            out.append(_table(table)); table = []
        if bullets:
            items = "".join(
                f'<li style="margin:0 0 6px">{_inline(b)}</li>' for b in bullets)
            out.append(f'<ul style="color:var(--mut);font-size:14px;'
                       f'margin:0 0 12px;padding-left:20px">{items}</ul>')
            bullets = []
        if para:
            out.append(f"<p {_MUT}>{_inline(' '.join(para))}</p>")
            para = []

    for line in lines:
        if rawhtml is not None:
            rawhtml.append(line)
            if line.strip() == "</figure>":
                out.append("\n".join(rawhtml)); rawhtml = None
            continue
        if line.strip().startswith("<figure"):
            flush(); rawhtml = [line]; continue
        if line.startswith("```"):
            if fence is None:
                flush(); fence = []
            else:
                out.append(
                    '<pre style="background:var(--codebg);color:#e8e8e8;'
                    'padding:12px 14px;border-radius:6px;overflow-x:auto;'
                    'font:12.5px/1.6 var(--font-mono)">'
                    + html.escape("\n".join(fence)) + "</pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line); continue
        if line.startswith("# "):
            h1 = line[2:].strip(); continue
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            anchor = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            out.append(f'<h2 id="{anchor}" style="font-size:21px;margin:46px 0 10px">'
                       f"{_inline(title)}</h2>")
            continue
        if line.startswith("### "):
            flush()
            out.append(f'<h3 style="font-size:16px;margin:26px 0 8px">'
                       f"{_inline(line[4:].strip())}</h3>")
            continue
        if line.startswith("|"):
            if para or bullets:
                flush()
            table.append(line); continue
        if re.match(r"^[-*] ", line):
            if para or table:
                flush()
            bullets.append(line[2:].strip()); continue
        if not line.strip():
            flush(); continue
        if table or bullets:
            flush()
        para.append(line.strip())
    flush()
    if rawhtml is not None:
        sys.exit("a <figure> block was never closed with </figure>")
    return h1, "\n        ".join(out)


def _var(slug: str) -> str:
    return "pg" + "".join(p.capitalize() for p in slug.split("-"))


def page_div(slug: str, label: str, kicker: str, h1: str, body: str) -> str:
    var = _var(slug)
    return (
        f'\n      <div data-screen-label="{html.escape(label)}" '
        f'data-page="{slug}" style="display:{{{{ {var} }}}}">\n'
        f'        <div style="font:600 11px/1 var(--font-mono);color:var(--accent);'
        f'letter-spacing:0.1em;margin:0 0 12px">{kicker}</div>\n'
        f'        <h1 style="font-size:30px">{html.escape(h1)}</h1>\n'
        f"        {body}\n"
        f"      </div>\n"
    )


def _sub(doc: str, old: str, new: str, what: str) -> str:
    if old not in doc:
        sys.exit(f"newpages: anchor for {what} not found in the design bundle: "
                 f"{old[:70]!r}")
    if doc.count(old) != 1:
        sys.exit(f"newpages: anchor for {what} is not unique ({doc.count(old)})")
    return doc.replace(old, new, 1)


def inject(doc: str) -> str:
    """Add every page in NEW_PAGES to the canvas document.

    Each of the four edits happens ONCE, for all pages together. Doing them
    per page consumed the anchor on the first pass and the second page then
    failed to find it, which is the kind of thing that reads as a missing
    bundle rather than as a bug here.
    """
    rendered = []
    for slug, md_name, label, after, kicker in NEW_PAGES:
        path = DOCS / md_name
        if not path.exists():
            sys.exit(f"newpages: {path} is missing; the site page is generated "
                     "from it and there is no second copy to fall back to")
        h1, body = render(path.read_text(encoding="utf-8"))
        if not h1:
            sys.exit(f"newpages: {md_name} has no level-one heading")
        rendered.append((slug, label, after, kicker, h1, body))

    slugs = [r[0] for r in rendered]

    # 1. the route allow-list `readPage` checks a hash against
    doc = _sub(doc, '"citing", "install"];',
               '"citing", "install", ' + ", ".join(f'"{s}"' for s in slugs) + "];",
               "known()")
    # 2. the values the template resolves `display:{{ pgSlug }}` against
    adds = " ".join(f'{_var(s)}: d("{s}"),' for s in slugs)
    doc = _sub(doc, 'pgCiting: d("citing"), pgInstall: d("install"),',
               f'pgCiting: d("citing"), pgInstall: d("install"), {adds}',
               "display values")
    # 3. the page bodies, before the container that closes the page list
    bodies = "".join(page_div(s, l, k, h, b) for s, l, _, k, h, b in rendered)
    doc = _sub(doc, "\n    </div>\n  </main>",
               bodies + "\n    </div>\n  </main>", "page divs")
    # 4. the sidebar links, grouped by the anchor they follow
    for anchor_key in dict.fromkeys(r[2] for r in rendered):
        anchor = NAV_AFTER[anchor_key]
        links = "".join(
            f'\n    <a href="#/{s}" data-nav="{s}">{html.escape(l)}</a>'
            for s, l, a, _, _, _ in rendered if a == anchor_key)
        doc = _sub(doc, anchor, anchor + links, f"nav links after {anchor_key}")
    return doc
