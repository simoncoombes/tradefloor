"""The parts of every page that are not that page.

The masthead, the search overlay, the "All pages" index and the prev/next
pair are generated here, once, from the site's page order. They are not
taken from the design files, for a reason worth stating: the twenty-five
copies in the handoff disagree with each other. Four pages have no menus and
no search box at all, one page's prev/next still points at two pages that
were deleted in the same review that added it, and the door menus and the
"All pages" index file the MCP page under different doors.

Generating the shell also moves it out of the per-page template, which is
what lets the client-side template stay small: the shell is static markup
that never re-renders, so it is not part of any page's component.

The markup here carries classes, not inline styles. The design files are
inline-styled because that is what the prototyping environment emitted, and
the handoff asks for that to be undone; the shell is the part that could be
rewritten by hand with the design in front of it, and it is repeated on all
twenty-five pages, so it is where the rewrite pays best. `SHELL_CSS` below
holds the declarations, unchanged in value from the design.
"""

from __future__ import annotations

import html

MONO = "'Spline Sans Mono',ui-monospace,monospace"
SERIF = "'Source Serif 4',ui-serif,Georgia,serif"

#: Every declaration below is lifted from the design's inline styles without
#: change. What is new is only that each is written once.
SHELL_CSS = f"""
.pt-head{{position:sticky;top:0;z-index:40;background:var(--header-bg);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}}
.pt-head-row{{max-width:1040px;margin:0 auto;padding:0.7rem 1.5rem;
  display:flex;align-items:center;gap:1.5rem}}
.pt-brand{{display:flex;align-items:center;gap:0.5rem;color:var(--ink)}}
.pt-brand img{{flex:none}}
.pt-wordmark{{font-family:{MONO};font-size:1.0625rem;font-weight:500;letter-spacing:-0.01em}}
.pt-nav{{display:flex;gap:1.3rem;align-items:center;flex-wrap:wrap}}
.pt-spacer{{flex:1}}

.ptmenu{{position:relative}}
.ptmenu > summary{{display:flex;align-items:center;gap:0.3rem;font-size:0.875rem;
  color:var(--ink-2);cursor:pointer;list-style:none;padding:0.2rem 0}}
.ptmenu.pt-here > summary{{color:var(--accent)}}
.ptmenu > summary .pt-caret{{font-size:0.5rem;color:var(--accent-line)}}
.ptmenu > div{{position:absolute;top:100%;left:-0.6rem;margin-top:0.35rem;min-width:11rem;
  background:var(--card);border:1px solid var(--line);border-radius:8px;
  box-shadow:0 12px 32px rgba(0,0,0,0.14);padding:0.35rem;
  display:flex;flex-direction:column;gap:1px;z-index:60}}
.ptmenu a,.ptmenu span.pt-current{{display:block;padding:0.35rem 0.6rem;border-radius:5px;
  font-size:0.8125rem;color:var(--ink-2);white-space:nowrap}}
.ptmenu span.pt-current{{color:var(--accent);background:var(--accent-soft)}}

.pt-pill{{font-family:{MONO};font-size:0.75rem;color:var(--ink-2);background:var(--surface);
  border:1px solid var(--line);border-radius:99px;padding:3px 11px;cursor:pointer}}
.pt-searchbtn{{display:flex;align-items:center;gap:0.45rem;font-size:0.8125rem;
  color:var(--ink-3);background:var(--surface);border:1px solid var(--line);
  border-radius:99px;padding:3px 10px 3px 12px;cursor:pointer}}
.pt-key{{font-family:{MONO};font-size:0.625rem;border:1px solid var(--line);
  border-radius:4px;padding:0 4px;color:var(--ink-3)}}

.pt-overlay{{position:fixed;inset:0;z-index:80;display:none;align-items:flex-start;
  justify-content:center;padding:5rem 1.5rem 1.5rem;background:rgba(16,26,24,0.32)}}
.pt-overlay-box{{width:100%;max-width:34rem;background:var(--card);border:1px solid var(--line);
  border-radius:12px;box-shadow:0 24px 60px rgba(0,0,0,0.28);overflow:hidden}}
.pt-overlay input{{width:100%;border:0;border-bottom:1px solid var(--line-soft);
  background:var(--card);color:var(--ink);font-size:1rem;padding:0.95rem 1.1rem;outline:none}}
.pt-results{{max-height:22rem;overflow-y:auto;padding:0.4rem}}
.pt-overlay-foot{{display:flex;justify-content:space-between;gap:1rem;padding:0.55rem 1.1rem;
  border-top:1px solid var(--line-soft);background:var(--surface);
  font-family:{MONO};font-size:0.625rem;color:var(--ink-3)}}

.pt-foot{{max-width:1040px;margin:0 auto;padding:0 1.5rem}}
.pt-index{{padding:2.8rem 0 0;margin-top:2.8rem;border-top:1px solid var(--line);
  display:flex;flex-direction:column;gap:1.1rem}}
.pt-index h2{{font-size:1.25rem}}
.pt-index-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:0.9rem}}
.pt-index-col{{display:flex;flex-direction:column;gap:0.5rem;min-width:0}}
.pt-index-door{{font-family:{SERIF};font-size:0.9375rem;font-weight:600;color:var(--ink)}}
.pt-index-links{{display:flex;flex-direction:column;gap:0.3rem}}
.pt-index-links > *{{display:flex;gap:0.4rem;align-items:baseline;
  font-size:0.8125rem;color:var(--ink-2)}}
.pt-index-links > span{{color:var(--accent)}}
.pt-arrow{{font-family:{MONO};font-size:0.6875rem;color:var(--accent-line);flex:none}}
.pt-index-links > span .pt-arrow{{color:inherit}}
.pt-door-name{{white-space:nowrap}}

.pt-prevnext{{display:flex;justify-content:space-between;gap:1rem;padding:2.5rem 0 0;
  margin-top:2.5rem;border-top:1px solid var(--line);font-size:0.875rem}}
.pt-prevnext a{{display:flex;flex-direction:column;gap:2px}}
.pt-prevnext a.pt-next{{text-align:right}}
.pt-eyebrow{{font-family:{MONO};font-size:0.6875rem;color:var(--ink-3)}}
"""


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ------------------------------------------------------------------- masthead

def _menu(short: str, entries, current_slug: str) -> str:
    active = any(slug == current_slug for _, slug in entries)
    items = []
    for name, slug in entries:
        if slug == current_slug:
            items.append(f'<span class="pt-current">{esc(name)}</span>')
        else:
            items.append(f'<a href="{slug}.html">{esc(name)}</a>')
    here = " pt-here" if active else ""
    return (
        f'<details class="ptmenu{here}">'
        f'<summary>{esc(short)}<span class="pt-caret">▼</span></summary>'
        '<div>' + "".join(items) + '</div></details>'
    )


def masthead(doors, current_slug: str) -> str:
    """The sticky top bar.

    The mark swaps for its dark twin in CSS rather than in JavaScript. The
    design bound both images to a `dark` flag the component held, which
    means the wrong one is on screen until the script runs; a class and two
    rules in the stylesheet get it right on the first paint instead.
    """
    menus = "".join(_menu(short, entries, current_slug) for _n, short, _s, entries in doors)
    return (
        '<header class="pt-head"><div class="pt-head-row">'
        '<a class="pt-brand" href="index.html">'
        '<img class="pt-light" src="mark-pretium.png" alt="pretium" width="15" height="22">'
        '<img class="pt-dark" src="mark-pretium-dark.png" alt="pretium" width="15" height="22">'
        '<span class="pt-wordmark">pretium</span></a>'
        f'<nav class="pt-nav">{menus}</nav>'
        '<span class="pt-spacer"></span>'
        '<button id="pt-search-open" class="pt-searchbtn" type="button" hidden '
        'aria-label="Search the docs">Search'
        '<span id="pt-search-hint" class="pt-key">CTRL K</span></button>'
        '<button id="pt-theme" class="pt-pill" type="button" aria-label="Switch theme">'
        '<span class="pt-light">dark</span><span class="pt-dark">light</span>'
        '</button>'
        '</div></header>'
    )


# -------------------------------------------------------------- search overlay

def search_overlay() -> str:
    """The ⌘K panel.

    Empty until the reader opens it: the results are built by the shell
    script from the index it fetches at that moment, so the markup here is
    the frame only and costs nothing on a page nobody searches from.
    """
    return (
        '<div id="pt-search" class="pt-overlay" hidden>'
        '<div class="pt-overlay-box">'
        '<input id="pt-search-input" type="text" placeholder="Search the docs" '
        'aria-label="Search the docs" autocomplete="off">'
        '<div id="pt-search-results" class="pt-results" role="listbox"></div>'
        '<div class="pt-overlay-foot">'
        '<span id="pt-search-count"></span><span>ENTER OPENS, ESC CLOSES</span>'
        '</div></div></div>'
    )


# ----------------------------------------------------------------- page footer

def door_index(doors, current_slug: str) -> str:
    """The "All pages" grid that closes every page.

    Page names are kept on one line, as the design draws them, except on a
    narrow screen where the longest of them — "Real companies from EDGAR",
    a page added after the design was drawn — is wider than the column it is
    given and pushes the whole page sideways. `.pt-door-name` is where the
    stylesheet lets it wrap.
    """
    cols = []
    for _name, short, _slug, entries in doors:
        links = []
        for page_name, slug in entries:
            inner = (f'<span class="pt-arrow">&gt;</span>'
                     f'<span class="pt-door-name">{esc(page_name)}</span>')
            if slug == current_slug:
                links.append(f'<span>{inner}</span>')
            else:
                links.append(f'<a href="{slug}.html">{inner}</a>')
        cols.append(
            '<div class="pt-index-col">'
            f'<span class="pt-index-door">{esc(short)}</span>'
            '<div class="pt-index-links">' + "".join(links) + '</div></div>'
        )
    return ('<section class="pt-index"><h2>All pages</h2>'
            '<div class="pt-index-grid">' + "".join(cols) + '</div></section>')


def prev_next(prev, nxt) -> str:
    """The BACK/NEXT pair, from the site's order rather than the page's memory."""
    def side(page, label, cls):
        if not page:
            return '<span></span>'
        return (f'<a class="{cls}" href="{page["slug"]}.html">'
                f'<span class="pt-eyebrow">{label}</span>'
                f'<span>{esc(page["name"])}</span></a>')

    return ('<nav class="pt-prevnext">'
            + side(prev, "BACK", "pt-prev") + side(nxt, "NEXT", "pt-next")
            + '</nav>')
