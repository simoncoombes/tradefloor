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

from pretium import facts

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

#: The fourteen habits, in plain English, listed under the table.
#:
#: Keyed on the statistic's real name so the list is generated from
#: `envelope.CERTIFIED` rather than written out: a fifteenth statistic joins
#: the panel and this list at once, and `check_properties` fails the build if
#: one arrives without a description. The technical name is printed beside
#: each so a reader can find it in `facts.measure` output.
#:
#: (short title, what it means)
PROPERTIES = {
    "annualised_vol_pct": (
        "How far prices move over a year",
        "The size of a typical stock's ups and downs, scaled up from daily "
        "moves to a yearly figure.",
    ),
    "excess_kurtosis": (
        "How often a wild day happens",
        "Markets produce far more extreme days than a bell curve allows. "
        "This measures how much fatter those tails are.",
    ),
    "return_acf1": (
        "Whether yesterday's direction predicts today's",
        "Close to zero in a real market. If it were not, the trade would be "
        "obvious and somebody would already have taken it.",
    ),
    "abs_return_acf1": (
        "Whether a wild day is followed by another one",
        "Volatility arrives in clusters rather than evenly. Measured one "
        "trading day apart.",
    ),
    "abs_return_acf5": (
        "Whether that clustering lasts a week",
        "The same effect, measured five trading days apart, where it has "
        "weakened but not gone.",
    ),
    "abs_return_acf20": (
        "Whether it lasts a month",
        "The same effect at twenty trading days, by which point a real "
        "market has nearly forgotten.",
    ),
    "cross_sectional_corr": (
        "How much shares move together",
        "What the average pair of stocks in the same market does over a "
        "year: some of every price move is the whole market moving.",
    ),
    "volume_abs_return_corr": (
        "Whether big moves come with heavy trading",
        "On days a price moves a long way, more shares change hands.",
    ),
    "leverage_effect": (
        "Whether falls unsettle the market more than rises",
        "A drop today raises tomorrow's volatility more than an equally "
        "sized gain does.",
    ),
    "volume_change_acf1": (
        "Whether trading volume swings back",
        "An unusually busy day tends to be followed by a quieter one rather "
        "than by another busy one.",
    ),
    "corr_asymmetry": (
        "Whether shares fall together harder than they rise together",
        "How much more tightly prices move as a group on sharp down days "
        "than on sharp up days.",
    ),
    "corr_asymmetry_lagged": (
        "And whether that grip lasts a day",
        "The same comparison, measured on the day after the sharp move "
        "rather than on the day itself.",
    ),
    "sector_excess_corr": (
        "Whether an industry moves as a bloc",
        "How much more two companies in the same industry move together "
        "than two companies in different ones.",
    ),
    "corr_persistence_acf1": (
        "Whether moving together is itself persistent",
        "Once shares start moving as a group, that state carries over "
        "instead of being redrawn from scratch.",
    ),
}

#: Statistics printed as percentages rather than bare numbers.
_PCT = {"annualised_vol_pct"}


def check_properties(panel) -> None:
    """Fail the build rather than publish a habit with no explanation."""
    missing = [k for k in panel if k not in PROPERTIES]
    extra = [k for k in PROPERTIES if k not in panel]
    if missing or extra:
        raise SystemExit(
            "presets.PROPERTIES does not match the certified panel:\n"
            + "".join(f"  {k}: in the panel, not described here\n" for k in missing)
            + "".join(f"  {k}: described here, not in the panel\n" for k in extra)
        )


def _range(key) -> str:
    lo, hi = facts.REAL_MARKETS[key]
    suffix = "%" if key in _PCT else ""
    def n(x):
        s = f"{x:g}"
        return s
    return f"{n(lo)}{suffix} to {n(hi)}{suffix}"


def properties_html(panel) -> str:
    """The fourteen, listed under the table with their real-market ranges."""
    check_properties(panel)
    rows = "".join(
        "<sc-raw-tr>"
        f'<sc-raw-td style="vertical-align:top"><strong>'
        f"{html.escape(PROPERTIES[k][0])}</strong>"
        f'<div style="font-family:var(--font-mono);font-size:11.5px;'
        f'color:var(--mut);margin:2px 0 0">{html.escape(k)}</div>'
        "</sc-raw-td>"
        f'<sc-raw-td style="vertical-align:top">'
        f"{html.escape(PROPERTIES[k][1])}</sc-raw-td>"
        f'<sc-raw-td style="vertical-align:top;text-align:right;'
        f'font-family:var(--font-mono);font-size:12.5px;white-space:nowrap">'
        f"{_range(k)}</sc-raw-td>"
        "</sc-raw-tr>"
        for k in panel
    )
    return (
        f'\n        <h3 style="font-size:16px;margin:34px 0 10px">The '
        f"{len(panel)} habits</h3>\n        "
        "<p>Each one is a thing real stock markets are known to do, with the "
        "range a real US large-cap market stayed inside between 2015 and "
        "2025. A preset matches a habit when its own measurement lands in "
        "that range. The ranges are wide because real markets are: a calm "
        "year and a crisis year are both real.</p>\n        "
        "<sc-raw-table>\n          "
        "<sc-raw-thead><sc-raw-tr>"
        "<sc-raw-th>Habit</sc-raw-th>"
        "<sc-raw-th>What it means</sc-raw-th>"
        '<sc-raw-th style="text-align:right">Real markets</sc-raw-th>'
        "</sc-raw-tr></sc-raw-thead>\n          "
        f'<sc-raw-tbody style="font-size:13.5px">{rows}</sc-raw-tbody>\n'
        "        </sc-raw-table>"
    )


#: What real markets read on the two numeric columns, so the table compares
#: against something rather than against nothing.
#:
#: The volatility figures are the three real one-year windows behind the
#: `annualised_vol_pct` band, read from `facts.REAL_MARKETS_PROVENANCE` rather
#: than copied, so they cannot drift from the band the columns beside them are
#: scored against. The crisis pair is `real_vix_lever.py`: a real US large-cap
#: cross-section ran 17.2% annualised on days the VIX sat below 12 and 106.1%
#: on days it sat above 45, and 106.1 / 17.2 is the 6.16x every crisis column
#: on this site is read against.
REAL_CALM_VOL_PCT = 17.2
REAL_CRISIS_VOL_PCT = 106.1


def real_vol_window() -> tuple[float, float]:
    """The lowest and highest of the three real one-year volatility windows."""
    w = facts.REAL_MARKETS_PROVENANCE["annualised_vol_pct"]["windows"]
    return min(w), max(w)


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

    Written for a reader with no context. An early version published columns
    headed "1 year" over cells reading "14 of 14", which means nothing unless
    you already know what the fourteen are -- and the people who most need
    this table are exactly the ones who do not. So the fourteen are named in
    plain English above it, every column says what it measures underneath its
    title, and the first row of the table is REAL MARKETS, so every number
    below it is read against something rather than against nothing.

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
    vol_lo, vol_hi = real_vol_window()

    def th(title, align=False):
        """A column heading, and nothing else.

        The design renders `th` as 10.5px uppercase monospace with letter
        spacing, which is right for one to three words and unreadable for a
        sentence. An earlier version of this table put a sentence of
        explanation under each heading; they rendered as six-line columns of
        shouting monospace, taller than the data they labelled. The
        explanations moved into the paragraph above the table.
        """
        style = ('style="text-align:right;vertical-align:bottom"' if align
                 else 'style="vertical-align:bottom"')
        return f"<sc-raw-th {style}>{title}</sc-raw-th>"

    #: Real markets first, and styled apart, because a ranked table of models
    #: with nothing real in it invites the reader to treat the top row as
    #: "correct" rather than as "closest".
    real_row = (
        '<sc-raw-tr style="background:var(--panel)">\n'
        f'              <sc-raw-td {_TD_MONO}><strong>Real markets</strong>'
        "</sc-raw-td>\n"
        f'              <sc-raw-td colspan="3" '
        'style="vertical-align:top;font-size:12.5px;color:var(--mut)">'
        "the ranges these three columns score against are measured from here"
        "</sc-raw-td>\n"
        f'              <sc-raw-td {_TD_NUM}><strong>{vol_lo:.0f}% to '
        f"{vol_hi:.0f}%</strong></sc-raw-td>\n"
        f'              <sc-raw-td {_TD_NUM}><strong>{real:.2f}x</strong>'
        "</sc-raw-td>\n"
        f"              <sc-raw-td {_TD}>A 40-stock US large-cap "
        "cross-section, 2015 to 2025.</sc-raw-td>\n"
        "            </sc-raw-tr>"
    )

    rows = [real_row]
    for name in shown:
        r = presets[name]
        badge = (' <span style="font-family:var(--font-sans);font-size:11px;'
                 'color:var(--accent)">default</span>') if name == default else ""
        rows.append(
            f"<sc-raw-tr>\n"
            f'              <sc-raw-td {_TD_MONO}>{html.escape(name)}{badge}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{_count(r["in_band_252"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{_count(r["in_band_504"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>'
            f'{_count(r["in_band_heldout_universe"], total)}</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{r["annualised_vol_pct"]:.1f}%</sc-raw-td>\n'
            f'              <sc-raw-td {_TD_NUM}>{r["crisis_lever"]:.2f}x</sc-raw-td>\n'
            f"              <sc-raw-td {_TD}>{NOTES.get(name, '')}</sc-raw-td>\n"
            f"            </sc-raw-tr>"
        )

    head = (
        "<sc-raw-thead><sc-raw-tr>"
        + th("Preset")
        + th("1 year", align=True)
        + th("2 years", align=True)
        + th("New roster", align=True)
        + th("Typical year", align=True)
        + th("Crisis", align=True)
        + th("What it is")
        + "</sc-raw-tr></sc-raw-thead>"
    )

    lede = (
        '<h3 style="font-size:16px;margin:30px 0 10px">How the presets '
        "compare, and how each compares to a real market</h3>\n        "
        f"<p>Real stock markets have habits, and this simulator is measured "
        f"against <strong>{total} of them</strong>: how far prices swing over "
        f"a year, how often a wild day happens, whether a wild day tends to "
        f"be followed by another one, whether shares fall together harder "
        f"than they rise together, whether heavy trading shows up alongside "
        f"big moves, and nine more. Each habit has a range measured from a "
        f"real US large-cap market between 2015 and 2025. A preset "
        f"<strong>matches</strong> a habit when its own measurement lands "
        f"inside that range, so <strong>{total} of {total}</strong> means it "
        f"behaved like a real market on every habit tested. The full list, "
        f'with every range and every measurement, is on <a href="#/trust">'
        f"Realism and limits</a>.</p>\n        "
        f"<p>The first row is the real market itself. All twelve presets were "
        f"measured in one run, thirty seeds each, one method throughout, so "
        f"the rows compare. Ranked best first.</p>\n        "
        f'<p style="font-size:13.5px;color:var(--mut)"><strong>The columns.</strong> '
        f"<strong>1 year</strong> and <strong>2 years</strong> count habits "
        f"matched out of {total}; the two-year column scores against ranges "
        f"re-derived for a two-year window, which is a stricter ruler. "
        f"<strong>New roster</strong> is one year on a 60-name roster no "
        f"preset was tuned against. <strong>Typical year</strong> is "
        f"annualised volatility, how far prices move over a year. "
        f"<strong>Crisis</strong> is how much more violently prices swing in "
        f"a crisis than in a calm market.</p>\n        "
        f'<p style="font-size:14px;color:var(--mut)">This is a '
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
        many = len(rest) > 1
        tail = (
            f'\n        <p style="font-size:14px;color:var(--mut)">Showing '
            f'the top {len(shown)} of {len(order)}. {names} '
            f'{"are" if many else "is"} still selectable and still '
            f'{"reproduce" if many else "reproduces"} bit for bit; '
            f'{"they rank" if many else "it ranks"} below the rows above on '
            f'the same measurement, which is why {"they are" if many else "it is"} '
            f'not listed rather than because {"they were" if many else "it was"} '
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
        + properties_html(data["panel"])
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
    vol_lo, vol_hi = real_vol_window()
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
        f"| preset | realistic over 1 year (of {total}) | over 2 years, "
        f"stricter ruler | on companies it never saw | a typical year's "
        f"swing | crisis severity | what it is |",
        "|---|---|---|---|---|---|---|",
        f"| **real markets** | the {total} ranges are measured from here | | "
        f"| {vol_lo:.0f}% to {vol_hi:.0f}% | {real:.2f}x | A 40-stock US "
        f"large-cap cross-section, 2015 to 2025. |",
    ]
    for name in rank(data)[:TOP_N]:
        r = presets[name]
        label = f"`{name}`" + (" **(default)**" if name == default else "")
        lines.append(
            f"| {label} | {r['in_band_252']}/{total} | "
            f"{r['in_band_504']}/{total} | "
            f"{r['in_band_heldout_universe']}/{total} | "
            f"{r['annualised_vol_pct']:.1f}% | "
            f"{r['crisis_lever']:.2f}x | {NOTES.get(name, '')} |"
        )
    return "\n".join(lines)
