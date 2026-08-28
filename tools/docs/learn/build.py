"""Build the pretium learning-path documentation site.

The design handoff ships twenty-five pages in a prototyping dialect. This
script turns them into a static site: one HTML file per page that reads
correctly with JavaScript switched off, one shared stylesheet, one shared
runtime, and two generated data modules.

    python tools/docs/learn/build.py [--out docs/learn] [--check]

What it owns, and therefore overwrites, is everything under the output
directory. What it reads is the handoff in `handoff/` plus the repository's
own measurement files — never a hand-maintained copy of them.

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

BASE_URL = "https://simoncoombes.github.io/pretium/learn"

#: Whether this site is the one served at the project root. It is not, yet —
#: `docs/*.html` is — and the difference decides two things: whether a
#: `robots.txt` is written (it only has effect at the root a crawler asks
#: for) and what the canonical URLs say. Flipping this and BASE_URL together
#: is most of what publishing the relaunch means.
AT_SITE_ROOT = False


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
        ("Real companies from EDGAR", "edgar"),
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

FRONT = ("Learn pretium", "index")

ASSETS = [
    "mark-pretium.png", "mark-pretium-dark.png",
    "logo-python.png", "logo-python-dark.png",
    "logo-rust.png", "logo-rust-dark.png",
]

#: The front door is set a shade tighter than the rest of the site: 1.6 line
#: height against 1.65, a 0.85rem paragraph margin against 0.9rem, 1.12
#: heading leading against 1.15. That is deliberate — it is a long page and
#: it is the only one a reader meets cold — so it survives the stylesheet
#: merge as a body-scoped override rather than being flattened away.
FRONT_OVERRIDES = """
body.pt-front{line-height:1.6}
body.pt-front p{margin:0 0 0.85rem}
body.pt-front h1,body.pt-front h2,body.pt-front h3{line-height:1.12}
"""


def door_list() -> list[dict]:
    """The one door list, in the shape the front door's cards expect.

    Handed to every component as a prop so that no page keeps its own copy
    of which pages exist. A page added to `DOORS` above appears in the front
    door's "Start here" cards as well as in the menus and the index.
    """
    return [
        {"title": short,
         "links": [{"label": name, "href": f"{slug}.html"} for name, slug in entries]}
        for _name, short, _slug, entries in DOORS
    ]


def pages() -> list[dict]:
    """Every page, in reading order, with its door and its neighbours."""
    out = [{"name": FRONT[0], "slug": FRONT[1], "door": None, "door_slug": None}]
    for door, _short, door_slug, entries in DOORS:
        for name, slug in entries:
            out.append({"name": name, "slug": slug, "door": door, "door_slug": door_slug})
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
    taking the first sheet is what lets every page share one file — and it
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
#: page will render an empty control rather than fail loudly — so fail here.
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
    if problems:
        sys.exit("the site describes presets that are not released:\n  " + "\n  ".join(problems))
    print(f"  presets: {len(shipped)} shipped, default {default}")


def dead_door_table(script: str, slugs: set[str]) -> bool:
    """Does this component still carry its own copy of the door index?

    Eight pages build the "All pages" section from a list of page names and
    links held in the component. The builder generates that section now, so
    the list is dead — but it is dead data that still asserts which pages
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
<link rel="canonical" href="{base}/{slug}.html">
<meta property="og:type" content="article">
<meta property="og:site_name" content="pretium">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{base}/{slug}.html">
<meta name="twitter:card" content="summary">
<link rel="icon" type="image/svg+xml" href="../favicon.svg">
<link rel="alternate icon" href="../favicon.ico" sizes="16x16 32x32 48x48">
<link rel="apple-touch-icon" href="../apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous">
<link rel="stylesheet" href="{fonts}">
<link rel="stylesheet" href="learn.css">
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
     anything it parses, and `<sc-for>` is not in it — so a loop written
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
#: enough across that a 14rem panel runs past the edge — and it counts
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
#: design file actually contains — 0.5cm margins, no page-break through a
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


def build(out_dir: pathlib.Path) -> None:
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

    by_slug = {p["slug"]: p for p in rendered["pages"]}
    overlay = shell.search_overlay()
    check_analytics_id()
    analytics = ANALYTICS.format(gid=GA_MEASUREMENT_ID) if GA_MEASUREMENT_ID else ""
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
                         f"the design — re-check the fix against the new page")
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
                     f"({', '.join(sorted(set(stray)))}) — the shell owns search now")
        doc = PAGE.format(
            title=esc(r["title"]),
            description=esc(r["description"]),
            base=BASE_URL,
            slug=page["slug"],
            fonts=FONTS,
            theme_script=THEME_SCRIPT,
            analytics=analytics,
            body_class="pt-front" if page["slug"] == "index" else "pt-page",
            overlay=overlay,
            masthead=shell.masthead(DOORS, page["slug"]),
            door_index=shell.door_index(DOORS, page["slug"]),
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
    write_seo(out_dir, all_pages, by_slug, emitted, version)

    print(f"built {len(all_pages)} pages into {out_dir}")
    if carrying:
        print(f"  doors: {len(carrying)} components rewired to the site's page list")
    lifted = sum(p.get("styleClasses", 0) for p in rendered["pages"])
    if lifted:
        print(f"  styles: {len(rendered['classes'])} classes replace "
              f"{lifted} inline style attributes")


def write_seo(out_dir, all_pages, by_slug, emitted, version) -> None:
    """The sitemap, and the two files a language model reads.

    `robots.txt` is written only when this site is the one at the root a
    crawler asks for. A robots.txt in a subdirectory is read by nothing, and
    writing one there would be a file that looks like a policy and is not.
    """
    summaries = {p["slug"]: by_slug[p["slug"]]["description"] for p in all_pages}
    dates = {p["slug"]: seo.last_changed(HANDOFF / p["src"], ROOT) for p in all_pages}

    (out_dir / "sitemap.xml").write_text(
        seo.sitemap(all_pages, BASE_URL, dates), encoding="utf-8")
    (out_dir / "llms.txt").write_text(
        seo.llms(all_pages, DOORS, BASE_URL, version, summaries), encoding="utf-8")
    (out_dir / "llms-full.txt").write_text(
        seo.llms_full(all_pages, BASE_URL, version, emitted, summaries), encoding="utf-8")
    if AT_SITE_ROOT:
        (out_dir / "robots.txt").write_text(seo.robots(BASE_URL), encoding="utf-8")


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
    the page it was on — and the index cannot fall behind the prose, because
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
    attribute is only ever set by the theme toggle — so a reader whose
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
    one page at a time. The rules that are *meant* to override — print,
    theme, the narrow-screen block — say `!important` and are unaffected.
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
    ap.add_argument("--out", default=str(ROOT / "docs" / "learn"))
    ap.add_argument("--check", action="store_true",
                    help="build to a temporary directory and diff, writing nothing")
    args = ap.parse_args()

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            build(pathlib.Path(tmp))
            diff = subprocess.run(["diff", "-ru", args.out, tmp], capture_output=True, text=True)
            if diff.returncode != 0:
                print(diff.stdout[:8000])
                sys.exit("docs/learn is stale — run tools/docs/learn/build.py")
            print("docs/learn is up to date")
        return

    build(pathlib.Path(args.out))


if __name__ == "__main__":
    main()
