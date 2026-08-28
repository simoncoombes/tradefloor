"""What the site owes to crawlers and to language models.

`tools/docs/build_site.py` produces these for the published site. The
learning path needs its own, and they are written here rather than shared
because the two sites have different page sets and different URLs, and a
shared generator would have to be told which it was serving on every call.

Four files:

    sitemap.xml    every page, with the date its source last changed
    llms.txt       the summary and the page list, per llmstxt.org
    llms-full.txt  the whole site as plain text, in reading order
    robots.txt     written only when the site is at the origin root

`llms-full.txt` matters more here than it did for the old docs. The pages
are a learning path with the caveats folded into `<details>` elements, and a
model reading rendered HTML gets those collapsed sections as text either
way — but it also gets the charts, the sliders and the tables, none of which
mean anything without the pictures. The plain-text form is the prose alone,
in the order a reader meets it.
"""

from __future__ import annotations

import datetime
import html
import pathlib
import re
import subprocess


def last_changed(path: pathlib.Path, root: pathlib.Path) -> str:
    """The date this page's source last changed, from git.

    A sitemap whose `lastmod` is the build date tells a crawler that
    everything changed every time anything did. The source file's own last
    commit is the honest answer, and it falls back to today only outside a
    checkout.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path)],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        stamp = out.stdout.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
            return stamp
    except Exception:
        pass
    return datetime.date.today().isoformat()


def sitemap(pages, base_url: str, dates: dict[str, str]) -> str:
    entries = []
    for page in pages:
        loc = f"{base_url}/{page['slug']}.html"
        priority = "1.0" if page["slug"] == "index" else "0.8"
        entries.append(
            f"<url><loc>{html.escape(loc)}</loc>"
            f"<lastmod>{dates[page['slug']]}</lastmod>"
            f"<priority>{priority}</priority></url>"
        )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(entries) + "</urlset>\n")


def llms(pages, doors, base_url: str, version: str, summaries: dict[str, str]) -> str:
    """The short form: what this is, and where each page is.

    Grouped by door, because the doors are the site's structure and a model
    choosing one page to read should be choosing from a shelf, not a list.
    """
    lines = [
        "# pretium — learn",
        "",
        "> A deterministic market simulator with a real limit order book. Give it a",
        "> seed and a roster of companies and it runs prices, a limit order book,",
        f"> fills and an economy forward, day by day. Version {version}.",
        "",
        "These pages are a learning path rather than a reference: they go from what a",
        "limit order book is, through running one and driving it with an agent, to",
        "what the simulator has been measured against and what it must not be used",
        "for. Each page states its own limits in place rather than in a separate",
        "caveats document.",
        "",
        "When quoting this project, cite the preset and the version, not \"pretium\":",
        "every preset is frozen and named, and the numbers change between them.",
        "",
    ]
    front = next(p for p in pages if p["slug"] == "index")
    lines += ["## Start", "",
              f"- [{front['name']}]({base_url}/index.html): "
              f"{summaries.get('index', '')}", ""]
    for name, _short, _slug, entries in doors:
        lines.append(f"## {name}")
        lines.append("")
        for page_name, slug in entries:
            summary = summaries.get(slug, "")
            lines.append(f"- [{page_name}]({base_url}/{slug}.html): {summary}")
        lines.append("")
    return "\n".join(lines)


#: Elements with no text worth reading: a chart is a picture, and a script
#: or a style is not prose at all.
_DROP = re.compile(r"<(script|style|svg|template)\b.*?</\1>", re.S | re.I)

_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
#: Body cells only. A header row is labels whatever the table holds, so
#: counting it dilutes exactly the tables this is trying to recognise: a
#: five-column preview of prices reads as half text until the `ticker` and
#: `sector` headings are set aside.
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)
_NUMERIC = re.compile(r"^[\s+\-−]*[\d.,]+\s*(%|x|bps)?\s*$")


def _table_is_data(table_html: str) -> bool:
    """Is this a preview of numbers, or a reference table?

    Both are tables and only one is worth reading as text. The schema,
    parameter and glossary tables are prose in a grid — a column name and a
    sentence saying what it holds — and a model should have them. The market
    previews are thirty rows of prices generated from a golden, and as text
    they are a wall of digits that says nothing the chart around them does
    not. Counting how many cells are bare numbers separates the two without
    needing a list of which page has which.
    """
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(table_html)]
    cells = [c for c in cells if c]
    if len(cells) < 6:
        return False
    numeric = sum(1 for c in cells if _NUMERIC.match(c))
    return numeric / len(cells) > 0.5


def page_text(page_html: str) -> str:
    """The reading text of one page, in document order."""
    start = page_html.find('<div id="pt-root">')
    end = page_html.find('<script id="pt-template"')
    if start < 0 or end < 0:
        return ""
    body = page_html[start:end]
    body = _DROP.sub(" ", body)
    body = _TABLE.sub(lambda m: " " if _table_is_data(m.group(0)) else m.group(0), body)
    # Keep the block structure as line breaks so the result reads as prose.
    body = re.sub(r"</(p|h1|h2|h3|li|tr|section|summary|pre)>", "\n\n", body, flags=re.I)
    body = re.sub(r"</(td|th|span)>", " ", body, flags=re.I)
    body = re.sub(r"<[^>]+>", "", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n\s*", "\n\n", body)
    return body.strip()


def llms_full(pages, base_url: str, version: str, rendered: dict[str, str],
              summaries: dict[str, str]) -> str:
    out = [
        "# pretium — learn",
        "",
        "> A deterministic market simulator with a real limit order book.",
        f"> Version {version}. Every page of the learning path, in reading order.",
        "",
    ]
    for page in pages:
        out.append("")
        out.append("=" * 70)
        out.append(f"# {page['name']}")
        out.append(f"{base_url}/{page['slug']}.html")
        if summaries.get(page["slug"]):
            out.append("")
            out.append(summaries[page["slug"]])
        out.append("=" * 70)
        out.append("")
        out.append(page_text(rendered[page["slug"]]))
    return "\n".join(out) + "\n"


#: The crawlers worth naming, kept in step with `docs/robots.txt`. Listing
#: them is not the same as allowing everything: a bot absent from the list
#: falls to the `*` rule, which is also Allow, so this is a statement of
#: intent rather than a gate.
ROBOTS_AGENTS = [
    "*", "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
    "Claude-SearchBot", "anthropic-ai", "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Applebot-Extended", "Amazonbot", "Bytespider", "CCBot",
    "cohere-ai", "Meta-ExternalAgent", "DuckAssistBot", "MistralAI-User",
]


def robots(base_url: str) -> str:
    blocks = "\n\n".join(f"User-agent: {a}\nAllow: /" for a in ROBOTS_AGENTS)
    return (blocks + "\n\n"
            f"Sitemap: {base_url}/sitemap.xml\n"
            f"# Summary for language models: {base_url}/llms.txt\n"
            f"# Full text for language models: {base_url}/llms-full.txt\n")
