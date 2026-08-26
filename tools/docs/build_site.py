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
        "Measured on the shipped preset, eight of the ten statistics have "
        "their 10th-to-90th percentile range across seeds crossing a band "
        "edge.",
        "Measured on the shipped preset, nine of the fourteen statistics "
        "have their 10th-to-90th percentile range across seeds crossing a "
        "band edge.",
    ),
    (
        '<code style="font-size:13px">abs_return_acf1</code> reads a median '
        "of 0.141 against a ceiling of 0.22, with a p90 of 0.426 and an "
        "across-seed standard deviation of 0.170, larger than the median "
        "itself.",
        '<code style="font-size:13px">abs_return_acf1</code> reads a median '
        "of 0.0994 against a ceiling of 0.22, with a p90 of 0.4063 and an "
        "across-seed standard deviation of 0.1467, larger than the median "
        "itself.",
    ),
    (
        'So "9/10 in band" describes the middle of thirty runs. Your one run '
        "can easily look worse. Read the spread before relying on one seed:",
        'So "14/14 in band" describes the middle of thirty runs, and the word '
        "certified describes that median rather than your run. It does not "
        "promise a typical single seed is in band on all fourteen; the spread "
        "below says most are not. Read it before relying on one seed:",
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
    # The presets page's own count of itself. "Three presets ship" was true
    # when the bundle was authored and the table beneath it now runs to ten,
    # so the sentence contradicted the rows immediately under it.
    (
        '<code style="font-size:13px">pt-v3</code> is the current default; '
        '<code style="font-size:13px">pt-v1</code> and '
        '<code style="font-size:13px">pt-v2</code> stay selectable and '
        "bit-reproducing forever.",
        '<code style="font-size:13px">pt-v10</code> is the current default, '
        'and every earlier preset from <code style="font-size:13px">pt-v1'
        '</code> to <code style="font-size:13px">pt-v9</code> stays '
        "selectable and reproduces bit for bit. What that guarantee covers is "
        "the PRESET: naming one pins the coefficient set and the market it "
        "produces on a given package version, and a run is only fully "
        "specified by (package version, model, universe fingerprint, seed). "
        'See <a href="#/install">Install and Versioning</a> for what a '
        "package-version change can and cannot move.",
    ),
    (
        "Three presets ship. All three stay selectable and bit-reproducing "
        "forever.",
        "Ten presets ship. pt-v10 is the default; the other nine are "
        "selectable by name and none of them has ever been un-shipped.",
    ),
    (
        "Built for long horizons. It halves the dual-horizon objective",
        "Built to INVESTIGATE long-horizon realism, which is not the same as "
        "being certified for long-horizon strategy studies: nothing in this "
        "project is certified past 252 days. It halves the dual-horizon "
        "objective",
    ),
    # The volume row is in band at one year since 0.2.0, so the calibration
    # example can no longer call it unreachable. Gap 1 on the realism page
    # withdrew that claim and this sentence had not caught up.
    (
        "The structurally unreachable statistic rides along in every result "
        "as a standing falsification verdict, and stays out of the loss, "
        "because an optimiser pointed at an unreachable target distorts every "
        "other parameter chasing it.",
        "The statistics outside the objective ride along in every result as a "
        "standing falsification verdict, and stay out of the loss, because an "
        "optimiser is only allowed to chase what the project decided it may "
        "chase. That is a policy about the search, not a claim that the rows "
        "are unreachable: the volume-change row was called unreachable until "
        "0.2.0, and pt-v10 holds it at one year.",
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
    (
        '<h2 style="font-size:21px;margin:46px 0 10px">Build Targets</h2>',
        '<h2 style="font-size:21px;margin:46px 0 10px">Using it from Rust</h2>\n'
        '        <p style="color:var(--mut);font-size:14px">The engine is a '
        'Rust crate and is published on crates.io as '
        '<code style="font-size:12.5px">pretium</code>, the same code the '
        'Python wheels are built from.</p>\n'
        '        <pre style="padding:13px 16px;overflow-x:auto"><code '
        'data-lang="sh" style="font:400 13px/1.7 var(--font-mono);'
        'color:var(--codefg)">cargo add pretium</code></pre>\n'
        '        <p style="color:var(--mut);font-size:14px">Docs at '
        '<a href="https://docs.rs/pretium">docs.rs/pretium</a>. The crate '
        'carries the engine, the presets and the determinism guarantees; the '
        'Python package adds the harness, the evaluation layer and the '
        'notebooks.</p>\n'
        '        <h2 style="font-size:21px;margin:46px 0 10px">Build Targets</h2>',
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
    doc = apply_scenarios_fixes(doc)
    doc = apply_internals_fixes(doc)
    doc = apply_factors_fixes(doc)
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


#: The held-out axes, re-measured on pt-v10 for 0.2.0 at the same thirty-seed
#: resolution as the certification itself (`docs/realism-envelope.md`, "The
#: claim survives the axes it was not fitted to"). The bundle carries the
#: pt-v3-era 9/10 version of this table.
#:
#: Not generated from `pretium.envelope` like the two tables above it,
#: because the module carries the certified panel and not the held-out runs.
#: A generated table cannot drift; this one can, so it names its provenance.
HELD_OUT_ROWS = (
    '<sc-raw-tbody style="font-family:var(--font-mono);font-size:12.5px">\n'
    "            <sc-raw-tr><sc-raw-td>training seeds (101-130), 40 names"
    '</sc-raw-td><sc-raw-td style="text-align:right">14/14 in band'
    '</sc-raw-td><sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
    "</sc-raw-tr>\n"
    "            <sc-raw-tr><sc-raw-td>held-out seeds (1-30), 40 names"
    '</sc-raw-td><sc-raw-td style="text-align:right">14/14 in band'
    '</sc-raw-td><sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
    "</sc-raw-tr>\n"
    "            <sc-raw-tr><sc-raw-td>held-out universe (60 names, seed 909)"
    '</sc-raw-td><sc-raw-td style="text-align:right">14/14 in band'
    '</sc-raw-td><sc-raw-td style="text-align:right">0.0000</sc-raw-td>'
    "</sc-raw-tr>\n          </sc-raw-tbody>"
)


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
        # Provenance. The three method rows at the top of the page name the
        # preset each figure came from, and two of them still said pt-v3
        # while the table below them was regenerated for pt-v10. A
        # provenance line that names the wrong preset is worse than none.
        (
            "certification panel</sc-raw-td><sc-raw-td>pt-v3, 30 seeds, 40 "
            "instruments, 252 days. The figures in the table below, and the "
            'only ones the word "certified" applies to.</sc-raw-td>',
            "certification panel</sc-raw-td><sc-raw-td>pt-v10, 30 seeds, 40 "
            "instruments, 252 days, every figure the MEDIAN across those "
            "seeds. The figures in the table below, and the only ones the "
            'word "certified" applies to. A median is not a typical run: '
            "read Seed-to-Seed Variation before quoting one.</sc-raw-td>",
        ),
        (
            "six-seed panel</sc-raw-td><sc-raw-td>pt-v3, sim seeds 1 to 6,",
            "six-seed panel</sc-raw-td><sc-raw-td>pt-v10, sim seeds 1 to 6,",
        ),
        (
            "One seed, a smaller roster, 120 days, a macro field held fixed. "
            'What the <a href="#/scenarios">scenario</a> figures come from. '
            "Good for comparing one pin against another, and not comparable "
            "to either panel.",
            "One seed, a smaller roster, 120 days, a macro field held fixed. "
            'What the <a href="#/scenarios">scenario</a> figures come from. '
            "Good for comparing one pin against another, and not comparable "
            "to either panel. In particular, the calm-to-crisis volatility "
            "ratio a pair of pinned runs implies is NOT the crisis lever in "
            "gap 5: that one is measured on the certified 40-name roster "
            "over 252 days at thirty seeds, from VIX 5 to VIX 65. Three "
            "numbers on this site describe how violent a crisis is, and they "
            "are three different measurements.",
        ),
        (
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v3</code> preset puts nine of the ten measured statistics in "
            "band. The tenth fails structurally and is named, in every result "
            "the library produces.</p>",
            "<p>At a 252-day horizon the shipped <code style=\"font-size:13px\">"
            "pt-v10</code> preset puts all fourteen measured statistics in "
            "band, on thirty calibration seeds and on a held-out 60-name "
            "universe measured at the same resolution. It is the first preset "
            "in the project with no miss at this horizon. Every figure below "
            "is the median of those thirty seeds, and that median is what the "
            "word certified describes: it is not a promise about your one "
            "run, which the spread further down prices.</p>",
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
            "4 · The endogenous economy cannot reach its own MACRO crisis "
            "regimes",
        ),
        (
            "<p>Over 504-day windows real markets show excess kurtosis of 7.1 "
            "to 22. The model shows 5.2. The 252-day band's floor of 1.6 is "
            "wide enough that this reads as comfortably in band on every "
            "252-day certificate, which is why it went unnoticed: nothing was "
            "measuring kurtosis where it fails.</p>",
            "<p>Two kinds of crisis, and the answer differs. A VOLATILITY "
            "crisis is endogenous since 0.2.0: the shipped preset's own VIX "
            "crosses its crisis threshold on 10.2% of days against a real "
            "12.5%, where the previous default reached it on none, so a "
            "panic is something this market produces and not only something "
            "a scenario drives. A MACRO crisis is not. Left to itself the "
            "macro state stays in a moderate band: endogenous inflation "
            "peaks at 4.06% to 4.11% over five seeds and five years, against "
            "a clamp of 6.0% and a US CPI that reached 9.1% in June 2022, "
            "and the central bank's own crisis cadence hangs off an "
            "inflation rate the economy never gets to. The cause is "
            "dispersion rather than persistence. This slot used to carry a "
            "thin-tails gap, retired at 0.2.0: two-year excess kurtosis "
            "moved from 5.23, under its band, to 8.26, inside it.</p>",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> tail-risk or "
            "VaR calibration at multi-year horizons. At the certified 252-day "
            "horizon, kurtosis is in band.</p>",
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> studying an "
            "inflation regime or a policy crisis from the endogenous economy "
            "alone. Drive one with a scenario instead. A volatility crisis "
            "does not need one.</p>",
        ),
        (
            '<p style="margin:14px 0 0">Five of the ten statistics were live '
            "calibration targets, so an in-band verdict on those is partly "
            "the tuning meeting its target. The held-out rows are what make "
            "the claim more than that.</p>",
            '<p style="margin:14px 0 0">Five of the fourteen statistics were '
            "live calibration targets, so an in-band verdict on those five is "
            "the tuning meeting its target rather than an independent "
            "success. The held-out rows test whether the fit generalises; "
            "they do not make those five independent.</p>\n        "
            '<p style="margin:14px 0 0">And read "held out" narrowly, because '
            "it is doing less work than the phrase suggests. It means "
            "simulation seeds the calibration never drew and a roster it "
            "never ran. It does not mean withheld market data: the bands come "
            "from real-market windows, and the same bands were used to tune "
            "against and to grade against, with no empirical train and test "
            "split behind them. The held-out universe is another "
            "<code style=\"font-size:12.5px\">Universe.random()</code> draw "
            "from the same generator, so it varies the names and not the "
            "shape of the roster. Gap 6 varies the shape, and the count "
            "drops.</p>",
        ),
        # The two worked examples' output blocks. Both were captured before
        # the era boundary and both carry a badge saying they were measured,
        # which makes a stale block worse than an unlabelled one. Their text
        # is what the installed 0.2.0 package prints.
        (
            "OUTSIDE the envelope\n  - horizon 504d exceeds the certified "
            "252d. At 504 days the model holds\n    5 of 10 against "
            "horizon-matched bands: abs_return_acf1 0.289 against\n    "
            "(0.04, 0.22), abs_return_acf5 0.152 against (0.02, 0.10)\n  - "
            "excess_kurtosis reads 5.23 at 504 days against a horizon-matched "
            "band\n    of (7.1, 22.0) -- the tails are too thin where you are "
            "measuring\n  - abs_return_acf20 depends on the decay shape, "
            "which is a mechanism gap:\n    log-log slope -0.953 against real "
            "markets' -0.436, and the curve is\n    negative by lag 30 where "
            "real markets stay positive to lag 60",
            "OUTSIDE the envelope\n  - horizon 504d exceeds the certified "
            "252d. At 504 days the model holds\n    13 of 14 against "
            "horizon-matched bands, missing only\n    volume_change_acf1 at "
            "-0.3156 against (-0.29, -0.21). Beyond 504\n    days nothing "
            "has been measured at all\n  - abs_return_acf20 depends on the "
            "decay shape, which is a mechanism gap:\n    log-log slope -0.953 "
            "against real markets' -0.436, and the curve is\n    negative by "
            "lag 30 where real markets stay positive to lag 60\n  ? "
            "excess_kurtosis reads 8.26 at 504 days against (7.1, 22.0): "
            "inside\n    it, but about 0.3 seed-sd above the floor, so a tail "
            "study at this\n    horizon is reading the low edge of the band",
        ),
        # The six-month worked example. Every figure in it is now a real
        # measurement, so the block's badge stops saying MIXED: the spread
        # columns and the strategy line were the composed part, and both were
        # re-run. Measured on pretium 0.2.0, pt-v10, the page's own code:
        # Universe.random(40, seed=111), sim seeds 1 to 30, 126 days.
        (
            '<span title="Medians and verdict text are quoted from the '
            "shipped envelope; the spread columns and strategy numbers were "
            'composed for the example." style="font:500 9.5px/1 '
            "var(--font-mono);letter-spacing:0.07em;border-radius:5px;"
            "padding:3px 6px;margin-left:8px;color:var(--codemut);border:1px "
            'solid var(--codeline)">MIXED, SEE NOTE</span></div>\n          '
            '<pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            'color:var(--codemut)"># 1.',
            '<span title="Captured from the run named beside this block." '
            'style="font:500 9.5px/1 var(--font-mono);letter-spacing:0.07em;'
            "border-radius:5px;padding:3px 6px;margin-left:8px;color:"
            'var(--tks);border:1px solid var(--tks)">MEASURED</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            'color:var(--codemut)"># 1.',
        ),
        (
            "# 1.\nTrue\ninside the envelope\n  - horizon 126d is within the "
            "certified 252d, and no named statistic\n    meets a measured "
            "gap\n  ? return_acf1 is in band at the certified horizon (0.0375 "
            "in\n    (-0.08, 0.06)) -- but that is a median across 30 seeds; "
            "check\n    `intervals` for the spread before relying on one "
            "seed\n  ? abs_return_acf1 is in band at the certified horizon "
            "(0.1413 in\n    (0.02, 0.22)) -- but that is a median across 30 "
            "seeds; check\n    `intervals` for the spread before relying on "
            "one seed",
            "# 1.\nTrue\ninside the envelope for the statistics you named\n"
            "  - horizon 126d is within the certified 252d, and no named "
            "statistic\n    meets a measured gap\n  ? return_acf1 is in band "
            "at the certified horizon (0.0195 in\n    (-0.08, 0.06)) -- but "
            "that is a median across 30 seeds; check\n    `intervals` for the "
            "spread before relying on one seed\n  ? abs_return_acf1 is in "
            "band at the certified horizon (0.0994 in\n    (0.02, 0.22)) -- "
            "but that is a median across 30 seeds; check\n    `intervals` for "
            "the spread before relying on one seed",
        ),
        (
            "annualised_vol_pct         24.0972   19.8410   30.1174    3.9026"
            "    (15.0, 36.0)  in band\nexcess_kurtosis             2.5305    "
            "1.7118    5.4402    1.4771     (1.6, 41.0)  in band\n"
            "return_acf1                 0.0375    0.0198    0.0631    0.0164"
            "   (-0.08, 0.06)  in band on the median; p10-p90 crosses an edge"
            "\nabs_return_acf1             0.1413    0.0492    0.4260    "
            "0.1701     (0.02, 0.22)  in band on the median; p10-p90 crosses "
            "an edge\n...\nvolume_change_acf1         -0.4598   -0.4712   "
            "-0.4441    0.0098  (-0.32, -0.20)  OUT 13.7 sd\n\n# 3.\n"
            "4.87 48719.20",
            "annualised_vol_pct         30.1974   26.5765   37.8424    7.1275"
            "     (15.0, 36.0)  in band on the median; p10-p90 crosses an edge"
            "\nexcess_kurtosis             6.9315    4.5513   11.3102    "
            "2.9010      (1.6, 41.0)  in band\n"
            "return_acf1                 0.0057   -0.0299    0.1492    0.0704"
            "    (-0.08, 0.06)  in band on the median; p10-p90 crosses an edge"
            "\nabs_return_acf1             0.0713    0.0280    0.1793    "
            "0.0941     (0.02, 0.22)  in band (an extreme seed crosses)\n"
            "abs_return_acf5             0.0190   -0.0146    0.1264    0.0555"
            "     (0.02, 0.09)  OUT 0.0 sd\n...\n"
            "volume_change_acf1         -0.3160   -0.3441   -0.2688    0.0282"
            "    (-0.32, -0.2)  in band on the median; p10-p90 crosses an edge"
            "\ncorr_persistence_acf1    unmeasured\n\n# 3.\n-52.87 -11.89",
        ),
        (
            "In that block the medians, bands, verdicts and the check reasons "
            "come from the shipped envelope. The p10, p90 and sd columns and "
            "the step 3 numbers are composed to show the shape of the report.",
            "Every figure in that block is a measurement: pretium 0.2.0, "
            "pt-v10, the code above it verbatim, sim seeds 1 to 30 over "
            "Universe.random(40, seed=111) at 126 days. Rows between "
            "abs_return_acf5 and volume_change_acf1 are elided for width.",
        ),
        (
            "Step 1 says yes, with two warnings. Step 2 shows why they were "
            "worth printing: <code style=\"font-size:13px\">abs_return_acf1"
            "</code> has a median of 0.141 inside its band and a p90 of 0.426, "
            "well above the ceiling of 0.22. A single seed can easily land out "
            "of band on a statistic that looks comfortable in the summary. "
            "That is what decides how many seeds step 3 needs.",
            "Step 1 says yes for the two statistics it was asked about, which "
            "is the whole of what it says. Step 2 is why that scope matters: "
            "at this horizon "
            "<code style=\"font-size:13px\">abs_return_acf5</code> sits on its "
            "floor and reads out, and "
            "<code style=\"font-size:13px\">corr_persistence_acf1</code> "
            "cannot be measured at all in 126 days, because twelve 21-day "
            "windows are not enough to autocorrelate. Neither was named in "
            "step 1, so neither appears in its verdict. The named statistics "
            "carry their own spread besides: "
            "<code style=\"font-size:13px\">return_acf1</code> has a median of "
            "0.0057 inside its band and a p90 of 0.149, well above the "
            "ceiling of 0.06. A single seed can easily land out of band on a "
            "statistic that looks comfortable in the summary. That is what "
            "decides how many seeds step 3 needs.",
        ),
        # The machine-readable companion carries the preset, not a digest, so
        # the check the page told a reader to run could not be run. The digest
        # it quoted was the pt-v3-era baseline besides.
        (
            "Machine-readable companion: envelope.json carries the "
            "per-statistic detail, the gap list and the provenance. Both "
            "measurement runs report known-answer digest 992ef95d…dc185e3. If "
            "the digest in envelope.json does not match your installed wheel, "
            "this page describes a different model than the one you are "
            "running.",
            "Machine-readable companion: envelope.json carries the "
            "per-statistic detail, the gap list and the preset the figures "
            "describe. The cross-platform determinism baseline for this era "
            "is known-answer v10, digest 4e22d5a6…860378, which the release "
            "workflow checks inside every wheel before it uploads. If "
            "pretium.model_preset()[\"name\"] is not the preset named in "
            "envelope.json, this page describes a different model than the "
            "one you are running.",
        ),
        # What the panel does and does not certify. The book is a real
        # mechanism rather than a slippage coefficient, and none of that is
        # an empirical microstructure claim: the fourteen statistics are
        # daily price-process and volume properties.
        (
            "<li>Strategy evaluation up to about one year, where the edge "
            "depends on statistics the panel certifies</li>",
            "<li>Strategy evaluation up to about one year, where the edge "
            "depends on statistics the panel certifies, which are daily "
            "price-process, correlation and volume properties</li>",
        ),
        (
            "<li>Any claim that simulated performance forecasts live "
            "returns</li>",
            "<li>Any claim that simulated performance forecasts live "
            "returns</li>\n              "
            "<li>Execution-sensitive conclusions that rest on the order "
            "book's own realism, which nothing here certifies</li>",
        ),
        (
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">Why There Is No "
            "Realism Score</h2>",
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">What the Panel "
            "Does Not Cover</h2>\n        "
            "<p>All fourteen statistics are daily properties of prices, "
            "correlations and volume. Matching is a real limit order book "
            "with price-time priority rather than a slippage coefficient, and "
            "impact is emergent, which is a statement about the MECHANISM. It "
            "is not a validation claim: nothing on this page measures depth "
            "shape, cancellation intensity, queue dynamics, the intraday "
            "spread distribution, impact decay, resiliency or order-sign "
            "autocorrelation against real market data. So a strategy whose "
            "edge or cost lives inside the book is leaning on a part of this "
            "simulator the envelope does not reach, however comfortably its "
            "returns sit in band. Execution cost here is measured and "
            "self-consistent, and it is not certified realistic.</p>\n        "
            "<h2 style=\"font-size:21px;margin:46px 0 10px\">Why There Is No "
            "Realism Score</h2>",
        ),
        # Gap 5. The lever moved from 3.07x to 5.05x at the era boundary and
        # this paragraph did not, so the page said "roughly half" while the
        # release notes said four fifths of the same quantity.
        (
            "measured on the 40-name reference roster: about 3.1 times here "
            "against real markets' 6.16 (17.2% annualised below VIX 12 "
            "against 106.1% above VIX 45). Roughly half. That is not the "
            "same quantity as the 82% against 62% in a single pinned 120-day "
            "run elsewhere on this site, which is one seed on a smaller "
            "roster over a shorter window.",
            "measured from VIX 5 to VIX 65 on the 40-name reference roster "
            "over 252 days at thirty seeds: 5.05 times here against real "
            "markets' 6.16 (17.2% annualised below VIX 12 against 106.1% "
            "above VIX 45). About four fifths, up from 3.07 times at the "
            "previous default. That is not the same quantity as the pair of "
            "pinned 120-day runs elsewhere on this site, which is one seed "
            "on a smaller roster over a shorter window and reads about 2.8 "
            "times from VIX 15 to VIX 45. Three numbers on this site "
            "describe how violent a crisis is; check which one you are "
            "reading before quoting it.",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> sizing a "
            "scenario's impact rather than detecting it. Use scenarios to ask "
            "whether a strategy breaks. A crisis here is about half as "
            "violent as a real one and arrives more slowly, so surviving one "
            "is a weaker test than the label suggests.</p>",
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> sizing a "
            "scenario's impact rather than detecting it. Use scenarios to ask "
            "whether a strategy breaks. A crisis here is about four fifths as "
            "violent as a real one and arrives more slowly, so surviving one "
            "is a weaker test than the label suggests.</p>",
        ),
        # Gap 3. "No parameter setting turns one slope into the other" is a
        # statement about this model class. Gap 1 made the stronger claim and
        # had to withdraw it, so the weaker one says out loud which it is.
        (
            "The model fits −0.953, about 2.2 times steeper. This is a "
            "mechanism gap: no parameter setting turns one slope into the "
            "other. It was tried, and a two-component variance mixture lands "
            "lag 20 while getting lag 60 wrong in both directions at once.",
            "The model fits −0.953, about 2.2 times steeper. This is a "
            "mechanism gap: no setting of this model's parameters turns one "
            "slope into the other, because the process is built from "
            "exponentials and a sum of exponentials is not a power law. It "
            "was tried, and a two-component variance mixture lands lag 20 "
            "while getting lag 60 wrong in both directions at once. Read "
            "that as a limit of this model class rather than of the project: "
            "gap 1 carried the stronger claim, that its row was structurally "
            "unreachable, and a new mechanism reached it. A gap is closed by "
            "adding mechanism, not by tuning what is already here.",
        ),
        # Gap 6. Round-robin over twelve sectors is not five names each, and
        # the three counts below it are a pt-v3-era ten-statistic panel.
        (
            "<p><code style=\"font-size:12.5px\">Universe.random()</code> places "
            "exactly five names in each of twelve sectors. No real index is "
            "balanced that way. The S&amp;P is roughly a third technology, "
            "and the Nasdaq more so. Varying only sector composition:</p>",
            "<p><code style=\"font-size:12.5px\">Universe.random()</code> assigns "
            "sectors round-robin over the twelve in "
            "<code style=\"font-size:12.5px\">sectors.SECTORS</code>, so a "
            "roster is as close to balanced as its size allows: the certified "
            "40 names put four in each of four sectors and three in each of "
            "the other eight. No real index is balanced that way. The "
            "S&amp;P is roughly a third technology, and the Nasdaq more so. "
            "Varying only sector composition, and measured on pt-v3 against "
            "the TEN-statistic panel of the time rather than re-measured on "
            "pt-v10, because what this establishes is a property of the "
            "roster rather than of a preset:</p>",
        ),
        (
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> inheriting "
            "these numbers for a sector-concentrated roster. Re-measure the "
            "panel on your own universe: <code style=\"font-size:12.5px\">"
            "facts.measure()</code> takes it directly, and "
            "<code style=\"font-size:12.5px\">envelope.intervals()</code> "
            "reports the spread.</p>",
            "<p>This is also the limit of the held-out universe above. That "
            "roster is another draw from the same generator, so it varies the "
            "names and not the shape; this table varies the shape, and the "
            "count drops.</p>\n            "
            "<p style=\"color:var(--fg)\"><strong>Forbids:</strong> inheriting "
            "these numbers for a sector-concentrated roster. Re-measure the "
            "panel on your own universe: <code style=\"font-size:12.5px\">"
            "facts.measure()</code> takes it directly, and "
            "<code style=\"font-size:12.5px\">envelope.intervals()</code> "
            "reports the spread.</p>",
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
    # The held-out table. The certified table above it was regenerated for
    # pt-v10 and this one was not, so the page claimed fourteen of fourteen
    # in one paragraph and nine of ten three lines later. Both counts were
    # real; they were two generations of the panel printed side by side.
    cut("A model graded on the same thirty seeds", "<sc-raw-tbody",
        "</sc-raw-tbody>", HELD_OUT_ROWS)
    return doc


def apply_scenarios_fixes(doc: str) -> str:
    """Re-measure the scenarios page on the shipped preset.

    Every figure on this page came from a run, and every one of those runs
    predates the 2026-08-26 era boundary that changed what a VIX pin does.
    The page also carried a third crisis-intensity number, so a reader met
    a pinned 59%-to-107% ratio here, a 3.1x steady-state lever on the
    realism page and a 5.05x lever in the release notes, with nothing saying
    they are three different measurements.

    Re-measured on the installed 0.2.0 package by the recipes the page
    states: volatility and the crisis pair over `Universe.random(20,
    seed=11)`, spreads and correlation over `Universe.random(25, seed=11)`,
    120 days, sim seed 3, pinned through the scenario API; the rate-shock
    table over `Universe.random(20, seed=4)`, `reference_agents(seed=3)`,
    sim seed 7, 20 days. Each replacement asserts.
    """
    swaps = [
        # The rate-shock table.
        (
            "<sc-raw-tr><sc-raw-td>buy_and_hold</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22127.16%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u221210.75%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.59</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>momentum</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22121.17%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.52%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22122.36</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>oracle</sc-raw-td>"
            '<sc-raw-td style="text-align:right">+11.11%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">+8.70%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22122.41</sc-raw-td>'
            "</sc-raw-tr>",
            "<sc-raw-tr><sc-raw-td>buy_and_hold</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22125.63%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22128.76%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22123.13</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>momentum</sc-raw-td>"
            '<sc-raw-td style="text-align:right">\u22127.88%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22129.74%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22121.86</sc-raw-td>'
            "</sc-raw-tr>\n            "
            "<sc-raw-tr><sc-raw-td>oracle</sc-raw-td>"
            '<sc-raw-td style="text-align:right">+14.98%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">+12.99%</sc-raw-td>'
            '<sc-raw-td style="text-align:right">\u22121.99</sc-raw-td>'
            "</sc-raw-tr>",
        ),
        (
            "Buy-and-hold is long only, holds through the repricing, and "
            "loses the most. Momentum and the Oracle trade around it and "
            "each give up about two and a half points. The sizes belong to "
            "the seed: across sim seeds 5 to 9 buy-and-hold gives up 3.4 to "
            "4.7 points every time, while momentum's give-up spans "
            "\u22125.1 to +0.5. Run more than one seed before you quote a "
            "size.",
            "Buy-and-hold is long only, holds through the repricing, and "
            "gives up the most. Momentum and the Oracle trade around it and "
            "each give up about two points. The sizes belong to the seed: "
            "across sim seeds 5 to 9 buy-and-hold gives up 3.0 to 5.0 points "
            "every time, while momentum's give-up spans \u22121.9 to +0.5. "
            "Run more than one seed before you quote a size.",
        ),
        # What a VIX pin actually does now.
        (
            "Realised annual volatility: 49% at VIX 5, 59% at 15, 107% at "
            "45, 124% at 65.",
            "Realised annual volatility: 31% at VIX 5, 37% at 15, 105% at "
            "45, 126% at 65.",
        ),
        (
            "Mean quoted spread goes from 11.5 bps at VIX 15 to 25.9 bps at "
            "VIX 65,",
            "Mean quoted spread goes from 11.7 bps at VIX 15 to 21.5 bps at "
            "VIX 65,",
        ),
        (
            "mean pairwise correlation reads +0.27 calm, +0.68 at VIX 45, "
            "+0.76 at 65.",
            "mean pairwise correlation reads +0.20 calm, +0.62 at VIX 45, "
            "+0.68 at 65.",
        ),
        (
            "pinned through the scenario API. What VIX never moves is a "
            "single name's own noise. These figures are much higher than the "
            '24.1% on <a href="#/trust">Realism and Limits</a> because a '
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. Compare pins "
            "against each other, and take the certified number from an "
            "unpinned run.",
            "pinned through the scenario API. Since the 2026-08-26 era "
            "boundary VIX also moves a name's own variance, through "
            "<code style=\"font-size:12px\">garch_vix_coupling</code>; before "
            "it, VIX sized only the shared factor. These figures sit above "
            'the 31.5% on <a href="#/trust">Realism and Limits</a> because a '
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. Compare pins "
            "against each other, and take the certified number from an "
            "unpinned run. The ratio between two pins is NOT the crisis "
            "lever the realism page and the release notes quote: that one "
            "runs VIX 5 to VIX 65 on the certified roster over 252 days at "
            "thirty seeds and reads 5.05x. Three numbers, three "
            "measurements.",
        ),
        # The liquidity-crisis pair, in the comment the example prints.
        (
            "# 61.76% calm, 82.16% crisis",
            "# 39.98% calm, 67.68% crisis",
        ),
        # The whole-crisis-study block. Every line in it is now a real run,
        # including the two the note called composed, so the badge moves.
        (
            '<span title="Medians and verdict text are quoted from the '
            "shipped envelope; the spread columns and strategy numbers were "
            'composed for the example." style="font:500 9.5px/1 '
            "var(--font-mono);letter-spacing:0.07em;border-radius:5px;"
            "padding:3px 6px;margin-left:8px;color:var(--codemut);border:1px "
            'solid var(--codeline)">MIXED, SEE NOTE</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            "color:var(--codemut)\">{'day': 0, 'vix': 18.0}",
            '<span title="Captured from the run named beside this block." '
            'style="font:500 9.5px/1 var(--font-mono);letter-spacing:0.07em;'
            "border-radius:5px;padding:3px 6px;margin-left:8px;color:"
            'var(--tks);border:1px solid var(--tks)">MEASURED</span></div>\n'
            '          <pre style="padding:13px 16px;overflow-x:auto"><code '
            'data-lang="txt" style="font:400 12.5px/1.75 var(--font-mono);'
            "color:var(--codemut)\">{'day': 0, 'vix': 18.0}",
        ),
        (
            "{'day': 1, 'vix': 18.0}\n...\n{'day': 20, 'vix': 18.0}\n"
            "{'day': 21, 'vix': 19.03}\n{'day': 22, 'vix': 20.07}\n"
            "{'day': 23, 'vix': 21.10}\n{'day': 24, 'vix': 22.13}\n\n"
            "inside the envelope\n  - horizon 120d is within the certified "
            "252d, and no named statistic\n    meets a measured gap\n  ? "
            "annualised_vol_pct is in band at the certified horizon (24.0972 "
            "in\n    (15.0, 36.0)) -- but that is a median across 30 seeds; "
            "check\n    `intervals` for the spread before relying on one "
            "seed\n  ? cross_sectional_corr is in band at the certified "
            "horizon (0.2558 in\n    (0.08, 0.56)) -- but that is a median "
            "across 30 seeds; check\n    `intervals` for the spread before "
            "relying on one seed\n\n(82.16, 61.76)      # annualised vol %, "
            "crisis against calm\n0.6781              # cross-sectional "
            "correlation under the crisis\n\ncalm    0.1142   -0.0871\n"
            "crisis  0.0316   -0.2043\n\n6.06 11.69          # shortfall "
            "bps, calm then VIX 45",
            "{'day': 1, 'vix': 18.0}\n...\n{'day': 19, 'vix': 18.0}\n"
            "{'day': 20, 'vix': 80.0}\n{'day': 21, 'vix': 78.97}\n"
            "{'day': 22, 'vix': 77.93}\n{'day': 23, 'vix': 76.90}\n"
            "{'day': 24, 'vix': 75.87}\n\n"
            "inside the envelope for the statistics you named\n  - horizon "
            "120d is within the certified 252d, and no named statistic\n    "
            "meets a measured gap\n  ? annualised_vol_pct is in band at the "
            "certified horizon (31.4632 in\n    (15.0, 36.0)) -- but that is "
            "a median across 30 seeds; check\n    `intervals` for the spread "
            "before relying on one seed\n  ? cross_sectional_corr is in band "
            "at the certified horizon (0.3063 in\n    (0.08, 0.56)) -- but "
            "that is a median across 30 seeds; check\n    `intervals` for the "
            "spread before relying on one seed\n\n(67.68, 39.98)      # "
            "annualised vol %, crisis against calm\n0.6235              # "
            "cross-sectional correlation under the crisis\n\n"
            "calm   -42.9347  11.9232\ncrisis -56.6869  95.0928\n\n"
            "6.06 11.69          # shortfall bps, calm then VIX 45",
        ),
        (
            "In that block the volatility pair and the two shortfall figures "
            "are measured runs, cited in the prose below. The scenario table "
            "rows and the two strategy lines are composed to show the shape "
            "of the output.",
            "Every line in that block is a measurement on pretium 0.2.0 "
            "under pt-v10, from the code above it. The scenario rows are "
            "elided in the middle and rounded to two decimals; nothing else "
            "is edited. Note what the path actually does: "
            "<code style=\"font-size:12.5px\">vix_shock</code> puts the peak "
            "on day <code style=\"font-size:12.5px\">at</code> and decays it "
            "over the window, rather than ramping up to it.",
        ),
        (
            "Volatility goes from 62% to 82%, and correlation climbs with "
            "it.",
            "Volatility goes from 40% to 68%, and correlation climbs with "
            "it, 0.49 to 0.62.",
        ),
        # The two "half as violent" lines, both quoting the pt-v3-era lever.
        (
            "And gap 5 on the realism page: scenario response is "
            "directional, not sized. A crisis here is about half as violent "
            "as a real one. Use scenarios to detect breakage, never to size "
            "losses.",
            "And gap 5 on the realism page: scenario response is "
            "directional, not sized. A crisis here is about four fifths as "
            "violent as a real one, 5.05x against 6.16x on the certified "
            "roster. Use scenarios to detect breakage, never to size losses.",
        ),
        (
            "Do not quote the sizes as real. A crisis here is roughly half "
            "as violent as a real one (gap 5). Read the direction and the "
            "ordering; leave the magnitudes alone.",
            "Do not quote the sizes as real. A crisis here is about four "
            "fifths as violent as a real one (gap 5), and the pinned pair "
            "above is a different measurement again. Read the direction and "
            "the ordering; leave the magnitudes alone.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a scenarios-page passage "
                     f"that apply_scenarios_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return doc


def apply_internals_fixes(doc: str) -> str:
    """Cross the "Under the Hood" page over the 0.2.0 era boundary.

    The page describes how the CURRENT default behaves, and it was written
    against pt-v3: it named pt-v3 as the shipped model, quoted that preset's
    momentum coefficient, and carried a pinned-VIX response measured before
    the era boundary rewired four of the mechanisms it describes. A page that
    explains the engine while quoting a superseded preset's numbers is a
    plausible and wrong explanation of what a reader is running.

    The replacement figures are measured on the installed 0.2.0 package by
    the recipe the page already states: annualised volatility over
    `Universe.random(20, seed=11)`, correlation over
    `Universe.random(25, seed=11)`, 120 days, sim seed 3, VIX pinned through
    the scenario API. Each replacement asserts.
    """
    swaps = [
        (
            "the shipped <code style=\"font-size:13px\">pt-v3</code> turns the "
            "term down to 0.0742 and measures +0.0375, inside the band.",
            "the shipped <code style=\"font-size:13px\">pt-v10</code> turns the "
            "term down to 0.0186 and measures +0.0195, inside the band.",
        ),
        (
            "On top sits a shared market factor with its own conditional "
            "variance, coupled to VIX. The factor's variance reverts to a "
            "target scaling with (VIX/15)\u00b2, so a pinned VIX moves "
            "realised volatility: 49% annualised at VIX 5, 59% at 15, 107% "
            "at 45, 124% at 65. Above VIX 25.5 the cross-section blends "
            "toward the factor, and mean pairwise correlation reads +0.27 "
            "calm, +0.68 at VIX 45, +0.76 at 65. Diversification fails under "
            "stress the way real crises make it fail.",
            "On top sits a shared market factor with its own conditional "
            "variance, coupled to VIX. The factor's variance reverts to a "
            "target scaling with (VIX/15)\u00b2, so a pinned VIX moves "
            "realised volatility: 31% annualised at VIX 5, 37% at 15, 105% "
            "at 45, 126% at 65. Above VIX 25.5 the cross-section blends "
            "toward the factor, and mean pairwise correlation reads +0.20 "
            "calm, +0.62 at VIX 45, +0.68 at 65. Diversification fails under "
            "stress the way real crises make it fail. Since the 2026-08-26 "
            "era boundary the coupling is not the factor's alone: a name's "
            "own GJR-GARCH variance follows the VIX too "
            "(<code style=\"font-size:13px\">garch_vix_coupling</code> 0.3), a "
            "quarter of sector variance follows it "
            "(<code style=\"font-size:13px\">sector_vix_coupling</code> 0.25), "
            "the VIX's own fear channel reads the day's index return rather "
            "than the closing minute, the volatility regimes come off the "
            "market instead of the business cycle, and the factor's variance "
            "clamp sits at 32x its baseline rather than 8x. Descriptions of "
            "these mechanics written for pt-v3 do not carry over.",
        ),
        (
            "Those levels are far above the 24.1% certified panel because a "
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252.",
            "Those levels sit above the certified panel's 31.5% because a "
            "pinned VIX is not a normal market: the pin drives the factor "
            "variance directly, and these runs use a smaller roster over 120 "
            "days rather than the certified 40 names over 252. The ratio "
            "between two of them is also not the crisis lever the realism "
            "page quotes, which is measured on the certified roster.",
        ),
        # The book is a mechanism claim. It is not an empirical
        # microstructure claim, and the page was inviting the second reading.
        (
            "Matching runs against a limit order book with price-time "
            "priority. You get queue position, partial fills, and a quoted "
            "spread that widens under stress. Impact is emergent: a large "
            "order pays worse prices because it consumed levels, and there is "
            "no slippage coefficient anywhere in the code.",
            "Matching runs against a limit order book with price-time "
            "priority. You get queue position, partial fills, and a quoted "
            "spread that widens under stress. Impact is emergent: a large "
            "order pays worse prices because it consumed levels, and there is "
            "no slippage coefficient anywhere in the code. Read that as a "
            "statement about the mechanism, not about calibration: the "
            "realism envelope measures daily price, correlation and volume "
            "statistics, and nothing in it grades depth shape, cancellation "
            "intensity, queue dynamics, the intraday spread distribution, "
            "impact decay, resiliency or order-sign autocorrelation against "
            "real market data. The book is real and it is not certified.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded an internals-page passage "
                     f"that apply_internals_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)
    return doc


def apply_factors_fixes(doc: str) -> str:
    """Give the components page the two columns the engine gained.

    The page was authored with seven components and still listed seven after
    the title was corrected to nine, which is worse than either: a reader
    counting the table found seven under a heading that said nine. Both
    additions are real columns of `truth()` and members of `Engine.FACTORS`.

    Its worked example was the sharper problem. It summed seven columns over
    a FIVE-day run and asserted a residual around 1e-16, which held only
    because five days is too short for a jump to fire. Measured on the same
    seed over 120 days, with 8 jump ticks, the seven-column sum is off by
    0.128 while the nine-column sum reads 5.0e-17. A check that passes by
    being too short to exercise the thing it checks is worse than no check.
    """
    swaps = [
        (
            "In order. The first three are the model\u2019s own dynamics; the "
            "last four are shocks.",
            "In order. The first three are the model\u2019s own dynamics, the "
            "next four are shocks, and the last two are discrete events the "
            "engine books after the tick chain.",
        ),
        (
            "Difference <code style=\"font-size:13px\">mispricing_s</code> "
            "across two ticks, add the seven columns, and the residual should "
            "be around 1e-16.",
            "Difference <code style=\"font-size:13px\">mispricing_s</code> "
            "across two ticks, add the nine columns, and the residual should "
            "be around 1e-16.",
        ),
        (
            "engine.run_days(5)\ntruth = pl.from_arrow(engine.truth())\n\n"
            'FACTORS = ["reversion", "momentum", "crowd_lean", "company_news",\n'
            '           "order_flow_impact", "short_squeeze_effect", "random_noise"]',
            "engine.run_days(120)   # long enough for a jump to fire\n"
            "truth = pl.from_arrow(engine.truth())\n\n"
            'FACTORS = ["reversion", "momentum", "crowd_lean", "company_news",\n'
            '           "order_flow_impact", "short_squeeze_effect", "random_noise",\n'
            '           "circuit_breaker", "jump"]                 # Engine.FACTORS',
        ),
        (
            "Where the residual is not zero, the mispricing clamp or a circuit "
            "breaker bound. That is worth seeing, and it would be invisible in "
            "a summary.",
            "Run it over five days instead of 120 and the seven-column version "
            "of this check also passes, because five days is too short for a "
            "jump to fire. On this seed over 120 days, with 8 jump ticks, "
            "seven columns are off by 0.128 and nine by 5.0e-17. Where a "
            "residual does remain, the mispricing clamp bound; the circuit "
            "breaker has had its own column since 2026-08-26 and is no longer "
            "part of it.",
        ),
    ]
    for old, new in swaps:
        if old not in doc:
            sys.exit("the design bundle reworded a components-page passage "
                     f"that apply_factors_fixes corrects: {old[:70]!r}")
        doc = doc.replace(old, new, 1)

    rows = (
        '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);'
        'font-size:12.5px">circuit_breaker</sc-raw-td><sc-raw-td>The session '
        'circuit breaker\u2019s own correction. When the model price leaves '
        'the allowed band the tick re-derives the state from the clamped '
        'price, and until 2026-08-26 that rewrite was booked to nobody, so on '
        'any day the breaker bound the columns did not reconstruct the move.'
        '</sc-raw-td></sc-raw-tr>\n            '
        '<sc-raw-tr><sc-raw-td style="font-family:var(--font-mono);'
        'font-size:12.5px">jump</sc-raw-td><sc-raw-td>The daily jump, applied '
        'after the tick loop rather than inside it, and recorded on the tick '
        'where it is first observed. Every preset from pt-v4 carries jumps, '
        'and without this column those days never reconstructed.'
        '</sc-raw-td></sc-raw-tr>'
    )
    # Anchored on a short unique phrase and the row end that follows it,
    # rather than on the whole row: the bundle uses typographic apostrophes
    # and matching them through two layers of escaping is how this function
    # failed the first time it was written.
    probe = "The idiosyncratic draw, scaled by the name"
    if probe not in doc:
        sys.exit("the components table's random_noise row was reworded; "
                 "apply_factors_fixes cannot append the two new rows")
    cut = doc.index("</sc-raw-tr>", doc.index(probe)) + len("</sc-raw-tr>")
    return doc[:cut] + "\n            " + rows + doc[cut:]

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
