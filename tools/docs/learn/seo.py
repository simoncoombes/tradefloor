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
way - but it also gets the charts, the sliders and the tables, none of which
mean anything without the pictures. The plain-text form is the prose alone,
in the order a reader meets it.
"""

from __future__ import annotations

import datetime
import html
import json
import pathlib
import re
import subprocess


REPO_URL = "https://github.com/simoncoombes/tradefloor"
AUTHOR = {"@type": "Person", "name": "Simon Coombes",
          "url": "https://github.com/simoncoombes"}
SAME_AS = [REPO_URL, "https://pypi.org/project/tradefloor/", "https://crates.io/crates/tradefloor"]


def software_node(version: str) -> dict:
    """The package itself, described once and referred to from every page.

    Without it the PyPI package, the crate, the repository and this site are
    four unrelated things to a search engine rather than one - and "tradefloor"
    is Latin for price, which is a crowded name to have no disambiguation.
    """
    return {
        "@type": "SoftwareApplication",
        "name": "tradefloor",
        "alternateName": "tradefloor market simulator",
        "applicationCategory": "DeveloperApplication",
        "applicationSubCategory": "Market simulation",
        "operatingSystem": "Linux, macOS, Windows",
        "programmingLanguage": ["Rust", "Python"],
        "softwareVersion": version,
        "codeRepository": REPO_URL,
        "license": "https://opensource.org/licenses/MIT",
        "author": AUTHOR,
        "sameAs": SAME_AS,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }


def article_node(page, base_url, version, description, published, modified) -> dict:
    """One page, as a technical article about the package."""
    node = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": page["name"],
        "description": description,
        "url": canonical(base_url, page['slug']),
        "inLanguage": "en",
        "author": AUTHOR,
        "isPartOf": {"@type": "WebSite", "name": "tradefloor documentation",
                     "url": f"{base_url}/"},
        "about": software_node(version),
    }
    if published:
        node["datePublished"] = published
    if modified:
        node["dateModified"] = modified
    return node


def breadcrumb_node(page, base_url) -> dict | None:
    """Where the page sits, which is also what the masthead says.

    Answer engines use this to say "in Trust it" rather than guessing from
    the URL, which carries no hierarchy here - every page is one level down.
    """
    if not page.get("door"):
        return None
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "tradefloor",
             "item": f"{base_url}/index.html"},
            {"@type": "ListItem", "position": 2, "name": page["door"]},
            {"@type": "ListItem", "position": 3, "name": page["name"],
             "item": f"{base_url}/{page['slug']}.html"},
        ],
    }


def website_node(base_url: str) -> dict:
    """The site, with its search. Only on the front door."""
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "tradefloor documentation",
        "url": f"{base_url}/",
        "inLanguage": "en",
        "publisher": AUTHOR,
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": f"{base_url}/index.html?q={{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }


def dataset_node(envelope: dict, base_url: str) -> dict:
    """The realism envelope as a citable dataset.

    It is the project's central claim and it is already published as JSON,
    so it should be findable as data rather than only as prose. Each of the
    fourteen statistics becomes a measured variable with the band it was
    scored against, which is the part a reader - or a model answering a
    question about it - actually needs.
    """
    measured = []
    for key, stat in envelope["statistics"].items():
        measured.append({
            "@type": "PropertyValue",
            "name": key,
            "value": stat["measured"],
            "minValue": stat["band"][0],
            "maxValue": stat["band"][1],
            "description": ("measured on the shipped preset; the range is the "
                            "band real equities hold"),
        })
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "tradefloor realism envelope",
        "alternateName": f"tradefloor {envelope['preset']} realism measurement",
        "description": (
            f"The {len(measured)} statistics tradefloor's default preset "
            f"{envelope['preset']} is measured on, each with the real-market "
            f"band it was scored against, a certified horizon of "
            f"{envelope['certified_horizon_days']} trading days, and the named "
            f"gaps stating what the simulator must not be used for."
        ),
        "url": f"{base_url}/realism-envelope.html",
        "identifier": f"{base_url}/envelope.json",
        "license": "https://opensource.org/licenses/MIT",
        "isAccessibleForFree": True,
        "creator": AUTHOR,
        "isBasedOn": REPO_URL,
        "measurementTechnique": (
            "Thirty-seed median of each statistic over a 252-day run on the "
            "certified roster, scored against bands derived from published "
            "real-market measurements."
        ),
        "variableMeasured": measured,
        "distribution": [{"@type": "DataDownload",
                          "encodingFormat": "application/json",
                          "contentUrl": f"{base_url}/envelope.json"}],
    }


def glossary_node(terms, base_url: str) -> dict | None:
    """The glossary as a set of defined terms.

    Forty-odd definitions in a filterable grid of cards are invisible as
    data. As a DefinedTermSet each one can be quoted with its source, which
    is the difference between a model paraphrasing a definition and citing
    it.

    The terms come from the page's own component rather than from its
    rendered markup, so the structured data and the page cannot disagree.
    """
    if not terms:
        return None
    defined = []
    for entry in terms:
        name = (entry.get("term") or "").strip()
        body = (entry.get("body") or "").strip()
        if not name or len(body) < 12:
            continue
        item = {"@type": "DefinedTerm", "name": name, "description": body,
                "inDefinedTermSet": f"{base_url}/glossary.html"}
        if entry.get("group"):
            item["termCode"] = entry["group"]
        defined.append(item)
    if len(defined) < 5:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "DefinedTermSet",
        "name": "tradefloor glossary",
        "description": (
            f"{len(defined)} terms used across the tradefloor documentation, "
            "each defined in the sense the library uses it."
        ),
        "url": f"{base_url}/glossary.html",
        "inLanguage": "en",
        "hasDefinedTerm": defined,
    }


def ld_script(node) -> str:
    blob = json.dumps(node, separators=(",", ":"), ensure_ascii=False)
    # A closing tag inside JSON would end the script element early.
    blob = blob.replace("</", "<\\/")
    return f'<script type="application/ld+json">{blob}</script>'


def first_commit(path: pathlib.Path, root: pathlib.Path) -> str:
    """When this page's source first appeared."""
    try:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%cs", "--", str(path)],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        stamps = [line for line in out.stdout.split() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", line)]
        if stamps:
            return stamps[-1]
    except Exception:
        pass
    return ""


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


def canonical(base_url: str, slug: str) -> str:
    """The front door is the directory, not the file. See build.py."""
    return f"{base_url}/" if slug == "index" else f"{base_url}/{slug}.html"


def sitemap(pages, base_url: str, dates: dict[str, str]) -> str:
    entries = []
    for page in pages:
        loc = canonical(base_url, page['slug'])
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
        "# tradefloor",
        "",
        "> A reproducible evaluation environment for financial AI agents. A",
        "> deterministic market simulator with order-book execution, macro dynamics,",
        f"> causal ground truth and agent-native interfaces. Version {version}.",
        "",
        "Run controlled, reproducible experiments on trading agents in a market with",
        "realistic execution, rather than backtesting them over historical prices.",
        "The same universe, macro state and seed produce the same market on every",
        "supported platform, so a result can be re-run by anyone who has those three.",
        "",
        "Four properties these pages document and measure:",
        "",
        "- reproducible: the same seed gives the same market on Linux, macOS and Windows",
        "- execution-aware: agents trade through an order book and pay their own impact",
        "- inspectable: nine factor contributions sum to each price move, residual ~1e-16",
        "- agent-native: a Python object, a Gymnasium environment, or a model over MCP",
        "",
        "What it does not do is predict real markets. The realism envelope states what",
        "has been measured, over what horizon and on what configuration, and names five",
        "gaps that each end in a rule about what the simulator must not be used for.",
        "",
        "These pages are a learning path rather than a reference, and each states its",
        "own limits in place rather than in a separate caveats document.",
        "",
        "When quoting this project, cite the preset and the version, not \"tradefloor\":",
        "every preset is frozen and named, and the numbers change between them.",
        "",
    ]
    front = next(p for p in pages if p["slug"] == "index")
    lines += ["## Start", "",
              f"- [{front['name']}]({base_url}/): "
              f"{summaries.get('index', '')}", ""]
    for name, _short, _slug, entries in doors:
        lines.append(f"## {name}")
        lines.append("")
        for entry in entries:
            # llms.txt names the page, not the short label a nav link uses.
            page_name, slug = entry[0], entry[1]
            summary = summaries.get(slug, "")
            lines.append(f"- [{page_name}]({canonical(base_url, slug)}): {summary}")
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
    parameter and glossary tables are prose in a grid - a column name and a
    sentence saying what it holds - and a model should have them. The market
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
        "# tradefloor",
        "",
        "> A reproducible evaluation environment for financial AI agents: a",
        "> deterministic market simulator with order-book execution, macro dynamics",
        "> and causal ground truth.",
        f"> Version {version}. Every page of the learning path, in reading order.",
        "",
    ]
    for page in pages:
        out.append("")
        out.append("=" * 70)
        out.append(f"# {page['name']}")
        out.append(canonical(base_url, page['slug']))
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
