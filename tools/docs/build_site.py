"""Build the published documentation site from the design bundle.

The design ships as one self-contained HTML file: a React app with every
route's markup inlined, addressed by hash (`#/mcp`). That is a good reading
experience and a bad indexing one, because everything after the `#` never
reaches a server and a crawler sees a single page whose body says "This page
requires JavaScript to display."

So this script emits two things from the one bundle:

1. `index.html`, the app itself, with the head a crawler actually reads
   filled in (description, Open Graph, canonical, JSON-LD) and a `<noscript>`
   index linking every route.
2. One static page per route, carrying that route's real prose with no
   JavaScript required. These are the URLs that get indexed, and they are
   what the README and any external link should point at.

Both carry the analytics snippet when a measurement ID is configured.

Run it after dropping a new design revision at DESIGN_BUNDLE:

    python tools/docs/build_site.py

Everything under docs/ that this script owns is overwritten. Files it does
not own (envelope.json, the markdown prose) are left alone.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DESIGN_BUNDLE = ROOT / "tools" / "docs" / "design-bundle.html"
OUT = ROOT / "docs"

#: Absolute base for canonical links, Open Graph URLs and the sitemap. These
#: three all need an absolute URL to work at all, so this cannot be relative.
BASE_URL = "https://simoncoombes.github.io/pretium"

#: GA4 measurement ID, the "G-XXXXXXXXXX" from the property's web data
#: stream. Empty means no analytics: the snippet is omitted entirely rather
#: than emitted with a placeholder, because a gtag call with a bad ID fails
#: silently and looks like working analytics that reports nothing.
GA_MEASUREMENT_ID = "G-1LBM3239ZF"

#: Read from pyproject so the site cannot drift from the package. Only two
#: places in the bundle should follow the version: the nav badge and the
#: BibTeX block. The release-notes headings and the "measured on pretium
#: 0.1.0" provenance lines are history and must stay where they are.
def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit("no version in pyproject.toml")
    return m.group(1)


VERSION = _project_version()

SITE_NAME = "pretium docs"
TAGLINE = "repeatable market simulation"
REPO_URL = "https://github.com/simoncoombes/pretium"

#: Tab icon. The SVG is the real one; the .ico exists because browsers ask
#: for /favicon.ico by default whether or not a page links to it, and that
#: request 404s otherwise. Both live at the site root, which is also where
#: every static page sits, so these paths are relative and need no base.
FAVICON_LINKS = (
    '<link rel="icon" type="image/svg+xml" href="favicon.svg">\n'
    '<link rel="alternate icon" href="favicon.ico" sizes="16x16 32x32 48x48">\n'
    '<link rel="apple-touch-icon" href="apple-touch-icon.png">'
)

#: Google Fonts covering the two families the design system asks for. The
#: bundle inlines them as woff2 blobs that only its own loader can resolve,
#: so the static pages fetch them the ordinary way and fall back to system
#: stacks if the request fails.
FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
    "?family=Public+Sans:ital,wght@0,400;0,600;0,650;1,400"
    "&family=Spline+Sans+Mono:wght@400;600"
    '&display=swap">'
)


#: House style is no em or en dashes in prose. The design bundle arrives with
#: a handful in visible text; they are corrected here rather than in the
#: bundle so a fresh design revision gets the same treatment automatically.
#: Dashes inside the bundler's own JavaScript comments are left alone.
PROSE_FIXES = [
    ("pretium docs — repeatable market simulation", "pretium docs: repeatable market simulation"),
    ("MIXED — SEE NOTE", "MIXED, SEE NOTE"),
]

#: The bundle was authored in the pt-v3 era, when the panel had ten
#: statistics and the tick decomposition had seven components. Both grew:
#: fourteen statistics since 2026-08-25, nine components since the jump and
#: circuit-breaker columns were added, and the default preset moved to
#: pt-v10 on 2026-08-26. The published site is generated from this bundle
#: rather than from the markdown in docs/, so a stale claim here is the
#: claim users actually read.
#:
#: These assert. A bundle revision that rewords any of them fails the build
#: rather than quietly restoring a number that is no longer true.
ERA_FIXES = [
    (
        "It measures ten statistics against real markets and gets nine of "
        "them right over a year. The ten results are published, along with "
        "six things it gets wrong and what each one rules out.",
        "It measures fourteen statistics against real markets and gets all "
        "fourteen right over a year. The fourteen results are published, "
        "along with six things it gets wrong and what each one rules out.",
    ),
    (
        "pretium publishes what it gets right and what it gets wrong: ten "
        "statistics measured against real markets, how long those results "
        "hold for",
        "pretium publishes what it gets right and what it gets wrong: "
        "fourteen statistics measured against real markets, how long those "
        "results hold for",
    ),
    (
        "Ten statistics measured against real markets, nine of them in band "
        "over a year, and six known gaps with what each one rules out.",
        "Fourteen statistics measured against real markets, all fourteen in "
        "band over a year, and six known gaps with what each one rules out.",
    ),
    (
        "Five of the ten statistics were live calibration targets",
        "Five of the fourteen statistics were live calibration targets",
    ),
    (
        "Measured on the shipped preset, eight of the ten statistics have "
        "their 10th-to-90th percentile range across seeds crossing a band "
        "edge.",
        "Measured on the shipped preset, nine of the fourteen statistics "
        "have their 10th-to-90th percentile range across seeds crossing a "
        "band edge.",
    ),
    (
        "eight of the ten statistics have their middle-eighty-percent range "
        "crossing a band edge",
        "nine of the fourteen statistics have their middle-eighty-percent "
        "range crossing a band edge",
    ),
    (
        'is a preset name or a ModelParams; the default is pt-v3.',
        'is a preset name or a ModelParams; the default is pt-v10.',
    ),
    (
        "The current default, and the one the realism numbers describe.",
        "The default until the 2026-08-26 era boundary, kept selectable so "
        "work published against it keeps reproducing.",
    ),
    (
        "The current default, produced the same way. This is the preset the "
        "realism numbers describe: nine of ten statistics in band at 252 "
        "days. Herding is turned well down (momentum_theta 0.0742).",
        "The default until the 2026-08-26 era boundary, produced the same "
        "way, and the preset the realism numbers described then: nine of the "
        "ten statistics measured at the time were in band at 252 days. "
        "Herding is turned well down (momentum_theta 0.0742).",
    ),
    (
        '<code style="font-size:13px">pt-v3</code> is the current default;',
        '<code style="font-size:13px">pt-v10</code> is the current default;',
    ),
    (
        'eng = pt.Engine(seed=42, universe=u, model="pt-v3")  # the default, spelled out',
        'eng = pt.Engine(seed=42, universe=u, model="pt-v10")  # the default, spelled out',
    ),
    (
        'eng = pt.Engine(seed=42, universe=u)                  # pt-v3, the default\n'
        'eng = pt.Engine(seed=42, universe=u, model="pt-v3")   # the same, spelled out',
        'eng = pt.Engine(seed=42, universe=u)                  # pt-v10, the default\n'
        'eng = pt.Engine(seed=42, universe=u, model="pt-v10")  # the same, spelled out',
    ),
    ("The Seven Components", "The Nine Components"),
    ("seven components", "nine components"),
    ("seven factors", "nine factors"),
]

#: The bundle was authored before anything shipped, so its Release Notes page
#: still says 0.1.0 is unreleased and nothing has been tagged. Every word of
#: that is now false, and a documentation page asserting a package is
#: unpublished while it sits on PyPI is worse than a missing page.
#:
#: Corrected here rather than in the bundle, for the same reason as
#: PROSE_FIXES: a fresh design revision gets the treatment automatically. The
#: replacements assert, so if a later bundle rewords this section the build
#: fails loudly instead of quietly shipping a stale claim.
#: The bundle's preset table stops at pt-v4. pt-v5 and pt-v6 exist and are
#: selectable, and a preset a reader cannot find out about is half shipped.
#: The rows live in their own file rather than escaped into a Python literal,
#: because the markup carries the quoting the design uses and fighting that
#: through two layers of escaping is how a build script grows a syntax error.
PRESET_ROWS_FILE = ROOT / "tools" / "docs" / "preset-rows.html"

#: The pt-v4 row's closing markup, which the new rows are inserted after.
#: Asserted at build time so a reworded table fails loudly rather than
#: silently dropping two presets.
PRESET_ROWS_ANCHOR = (
    'having surrendered <code style="font-size:12px">return_acf1</code>.'
    "</sc-raw-td>\n            </sc-raw-tr>"
)

RELEASE_STATUS_FIXES = [
    (
        '<h2 style="font-size:19px;margin:40px 0 4px">0.1.0 '
        '<span style="font-weight:400;color:var(--faint);font-size:15px">'
        "- unreleased</span></h2>",
        '<h2 style="font-size:19px;margin:40px 0 4px">Status '
        '<span style="font-weight:400;color:var(--faint);font-size:15px">'
        "- {version}, current</span></h2>",
    ),
    (
        "Pre-release. Everything documented works and is tested. The API may "
        "move before 1.0. Nothing has been tagged, and there is no DOI yet.",
        "Published on PyPI as <code style=\"font-size:12.5px\">pretium</code> "
        "and on crates.io as the <code style=\"font-size:12.5px\">pretium</code> "
        "crate. Everything documented works and is tested. The API may move "
        "before 1.0. Each release is tagged and its wheels are built by the "
        "release workflow, which runs one fixed simulation inside every wheel "
        "and compares digests before anything is uploaded. There is no DOI yet.",
    ),
]


#: The release-notes page in the bundle carries release STATUS (which version
#: is current, what the era boundary means) and no release notes: a reader
#: clicking "Release notes" found nothing about what changed in 0.1.1 or
#: 0.1.3. CHANGELOG.md has all of it and is the file the release workflow
#: already cuts GitHub release notes from, so the site renders that rather
#: than keeping a second copy that can disagree with the first.
CHANGELOG = ROOT / "CHANGELOG.md"

#: The heading the rendered changelog is inserted before, so the page reads
#: status, then what changed in each release, then the era boundary note.
#: Asserted at build time: a reworded bundle fails loudly rather than
#: silently shipping a page with no release notes again.
CHANGELOG_ANCHOR = (
    '<h2 style="font-size:19px;margin:40px 0 4px">Known-answer v8 '
)

_MUT = 'style="color:var(--mut);font-size:14px'
_CODE = 'style="font-size:12.5px"'


def _inline(text: str) -> str:
    """Markdown inline markup to the design's inline-styled HTML."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", lambda m: f'<code {_CODE}>{m.group(1)}</code>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: f'<a href="{m.group(2)}" style="color:var(--accent)">{m.group(1)}</a>',
                 out)
    return out


def _table(rows: list[str]) -> str:
    """A markdown table, minus its separator row."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    body = [r for r in cells[1:] if not all(set(c) <= set("-: ") for c in r)]
    head = "".join(
        f'<th style="text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);'
        f'font:600 12px var(--font-ui)">{_inline(c)}</th>' for c in cells[0])
    trs = "".join(
        "<tr>" + "".join(
            f'<td style="padding:6px 10px;border-bottom:1px solid var(--linesoft);'
            f'font-size:13px;color:var(--mut)">{_inline(c)}</td>' for c in r) + "</tr>"
        for r in body)
    return ('<div style="overflow-x:auto"><table style="border-collapse:collapse;'
            f'margin:12px 0;min-width:100%">{"<tr>" + head + "</tr>"}{trs}</table></div>')


def changelog_html(version: str, dates: dict[str, str]) -> str:
    """CHANGELOG.md as the release-notes page's body.

    Handles exactly the markup the changelog uses: version and section
    headings, paragraphs with bold, code and links, tables, and fenced code
    blocks. An "Unreleased" section is skipped, because the site documents
    what a reader can install.
    """
    if not CHANGELOG.exists():
        sys.exit("CHANGELOG.md not found; the release-notes page needs it")
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    para: list[str] = []
    table: list[str] = []
    fence: list[str] | None = None
    skipping = False

    def flush() -> None:
        nonlocal para, table
        if table:
            out.append(_table(table)); table = []
        if para:
            out.append(f'<p {_MUT};margin:0 0 12px">{_inline(" ".join(para))}</p>')
            para = []

    for line in lines:
        if line.startswith("```"):
            if fence is None:
                flush(); fence = []
            else:
                if not skipping:
                    out.append(
                        '<pre style="background:var(--codebg);color:#e8e8e8;padding:12px 14px;'
                        'border-radius:6px;overflow-x:auto;font:12.5px/1.6 var(--font-mono)">'
                        + html.escape("\n".join(fence)) + "</pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line); continue
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            skipping = title.lower().startswith("unreleased")
            if skipping:
                continue
            when = dates.get(title, "")
            tag = " - current" if title == version else (f" - {when}" if when else "")
            out.append(
                f'<h2 style="font-size:19px;margin:44px 0 4px">{_inline(title)}'
                f'<span style="font-weight:400;color:var(--faint);font-size:15px">'
                f"{html.escape(tag)}</span></h2>")
            continue
        if skipping:
            continue
        if line.startswith("### "):
            flush()
            out.append(f'<h3 style="font-size:15px;margin:24px 0 6px">'
                       f"{_inline(line[4:].strip())}</h3>")
            continue
        if line.startswith("# "):
            continue
        if line.startswith("|"):
            if para:
                flush()
            table.append(line); continue
        if not line.strip():
            flush(); continue
        if table:
            flush()
        para.append(line.strip())
    flush()
    return "\n        ".join(out)


def tag_dates() -> dict[str, str]:
    """Release dates from the annotated tags, so the page cannot invent one."""
    try:
        import subprocess
        raw = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short) %(creatordate:short)",
             "refs/tags"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001 - a missing git is not a build failure
        return {}
    out = {}
    for row in raw.splitlines():
        parts = row.split()
        if len(parts) == 2 and parts[0].startswith("v"):
            out[parts[0][1:]] = parts[1]
    return out


def read_bundle() -> str:
    """The bundle, with our corrections applied to the readable document.

    The document lives as a JSON string inside a `<script>` tag, so editing
    the raw file means knowing how the encoder escaped every character: a
    `/` arrives as `\\u002F`, which is why matching a literal `</span>`
    silently finds nothing. Decoding first, editing plain text, then
    re-encoding removes the guesswork.
    """
    if not DESIGN_BUNDLE.exists():
        sys.exit(f"design bundle not found: {DESIGN_BUNDLE}")
    raw = DESIGN_BUNDLE.read_text(encoding="utf-8")
    payload = script_payload(raw, "template")
    doc = json.loads(payload)

    for old, new in PROSE_FIXES:
        doc = doc.replace(old, new)
    for old, new in ERA_FIXES:
        if old not in doc:
            sys.exit(
                "the design bundle no longer contains a phrase ERA_FIXES "
                f"corrects, so the correction would be silently skipped: {old[:60]!r}"
            )
        doc = doc.replace(old, new)
    doc = apply_trust_fixes(doc)
    # Two spots track the package version; the rest are history. See VERSION.
    doc = doc.replace(">v0.1.0<", f">v{VERSION}<")
    doc = doc.replace("version = {0.1.0}", "version = {%s}" % VERSION)
    if PRESET_ROWS_ANCHOR not in doc:
        sys.exit("the pt-v4 row was reworded; PRESET_ROWS_ANCHOR needs "
                 "updating rather than silently dropping pt-v5 and pt-v6")
    doc = doc.replace(
        PRESET_ROWS_ANCHOR,
        PRESET_ROWS_ANCHOR + "\n            "
        + PRESET_ROWS_FILE.read_text(encoding="utf-8").strip(),
        1,
    )
    if CHANGELOG_ANCHOR not in doc:
        sys.exit("the release-notes era-boundary heading was reworded; "
                 "CHANGELOG_ANCHOR needs updating rather than silently "
                 "shipping a release-notes page with no release notes")
    doc = doc.replace(
        CHANGELOG_ANCHOR,
        changelog_html(VERSION, tag_dates())
        + "\n\n        " + CHANGELOG_ANCHOR,
        1,
    )

    for stale, current in RELEASE_STATUS_FIXES:
        if stale not in doc:
            sys.exit(
                "release-status text not found in the bundle. It was reworded "
                "upstream, so RELEASE_STATUS_FIXES needs updating rather than "
                "silently shipping a stale claim:\n  " + stale[:90]
            )
        doc = doc.replace(stale, current.format(version=VERSION))

    # Re-encode, then escape every forward slash the way the bundler does.
    # Without that, a literal "</script>" inside the document would close the
    # tag early and the page would never boot.
    encoded = json.dumps(doc).replace("/", "\\u002F")
    out = raw.replace(payload, encoded, 1)
    json.loads(script_payload(out, "template"))  # prove it still parses
    return out


def script_payload(bundle: str, kind: str) -> str:
    """Return the text of one `__bundler/<kind>` script tag."""
    m = re.search(
        rf'<script[^>]*type="__bundler/{kind}"[^>]*>(.*?)</script>', bundle, re.S
    )
    if not m:
        sys.exit(f"bundle has no __bundler/{kind} block")
    return m.group(1).strip()


def inner_document(bundle: str) -> str:
    """The readable document, which the bundle carries as a JSON string."""
    doc = json.loads(script_payload(bundle, "template"))
    if not isinstance(doc, str):
        sys.exit("template block was not a JSON string")
    return doc


def design_css(doc: str) -> str:
    """The design system, minus the @font-face block bound to bundle blobs."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", doc, re.S)
    if not blocks:
        sys.exit("no <style> block in the design document")
    # The font block is by far the largest and is almost entirely @font-face
    # rules pointing at uuid blob URLs. The design system is the other one.
    system = min(blocks, key=lambda b: b.count("@font-face"))
    return re.sub(r"@font-face\s*\{.*?\}", "", system, flags=re.S).strip()


def split_pages(doc: str) -> list[dict]:
    """Every `data-page` div, in document order, with its label and title."""
    marks = list(re.finditer(r'<div([^>]*?)data-page="([^"]+)"([^>]*)>', doc))
    if not marks:
        sys.exit("no data-page divs found")
    pages = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else doc.rindex("</x-dc>")
        raw = doc[m.end() : end]
        # Each page div is closed by the </div> that precedes the next one.
        raw = raw[: raw.rindex("</div>")] if "</div>" in raw else raw
        attrs = m.group(1) + m.group(3)
        label = re.search(r'data-screen-label="([^"]*)"', attrs)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S)
        pages.append(
            {
                "slug": m.group(2),
                "label": label.group(1) if label else m.group(2),
                "h1": strip_tags(h1.group(1)) if h1 else (label.group(1) if label else m.group(2)),
                "html": raw,
            }
        )
    return pages


def strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def page_url(slug: str) -> str:
    return "./" if slug == "home" else f"{slug}.html"


def absolute(slug: str) -> str:
    return f"{BASE_URL}/" if slug == "home" else f"{BASE_URL}/{slug}.html"


def clean_content(fragment: str) -> str:
    """Turn a bundle page fragment into standalone, JS-free HTML."""
    out = fragment
    # Tables were escaped into custom elements to survive the bundler.
    out = re.sub(r"<(/?)sc-raw-([a-z]+)", r"<\1\2", out)
    # camelCase attributes were escaped the same way.
    out = re.sub(
        r"\bsc-camel-([a-z-]+)=",
        lambda m: m.group(1).replace("-", "") + "=",
        out,
    )
    # Copy-to-clipboard buttons do nothing without the app behind them.
    out = re.sub(
        r'<button[^>]*\{\{\s*copyCode\s*\}\}[^>]*>.*?</button>', "", out, flags=re.S
    )
    out = re.sub(r'<button[^>]*>\s*Copy\s*</button>', "", out, flags=re.S)
    # Hash routes become real page links; in-page anchors are left alone.
    out = re.sub(r'href="#/([a-z0-9-]+)"', lambda m: f'href="{page_url(m.group(1))}"', out)
    out = out.replace('href="#/"', 'href="./"')
    # Any template variable left over would render as literal braces.
    out = re.sub(r"\s*\w+=\"[^\"]*\{\{[^}]*\}\}[^\"]*\"", "", out)
    out = re.sub(r"\{\{[^}]*\}\}", "", out)
    return out.strip()


def description_for(page: dict) -> str:
    """First real sentence of the page, trimmed to a sensible meta length."""
    body = re.sub(r"<h1[^>]*>.*?</h1>", " ", page["html"], flags=re.S)
    for para in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S):
        text = strip_tags(para)
        if len(text) > 60:
            if len(text) > 155:
                cut = text[:155].rsplit(" ", 1)[0]
                return cut.rstrip(",;:") + "..."
            return text
    return f"{page['h1']}. Documentation for pretium, a deterministic market simulator."


def analytics() -> str:
    if not GA_MEASUREMENT_ID:
        return ""
    gid = GA_MEASUREMENT_ID
    return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{gid}');
</script>"""


def spa_analytics() -> str:
    """Analytics for the app, which changes route without changing URL path.

    A hash change fires no navigation, so gtag would record one pageview for
    a whole reading session. This sends one per route instead.
    """
    if not GA_MEASUREMENT_ID:
        return ""
    gid = GA_MEASUREMENT_ID
    return f"""<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{gid}', {{send_page_view: false}});
function pretiumPageview() {{
  var route = location.hash.replace(/^#\\/?/, '') || 'home';
  gtag('event', 'page_view', {{
    page_title: document.title,
    page_location: location.href,
    page_path: '/' + route
  }});
}}
window.addEventListener('hashchange', pretiumPageview);
pretiumPageview();
</script>"""


def json_ld(page: dict, desc: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": page["h1"],
        "description": desc,
        "url": absolute(page["slug"]),
        "isPartOf": {
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": f"{BASE_URL}/",
        },
        "about": {
            "@type": "SoftwareApplication",
            "name": "pretium",
            "applicationCategory": "DeveloperApplication",
            "operatingSystem": "Linux, macOS, Windows",
            "codeRepository": REPO_URL,
        },
    }
    return (
        '<script type="application/ld+json">'
        + json.dumps(data, separators=(",", ":"))
        + "</script>"
    )



def _fmt(v: float) -> str:
    # The bundle's own convention: a real minus sign, not a hyphen.
    s = f"{v:.2f}" if abs(v) >= 1 else f"{v:+.4f}"
    return s.replace("-", "\u2212")


def _band(lo: float, hi: float) -> str:
    def n(x):
        s = f"{x:.4f}".rstrip("0").rstrip(".")
        return (s if s not in ("-0", "") else "0").replace("-", "\u2212")
    return f"{n(lo)} - {n(hi)}"


def certified_rows() -> str:
    """The certification table's body, generated from the shipped envelope.

    The bundle carries a pt-v3-era table: ten statistics, `volume_change_acf1`
    at -0.4598 and out by 13.7 sd. Every figure moved at the 2026-08-26 era
    boundary, and this is the page a reader consults to decide whether to
    trust a result. Generating it from `pretium.envelope` means the published
    table cannot drift from the module again.
    """
    from pretium import envelope as env, facts

    rows = []
    for name, value in env.CERTIFIED.items():
        if name not in facts.REAL_MARKETS:
            continue
        lo, hi = facts.REAL_MARKETS[name]
        ok = lo <= value <= hi
        verdict = "in band" if ok else "out"
        colour = "var(--mut)" if ok else "var(--warn, #b45309)"
        rows.append(
            f"<sc-raw-tr><sc-raw-td>{name}</sc-raw-td>"
            f'<sc-raw-td style="text-align:right">{_fmt(value)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_band(lo, hi)}</sc-raw-td>'
            f'<sc-raw-td style="color:{colour}">{verdict}</sc-raw-td></sc-raw-tr>'
        )
    body = "\n            ".join(rows)
    return (
        '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12.5px">\n'
        f"            {body}\n          </sc-raw-tbody>"
    )


def horizon_rows() -> str:
    """The 504-day comparison table, likewise generated."""
    from pretium import envelope as env, facts

    rows = []
    for name in facts.REAL_MARKETS:
        v252, v504 = env.CERTIFIED.get(name), env.MEASURED_504.get(name)
        if v252 is None or v504 is None:
            continue
        lo, hi = facts.REAL_MARKETS_504[name]
        ok = lo <= v504 <= hi
        rows.append(
            f"<sc-raw-tr><sc-raw-td>{name}</sc-raw-td>"
            f'<sc-raw-td style="text-align:right">{_fmt(v252)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_fmt(v504)}</sc-raw-td>'
            f'<sc-raw-td style="text-align:right">{_band(lo, hi)}</sc-raw-td>'
            f'<sc-raw-td>{"in" if ok else "out"}</sc-raw-td></sc-raw-tr>'
        )
    return "\n            ".join(rows)


def apply_trust_fixes(doc: str) -> str:
    """Bring the realism page's measured content up to the shipped envelope.

    The bundle's "What Is Certified" table, its 504-day comparison and its
    first two gaps are all pt-v3 era. This is the page the site points a
    reader to before they trust a result, so a stale number here is the worst
    stale number in the project. Each replacement asserts.
    """
    def cut(marker: str, start_tag: str, end_tag: str, replacement: str) -> None:
        nonlocal doc
        if marker not in doc:
            sys.exit(f"the design bundle no longer contains {marker[:60]!r}; "
                     "apply_trust_fixes needs updating rather than silently "
                     "shipping a pt-v3-era realism page")
        i = doc.index(marker)
        s = doc.index(start_tag, i)
        e = doc.index(end_tag, s) + len(end_tag)
        doc = doc[:s] + replacement + doc[e:]

    swaps = [
        (
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v3</code> preset puts nine of the ten measured statistics in "
            "band. The tenth fails structurally and is named, in every result "
            "the library produces.</p>",
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v10</code> preset puts all fourteen measured statistics in "
            "band, on thirty calibration seeds and on a held-out 60-name "
            "universe measured at the same resolution. It is the first preset "
            "in the project with no miss at this horizon.</p>",
        ),
        (
            "Measured: preset pt-v3 · 30 seeds · 40 instruments · 252 trading "
            "days · band-distance loss L_real = 0.0000.",
            "Measured: preset pt-v10 · 30 seeds · 40 instruments · 252 trading "
            "days · band-distance loss L_real = 0.0000.",
        ),
        (
            "1 · The tenth statistic is structurally unreachable",
            "1 · Volume-change autocorrelation leaves its band past a year",
        ),
        (
            "<p><code style=\"font-size:12.5px\">volume_change_acf1</code> reads "
            "−0.46 against a real band of −0.32 to −0.20: 13.7 "
            "seed-standard-deviations out. A held volume level plus "
            "independent per-tick noise sits near −0.5 at any coefficients, "
            "which means no parameter setting can reach it. It is excluded "
            "from the calibration objective deliberately, because an optimiser "
            "pointed at an unreachable target distorts every other parameter "
            "chasing it. It stays in every report as a standing falsification "
            "verdict.</p>",
            "<p><code style=\"font-size:12.5px\">volume_change_acf1</code> reads "
            "−0.3130 at 252 days against a real band of −0.32 to −0.20, inside "
            "it but only 0.7 seed-sd clear of the floor, and −0.3156 at 504 "
            "days against a tighter −0.29 to −0.21, about 2.2 seed-sd out. "
            "This page called the row structurally unreachable until 0.2.0, "
            "because every way of reaching it cost "
            "<code style=\"font-size:12.5px\">volume_abs_return_corr</code> its "
            "own band. That is withdrawn: pt-v10 holds both at one year. It "
            "stays outside the calibration objective, which is a statement "
            "about what an optimiser may chase, not about reachability.</p>",
        ),
        (
            "Measured against bands re-derived at the matching 504-day window, "
            "the model holds 5 of 10.",
            "Measured against bands re-derived at the matching 504-day window, "
            "the model holds 13 of 14, missing only the volume-change row.",
        ),
        (
            "Volatility clustering roughly doubles from 252 to 504 days where "
            "real markets move about 14%. The price level stays plausible, so "
            "long runs look fine and are not. The dynamics leave the envelope "
            "while the chart still looks like a market.",
            "That is the best two-year reading this project has measured, and "
            "it is still not a certification: CERTIFIED is measured at 252 "
            "days, the 504-day table is measured but not certified, and "
            "beyond 504 days nothing has been measured at all. Excess "
            "kurtosis deserves a reader's caution at this horizon, inside its "
            "band at 8.26 but only about 0.3 seed-sd above a floor of 7.1.",
        ),
    ]
    swaps.extend([
        (
            "4 · Tails are too thin over multi-year windows",
            "4 · The endogenous economy cannot reach its own crisis regimes",
        ),
        (
            "<p>Over 504-day windows real markets show excess kurtosis of 7.1 "
            "to 22. The model shows 5.2. The 252-day band's floor of 1.6 is "
            "wide enough that this reads as comfortably in band on every "
            "252-day certificate, which is why it went unnoticed: nothing was "
            "measuring kurtosis where it fails.</p>",
            "<p>Left to itself the macro state stays in a moderate band. "
            "Endogenous inflation peaks at 4.06% to 4.11% over five seeds and "
            "five years, against a clamp of 6.0% and a US CPI that reached "
            "9.1% in June 2022. The cause is dispersion rather than "
            "persistence. This slot used to carry a thin-tails gap, retired "
            "at 0.2.0: two-year excess kurtosis moved from 5.23, under its "
            "band, to 8.26, inside it.</p>",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> tail-risk or "
            "VaR calibration at multi-year horizons. At the certified 252-day "
            "horizon, kurtosis is in band.</p>",
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> studying an "
            "inflation regime or a policy crisis from the endogenous economy "
            "alone. Drive one with a scenario instead.</p>",
        ),
    ])

    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a realism-page passage that "
                     f"apply_trust_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)

    cut("At a 252-day horizon the shipped", "<sc-raw-tbody", "</sc-raw-tbody>",
        certified_rows())
    cut("2 · The certified horizon is 252 days", "<sc-raw-tbody",
        "</sc-raw-tbody>",
        '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12px">\n'
        f"                {horizon_rows()}\n              </sc-raw-tbody>")
    return doc

def nav_html(pages: list[dict], current: str) -> str:
    items = []
    for p in pages:
        if p["slug"] == current:
            items.append(f'<li><span aria-current="page">{html.escape(p["label"])}</span></li>')
        else:
            items.append(
                f'<li><a href="{page_url(p["slug"])}">{html.escape(p["label"])}</a></li>'
            )
    return "<ul>" + "".join(items) + "</ul>"


STATIC_CSS = """
.layout{max-width:1120px;margin:0 auto;padding:0 24px;display:grid;
  grid-template-columns:232px minmax(0,1fr);gap:40px}
.sidebar{padding:28px 0;border-right:1px solid var(--linesoft)}
.sidebar ul{list-style:none;padding:0;margin:0;font-size:13.5px}
.sidebar li{margin:0 0 2px}
.sidebar a,.sidebar span{display:block;padding:5px 10px;border-radius:6px;color:var(--mut)}
.sidebar a:hover{background:var(--panel);text-decoration:none;color:var(--fg)}
.sidebar span[aria-current]{background:var(--navact);color:var(--fg);font-weight:600}
main{padding:28px 0 72px;min-width:0}
main h1{font-size:30px;margin:0 0 18px}
main h2{font-size:20px;margin:34px 0 12px}
main h3{font-size:16px;margin:24px 0 8px}
.masthead{border-bottom:1px solid var(--line);background:var(--panel)}
.masthead .inner{max-width:1120px;margin:0 auto;padding:14px 24px;
  display:flex;align-items:baseline;gap:12px}
.masthead a.brand{font-weight:650;color:var(--fg);font-size:15px}
.masthead .tag{color:var(--mut);font-size:13px}
.masthead .spacer{flex:1}
.appnote{margin:0 0 26px;padding:10px 14px;border:1px solid var(--linesoft);
  border-radius:8px;background:var(--panel);font-size:13px;color:var(--mut)}
pre{background:var(--codebg);color:var(--codefg);border-radius:9px;
  padding:13px 16px;overflow-x:auto;font-size:13px}
pre code{color:inherit}
.tablewrap{overflow-x:auto}
footer.site{border-top:1px solid var(--line);margin-top:48px;padding:22px 0;
  color:var(--mut);font-size:13px}
@media(max-width:820px){.layout{grid-template-columns:1fr;gap:0}
  .sidebar{border-right:0;border-bottom:1px solid var(--linesoft)}}
"""


def build_static_page(page: dict, pages: list[dict], css: str) -> str:
    desc = description_for(page)
    title = f"{page['h1']} · {SITE_NAME}"
    content = clean_content(page["html"])
    # Give wide tables their own scroll container so the page body never
    # scrolls sideways on a phone.
    content = re.sub(r"<table", '<div class="tablewrap"><table', content)
    content = re.sub(r"</table>", "</table></div>", content)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{absolute(page['slug'])}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{html.escape(page['h1'], quote=True)}">
<meta property="og:description" content="{html.escape(desc, quote=True)}">
<meta property="og:url" content="{absolute(page['slug'])}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{html.escape(page['h1'], quote=True)}">
<meta name="twitter:description" content="{html.escape(desc, quote=True)}">
{json_ld(page, desc)}
{FAVICON_LINKS}
{FONT_LINK}
<style>{css}{STATIC_CSS}</style>
{analytics()}
</head>
<body>
<header class="masthead"><div class="inner">
<a class="brand" href="./">pretium</a>
<span class="tag">{TAGLINE}</span>
<span class="spacer"></span>
<a href="{REPO_URL}">GitHub</a>
</div></header>
<div class="layout">
<nav class="sidebar" aria-label="Documentation">{nav_html(pages, page['slug'])}</nav>
<main>
<p class="appnote">This is the plain version of the page. The
<a href="./#/{page['slug']}">full documentation app</a> adds search, dark mode
and copy buttons.</p>
{content}
</main>
</div>
<footer class="site"><div class="layout" style="display:block">
pretium {TAGLINE}. <a href="{REPO_URL}">Source on GitHub</a>.
</div></footer>
</body>
</html>
"""


def build_index(bundle: str, pages: list[dict]) -> str:
    """The app, with the head a crawler reads and a no-JS route index."""
    home = next(p for p in pages if p["slug"] == "home")
    desc = (
        "Documentation for pretium, a deterministic market simulator with a "
        "real limit order book. Rust core, Python API, published realism limits."
    )
    site_ld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "pretium",
        "description": desc,
        "url": f"{BASE_URL}/",
        "applicationCategory": "DeveloperApplication",
        "operatingSystem": "Linux, macOS, Windows",
        "codeRepository": REPO_URL,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    head_extra = f"""<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{html.escape(desc, quote=True)}">
<link rel="canonical" href="{BASE_URL}/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{SITE_NAME}: {TAGLINE}">
<meta property="og:description" content="{html.escape(desc, quote=True)}">
<meta property="og:url" content="{BASE_URL}/">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{SITE_NAME}: {TAGLINE}">
<meta name="twitter:description" content="{html.escape(desc, quote=True)}">
<script type="application/ld+json">{json.dumps(site_ld, separators=(",", ":"))}</script>
{FAVICON_LINKS}
{spa_analytics()}
"""
    out = bundle.replace("<head>", "<head>\n" + head_extra, 1)
    out = out.replace("<html>", '<html lang="en">', 1)

    # Without JavaScript the app renders nothing. Give that reader, and any
    # crawler that does not execute the bundle, a real route index.
    links = "".join(
        f'<li><a href="{page_url(p["slug"])}">{html.escape(p["label"])}</a></li>'
        for p in pages
    )
    noscript = (
        "<noscript><div style=\"max-width:720px;margin:40px auto;padding:0 24px;"
        "font:15px/1.7 system-ui,sans-serif\">"
        f"<h1 style=\"font-size:24px\">{SITE_NAME}</h1>"
        f"<p>{html.escape(desc)}</p>"
        "<p>The documentation app needs JavaScript. Every page is also "
        "available as plain HTML:</p>"
        f"<ul>{links}</ul></div></noscript>"
    )
    return out.replace("<body>", "<body>\n" + noscript, 1)


def _last_content_change() -> str:
    """The date the published content last moved, as W3C YYYY-MM-DD.

    Taken from the last commit touching docs/ or this script rather than from
    the clock, so rebuilding without changing anything does not churn every
    lastmod and tell Google 24 pages changed when none did. Falls back to
    omitting the field, which is valid, if git is unavailable.
    """
    import subprocess

    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", "docs", __file__],
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def build_sitemap(pages: list[dict]) -> str:
    lastmod = _last_content_change()
    stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    urls = "".join(
        f"<url><loc>{absolute(p['slug'])}</loc>{stamp}"
        f"<priority>{'1.0' if p['slug'] == 'home' else '0.8'}</priority></url>"
        for p in pages
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>\n"
    )


def build_robots() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"


def main() -> None:
    bundle = read_bundle()
    doc = inner_document(bundle)
    css = design_css(doc)
    pages = split_pages(doc)

    OUT.mkdir(exist_ok=True)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "index.html").write_text(build_index(bundle, pages), encoding="utf-8")
    (OUT / "sitemap.xml").write_text(build_sitemap(pages), encoding="utf-8")
    (OUT / "robots.txt").write_text(build_robots(), encoding="utf-8")

    written = 0
    for page in pages:
        if page["slug"] == "home":
            continue
        (OUT / f"{page['slug']}.html").write_text(
            build_static_page(page, pages, css), encoding="utf-8"
        )
        written += 1

    print(f"index.html          {len((OUT / 'index.html').read_bytes()):>9,} bytes")
    print(f"static pages        {written}")
    print(f"sitemap entries     {len(pages)}")
    print(f"analytics           {GA_MEASUREMENT_ID or 'not configured'}")


if __name__ == "__main__":
    main()
