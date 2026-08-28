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

Markup here matches the design's inline-styled output deliberately. The
handoff asks for the token set to become CSS custom properties, and it has —
every colour below is a `var(--token)`. Turning the layout declarations into
classes as well is worth doing and is not done here; see ISSUES.md.
"""

from __future__ import annotations

import html

MONO = "'Spline Sans Mono',ui-monospace,monospace"
SERIF = "'Source Serif 4',ui-serif,Georgia,serif"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ------------------------------------------------------------------- masthead

def _menu(door_name: str, short: str, entries, current_slug: str) -> str:
    active = any(slug == current_slug for _, slug in entries)
    colour = "var(--accent)" if active else "var(--ink-2)"
    items = []
    for name, slug in entries:
        if slug == current_slug:
            items.append(
                f'<span style="display:block;padding:0.35rem 0.6rem;border-radius:5px;'
                f'font-size:0.8125rem;color:var(--accent);background:var(--accent-soft);'
                f'white-space:nowrap">{esc(name)}</span>'
            )
        else:
            items.append(
                f'<a href="{slug}.html" style="display:block;padding:0.35rem 0.6rem;'
                f'border-radius:5px;font-size:0.8125rem;color:var(--ink-2);'
                f'white-space:nowrap">{esc(name)}</a>'
            )
    return (
        '<details class="ptmenu" style="position:relative">'
        f'<summary style="display:flex;align-items:center;gap:0.3rem;font-size:0.875rem;'
        f'color:{colour};cursor:pointer;list-style:none;padding:0.2rem 0">{esc(short)}'
        f'<span style="font-size:0.5rem;color:var(--accent-line)">▼</span></summary>'
        '<div style="position:absolute;top:100%;left:-0.6rem;margin-top:0.35rem;'
        'min-width:11rem;background:var(--card);border:1px solid var(--line);'
        'border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,0.14);padding:0.35rem;'
        'display:flex;flex-direction:column;gap:1px;z-index:60">'
        + "".join(items) +
        '</div></details>'
    )


def masthead(doors, current_slug: str) -> str:
    """The sticky top bar.

    The mark swaps for its dark twin in CSS rather than in JavaScript. The
    design bound both images to a `dark` flag the component held, which
    means the wrong one is on screen until the script runs; a class and two
    rules in the stylesheet get it right on the first paint instead.
    """
    menus = "".join(
        _menu(name, short, entries, current_slug) for name, short, _slug, entries in doors
    )
    return (
        '<header style="position:sticky;top:0;z-index:40;background:var(--header-bg);'
        'backdrop-filter:blur(8px);border-bottom:1px solid var(--line)">'
        '<div style="max-width:1040px;margin:0 auto;padding:0.7rem 1.5rem;display:flex;'
        'align-items:center;gap:1.5rem">'
        '<a href="index.html" style="display:flex;align-items:center;gap:0.5rem;color:var(--ink)">'
        '<img class="pt-light" src="mark-pretium.png" alt="pretium" width="15" height="22" style="flex:none">'
        '<img class="pt-dark" src="mark-pretium-dark.png" alt="pretium" width="15" height="22" style="flex:none">'
        f'<span style="font-family:{MONO};font-size:1.0625rem;font-weight:500;'
        'letter-spacing:-0.01em">pretium</span></a>'
        '<nav style="display:flex;gap:1.3rem;align-items:center;flex-wrap:wrap">'
        + menus +
        '</nav>'
        '<span style="flex:1"></span>'
        '<button id="pt-search-open" type="button" aria-label="Search the docs" '
        'style="display:flex;align-items:center;gap:0.45rem;font-size:0.8125rem;'
        'color:var(--ink-3);background:var(--surface);border:1px solid var(--line);'
        'border-radius:99px;padding:3px 10px 3px 12px;cursor:pointer">Search'
        f'<span id="pt-search-hint" style="font-family:{MONO};font-size:0.625rem;'
        'border:1px solid var(--line);border-radius:4px;padding:0 4px;color:var(--ink-3)">CTRL K</span>'
        '</button>'
        '<button id="pt-theme" type="button" aria-label="Switch theme" '
        f'style="font-family:{MONO};font-size:0.75rem;color:var(--ink-2);'
        'background:var(--surface);border:1px solid var(--line);border-radius:99px;'
        'padding:3px 11px;cursor:pointer">'
        '<span class="pt-light">dark</span><span class="pt-dark">light</span>'
        '</button>'
        '</div></header>'
    )


# -------------------------------------------------------------- search overlay

def search_overlay() -> str:
    """The ⌘K panel.

    Empty until the reader opens it: the results are built by the shell
    script from the generated index, so the markup here is the frame only
    and costs nothing on a page nobody searches from.
    """
    return (
        '<div id="pt-search" hidden style="position:fixed;inset:0;z-index:80;'
        'display:none;align-items:flex-start;justify-content:center;'
        'padding:5rem 1.5rem 1.5rem;background:rgba(16,26,24,0.32)">'
        '<div style="width:100%;max-width:34rem;background:var(--card);'
        'border:1px solid var(--line);border-radius:12px;'
        'box-shadow:0 24px 60px rgba(0,0,0,0.28);overflow:hidden">'
        '<input id="pt-search-input" type="text" placeholder="Search the docs" '
        'aria-label="Search the docs" autocomplete="off" '
        'style="width:100%;border:0;border-bottom:1px solid var(--line-soft);'
        'background:var(--card);color:var(--ink);font-size:1rem;'
        'padding:0.95rem 1.1rem;outline:none">'
        '<div id="pt-search-results" role="listbox" '
        'style="max-height:22rem;overflow-y:auto;padding:0.4rem"></div>'
        '<div style="display:flex;justify-content:space-between;gap:1rem;'
        'padding:0.55rem 1.1rem;border-top:1px solid var(--line-soft);'
        f'background:var(--surface);font-family:{MONO};font-size:0.625rem;color:var(--ink-3)">'
        '<span id="pt-search-count"></span><span>ENTER OPENS, ESC CLOSES</span>'
        '</div></div></div>'
    )


# ----------------------------------------------------------------- page footer

def door_index(doors, current_slug: str) -> str:
    """The "All pages" grid that closes every page.

    Page names are kept on one line, as the design draws them, except on a
    narrow screen where the longest of them — "Real companies from EDGAR",
    a page added after the design was drawn — is wider than the column it
    is given and pushes the whole page sideways. The class is where the
    stylesheet lets it wrap.
    """
    cols = []
    for name, short, _slug, entries in doors:
        links = []
        for page_name, slug in entries:
            if slug == current_slug:
                links.append(
                    '<span style="display:flex;gap:0.4rem;align-items:baseline;'
                    'font-size:0.8125rem;color:var(--accent)">'
                    f'<span style="font-family:{MONO};font-size:0.6875rem;flex:none">&gt;</span>'
                    f'<span class="pt-door-name" style="white-space:nowrap">{esc(page_name)}</span></span>'
                )
            else:
                links.append(
                    f'<a href="{slug}.html" style="display:flex;gap:0.4rem;'
                    'align-items:baseline;font-size:0.8125rem;color:var(--ink-2)">'
                    f'<span style="font-family:{MONO};font-size:0.6875rem;'
                    'color:var(--accent-line);flex:none">&gt;</span>'
                    f'<span class="pt-door-name" style="white-space:nowrap">{esc(page_name)}</span></a>'
                )
        cols.append(
            '<div style="display:flex;flex-direction:column;gap:0.5rem;min-width:0">'
            f'<span style="font-family:{SERIF};font-size:0.9375rem;font-weight:600;'
            f'color:var(--ink)">{esc(short)}</span>'
            '<div style="display:flex;flex-direction:column;gap:0.3rem">'
            + "".join(links) + '</div></div>'
        )
    return (
        '<section style="padding:2.8rem 0 0;margin-top:2.8rem;'
        'border-top:1px solid var(--line);display:flex;flex-direction:column;gap:1.1rem">'
        '<h2 style="font-size:1.25rem">All pages</h2>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));'
        'gap:0.9rem">' + "".join(cols) + '</div></section>'
    )


def prev_next(prev, nxt) -> str:
    """The BACK/NEXT pair, from the site's order rather than the page's memory."""
    def side(page, label, align):
        if not page:
            return '<span></span>'
        extra = ";text-align:right" if align == "right" else ""
        return (
            f'<a href="{page["slug"]}.html" style="display:flex;flex-direction:column;'
            f'gap:2px{extra}">'
            f'<span style="font-family:{MONO};font-size:0.6875rem;color:var(--ink-3)">{label}</span>'
            f'<span>{esc(page["name"])}</span></a>'
        )

    return (
        '<nav style="display:flex;justify-content:space-between;gap:1rem;'
        'padding:2.5rem 0 0;margin-top:2.5rem;border-top:1px solid var(--line);'
        'font-size:0.875rem">'
        + side(prev, "BACK", "left") + side(nxt, "NEXT", "right") +
        '</nav>'
    )
