"""The preset table, ranked, generated from one measurement.

The table this replaces was twelve rows of prose, each quoting the panel
count that was current when that preset shipped. pt-v5 said "9 of 10",
pt-v7 said "twelve of thirteen", pt-v10 said "all fourteen". The panel grew
from ten statistics to thirteen to fourteen over those releases, so the rows
were three different rulers laid side by side, and a reader comparing them
was comparing the rulers.

Every number here comes from `tools/docs/preset-panel.json`, written by
`tools/calibration/preset_panel.py`: all twelve presets, the fourteen
statistic panel, thirty seeds, one method. Generated rather than written, for
the same reason the certification tables are, which is that a hand-written
number drifts and a generated one cannot.

The prose column is hand-written because "what it is" is not measurable. It
is one line per preset, taken from the preset's own Rust docstring, which is
the definition rather than a description of it.
"""

from __future__ import annotations

import html
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_JSON = ROOT / "tools" / "docs" / "preset-panel.json"

#: One line per preset: what the MARKET is like, not what changed since the
#: preset before it.
#:
#: The first version of this column read "pt-v6 with sector structure that
#: survives a crisis", which asks a reader to chain backwards through five
#: presets they have never heard of to learn anything. Each line now stands
#: on its own, and the mechanism it names is the one in that preset's Rust
#: docstring, which is the definition rather than a description.
NOTES = {
    "pt-v1": "The original reference model, never calibrated. Trends far "
             "harder than a real market, so momentum strategies look "
             "brilliant here for a reason that is not real.",
    "pt-v2": "The first preset fitted to real-market ranges rather than "
             "chosen by hand.",
    "pt-v3": "The default until August 2026, and the market three releases "
             "of published numbers describe.",
    "pt-v4": "Built for two-year questions, back when one year was all that "
             "had been measured.",
    "pt-v5": "Separates two things that had been one: a sudden jump in a "
             "price no longer drags the following days along behind it.",
    "pt-v6": "Halves how much traders here follow each other, which was "
             "still making prices trend more than real ones do.",
    "pt-v7": "The first market where companies in the same industry move "
             "together more than companies in different ones, in calm "
             "weather and in a crash alike.",
    "pt-v8": "Gives correlation a memory: how much shares move together "
             "carries over from one day to the next instead of being redrawn "
             "each morning.",
    "pt-v9": "A market that frightens itself. A bad day raises its own fear "
             "gauge, so a crisis is something this market produces rather "
             "than something you have to script.",
    "pt-v10": "Trading volume that remembers: busy days follow busy days, "
              "the way they do in a real market.",
    "pt-v11": "The first market whose crises are about as violent as real "
              "ones, and where shares crash together the way they really do.",
    "pt-v12": "The default. Lets a day's volume keep responding to moves as "
              "far as 12%, roughly where a real exchange starts halting "
              "trade. One number, and the largest single measured gain in "
              "this project's record.",
}

#: How many rows the table shows. Ten of the twelve, because a ranking is
#: for reading and the tail of it is not a recommendation. The presets below
#: the cut are NOT withdrawn -- every one stays selectable and reproduces bit
#: for bit -- so the table says so in a line underneath rather than letting
#: their absence imply they were dropped.
TOP_N = 10

_TD = 'style="vertical-align:top"'
_TD_MONO = ('style="font-family:var(--font-mono);font-size:12.5px;'
            'white-space:nowrap;vertical-align:top"')
_TD_NUM = ('style="font-family:var(--font-mono);font-size:12.5px;'
           'white-space:nowrap;vertical-align:top;text-align:right"')


def load(path: pathlib.Path | None = None) -> dict:
    p = path or PANEL_JSON
    if not p.exists():
        raise SystemExit(
            f"{p} is missing. It is written by "
            "tools/calibration/preset_panel.py and the preset table is "
            "generated from it; the table cannot be built without it."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def rank(data: dict) -> list[str]:
    """Best first.

    Ordered on the three counts, then on how close the crisis lever lands to
    the real one. The counts come first because being in band is the claim
    the envelope makes; the lever breaks ties because it is the axis the
    project spent four presets on and the one a scenario user feels.

    Ties are ordered by preset number, so a tie reads as "these are the same"
    rather than as an arbitrary winner.
    """
    real = data["method"]["real_crisis_lever"]
    presets = data["presets"]

    def key(name):
        r = presets[name]
        return (
            -r["in_band_252"],
            -r["in_band_504"],
            -r["in_band_heldout_universe"],
            # Rounded to what the table PRINTS. At full float precision two
            # rows showing the same 2.64x sort by a difference the reader
            # cannot see, which makes the order look arbitrary. Rounding here
            # means a visible tie is a real tie and falls through to the
            # preset number below.
            round(abs(r["crisis_lever"] - real), 2),
            -int(name.split("-v")[1]),
        )

    return sorted(presets, key=key)


def _count(n: int, total: int) -> str:
    """A count, with the perfect one marked. 14 of 14 is the whole claim."""
    body = f"{n} of {total}"
    return f"<strong>{body}</strong>" if n == total else body


def table_html(data: dict) -> str:
    """The replacement table, in the bundle's own escaped markup.

    Written for a reader with no context. The first version of this table
    published columns headed "1 year" and "2 years" with cells reading
    "14 of 14", which is meaningless unless you already know what the
    fourteen are -- and the people who most need this table are exactly the
    ones who do not. So the fourteen are named in plain English above it,
    every column carries what it measures underneath its title, and the
    crisis column shows the real-market figure beside the model's.

    `sc-raw-*` is how the design bundle carries table tags through its
    bundler; `clean_content` in build_site.py unescapes them back to real
    ones. Writing plain `<tr>` here would survive the build and then be
    stripped from the app.
    """
    presets = data["presets"]
    total = len(data["panel"])
    real = data["method"]["real_crisis_lever"]
    default = data["default_preset"]
    order = rank(data)
    shown, rest = order[:TOP_N], order[TOP_N:]

    rows = []
    for name in shown:
        r = presets[name]
        badge = (' <span style="font-family:var(--font-sans);font-size:11px;'
                 'color:var(--accent)">default</span>') if name == default else ""
        note = NOTES.get(name, "")
        rows.append(
            f"<sc-raw-tr>\n"
            f'              <sc-raw-td {_TD_MONO}>{html.escape(name)}{badge}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{_count(r["in_band_252"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{_count(r["in_band_504"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>'
            f'{_count(r["in_band_heldout_universe"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{r["crisis_lever"]:.2f}x</sc-raw-td>\n'
            f"              <sc-raw-td {_TD}>{note}</sc-raw-td>\n"
            f"            </sc-raw-tr>"
        )

    def th(title, explain, align=""):
        style = ' style="text-align:right;vertical-align:bottom"' if align \
            else ' style="vertical-align:bottom"'
        sub = (f'<div style="font-weight:400;font-size:11.5px;color:var(--mut);'
               f'margin:3px 0 0;max-width:170px;white-space:normal">'
               f'{explain}</div>') if explain else ""
        return f"<sc-raw-th{style}>{title}{sub}</sc-raw-th>"

    head = (
        "<sc-raw-thead><sc-raw-tr>"
        + th("Preset", "")
        + th("Realistic over one&nbsp;year",
             f"properties passed, out of {total}", align=True)
        + th("&hellip;and over two&nbsp;years",
             "scored against bands re-derived for a two-year window, which "
             "is a stricter ruler", align=True)
        + th("&hellip;on companies it never saw",
             "one year, on a 60-name roster no preset was tuned against",
             align=True)
        + th("Crisis severity",
             f"how much more violently prices swing in a crisis than in a "
             f"calm market. Real markets: {real:.2f}x", align=True)
        + th("What it is", "")
        + "</sc-raw-tr></sc-raw-thead>"
    )

    lede = (
        '<h3 style="font-size:16px;margin:30px 0 10px">How the presets '
        "compare</h3>\n        "
        f"<p>Real stock markets have habits, and this simulator is measured "
        f"against <strong>{total} of them</strong>: how far prices swing over "
        f"a year, how often a wild day happens, whether a wild day tends to "
        f"be followed by another one, whether shares fall together harder "
        f"than they rise together, whether heavy trading shows up alongside "
        f"big moves, and nine more. Each habit has a range measured from real "
        f"market data. A preset <strong>passes</strong> one when its own "
        f"measurement lands inside that range, so "
        f"<strong>{total} of {total}</strong> means it behaved like a real "
        f"market on every habit tested. The full list, with every range and "
        f'every measurement, is on <a href="#/trust">Realism and '
        f"limits</a>.</p>\n        "
        f"<p>All twelve presets were measured in one run, thirty seeds each, "
        f"one method throughout, so the rows compare. Ranked best first.</p>"
        f'\n        <p style="font-size:14px;color:var(--mut)">This is a '
        f"ranking, not a menu. Use the default unless you have a reason not "
        f"to, and name it either way: the earlier presets are here so a "
        f"result recorded under one still replays bit for bit, not so you "
        f"can shop among them. A preset further down this table is not "
        f"broken, it is earlier.</p>\n        "
    )

    tail = ""
    if rest:
        names = ", ".join(
            f'<code style="font-size:12px">{html.escape(n)}</code>'
            for n in rest
        )
        tail = (
            f'\n        <p style="font-size:14px;color:var(--mut)">Showing '
            f'the top {len(shown)} of {len(order)}. {names} '
            f'{"are" if len(rest) > 1 else "is"} still selectable and still '
            f'{"reproduce" if len(rest) > 1 else "reproduces"} bit for bit; '
            f'{"they rank" if len(rest) > 1 else "it ranks"} below the rows '
            f'above on the same measurement, which is why '
            f'{"they are" if len(rest) > 1 else "it is"} not listed rather '
            f'than because {"they were" if len(rest) > 1 else "it was"} '
            f'withdrawn.</p>'
        )

    return (
        lede
        + "<sc-raw-table>\n          "
        + head
        + '\n          <sc-raw-tbody style="font-size:13.5px">\n            '
        + "\n            ".join(rows)
        + "\n          </sc-raw-tbody>\n        </sc-raw-table>"
        + tail
    )


#: The paragraph the old table follows. Matching the table by its own opening
#: tag is not enough: the change page has more than one. This anchors on the
#: heading above it, which is unique, and asserts.
TABLE_ANCHOR_START = "<sc-raw-table>\n          <sc-raw-thead><sc-raw-tr><sc-raw-th>Preset</sc-raw-th>"
TABLE_ANCHOR_END = "</sc-raw-table>"


def replace_table(doc: str, data: dict) -> str:
    """Swap the hand-written preset table for the generated one.

    Asserts on both ends. A reworded bundle fails the build rather than
    quietly shipping two tables or none, which is the discipline the era and
    release-status corrections beside it already follow.
    """
    start = doc.find(TABLE_ANCHOR_START)
    if start == -1:
        raise SystemExit(
            "the preset table's opening markup was not found in the design "
            "bundle, so the generated ranking table has nothing to replace. "
            "presets.TABLE_ANCHOR_START needs updating rather than silently "
            "leaving the hand-written table in place."
        )
    end = doc.find(TABLE_ANCHOR_END, start)
    if end == -1:
        raise SystemExit("the preset table has no closing tag")
    end += len(TABLE_ANCHOR_END)
    if "pt-v1" not in doc[start:end]:
        raise SystemExit(
            "the table matched by presets.TABLE_ANCHOR_START does not "
            "contain pt-v1, so it is not the preset table"
        )
    return doc[:start] + table_html(data) + doc[end:]


def markdown_table(data: dict) -> str:
    """The same ranking for `docs/model-presets.md`, so the two agree.

    The markdown docs are a second, independently maintained source, which
    is the mechanism behind the class-6 findings of the 0.3.0 audit. This
    does not fix that, but it stops the preset ranking being an instance of
    it: both surfaces are rendered from this one file.
    """
    presets = data["presets"]
    total = len(data["panel"])
    real = data["method"]["real_crisis_lever"]
    default = data["default_preset"]
    lines = [
        f"Real stock markets have habits, and this simulator is measured "
        f"against **{total} of them**: how far prices swing over a year, how "
        f"often a wild day happens, whether a wild day tends to be followed "
        f"by another one, whether shares fall together harder than they rise "
        f"together, and ten more. Each habit has a range measured from real "
        f"market data, and a preset **passes** one when its own measurement "
        f"lands inside that range. All twelve were measured in a single run, "
        f"thirty seeds each. Ranked best first.",
        "",
        f"| preset | realistic over 1 year "
        f"(of {total}) | over 2 years, stricter ruler | on companies it "
        f"never saw | crisis severity (real {real:.2f}x) | what it is |",
        "|---|---|---|---|---|---|",
    ]
    for name in rank(data)[:TOP_N]:
        r = presets[name]
        label = f"`{name}`" + (" **(default)**" if name == default else "")
        lines.append(
            f"| {label} | {r['in_band_252']}/{total} | "
            f"{r['in_band_504']}/{total} | "
            f"{r['in_band_heldout_universe']}/{total} | "
            f"{r['crisis_lever']:.2f}x | {NOTES.get(name, '')} |"
        )
    return "\n".join(lines)
