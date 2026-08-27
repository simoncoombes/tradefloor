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

#: One line per preset, from `rust/src/params.rs`. What the preset IS, not
#: how well it does: the columns beside it are what it does, and they are
#: measured.
NOTES = {
    "pt-v1": "The original reference model, ported from the engine this "
             "library grew out of. Uncalibrated, and herding runs strong, so "
             "returns trend far harder than a real market's.",
    "pt-v2": "The first calibrated preset, fitted against the re-derived "
             "realism bands. Selectable, never the default.",
    "pt-v3": "The default until the first 2026-08-26 era boundary, and the "
             "preset the realism numbers described through three releases.",
    "pt-v4": "pt-v3 plus nine coefficients that ship inert: the 504-day "
             "variant, built for two-year questions.",
    "pt-v5": "pt-v4 with the jump decoupled from herding, which had been one "
             "write to one variable rather than two mechanisms.",
    "pt-v6": "pt-v5 with the herding term halved.",
    "pt-v7": "pt-v6 with sector structure that survives a crisis: the first "
             "preset where names in a sector co-move more than names across "
             "sectors, in calm markets and in a crisis alike.",
    "pt-v8": "pt-v7 with the market factor's variance given a memory, so "
             "correlation persists rather than resetting each day.",
    "pt-v9": "pt-v8 with a market that frightens itself: the VIX reads the "
             "day's index return, so a crisis is something this market "
             "produces rather than something a scenario drives.",
    "pt-v10": "pt-v9 with volume that remembers.",
    "pt-v11": "pt-v10 with a crisis that behaves like one: the first preset "
              "to hold the crisis lever, crisis co-movement and crisis "
              "sector structure at the same time.",
    "pt-v12": "pt-v11 with the volume-move cap lifted from 4% to 12%, "
              "roughly where a real exchange starts halting. One number, and "
              "the largest single measured gain in the project's record.",
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

    head = (
        "<sc-raw-thead><sc-raw-tr>"
        "<sc-raw-th>Preset</sc-raw-th>"
        "<sc-raw-th>Realistic over 1 year</sc-raw-th>"
        "<sc-raw-th>over 2 years</sc-raw-th>"
        "<sc-raw-th>on a roster it never saw</sc-raw-th>"
        "<sc-raw-th>How violent a crisis gets</sc-raw-th>"
        "<sc-raw-th>What it is</sc-raw-th>"
        "</sc-raw-tr></sc-raw-thead>"
    )

    lede = (
        f'<p>Ranked best first, and every number measured in one run so the '
        f'rows compare. The first three columns count how many of the '
        f'{total} realism statistics land inside their real-market band, on '
        f'the median of thirty seeds: over one year, over two years against '
        f'the bands re-derived at that window, and over one year on a '
        f'60-name roster no preset was calibrated against. The last number '
        f'is how much more violent a crisis is than a calm market, held at '
        f'VIX 65 against VIX 5 -- real markets read '
        f'<strong>{real:.2f}x</strong>.</p>\n'
        f'        <p style="font-size:14px;color:var(--mut)">This is a '
        f'ranking, not a menu. Use the default unless you have a reason not '
        f'to, and name it either way: the earlier presets are here so a '
        f'result recorded under one still replays bit for bit, not so you '
        f'can shop among them. A preset further down this table is not '
        f'broken, it is earlier.</p>\n        '
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
            f'reproduces bit for bit; {"they rank" if len(rest) > 1 else "it ranks"} '
            f'below the rows above on the same measurement, which is why '
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
        f"| preset | 1 year | 2 years | untuned roster | crisis lever "
        f"(real {real:.2f}x) | what it is |",
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
