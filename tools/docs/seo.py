"""Search and answer-engine surfaces for the generated site.

Everything a crawler or a language model reads *about* a page that is not the
page itself: its description, its structured data, its line in the sitemap,
robots.txt, and the llms.txt pair.

This lives outside `build_site.py` for two reasons. That file is two thousand
lines of content correction and this is a different job. And every surface
here has to agree with every other one: the `<meta name=description>`, the
`description` in the JSON-LD, the Open Graph description and the one-line
summary in llms.txt are the same sentence, written once, here.

Nothing in this module invents a claim. Descriptions are hand-written from
what the page says; the realism dataset is generated from `pretium.envelope`,
which is the same module the certification tables come from, so the numbers a
crawler reads cannot drift from the numbers a reader reads.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


# --------------------------------------------------------------------------
# Descriptions
# --------------------------------------------------------------------------

#: One hand-written sentence per page, 120-165 characters.
#:
#: These replace a heuristic that took the first paragraph over sixty
#: characters and cut it at 155 with an ellipsis. Measured on the 0.3.0 build,
#: that produced thirteen descriptions ending mid-clause ("...the roster they
#: were measured on, and six known gaps. Each gap ends with a plain rule
#: about...") and eight under 120 characters, two of them under 95. A search
#: result is often the only sentence about this project anyone reads, and a
#: sentence that stops mid-clause reads as a broken page.
#:
#: They are also written for the question someone would actually type. The
#: word "pretium" is Latin for price and collides with a great deal else, so
#: the phrases that have to appear are the ones a person searches: market
#: simulator, limit order book, backtest, trading strategy, deterministic.
#:
#: `check_descriptions` asserts one exists per page and that its length is in
#: band, so a page added later cannot silently inherit a truncation.
DESCRIPTIONS = {
    "home": (
        "A deterministic market simulator with a real limit order book. Rust "
        "core, Python API, and fourteen realism statistics published with the "
        "limits they hold to."
    ),
    "simulate": (
        "Build a universe, run a market and score a trading strategy in a few "
        "lines of Python, and why a simulated market answers questions a "
        "historical backtest cannot."
    ),
    "scenarios": (
        "Force the economy down a path you choose: a hiking cycle, a VIX "
        "spike, a liquidity crisis. Run the same shock across many seeds and "
        "measure what it actually did."
    ),
    "llm-agent": (
        "A worked example wiring a language model into the agent protocol and "
        "scoring it against the reference strategies. Any provider returning "
        "structured output fits."
    ),
    "mcp": (
        "Drive the market simulator from a coding agent through MCP tool "
        "calls instead of Python: installing the server, registering it, and "
        "the market data its tools return."
    ),
    "wasm": (
        "pretium compiles to WebAssembly and the browser build is bit "
        "identical to the native one, so a permalink carrying seed and roster "
        "reproduces a market exactly."
    ),
    "edgar": (
        "Seed a universe from real SEC EDGAR filings. The fundamentals are "
        "real and usefully messy; every price path stays synthetic, and the "
        "filings snapshot is citable."
    ),
    "forking": (
        "Freeze a market and resume it later, or split one identical state "
        "into several futures. This is the machinery for measuring what a "
        "single decision was worth."
    ),
    "trust": (
        "What this market simulator gets right and what it gets wrong: "
        "fourteen statistics measured against real markets, the horizon they "
        "hold to, and the named gaps."
    ),
    "interrogate": (
        "A backtest runs one market and cannot tell you what your result "
        "rests on. Atlas runs hundreds and shows which model settings "
        "actually moved your number."
    ),
    "change": (
        "Model coefficients ship as named presets. Change any settable one at "
        "runtime; the model fingerprint stops a changed market ever passing "
        "as the shipped one."
    ),
    "atlas": (
        "Atlas runs your measurement across hundreds of slightly different "
        "markets and builds a table you can ask which model settings your "
        "result actually depends on."
    ),
    "internals": (
        "How the simulator works: the Rust and Python split, the three layers "
        "of the price model, the order book, the macro chain, and why "
        "determinism holds to the last bit."
    ),
    "releases": (
        "Release notes for pretium on PyPI and crates.io: what changed in "
        "each version, and what an era boundary means for reproducing a "
        "result you recorded earlier."
    ),
    "agents": (
        "Any object with an act method is an agent. The whole contract with "
        "the evaluation harness: what it hands you, what you return, and how "
        "it scores what you did."
    ),
    "factors": (
        "Every tick's change in mispricing splits into nine components that "
        "sum to it: the names the truth table uses and the values "
        "explain_price_move returns."
    ),
    "schemas": (
        "Every column a run gives you across the five output tables, plus the "
        "enumerations. All five read zero copy into polars, pandas, pyarrow "
        "and duckdb."
    ),
    "citing": (
        "A citation identifies the software; a methods section also has to "
        "identify the run. How to cite pretium, one specific simulation, and "
        "a published realism claim."
    ),
    "install": (
        "Install pretium from PyPI, choose an extra, and check bit identity "
        "on your own machine. What moves between versions and what is "
        "guaranteed to stay put."
    ),
    "api-core": (
        "API reference for Engine, Universe, Instrument, Macro, ModelParams "
        "and Scenario: building a market, running it, and reading the tables "
        "it gives back."
    ),
    "api-run": (
        "API reference for the evaluation harness: scoring agents, ranking "
        "across seeds, scenarios, transaction cost analysis and the "
        "reinforcement learning environment."
    ),
    "api-realism": (
        "API reference for pretium.facts, loss, envelope and atlas: measuring "
        "what the market reproduces, pricing what it does not, and mapping "
        "what it responds to."
    ),
    "api-params": (
        "Every runtime settable model coefficient and what it controls, "
        "across the shared factors, order flow, GJR-GARCH volatility, crisis "
        "behaviour and jumps."
    ),
    "api-presets": (
        "A preset is a complete, named, frozen set of model coefficients. The "
        "twelve shipped presets, why they exist, and how to choose between "
        "them for a run."
    ),
}

#: Titles that differ from the page's own h1, because the h1 is written for a
#: reader who is already here and the title is written for someone who is not.
#: "The MCP Server" tells a search engine nothing about what the server is
#: for; the words people actually type are "MCP server" and "market data".
TITLES = {
    "mcp": "MCP Server for Market Simulation",
    "trust": "Realism and Limits: What This Market Simulator Reproduces",
    "home": "pretium: repeatable market simulation",
}

DESC_MIN, DESC_MAX = 120, 165


def check_descriptions(pages: list[dict]) -> None:
    """Fail the build rather than ship a page with no description of its own.

    The old heuristic degraded silently: a new page simply got whatever its
    first long paragraph happened to be, cut mid-clause. This makes adding a
    page a two-line change instead of a one-line change, which is the point.
    """
    problems = []
    for page in pages:
        slug = page["slug"]
        desc = DESCRIPTIONS.get(slug)
        if not desc:
            problems.append(f"{slug}: no entry in seo.DESCRIPTIONS")
            continue
        if not DESC_MIN <= len(desc) <= DESC_MAX:
            problems.append(
                f"{slug}: description is {len(desc)} characters, outside "
                f"{DESC_MIN}-{DESC_MAX}"
            )
        if desc.rstrip().endswith("..."):
            problems.append(f"{slug}: description ends in an ellipsis")
    known = {p["slug"] for p in pages}
    for slug in DESCRIPTIONS:
        if slug not in known:
            problems.append(f"{slug}: described here but no such page")
    if problems:
        raise SystemExit(
            "seo.DESCRIPTIONS does not match the site:\n  "
            + "\n  ".join(problems)
        )


def description(slug: str) -> str:
    return DESCRIPTIONS[slug]


def title(slug: str, h1: str) -> str:
    return TITLES.get(slug, h1)


# --------------------------------------------------------------------------
# Entity identity
# --------------------------------------------------------------------------

#: Every other place this software is published. Without these a search engine
#: has no way to know that the PyPI package, the crate, the docs and this site
#: are one thing rather than four, and "pretium" is Latin for price, so the
#: name alone disambiguates nothing at all.
SAME_AS = [
    "https://pypi.org/project/pretium/",
    "https://crates.io/crates/pretium",
    "https://docs.rs/pretium",
    "https://github.com/simoncoombes/pretium",
]

ALTERNATE_NAME = "pretium market simulator"


def software_node(base_url: str, repo_url: str, version: str) -> dict:
    """The SoftwareApplication node, one definition used by every page."""
    return {
        "@type": "SoftwareApplication",
        "name": "pretium",
        "alternateName": ALTERNATE_NAME,
        "applicationCategory": "DeveloperApplication",
        "applicationSubCategory": "Market simulation",
        "operatingSystem": "Linux, macOS, Windows",
        "programmingLanguage": ["Rust", "Python"],
        "softwareVersion": version,
        "codeRepository": repo_url,
        "license": "https://opensource.org/licenses/MIT",
        "sameAs": SAME_AS,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return ""


#: What a page is generated FROM. Every static page comes out of the design
#: bundle through the builder, so these are the files whose history says when
#: the page's content actually moved.
PAGE_SOURCES = (
    "tools/docs/design-bundle.html",
    "tools/docs/build_site.py",
    "tools/docs/seo.py",
    "tools/docs/presets.py",
    "tools/docs/preset-panel.json",
)

#: The release-notes page also renders CHANGELOG.md, so it moves when that
#: moves even if nothing else did.
EXTRA_SOURCES = {"releases": ("CHANGELOG.md",)}


def page_dates(slug: str) -> tuple[str, str]:
    """(datePublished, dateModified) for one page, from git.

    `dateModified` comes from the page's SOURCES, never from the generated
    file. Reading it from the generated file does not converge: the build
    writes today's date, committing that makes today the file's last commit,
    and the next build writes a later date again. Every build-and-commit
    cycle then produces a spurious diff across all 24 pages and tells a
    crawler they all changed when none did, which is the exact failure the
    single shared lastmod this replaced was written to avoid. It was
    reintroduced here on 2026-08-27 and caught by rebuilding after a commit.

    `datePublished` is safe to read from the generated file, because a first
    commit does not move.

    Both are empty when git is unavailable, and the caller omits the fields.
    An absent date is valid; a churning one is worse than none.
    """
    generated = "docs/index.html" if slug == "home" else f"docs/{slug}.html"
    published = _git(["log", "--reverse", "--format=%cI", "--", generated])
    published = published.splitlines()[0] if published else ""

    sources = list(PAGE_SOURCES) + list(EXTRA_SOURCES.get(slug, ()))
    modified = _git(["log", "-1", "--format=%cI", "--"] + sources)

    return published or modified, modified


# --------------------------------------------------------------------------
# The realism envelope, as a citable dataset
# --------------------------------------------------------------------------

#: The published measurement is the most citable thing on this site and it had
#: no machine-readable identity at all: `docs/envelope.json` was linked from
#: four pages and described by none of them. This gives it a Dataset node and
#: an HTML home, so a citation can point at a page rather than at a raw file.
ENVELOPE_JSON = DOCS / "envelope.json"


def _envelope() -> dict:
    return json.loads(ENVELOPE_JSON.read_text(encoding="utf-8"))


def dataset_node(base_url: str, repo_url: str) -> dict:
    """Dataset JSON-LD for the realism envelope.

    `variableMeasured` carries the fourteen statistics with the band each was
    scored against, generated from the same file the site's tables come from,
    so a crawler cannot read a different number from a reader.
    """
    env = _envelope()
    stats = env["statistics"]
    measured = [
        {
            "@type": "PropertyValue",
            "name": name,
            "value": s["measured"],
            "minValue": s["band"][0],
            "maxValue": s["band"][1],
            "description": (
                f"Measured {s['measured']} against a real-market band of "
                f"{s['band'][0]} to {s['band'][1]}; "
                + ("inside the band." if s["in_band"] else "outside the band.")
            ),
        }
        for name, s in stats.items()
    ]
    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "pretium realism envelope",
        "alternateName": f"pretium {env['preset']} realism measurement",
        "description": (
            f"The {len(stats)} statistics pretium's default preset "
            f"{env['preset']} is measured on, each with the real-market band "
            f"it was scored against, the certified horizon of "
            f"{env['certified_horizon_days']} trading days, and the named "
            f"gaps stating what the simulator must not be used for."
        ),
        "url": f"{base_url}/envelope.html",
        "identifier": f"{base_url}/envelope.json",
        "license": "https://opensource.org/licenses/MIT",
        "isAccessibleForFree": True,
        "creator": {
            "@type": "Person",
            "name": "Simon Coombes",
            "url": "https://github.com/simoncoombes",
        },
        "isBasedOn": repo_url,
        "measurementTechnique": (
            "Thirty-seed median of each statistic over a 252-day run on the "
            "certified 40-name roster, scored against bands derived from "
            "published real-market measurements."
        ),
        "variableMeasured": measured,
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"{base_url}/envelope.json",
            }
        ],
    }


# --------------------------------------------------------------------------
# The markdown documentation
# --------------------------------------------------------------------------

#: `docs/*.md` is served literally by Pages: `.nojekyll` is present and there
#: is no `_config.yml`, so nothing renders them but nothing hides them either.
#: They were linked from nowhere, absent from the sitemap and invisible to any
#: crawler, which is the worst of both worlds: published, and unfindable.
#:
#: Listing them here is the cheap half of the fix. The expensive half is that
#: they are a SECOND independently maintained source -- the site comes from
#: the design bundle, these are hand-written, and neither derives from the
#: other. That is the mechanism behind the class-6 findings in the 0.3.0
#: audit, where `trust.html` and `realism-envelope.md` disagreed about the
#: band count at 504 days. Converging them is a structural change and is
#: recorded as the next one, not folded into a release.
def markdown_docs() -> list[str]:
    return sorted(p.name for p in DOCS.glob("*.md"))


# --------------------------------------------------------------------------
# sitemap.xml
# --------------------------------------------------------------------------

def build_sitemap(pages: list[dict], absolute, base_url: str) -> str:
    """The sitemap, with a real per-page lastmod.

    Every URL previously carried one shared date taken from the last commit
    touching `docs/`, which tells a crawler that all twenty four pages change
    together. They do not, and a crawler that learns that stops believing any
    of them.
    """
    entries = []
    for page in pages:
        pub, mod = page_dates(page["slug"])
        stamp = f"<lastmod>{mod[:10]}</lastmod>" if mod else ""
        pri = "1.0" if page["slug"] == "home" else "0.8"
        entries.append(
            f"<url><loc>{absolute(page['slug'])}</loc>{stamp}"
            f"<priority>{pri}</priority></url>"
        )

    env_mod = _git(["log", "-1", "--format=%cI", "--", "docs/envelope.json"])
    stamp = f"<lastmod>{env_mod[:10]}</lastmod>" if env_mod else ""
    entries.append(
        f"<url><loc>{base_url}/envelope.html</loc>{stamp}"
        f"<priority>0.9</priority></url>"
    )

    for name in markdown_docs():
        mod = _git(["log", "-1", "--format=%cI", "--", f"docs/{name}"])
        stamp = f"<lastmod>{mod[:10]}</lastmod>" if mod else ""
        entries.append(
            f"<url><loc>{base_url}/{name}</loc>{stamp}"
            f"<priority>0.5</priority></url>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(entries)
        + "</urlset>\n"
    )


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------

#: Named because a bare "User-agent: *" is ambiguous to several of these.
#: Google-Extended and Applebot-Extended in particular are opt-outs that do
#: not inherit from the wildcard in the way a reader would expect, so a
#: project that wants to be quotable has to say so by name.
AI_AGENTS = [
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "anthropic-ai",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Applebot-Extended",
    "Amazonbot",
    "Bytespider",
    "CCBot",
    "cohere-ai",
    "Meta-ExternalAgent",
    "DuckAssistBot",
    "MistralAI-User",
]


def build_robots(base_url: str) -> str:
    """Allow everything, by name, and point at both maps.

    This site is documentation for a library whose whole argument is that it
    publishes its own limits. Being quoted accurately by an answer engine is
    the entire point, so every one of these is allowed deliberately rather
    than by the wildcard's default.
    """
    blocks = ["User-agent: *", "Allow: /", ""]
    for agent in AI_AGENTS:
        blocks += [f"User-agent: {agent}", "Allow: /", ""]
    blocks += [
        f"Sitemap: {base_url}/sitemap.xml",
        f"# Summary for language models: {base_url}/llms.txt",
        f"# Full text for language models: {base_url}/llms-full.txt",
        "",
    ]
    return "\n".join(blocks)


# --------------------------------------------------------------------------
# llms.txt
# --------------------------------------------------------------------------

#: Grouping for llms.txt, in the order a reader should meet the site. The
#: bundle's own nav order is by section already, but llms.txt wants an
#: explicit hierarchy rather than a flat list, because the file is read whole.
LLMS_SECTIONS = [
    ("Start here", ["home", "install", "simulate"]),
    ("What it is measured to do", ["trust", "interrogate", "atlas"]),
    ("Guide", ["scenarios", "forking", "agents", "change"]),
    ("Connect it to something", ["mcp", "llm-agent", "wasm", "edgar"]),
    ("Reference", ["internals", "factors", "schemas", "citing", "releases"]),
    ("API", ["api-core", "api-run", "api-realism", "api-params",
             "api-presets"]),
]


def _llms_header(version: str, base_url: str) -> str:
    """The preamble both llms files share.

    Wrapped AFTER interpolation, not before. Written as a pre-wrapped literal
    the numbers were substituted into, the paragraph came out ragged -- "is
    measured on 14\nstatistics against real markets" -- because a two-digit
    count replaced a placeholder that had been sized for something else. A
    file whose whole purpose is to be read by a language model should not
    look like a botched mail merge.
    """
    import textwrap

    env = _envelope()
    body = (
        f"The reason to be careful with it is published rather than implied. "
        f"The default preset {env['preset']} is measured on "
        f"{len(env['statistics'])} statistics against real markets, certified "
        f"to a horizon of {env['certified_horizon_days']} trading days, and "
        f"it names {len(env['gaps'])} gaps, each ending in a plain rule about "
        f"what the simulator must not be used for. Those measurements are at "
        f"{base_url}/envelope.json and described at "
        f"{base_url}/envelope.html."
    )
    return f"""# pretium

> A deterministic market simulator with a real limit order book. A Rust core
> with a Python API: you give it a seed and a list of companies and it runs a
> market with prices, a live order book, fills and an economy that moves every
> day. Version {version}.

The reason to prefer it to a historical backtest is that history happened
once. Here you run the same question on many markets, force events through
them, and fork one state into several futures.

{textwrap.fill(body, width=78, break_long_words=False, break_on_hyphens=False)}

When quoting this project: the numbers change between versions and every
preset is frozen and named, so cite the preset and the version, not
"pretium".
"""


def build_llms(pages: list[dict], version: str, base_url: str,
               absolute) -> str:
    """llms.txt: the map, one line per page."""
    by_slug = {p["slug"]: p for p in pages}
    out = [_llms_header(version, base_url)]
    for heading, slugs in LLMS_SECTIONS:
        out.append(f"\n## {heading}\n")
        for slug in slugs:
            page = by_slug.get(slug)
            if page is None:
                continue
            out.append(
                f"- [{title(slug, page['h1'])}]({absolute(slug)}): "
                f"{DESCRIPTIONS[slug]}"
            )
    md = markdown_docs()
    if md:
        out.append(
            "\n## Long-form notes\n\n"
            "Hand-written companions to the pages above, served as markdown. "
            "They go deeper and are maintained separately from the generated "
            "site, so where one contradicts the other, the generated page is "
            "the one that is checked against the code.\n"
        )
        for name in md:
            out.append(f"- [{name[:-3]}]({base_url}/{name})")
    out.append("")
    return "\n".join(out)


def build_llms_full(pages: list[dict], version: str, base_url: str,
                    absolute, plain_text) -> str:
    """llms-full.txt: the map, then every page's text in one file.

    Generated in the same build from the same pages as the site, so the two
    cannot drift. A model that reads this has read the documentation, which
    is the only way to be quoted correctly about a library whose central
    claim is a set of numbers with bands around them.
    """
    by_slug = {p["slug"]: p for p in pages}
    out = [_llms_header(version, base_url), "\n---\n"]
    for heading, slugs in LLMS_SECTIONS:
        for slug in slugs:
            page = by_slug.get(slug)
            if page is None:
                continue
            out.append(f"\n# {title(slug, page['h1'])}\n")
            out.append(f"Source: {absolute(slug)}\n")
            out.append(plain_text(page["html"]))
            out.append("\n---\n")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Plain text, for llms-full.txt
# --------------------------------------------------------------------------

def plain_text(fragment: str) -> str:
    """A page's HTML reduced to something worth putting in a context window.

    Headings keep their level as markdown, code blocks keep their fences and
    their line breaks, tables become pipe rows. Everything else becomes a
    paragraph. The point is not fidelity to the design; it is that a model
    reading this can tell a heading from a number from a command.
    """
    import html as _html
    import re as _re

    out = fragment
    out = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", out, flags=_re.S)

    # Code blocks, before whitespace collapses inside them.
    def _pre(m):
        body = _re.sub(r"<[^>]+>", "", m.group(1))
        body = _html.unescape(body).strip("\n")
        return "\n```\n" + body + "\n```\n"

    out = _re.sub(r"<pre[^>]*>(.*?)</pre>", _pre, out, flags=_re.S)

    # Tables as pipe rows, so a band stays attached to its statistic.
    def _row(m):
        cells = _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), _re.S)
        clean = [
            _html.unescape(_re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", c))).strip()
            for c in cells
        ]
        return "\n| " + " | ".join(clean) + " |"

    out = _re.sub(r"<tr[^>]*>(.*?)</tr>", _row, out, flags=_re.S)

    for level in (1, 2, 3, 4):
        out = _re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, n=level: "\n\n" + "#" * n + " "
            + _re.sub(r"<[^>]+>", "", m.group(1)).strip() + "\n",
            out,
            flags=_re.S,
        )

    out = _re.sub(r"</(p|li|div|section|tr|table)>", "\n", out)
    out = _re.sub(r"<li[^>]*>", "- ", out)
    out = _re.sub(r"<[^>]+>", "", out)
    out = _html.unescape(out)
    # Collapse runs of blank lines, but keep paragraph separation.
    out = _re.sub(r"[ \t]+", " ", out)
    out = _re.sub(r" *\n *", "\n", out)
    out = _re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# --------------------------------------------------------------------------
# Frequently asked questions
# --------------------------------------------------------------------------

#: Question and answer pairs for the two pages where the question a reader
#: arrives with is the whole point: can I trust this, and how do I install it.
#:
#: These are RENDERED VISIBLY on the page as well as marked up. FAQPage
#: structured data whose questions do not appear on the page is a structured
#: data violation, not a shortcut, and a project whose argument is that it
#: publishes its own limits should not cheat at the markup.
#:
#: Every number here is interpolated from `docs/envelope.json`, the same file
#: the certification tables are built from, so an answer cannot drift from the
#: measurement it quotes.
def faq(slug: str) -> list[tuple[str, str]]:
    env = _envelope()
    n_stats = len(env["statistics"])
    n_gaps = len(env["gaps"])
    horizon = env["certified_horizon_days"]
    preset = env["preset"]
    in_band = sum(1 for s in env["statistics"].values() if s["in_band"])

    if slug == "trust":
        return [
            (
                "Is this market simulator realistic enough to test a trading "
                "strategy on?",
                f"That is measured rather than asserted. The default preset "
                f"{preset} reproduces {in_band} of {n_stats} statistics of "
                f"real equity markets inside published bands over {horizon} "
                f"trading days, on the median of thirty seeds. Whether it is "
                f"realistic enough depends on your question, which is why "
                f"each of the {n_gaps} named gaps ends in a plain rule about "
                f"what it rules out.",
            ),
            (
                "How long do the realism results hold for?",
                f"The certified horizon is {horizon} trading days, which is "
                f"one trading year. That is the run length every number on "
                f"this page was measured at, and a longer run is outside what "
                f"has been certified even where it has been measured.",
            ),
            (
                "What is this market simulator measured to get wrong?",
                "The gaps are named, not hidden: "
                + ", ".join(g["id"] if isinstance(g, dict) else str(g)
                            for g in env["gaps"])
                + ". Each one states what it forbids you to conclude, and "
                "each is on this page with the measurement behind it.",
            ),
            (
                "Can I reproduce a simulated result exactly?",
                "Yes, given the seed, the roster and the preset name. Every "
                "preset is frozen and named, so a result recorded under one "
                "replays bit for bit under that name even after the default "
                "moves. A result recorded without naming a preset will not.",
            ),
        ]

    if slug == "install":
        return [
            (
                "How do I install pretium?",
                "pip install pretium. The core package has no dependencies at "
                "all. The extras are opt-in: [rl] adds numpy and gymnasium, "
                "[arrow] adds pyarrow, [mcp] adds the MCP server, and "
                "[claude] adds what the Claude agent example needs.",
            ),
            (
                "Does it need numpy or pandas?",
                "No. The core package depends on nothing. Runs come back as "
                "Arrow tables that read zero copy into polars, pandas, "
                "pyarrow or duckdb, and the package requires none of them: "
                "you use whichever you already have.",
            ),
            (
                "How do I check I am getting bit-identical results?",
                "The repository ships a known-answer baseline: a fixed seed "
                "and roster with a recorded SHA-256 of the resulting "
                "simulation. Re-running it on your machine and comparing the "
                "digest is the check, and it is what the determinism workflow "
                "runs on every supported platform.",
            ),
            (
                "Will upgrading change my results?",
                "Only at an era boundary, and the release notes say when one "
                "happens. Pin the preset by name and your numbers survive the "
                f"upgrade; the current default is {preset}.",
            ),
        ]

    return []


def faq_html(slug: str) -> str:
    """The visible half of the FAQ, appended to the page's own content."""
    pairs = faq(slug)
    if not pairs:
        return ""
    import html as _html

    items = "".join(
        f'<h3 id="faq-{i}">{_html.escape(q)}</h3>\n<p>{_html.escape(a)}</p>'
        for i, (q, a) in enumerate(pairs, 1)
    )
    return f'\n<section id="faq">\n<h2>Common questions</h2>\n{items}\n</section>\n'


def faq_node(slug: str) -> dict | None:
    """The marked-up half, which must match the visible half exactly."""
    pairs = faq(slug)
    if not pairs:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in pairs
        ],
    }


# --------------------------------------------------------------------------
# envelope.html
# --------------------------------------------------------------------------

def envelope_page(base_url: str, repo_url: str, version: str) -> str:
    """A citable HTML home for the realism measurement.

    `docs/envelope.json` was linked from four pages and described by none of
    them, so a citation had nowhere to point but a raw file. This is the
    landing page for the Dataset node: the same numbers, rendered, with the
    provenance and the licence beside them.

    Built entirely from the JSON, so it cannot state a number the shipped
    measurement does not.
    """
    import html as _html

    env = _envelope()
    rows = "".join(
        f"<tr><td id=\"stat-{_html.escape(name)}\"><code>{_html.escape(name)}</code></td>"
        f"<td>{s['measured']}</td>"
        f"<td>{s['band'][0]} to {s['band'][1]}</td>"
        f"<td>{'in band' if s['in_band'] else 'OUT OF BAND'}</td></tr>"
        for name, s in env["statistics"].items()
    )
    gaps = "".join(
        f"<li id=\"gap-{_html.escape(str(g['id'] if isinstance(g, dict) else g))}\">"
        f"<code>{_html.escape(str(g['id'] if isinstance(g, dict) else g))}</code>"
        + (f": {_html.escape(str(g.get('summary', '')))}" if isinstance(g, dict) else "")
        + "</li>"
        for g in env["gaps"]
    )
    ld = json.dumps(dataset_node(base_url, repo_url), separators=(",", ":"))
    desc = (
        f"The {len(env['statistics'])} statistics pretium {version} is "
        f"measured on, each with the real-market band it was scored against "
        f"and the certified horizon of {env['certified_horizon_days']} days."
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Realism Envelope Dataset · pretium docs</title>
<meta name="description" content="{_html.escape(desc, quote=True)}">
<link rel="canonical" href="{base_url}/envelope.html">
<script type="application/ld+json">{ld}</script>
<style>
body{{max-width:900px;margin:0 auto;padding:32px 24px;
  font:15px/1.65 system-ui,-apple-system,sans-serif;color:#1a1a1a;
  background:#fbfaf7}}
table{{border-collapse:collapse;width:100%;margin:18px 0;font-size:13.5px}}
th,td{{border-bottom:1px solid #e3e0d8;padding:6px 10px;text-align:left}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}}
a{{color:#12507a}}
@media(prefers-color-scheme:dark){{body{{background:#14150f;color:#eae7dd}}
  th,td{{border-color:#333}} a{{color:#7fb6dd}}}}
</style>
</head>
<body>
<h1>Realism envelope</h1>
<p>{_html.escape(desc)} Measured on preset
<code>{_html.escape(env['preset'])}</code>, thirty-seed median, on the
certified roster. The machine-readable form is
<a href="{base_url}/envelope.json">envelope.json</a>, and the prose that
explains each number is on <a href="{base_url}/trust.html">Realism and
limits</a>.</p>

<h2>The statistics</h2>
<table><thead><tr><th>statistic</th><th>measured</th><th>real-market band</th>
<th>result</th></tr></thead><tbody>{rows}</tbody></table>

<h2>The gaps</h2>
<p>What the measurement does not cover, named rather than left implicit. Each
one carries a rule about what it forbids you to conclude.</p>
<ul>{gaps}</ul>

<h2>Citing this</h2>
<p>Cite the preset and the version, never the project alone: the numbers above
are properties of <code>{_html.escape(env['preset'])}</code> as shipped in
pretium {version}, and every preset is frozen under its own name. Source and
licence at <a href="{repo_url}">{repo_url}</a>.</p>
</body>
</html>
"""
