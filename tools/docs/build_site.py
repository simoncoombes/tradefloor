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


def read_bundle() -> str:
    if not DESIGN_BUNDLE.exists():
        sys.exit(f"design bundle not found: {DESIGN_BUNDLE}")
    text = DESIGN_BUNDLE.read_text(encoding="utf-8")
    for old, new in PROSE_FIXES:
        text = text.replace(old, new)
    # Version-bearing spots, by their surrounding markup so history is safe.
    # ">v0.1.0<" rather than the full tag: the template is JSON-encoded
    # inside the bundle, so "</span>" arrives as "<\\u002Fspan>".
    text = text.replace(">v0.1.0<", f">v{VERSION}<")
    text = text.replace("version = {0.1.0}", "version = {%s}" % VERSION)
    # The fixes land inside a JSON string in the template block, so prove the
    # block still parses before anything downstream depends on it.
    json.loads(script_payload(text, "template"))
    return text


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


def build_sitemap(pages: list[dict]) -> str:
    urls = "".join(
        f"<url><loc>{absolute(p['slug'])}</loc>"
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
