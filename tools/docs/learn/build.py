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

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HANDOFF = HERE / "handoff"
SITE = HERE / "site"

BASE_URL = "https://simoncoombes.github.io/pretium/learn"

#: The five doors, in reading order, and the pages behind each. The handoff
#: left four pages linked only by their own prev/next chain; they are placed
#: here, which is what puts them in the menus, the footer index and search.
DOORS: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("Start here", "start", [
        ("Install", "install"),
        ("Core concepts", "core-concepts"),
        ("The two loops", "two-loops"),
        ("Running a market", "running-a-market"),
    ]),
    ("Use it", "use", [
        ("Agents", "agents"),
        ("Scenarios", "scenarios"),
        ("Execution cost", "execution-cost"),
        ("Checkpoints and forking", "checkpoints"),
        ("Real companies from EDGAR", "edgar"),
        ("RL environment", "rl-environment"),
        ("The MCP server", "mcp"),
    ]),
    ("Trust it", "trust", [
        ("Realism envelope", "realism-envelope"),
        ("The metrics", "metrics"),
        ("Principles", "principles"),
        ("Citing a run", "citing"),
    ]),
    ("Reference", "reference", [
        ("Glossary", "glossary"),
        ("The nine factors", "factors"),
        ("Conventions", "conventions"),
        ("Schemas", "schemas"),
        ("Presets", "presets"),
        ("Release notes", "release-notes"),
    ]),
    ("API", "api", [
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


def pages() -> list[dict]:
    """Every page, in reading order, with its door and its neighbours."""
    out = [{"name": FRONT[0], "slug": FRONT[1], "door": None, "door_slug": None}]
    for door, door_slug, entries in DOORS:
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


HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{base}/{slug}.html">
<meta property="og:type" content="article">
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
</head>
<body class="{body_class}">
<div id="pt-root">{html}</div>
<template id="pt-template">{template}</template>
<script src="pt-data.js"></script>
<script src="pt-search.js"></script>
<script src="learn-runtime.js"></script>
<script>{script}
;(function(){{
  var host = document.getElementById('pt-root');
  var ast = PTLearn.parse(document.getElementById('pt-template').innerHTML);
  PTLearn.mount(host, Component, ast, {props});
}})();
</script>
</body>
</html>
"""

#: Read the stored theme before the first paint. Without this the page
#: renders light and then flips, which is worse than either theme. It is
#: inline and tiny for the same reason: a separate file would arrive after
#: the paint it exists to prevent.
THEME_SCRIPT = """<script>
(function(){try{var s=localStorage.getItem('pt-learn-theme');
var d=s?s==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
if(d)document.documentElement.setAttribute('data-theme','dark');}catch(e){}})();
</script>"""


def build(out_dir: pathlib.Path) -> None:
    all_pages = pages()
    slug_of = {p["name"]: p["slug"] for p in all_pages}
    # The handoff's own front-door filename, used in every masthead link.
    slug_of.setdefault("Learn pretium", "index")

    manifest = {
        "base": str(HANDOFF),
        "data": ["pt-data.js", "pt-search.js"],
        "pages": [{"src": p["src"], "slug": p["slug"]} for p in all_pages],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(manifest, fh)
        manifest_path = fh.name

    result = subprocess.run(
        ["node", str(HERE / "prerender.cjs"), manifest_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit("prerender failed:\n" + result.stderr)
    rendered = json.loads(result.stdout)

    out_dir.mkdir(parents=True, exist_ok=True)

    css = merge_stylesheet([s["css"] for s in rendered["styles"]]) + FRONT_OVERRIDES
    (out_dir / "learn.css").write_text(css.strip() + "\n", encoding="utf-8")

    shutil.copy(SITE / "learn-runtime.js", out_dir / "learn-runtime.js")
    for asset in ASSETS:
        shutil.copy(HANDOFF / asset, out_dir / asset)

    by_slug = {p["slug"]: p for p in rendered["pages"]}
    for page in all_pages:
        r = by_slug[page["slug"]]
        html = rewrite_links(r["html"], slug_of)
        template = rewrite_links(r["template"], slug_of)
        doc = HEAD.format(
            title=esc(r["title"]),
            description=esc(r["description"]),
            base=BASE_URL,
            slug=page["slug"],
            fonts=FONTS,
            theme_script=THEME_SCRIPT,
            body_class="pt-front" if page["slug"] == "index" else "pt-page",
            html=html,
            template=template,
            script=r["script"],
            props=json.dumps(r["props"]),
        )
        (out_dir / f"{page['slug']}.html").write_text(doc, encoding="utf-8")

    print(f"built {len(all_pages)} pages into {out_dir}")


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
