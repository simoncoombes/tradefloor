"""Build the tradefloor learning-path documentation site.

The design handoff ships twenty-five pages in a prototyping dialect. This
script turns them into a static site: one HTML file per page that reads
correctly with JavaScript switched off, one shared stylesheet, one shared
runtime, and two generated data modules.

    python tools/docs/learn/build.py [--out docs] [--check]

What it owns, and therefore overwrites, is everything under the output
directory. What it reads is the handoff in `handoff/` plus the repository's
own measurement files - never a hand-maintained copy of them.

Three things the handoff asked for happen here rather than being deferred:

* the page data is generated from `rust/goldens/*.json` and the measurement
  files at build time, so a golden that changes either updates the charts or
  fails the build (see `data.py`);
* the search index is extracted from the rendered pages, so it finds
  passages rather than the hand-written page keywords it shipped with;
* the four pages added after the handoff's review are wired into the door
  menus and the index like every other page.

`--check` builds into a temporary directory and diffs against the committed
output, so CI can fail on a stale site without writing to the tree.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import data
import seo
import shell

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HANDOFF = HERE / "handoff"
SITE = HERE / "site"

#: Where a build is going, and what that implies.
#:
#: There are two: the published site, and a staging copy at
#: `tradefloor.dev-dev` for looking at a change before it is
#: the documentation. Everything that differs between them is here, so
#: nothing about the staging site is a hand edit that could be forgotten on
#: the way to production.
#:
#: `indexable` is the one that matters. A staging copy of a documentation
#: site competing with the real one in search results is worse than having
#: no staging copy: the same words at two addresses, and no way to tell a
#: search engine which is the real one after the fact.
class Target:
    def __init__(self, base_url, indexable, analytics, sitemap):
        self.base_url = base_url
        self.indexable = indexable
        self.analytics = analytics
        self.sitemap = sitemap


TARGETS = {
    "live": Target(
        base_url="https://tradefloor.dev",
        indexable=True, analytics=True, sitemap=True),
    "dev": Target(
        base_url="https://tradefloor.dev-dev",
        indexable=False, analytics=False, sitemap=False),
}

#: Set by `main()` before anything is written. Module-level because the
#: builder was written against one destination and threading a target
#: through every function would be a larger change than the difference
#: between them warrants.
TARGET = TARGETS["live"]
BASE_URL = TARGET.base_url

#: True because the live site is the one served at the root a crawler asks
#: for, which is what makes a `robots.txt` worth writing. The staging site
#: is served at its own root too, and writes a robots.txt that says the
#: opposite.
AT_SITE_ROOT = True

#: Kept out of the index, and out of the crawl.
#:
#: The meta tag is the part that works: a page a crawler is forbidden to
#: fetch can still be indexed as a bare URL from a link elsewhere, whereas
#: one it fetches and finds `noindex` on is dropped. The robots.txt is there
#: to stop well-behaved crawlers spending anything on the site at all.
#: Carries its own leading newline, so that a live build - where it is the
#: empty string - is byte-identical to one made before staging existed. A
#: blank line in the head is harmless and a spurious diff across 25 pages is
#: not: it would make the staleness check fail for a change to nothing.
NOINDEX = ('\n<meta name="robots" content="noindex, nofollow">'
           '\n<meta name="googlebot" content="noindex, nofollow">')

STAGING_ROBOTS = """User-agent: *
Disallow: /

# This is a staging copy of https://tradefloor.dev.
# Every page also carries <meta name="robots" content="noindex, nofollow">,
# which is the part that keeps it out of an index; this file is here to
# keep a crawler from spending anything on it in the first place.
"""


def project_version() -> str:
    """Read from pyproject, so the site cannot claim a version the package
    does not have."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit("no version in pyproject.toml")
    return m.group(1)


#: The GA4 measurement ID, which `tools/docs/build_site.py` also carries. It
#: is duplicated rather than imported because importing that module runs it,
#: and `check_analytics_id()` asserts the two agree so the duplicate cannot
#: quietly diverge.
GA_MEASUREMENT_ID = "G-1LBM3239ZF"


def check_analytics_id() -> None:
    other = (ROOT / "tools" / "docs" / "build_site.py").read_text(encoding="utf-8")
    m = re.search(r'GA_MEASUREMENT_ID\s*=\s*"([^"]*)"', other)
    if m and m.group(1) != GA_MEASUREMENT_ID:
        sys.exit(f"analytics ID disagrees with build_site.py: "
                 f"{GA_MEASUREMENT_ID} here, {m.group(1)} there")


ANALYTICS = """<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}
gtag('js',new Date());gtag('config','{gid}');</script>"""

#: The five doors, in reading order, and the pages behind each. The handoff
#: left four pages linked only by their own prev/next chain; they are placed
#: here, which is what puts them in the menus, the footer index and search.
#: The five doors, in reading order: full name, the short label the menus
#: use, a slug, and the pages behind it. The handoff left four pages linked
#: only by their own prev/next chain; placing them here is what puts them in
#: the menus, the footer index and the search results.
#:
#: The MCP server sits under "Use it". The handoff's own door table puts it
#: there and its front door calls MCP one of the three ways to drive the
#: simulator; the design files' menus filed it under API, which is the one
#: place the two disagree. Driving it is a use, so it is a use.
DOORS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    ("Start here", "Start", "start", [
        ("Install", "install"),
        ("Core concepts", "core-concepts"),
        ("The two loops", "two-loops"),
        ("Running a market", "running-a-market"),
    ]),
    ("Use it", "Use it", "use", [
        ("Agents", "agents"),
        ("Scenarios", "scenarios"),
        ("Execution cost", "execution-cost"),
        ("Checkpoints and forking", "checkpoints"),
        ("Real companies from EDGAR", "edgar", "EDGAR companies"),
        ("RL environment", "rl-environment"),
        ("The MCP server", "mcp"),
    ]),
    ("Trust it", "Trust it", "trust", [
        ("Realism envelope", "realism-envelope"),
        ("The metrics", "metrics"),
        ("Principles", "principles"),
        ("Citing a run", "citing"),
    ]),
    ("Reference", "Reference", "reference", [
        ("Glossary", "glossary"),
        ("The nine factors", "factors"),
        ("Conventions", "conventions"),
        ("Schemas", "schemas"),
        ("Presets", "presets"),
        ("Release notes", "release-notes"),
    ]),
    ("API", "API", "api", [
        ("Core types", "core-types"),
        ("Evaluate", "evaluate"),
        ("Parameters", "parameters"),
    ]),
]

FRONT = ("Learn tradefloor", "index")

#: Where each page of the previous site goes.
#:
#: The two sites are not a restyling of each other: the learning path was
#: rebuilt around what a reader does, and the review that produced it cut
#: four pages outright. Every old URL still gets an answer, because a link
#: someone saved or a search result someone follows should not end in a 404
#: on the day the docs improve.
#:
#: An old slug that is also a new slug is absent from this table: the new
#: page simply takes the URL. Anything else that used to exist and is not
#: here fails the build, so a page cannot be dropped by forgetting it.
REDIRECTS = {
    "api-core": "core-types",
    "api-params": "parameters",
    "api-presets": "presets",
    "api-realism": "realism-envelope",
    "api-run": "running-a-market",
    "change": "release-notes",
    "envelope": "realism-envelope",
    "forking": "checkpoints",
    "llm-agent": "mcp",
    "releases": "release-notes",
    "simulate": "running-a-market",
    "trust": "realism-envelope",
    # Cut in the design review with nothing to take their place. They go to
    # the front door rather than to a page that merely sounds similar, and
    # say so, because guessing what the reader wanted is worse than telling
    # them the page is gone.
    "atlas": "index",
    "internals": "index",
    "interrogate": "index",
    "wasm": "index",
}

#: The four above, kept separate so the stub can say something true about
#: why the page is not there any more.
RETIRED = {"atlas", "internals", "interrogate", "wasm"}

#: Written by `tools/docs/learn/make_icons.py` from the design's own mark.
#: The build checks they are present rather than generating them, because
#: tracing the mark needs a browser and a build should not need one.
ICONS = ["favicon.svg", "favicon.ico", "apple-touch-icon.png",
         "icon-512.png", "og-card.png", "site.webmanifest"]

ASSETS = [
    "mark-tradefloor.png", "mark-tradefloor-dark.png",
    "logo-python.png", "logo-python-dark.png",
    "logo-rust.png", "logo-rust-dark.png",
]

#: The front door is set a shade tighter than the rest of the site: 1.6 line
#: height against 1.65, a 0.85rem paragraph margin against 0.9rem, 1.12
#: heading leading against 1.15. That is deliberate - it is a long page and
#: it is the only one a reader meets cold - so it survives the stylesheet
#: merge as a body-scoped override rather than being flattened away.
FRONT_OVERRIDES = """
body.pt-front{line-height:1.6}
body.pt-front p{margin:0 0 0.85rem}
body.pt-front h1,body.pt-front h2,body.pt-front h3{line-height:1.12}
"""


REDIRECT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Moved - tradefloor</title>
<link rel="canonical" href="{base}/{target}.html">
<meta http-equiv="refresh" content="0; url={target}.html">
<meta name="robots" content="noindex, follow">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<style>
body{{margin:0;background:#FAFBFA;color:#3C4A47;
  font:400 1rem/1.65 'Public Sans',system-ui,-apple-system,sans-serif}}
main{{max-width:34rem;margin:0 auto;padding:6rem 1.5rem}}
h1{{font:600 1.5rem/1.2 'Source Serif 4',ui-serif,Georgia,serif;color:#101A18;margin:0 0 0.6rem}}
a{{color:#0E6B65}}
@media (prefers-color-scheme: dark){{
  body{{background:#0D1312;color:#B2C0BD}} h1{{color:#E5ECEA}} a{{color:#5FC9BE}}
}}
</style>
</head>
<body>
<main>
<h1>{heading}</h1>
<p>{sentence} You are being sent to <a href="{target}.html">{title}</a>.</p>
</main>
</body>
</html>
"""


def write_redirects(out_dir: pathlib.Path, all_pages) -> None:
    """Give every URL the old site published an answer.

    A redirect is a page, not a server rule: GitHub Pages serves static
    files and has no redirect table, so the only thing that can forward a
    reader is a document that says so. Each carries a canonical link to its
    destination, so a search engine consolidates the two rather than
    indexing both, and `noindex` so the stub itself never becomes a result.
    """
    titles = {p["slug"]: p["name"] for p in all_pages}
    new_slugs = set(titles)

    for old_slug, target in sorted(REDIRECTS.items()):
        if target not in new_slugs:
            sys.exit(f"redirect {old_slug} points at {target}, which is not a page")
        if old_slug in new_slugs:
            sys.exit(f"redirect {old_slug} would overwrite the page of the same name")
        retired = old_slug in RETIRED
        doc = REDIRECT_PAGE.format(
            base=BASE_URL,
            target=target,
            title=esc(titles[target]),
            heading="This page was retired" if retired else "This page has moved",
            sentence=(
                f"<code>{esc(old_slug)}.html</code> was part of the previous "
                "documentation and has no direct replacement."
                if retired else
                f"<code>{esc(old_slug)}.html</code> is now part of another page."
            ),
        )
        (out_dir / f"{old_slug}.html").write_text(doc, encoding="utf-8")

    print(f"  redirects: {len(REDIRECTS)} old URLs answered")


def check_nothing_orphaned(out_dir: pathlib.Path, all_pages) -> None:
    """Fail if a page the old site published has no answer at all.

    Run against whatever `docs/` holds after the build, so a file left over
    from the previous site is either a page now, a redirect now, or a build
    failure - never a stale document quietly still being served.
    """
    expected = {p["slug"] for p in all_pages} | set(REDIRECTS)
    stale = sorted(f.stem for f in out_dir.glob("*.html") if f.stem not in expected)
    if stale:
        sys.exit("these pages are neither built nor redirected - add them to "
                 "REDIRECTS or delete them:\n  " + "\n  ".join(stale))


def nav_label(entry) -> str:
    """What a link to this page says.

    A page title can be a sentence; a link in a five-column index has about
    fifteen characters before it wraps or pushes the column out. Where the
    two want different words the entry carries both, and the title is still
    what the page itself is called.
    """
    return entry[2] if len(entry) > 2 else entry[0]


def door_list() -> list[dict]:
    """The one door list, in the shape the front door's cards expect.

    Handed to every component as a prop so that no page keeps its own copy
    of which pages exist. A page added to `DOORS` above appears in the front
    door's "Start here" cards as well as in the menus and the index.
    """
    return [
        {"title": short,
         "links": [{"label": nav_label(e), "href": f"{e[1]}.html"} for e in entries]}
        for _name, short, _slug, entries in DOORS
    ]


def pages() -> list[dict]:
    """Every page, in reading order, with its door and its neighbours."""
    out = [{"name": FRONT[0], "slug": FRONT[1], "door": None, "door_slug": None}]
    for door, _short, door_slug, entries in DOORS:
        for entry in entries:
            out.append({"name": entry[0], "slug": entry[1], "nav": nav_label(entry),
                        "door": door, "door_slug": door_slug})
    for i, p in enumerate(out):
        p["src"] = f"{p['name']}.dc.html"
        p["prev"] = out[i - 1] if i else None
        p["next"] = out[i + 1] if i + 1 < len(out) else None
    return out


# ------------------------------------------------------------------ stylesheet

def split_rules(css: str) -> list[str]:
    """Top-level blocks of a stylesheet, in source order."""
    out, depth, buf = [], 0, ""
    for ch in css:
        buf += ch
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(buf.strip())
                buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def merge_stylesheet(blocks: list[str]) -> str:
    """Union the per-page stylesheets into one.

    The pages carry the same base sheet with per-page additions: the colour
    tokens a page actually uses, mostly. Merging by selector rather than
    taking the first sheet is what lets every page share one file - and it
    asserts on a genuine conflict, because two different values for one
    token is a design decision that has to be made deliberately, not
    resolved by file order.
    """
    order: list[str] = []
    decls: dict[str, dict[str, str]] = {}
    raw: dict[str, str] = {}
    conflicts: list[str] = []

    for block in blocks:
        for rule in split_rules(block):
            sel, body = rule.split("{", 1)
            sel = sel.strip()
            body = body.rstrip().rstrip("}")
            if sel.startswith("@"):
                # At-rules nest; keep the first and trust they agree, but say
                # so if they do not.
                if sel in raw and raw[sel] != body:
                    conflicts.append(f"{sel} (at-rule bodies differ)")
                raw.setdefault(sel, body)
                if sel not in order:
                    order.append(sel)
                continue
            if sel not in decls:
                decls[sel] = {}
                order.append(sel)
            for d in body.split(";"):
                d = d.strip()
                if not d:
                    continue
                prop, _, val = d.partition(":")
                prop, val = prop.strip(), val.strip()
                prev = decls[sel].get(prop)
                if prev is not None and prev != val and not _tighter(sel, prop):
                    conflicts.append(f"{sel} {{ {prop}: {prev} }} vs {{ {prop}: {val} }}")
                decls[sel].setdefault(prop, val)

    if conflicts:
        sys.exit("stylesheet merge found real conflicts:\n  " + "\n  ".join(conflicts))

    out = []
    for sel in order:
        if sel in raw:
            out.append(f"{sel}{{{raw[sel]}}}")
        else:
            body = ";".join(f"{p}:{v}" for p, v in decls[sel].items())
            out.append(f"{sel}{{{body}}}")
    return "\n".join(out)


#: Properties the front door deliberately tightens. They are merged
#: first-wins and re-stated in FRONT_OVERRIDES rather than reported as a
#: conflict, because the difference is intended.
_TIGHTER = {
    ("body", "line-height"), ("p", "margin"),
    ("h1,h2,h3", "line-height"),
    ("body", "font-family"),
}


def _tighter(sel: str, prop: str) -> bool:
    return (sel, prop) in _TIGHTER


# ---------------------------------------------------------------- page assembly

#: Bindings the search overlay used to supply. If one survives in a page's
#: template the shell and the page are both trying to own search, and the
#: page will render an empty control rather than fail loudly - so fail here.
#: Corrections applied to a page's component on the way out.
#:
#: Each is asserted, so a redrawn design that no longer contains the text
#: being replaced fails the build rather than silently dropping the fix.
#: That is the same discipline `tools/docs/build_site.py` uses for prose,
#: and for the same reason: a fix that can go missing quietly will.
SCRIPT_FIXES: dict[str, list[tuple[str, str]]] = {
    # The front door reveals its verdict a word at a time. Its three other
    # players check `prefers-reduced-motion` and stay still; this one was
    # written later and does not, so a reader who has asked the platform to
    # stop moving things gets text that types itself anyway.
    "index": [(
        """    let n = 0;
    this.setState({ vSnap: next, vStream: '' });""",
        """    let n = 0;
    const still = typeof window !== 'undefined' && window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (still) { this.setState({ vSnap: next, vStream: null }); return; }
    this.setState({ vSnap: next, vStream: '' });""",
    )],
}

SEARCH_BINDING = re.compile(r"\{\{\s*(search[A-Za-z]*)\s*\}\}")

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=Source+Serif+4:opsz,wght@8..60,400;8..60,600"
         "&family=Public+Sans:wght@400;500;600"
         "&family=Spline+Sans+Mono:wght@400;500&display=swap")


def rewrite_links(text: str, slug_of: dict[str, str]) -> str:
    """`./Core concepts.dc.html` -> `core-concepts.html`.

    Applied to the prerendered markup and to the template that ships beside
    it, from the same table, so the static page and the upgraded one cannot
    point at different places.
    """
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in slug_of:
            sys.exit(f"link to an unknown page: {name}.dc.html")
        return f"{slug_of[name]}.html"

    return re.sub(r"\./([^\"'<>]+?)\.dc\.html", sub, text)


def registered_presets() -> tuple[set[str], str]:
    """The presets the crate actually ships, and which one is the default.

    Read from `rust/src/params.rs` rather than kept in a list here, because
    a list here is a second source that can fall behind the first.
    """
    src = (ROOT / "rust" / "src" / "params.rs").read_text(encoding="utf-8")
    default = re.search(r'DEFAULT_PRESET_NAME:\s*&str\s*=\s*"([^"]+)"', src)
    if not default:
        sys.exit("cannot find DEFAULT_PRESET_NAME in rust/src/params.rs")
    names = set(re.findall(r'"(pt-v\d+)"\s*=>\s*Some\(', src))
    if not names:
        sys.exit("cannot find the preset table in rust/src/params.rs")
    return names, default.group(1)


def check_release_notes(pages_html: dict[str, str]) -> None:
    """Fail if the release-notes page has no section for the shipped version.

    The nav badge and the JSON-LD read `pyproject.toml`, so they move with a
    release on their own. The release-notes page does not: it is written by
    hand, one section per version. At 0.4.1 the badge said 0.4.1 and the page
    stopped at 0.4.0, because the changelog entry was written and the page
    section was not, and nothing compared them.

    Checked against the built page rather than the source, because that is
    what a reader gets.
    """
    version = project_version()
    page = pages_html.get("release-notes")
    if page is None:
        sys.exit("no release-notes page was built")
    if not re.search(rf">{re.escape(version)}</h2>", page):
        found = re.findall(r">(\d+\.\d+\.\d+)</h2>", page)
        sys.exit(
            f"release-notes.html has no section for {version}, the version in "
            f"pyproject.toml.\n  It carries: {', '.join(dict.fromkeys(found)) or 'none'}"
            f"\n  Add the section to tools/docs/learn/handoff/Release notes.dc.html."
        )
    print(f"  release notes: newest section is {version}")


def check_presets(pages_html: dict[str, str]) -> None:
    """Fail if the site names a preset the package does not ship.

    The site is the front door for someone installing from PyPI or
    crates.io. A page that mentions a preset registered only in the working
    tree tells that reader to type a name the released package will reject.
    This is the manual `grep pt-vNN` step, made part of the build so it
    cannot be the step that gets skipped.
    """
    shipped, default = registered_presets()
    problems = []
    for slug, html in pages_html.items():
        for named in sorted(set(re.findall(r"pt-v\d+", html))):
            if named not in shipped:
                problems.append(f"{slug}.html names {named}, which the crate does not ship")
    # A written count goes stale the other way: the crate gains a preset and
    # the sentence that counts them does not. 0.4.0 shipped "Twelve presets
    # ship, pt-v1 through pt-v14" on the install page, and the bar chart
    # beside it drew twelve bars, because both were hand-written and only the
    # names were updated. Reported per page so the failure names the sentence.
    words = {1:"one",2:"two",3:"three",4:"four",5:"five",6:"six",7:"seven",
             8:"eight",9:"nine",10:"ten",11:"eleven",12:"twelve",
             13:"thirteen",14:"fourteen",15:"fifteen",16:"sixteen",
             17:"seventeen",18:"eighteen",19:"nineteen",20:"twenty"}
    expected = words.get(len(shipped), str(len(shipped)))
    counted = re.compile(
        r"\b([A-Za-z]+|\d+)\s+(?:presets ship|named coefficient sets)", re.I)
    for slug, html in pages_html.items():
        for said in counted.findall(html):
            if said.lower() != expected:
                problems.append(
                    f"{slug}.html says {said!r} presets ship; the crate ships "
                    f"{len(shipped)} ({expected})")

    if problems:
        sys.exit("the site describes presets that are not released:\n  " + "\n  ".join(problems))
    print(f"  presets: {len(shipped)} shipped, default {default}")


def dead_door_table(script: str, slugs: set[str]) -> bool:
    """Does this component still carry its own copy of the door index?

    Eight pages build the "All pages" section from a list of page names and
    links held in the component. The builder generates that section now, so
    the list is dead - but it is dead data that still asserts which pages
    exist, and dead data that disagrees with the site is how a stale menu
    finds its way back. Its links are rewritten so nothing can 404, and the
    build reports the pages that still need the list deleted at source.
    """
    found = set(re.findall(r"['\"](?:\./)?([a-z0-9-]+)\.html['\"]", script))
    return len(found & slugs) >= 5


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="tradefloor">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{base}/og-card.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="tradefloor - a market you can run a strategy against">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{base}/og-card.png">
<meta name="author" content="Simon Coombes">{robots_meta}
<meta name="theme-color" content="#FAFBFA" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0D1312" media="(prefers-color-scheme: dark)">
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="alternate icon" href="favicon.ico" sizes="16x16 32x32 48x48">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="site.webmanifest">
<!-- The measurements every claim on this site rests on, as data. -->
<link rel="alternate" type="application/json" href="envelope.json"
      title="the realism envelope">
<!-- The same pages as plain text, for a reader that is a model. -->
<link rel="alternate" type="text/plain" href="llms.txt" title="summary for language models">
<link rel="alternate" type="text/plain" href="llms-full.txt" title="full text for language models">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
<link rel="stylesheet" href="{fonts}">
<link rel="stylesheet" href="learn.css">
{structured_data}
{theme_script}
{analytics}
</head>
<body class="{body_class}">
<div style="min-height:100vh;padding:0 0 5rem">
{overlay}
{masthead}
<div id="pt-root">{html}</div>
<div class="pt-foot">
{door_index}
{prev_next}
</div>
</div>
<!-- The client-side copy of this page's template.

     It is a raw-text script block, not a <template> element, and that is
     not a style choice. An HTML parser applies the table content model to
     anything it parses, and `<sc-for>` is not in it - so a loop written
     inside a table is foster-parented out of the table and its rows are
     left behind ungrouped. The template survives a <script> intact because
     a script's contents are never parsed as markup. -->
<script id="pt-template" type="text/x-pt-template">{template}</script>
<script src="pt-data.js"></script>
<script src="learn-runtime.js"></script>
<script src="learn-shell.js" defer></script>
<script>
/* The handoff's components are written as `class Component extends DCLogic`,
   so the base class has to be a global by the time the class statement is
   evaluated. */
var DCLogic = PTLearn.DCLogic;
{script}
;(function(){{
  /* The search overlay belongs to the shell now, so the per-page
     `searchVals` has no consumer: its markup was stripped from this
     page's template. Left in place it would still run on every render,
     against an index whose shape it no longer knows. */
  if (Component.prototype.searchVals) Component.prototype.searchVals = function () {{ return {{}}; }};
  /* The theme belongs to the shell too. Left alone, every page would write
     the reader's *system* preference into localStorage on first load, which
     turns "follow my system" into a choice they never made. */
  if (Component.prototype.applyTheme) Component.prototype.applyTheme = function () {{}};
  var host = document.getElementById('pt-root');
  var ast = PTLearn.parse(document.getElementById('pt-template').textContent);
  PTLearn.mount(host, Component, ast, {props});
}})();
</script>
</body>
</html>
"""

#: Read the stored theme before the first paint. Without it the page renders
#: light and then flips, which is worse than either theme on its own. Inline
#: and tiny for the same reason: a separate file would arrive after the
#: paint it exists to prevent.
THEME_SCRIPT = """<script>
(function(){try{var s=localStorage.getItem('pt-learn-theme');
if(s)document.documentElement.setAttribute('data-theme',s==='dark'?'dark':'light');}catch(e){}})();
</script>"""

#: Two rules that replace a piece of the design's JavaScript. The mark and
#: the language logos shipped as image pairs whose visibility was bound to a
#: `dark` flag held in each page's component, so the wrong one is on screen
#: until the script runs. CSS gets it right on the first paint and keeps
#: working when the script is blocked.
THEME_CSS = """
.pt-dark{display:none !important}
html[data-theme="dark"] .pt-dark{display:revert !important}
html[data-theme="dark"] .pt-light{display:none !important}
@media (prefers-color-scheme: dark){
  html:not([data-theme="light"]) .pt-dark{display:revert !important}
  html:not([data-theme="light"]) .pt-light{display:none !important}
}
#pt-search-open[hidden]{display:none}
.pt-scroll{overflow-x:auto;max-width:100%}
"""

#: What the handoff left open: "responsive behaviour below 620px is
#: specified and coded but unverified on a device". Checked now, with
#: `verify.cjs --responsive`, and it was not right.
#:
#: Each door's panel is absolutely positioned against its own <details>, so
#: the panels belonging to the menus on the right of the bar start far
#: enough across that a 14rem panel runs past the edge - and it counts
#: toward the page width even closed, so every page scrolled sideways by
#: 16px at 360px. Taking the positioning off the <details> at that size
#: lets the panel resolve against the header instead and span it, which is
#: the behaviour the design intends and the only one that fits.
NARROW_CSS = """
@media (max-width: 620px){
  details.ptmenu{position:static !important}
  .ptmenu > div{left:1.1rem !important;right:1.1rem !important;min-width:0 !important}
  .pt-door-name{white-space:normal !important}
  /* The control rows above the charts are flex rows that do not wrap. At
     a phone's width a row of a label, a number and a slider is wider than
     the screen, and because nothing between it and the body scrolls, the
     page scrolls. Wrapping is what the design does with the masthead nav
     at the same size; these rows were simply never given the same rule.
     Wrapping is applied to rows only. A column flex container told to
     wrap starts a second column instead of a second line, which widens
     the very thing it was meant to narrow. The design writes its layout
     inline, so the direction is readable from the attribute. */
  #pt-root div:not([style*="flex-direction:column"]){flex-wrap:wrap}
  /* A grid or flex item will not shrink below its content unless told to,
     which is what keeps a code block or a table from fitting even inside a
     box that scrolls. */
  #pt-root *{min-width:0}
  #pt-root input[type="range"]{max-width:100%}
}
"""

#: The design's print block, which the handoff specifies page by page and no
#: design file actually contains - 0.5cm margins, no page-break through a
#: figure or a table, colours printed rather than dropped. The sticky
#: masthead is unstuck and the controls that only work on screen are
#: dropped, because a printed page cannot be searched or toggled.
PRINT_CSS = """
@media print{
  @page{margin:0.5cm}
  html,body{background:#fff}
  /* !important throughout, because everything being overridden here is set
     by a class or an inline style, either of which outranks a bare element
     selector. A print rule that loses is a print rule that is not there. */
  *{print-color-adjust:exact !important;-webkit-print-color-adjust:exact !important;
    backdrop-filter:none !important}
  .pt-head{position:static !important}
  .pt-nav,#pt-search-open,#pt-theme,#pt-search{display:none !important}
  #pt-root{height:auto !important}
  figure,table,pre,details{break-inside:avoid}
  h1,h2,h3{break-after:avoid}
  a{text-decoration:none;color:inherit}
}
"""


def build(out_dir: pathlib.Path) -> set[str]:
    """Write the site, and report which files this builder owns.

    `docs/` also holds things it does not write - the published
    `envelope.json`, the markdown the measurement inventory points at, and
    `.nojekyll` - so the staleness check has to compare what is generated
    rather than the directory.
    """
    owned: set[str] = set()
    all_pages = pages()
    slug_of = {p["name"]: p["slug"] for p in all_pages}

    manifest = {
        "base": str(HANDOFF),
        "data": ["pt-data.js", "pt-search.js"],
        "doors": door_list(),
        "pages": [{"src": p["src"], "slug": p["slug"]} for p in all_pages],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(manifest, fh)
        manifest_path = fh.name

    result = subprocess.run(
        ["node", str(HERE / "prerender.cjs"), manifest_path],
        capture_output=True, text=True,
    )
    if result.stderr.strip():
        print(result.stderr.rstrip())
    if result.returncode != 0:
        sys.exit("prerender failed")
    rendered = json.loads(result.stdout)

    out_dir.mkdir(parents=True, exist_ok=True)

    css = merge_stylesheet([s["css"] for s in rendered["styles"]])
    (out_dir / "learn.css").write_text(
        (css + system_dark(css) + shell.SHELL_CSS
         + extracted_classes(rendered.get("classes", {}))
         + FRONT_OVERRIDES + THEME_CSS + NARROW_CSS + PRINT_CSS).strip() + "\n",
        encoding="utf-8")

    for name in ("learn-runtime.js", "learn-shell.js"):
        shutil.copy(SITE / name, out_dir / name)
    for asset in ASSETS:
        shutil.copy(HANDOFF / asset, out_dir / asset)
    #: GitHub Pages runs Jekyll unless told not to, and Jekyll skips files
    #: whose names begin with an underscore and rewrites some of the rest.
    #: The live site has carried this marker for a while as a tracked file;
    #: writing it makes any build self-sufficient, which is what lets the
    #: staging copy be a directory that is simply pushed.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    for icon in ICONS:
        if (out_dir / icon).exists():
            continue
        # Building somewhere other than the site itself - `--check` uses a
        # temporary directory. The icons are generated by a separate tool
        # because tracing the mark needs a browser and a build should not,
        # so take the committed ones; only their absence there is a fault.
        committed = ROOT / "docs" / icon
        if not committed.exists():
            sys.exit(f"missing {committed} - run tools/docs/learn/make_icons.py")
        shutil.copy(committed, out_dir / icon)

    by_slug = {p["slug"]: p for p in rendered["pages"]}
    overlay = shell.search_overlay()
    check_analytics_id()
    analytics = (ANALYTICS.format(gid=GA_MEASUREMENT_ID)
                 if GA_MEASUREMENT_ID and TARGET.analytics else "")
    envelope = json.loads((ROOT / "docs" / "envelope.json").read_text(encoding="utf-8"))
    version = project_version()
    all_slugs = {p["slug"] for p in all_pages}
    carrying: list[str] = []
    emitted: dict[str, str] = {}



    for page in all_pages:
        r = by_slug[page["slug"]]
        html = rewrite_links(r["html"], slug_of)
        template = rewrite_links(r["template"], slug_of)
        # Components carry links too: one page routes the reader onward from
        # data rather than markup, and eight carry a now-dead copy of the
        # door table. Both go through the same rewrite so neither can point
        # at a filename this site does not publish.
        script = rewrite_links(r["script"], slug_of)
        for before, after in SCRIPT_FIXES.get(page["slug"], ()):
            if before not in script:
                sys.exit(f"{page['slug']}: a SCRIPT_FIXES anchor no longer matches "
                         f"the design - re-check the fix against the new page")
            script = script.replace(before, after, 1)
        if ".dc.html" in script:
            sys.exit(f"{page['slug']}: a link in the component survived rewriting")
        if r.get("doorsWired"):
            carrying.append(page["slug"])
        # A raw-text script ends at the first `</script`, wherever it is.
        if "</script" in template.lower():
            sys.exit(f"{page['slug']}: template contains a closing script tag")
        stray = SEARCH_BINDING.findall(template)
        if stray:
            sys.exit(f"{page['slug']}: template still binds search values "
                     f"({', '.join(sorted(set(stray)))}) - the shell owns search now")
        doc = PAGE.format(
            title=esc(r["title"]),
            description=esc(r["description"]),
            base=BASE_URL,
            canonical=canonical_url(page["slug"]),
            slug=page["slug"],
            fonts=FONTS,
            theme_script=THEME_SCRIPT,
            analytics=analytics,
            robots_meta="" if TARGET.indexable else NOINDEX,
            structured_data=structured_data(page, r, version, envelope, emitted),
            body_class="pt-front" if page["slug"] == "index" else "pt-page",
            overlay=overlay,
            masthead=shell.masthead(DOORS, page["slug"]),
            # The front door carries its own five-door index under "Start
            # here", so the generated one would be the same twenty-five
            # links a second time, one screen further down.
            door_index="" if page["slug"] == "index" else shell.door_index(DOORS, page["slug"]),
            prev_next=shell.prev_next(page["prev"], page["next"]),
            html=html,
            template=template,
            script=script,
            props=json.dumps({**r["props"], "doors": door_list()}),
        )
        (out_dir / f"{page['slug']}.html").write_text(doc, encoding="utf-8")
        emitted[page["slug"]] = doc

    write_search_index(out_dir, all_pages, by_slug)
    write_data(out_dir)
    check_presets(emitted)
    check_release_notes(emitted)
    write_redirects(out_dir, all_pages)
    check_nothing_orphaned(out_dir, all_pages)
    write_seo(out_dir, all_pages, by_slug, emitted, version)

    print(f"built {len(all_pages)} pages into {out_dir}")
    owned.update(f"{p['slug']}.html" for p in all_pages)
    owned.update(f"{slug}.html" for slug in REDIRECTS)
    owned.update(ICONS)
    owned.update(ASSETS)
    owned.update(["learn.css", "learn-runtime.js", "learn-shell.js",
                  "pt-data.js", "pt-search.js",
                  "llms.txt", "llms-full.txt", "og-card.png", "site.webmanifest"])
    owned.add(".nojekyll")
    if TARGET.sitemap:
        owned.add("sitemap.xml")
    if AT_SITE_ROOT:
        owned.add("robots.txt")
    if carrying:
        print(f"  doors: {len(carrying)} components rewired to the site's page list")
    lifted = sum(p.get("styleClasses", 0) for p in rendered["pages"])
    if lifted:
        print(f"  styles: {len(rendered['classes'])} classes replace "
              f"{lifted} inline style attributes")
    return owned


def canonical_url(slug: str) -> str:
    """One address per page.

    GitHub Pages serves the front door at both `/tradefloor/` and
    `/tradefloor/index.html`. Naming the directory form as canonical is what
    stops the two being indexed as separate pages, and it is the one people
    actually link to.
    """
    return f"{BASE_URL}/" if slug == "index" else f"{BASE_URL}/{slug}.html"


def structured_data(page, rendered_page, version, envelope, emitted) -> str:
    """The JSON-LD for one page.

    The site it replaces carried this and it would be a plain regression to
    drop it: without a SoftwareApplication node the PyPI package, the crate,
    the repository and these pages are four unrelated things rather than one
    product, and "tradefloor" is Latin for price, which is a crowded name to
    have no disambiguation at all.

    Two pages carry more. The realism envelope is the project's central
    claim and is already published as JSON, so it is declared a Dataset with
    each of the fourteen statistics as a measured variable and its band as
    the range - which is the shape a question about it actually has. The
    glossary becomes a DefinedTermSet, which is the difference between a
    model paraphrasing a definition and citing one.
    """
    blocks = [seo.article_node(
        page, BASE_URL, version, rendered_page["description"],
        seo.first_commit(HANDOFF / page["src"], ROOT),
        seo.last_changed(HANDOFF / page["src"], ROOT),
    )]

    crumb = seo.breadcrumb_node(page, BASE_URL)
    if crumb:
        blocks.append(crumb)
    if page["slug"] == "index":
        blocks.append(seo.website_node(BASE_URL))
    if page["slug"] in ("realism-envelope", "metrics"):
        blocks.append(seo.dataset_node(envelope, BASE_URL))
    if page["slug"] == "glossary":
        terms = seo.glossary_node(rendered_page.get("terms"), BASE_URL)
        if terms:
            blocks.append(terms)
        else:
            sys.exit("the glossary page no longer exposes its terms - "
                     "the DefinedTermSet would silently disappear")

    return "\n".join(seo.ld_script(b) for b in blocks)


def write_seo(out_dir, all_pages, by_slug, emitted, version) -> None:
    """The sitemap, and the two files a language model reads.

    `robots.txt` is written only when this site is the one at the root a
    crawler asks for. A robots.txt in a subdirectory is read by nothing, and
    writing one there would be a file that looks like a policy and is not.
    """
    summaries = {p["slug"]: by_slug[p["slug"]]["description"] for p in all_pages}
    dates = {p["slug"]: seo.last_changed(HANDOFF / p["src"], ROOT) for p in all_pages}

    if TARGET.sitemap:
        (out_dir / "sitemap.xml").write_text(
            seo.sitemap(all_pages, BASE_URL, dates), encoding="utf-8")
    (out_dir / "llms.txt").write_text(
        seo.llms(all_pages, DOORS, BASE_URL, version, summaries), encoding="utf-8")
    (out_dir / "llms-full.txt").write_text(
        seo.llms_full(all_pages, BASE_URL, version, emitted, summaries), encoding="utf-8")
    if AT_SITE_ROOT:
        (out_dir / "robots.txt").write_text(
            seo.robots(BASE_URL) if TARGET.indexable else STAGING_ROBOTS,
            encoding="utf-8")


def write_data(out_dir: pathlib.Path) -> None:
    """Emit the chart data, generated from the repository's own files.

    This is the change the handoff asked for by name. The design shipped
    `pt-data.js` as a hand-decoded snapshot of the goldens and said so: "if
    the goldens change, the charts silently lie". Generating it means a
    golden that moves either moves the chart with it, or fails
    `data.py --check`, which is a build failure rather than a wrong picture.
    """
    body = json.dumps(data.build(), separators=(",", ":"), ensure_ascii=False)
    (out_dir / "pt-data.js").write_text(
        "/* Generated by tools/docs/learn/data.py from rust/goldens,\n"
        "   measurements, docs/envelope.json and examples/data.\n"
        "   Do not edit: rebuild instead. */\n"
        f"window.PT = {body};\n", encoding="utf-8")


def write_search_index(out_dir, all_pages, by_slug) -> None:
    """Build the search index from the rendered pages.

    The handoff shipped a hand-written keyword list per page and said so:
    it finds pages, not passages. This reads the text the build just
    produced, so a phrase a reader remembers seeing is a phrase that finds
    the page it was on - and the index cannot fall behind the prose, because
    it is made from it.
    """
    entries = []
    for page in all_pages:
        r = by_slug[page["slug"]]
        text = re.sub(r"<[^>]+>", " ", r["html"])
        text = re.sub(r"\s+", " ", text).strip()
        entries.append({
            "title": page["name"],
            "slug": page["slug"],
            "door": (page["door"] or "Start").upper(),
            "text": text[:6000],
        })
    body = json.dumps(entries, separators=(",", ":"), ensure_ascii=False)
    (out_dir / "pt-search.js").write_text(
        "/* Generated by tools/docs/learn/build.py from the rendered pages.\n"
        "   Do not edit: rebuild instead. */\n"
        f"window.PT_SEARCH = {body};\n", encoding="utf-8")


def system_dark(css: str) -> str:
    """Follow the system when the reader has not chosen.

    The design's dark palette hangs off `html[data-theme="dark"]`, and that
    attribute is only ever set by the theme toggle - so a reader whose
    system is dark, and who has never touched the toggle, is served the
    light site. Restating the same tokens under `prefers-color-scheme`
    gives the three states a theme needs: system by default, and an explicit
    choice that wins in either direction.

    The tokens are lifted from the rule that already exists rather than
    written out again, so the two palettes cannot drift apart.
    """
    m = re.search(r'html\[data-theme="dark"\]\{([^}]*)\}', css)
    if not m:
        sys.exit("no dark palette in the merged stylesheet")
    return (
        "\n@media (prefers-color-scheme: dark){\n"
        f'  html:not([data-theme="light"]){{{m.group(1)}}}\n'
        "}\n"
    )


def extracted_classes(classes: dict[str, str]) -> str:
    """The rules for the styles the prerenderer lifted out of the markup.

    Written as `#pt-root .pt-sN` rather than `.pt-sN`. The declarations
    being moved were inline, and an inline style outranks every selector
    that is not `!important`; the id prefix is what preserves that ranking,
    so a rule that used to win still wins and nothing needs to be re-checked
    one page at a time. The rules that are *meant* to override - print,
    theme, the narrow-screen block - say `!important` and are unaffected.
    """
    if not classes:
        return ""
    lines = [f"#pt-root .{name}{{{style}}}" for name, style in sorted(classes.items(),
             key=lambda kv: int(kv[0].removeprefix("pt-s")))]
    return "\n/* Lifted out of the markup by prerender.cjs. */\n" + "\n".join(lines) + "\n"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=sorted(TARGETS), default="live",
                    help="live: the published site. dev: a staging copy, "
                         "kept out of search and out of analytics.")
    ap.add_argument("--out", default=None)
    ap.add_argument("--check", action="store_true",
                    help="build to a temporary directory and diff, writing nothing")
    args = ap.parse_args()

    global TARGET, BASE_URL
    TARGET = TARGETS[args.target]
    BASE_URL = TARGET.base_url
    if args.out is None:
        args.out = str(ROOT / "docs") if args.target == "live" else str(ROOT / "dist" / "dev-site")

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            owned = build(pathlib.Path(tmp))
            stale = []
            for name in sorted(owned):
                here, there = pathlib.Path(args.out) / name, pathlib.Path(tmp) / name
                if not here.exists():
                    stale.append(f"{name}: not committed")
                elif here.read_bytes() != there.read_bytes():
                    stale.append(f"{name}: differs from what the sources build")
            if stale:
                print("\n".join("  " + line for line in stale[:40]))
                sys.exit(f"{args.out} is stale - run tools/docs/learn/build.py")
            print(f"{args.out} is up to date ({len(owned)} generated files)")
        return

    build(pathlib.Path(args.out))


if __name__ == "__main__":
    main()
