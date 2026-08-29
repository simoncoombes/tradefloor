"""Four in-place additions to pages the design bundle already carries.

Each one moves material the page currently assembles sentence by sentence into
a shape that shows it at once: two diagrams, a table, and a list of things a
result from this simulator does not license you to say.

Applied here rather than edited into `docs/*.html`, because the build
overwrites every page it owns. Every insertion asserts on its anchor, so a
reworded bundle fails the build rather than silently dropping the addition.

The loss table takes its membership from `tradefloor.loss` at build time. The
three roles are one tuple each and the third is derived as the complement, so
a statistic promoted between releases moves in this table without anyone
editing it.
"""

from __future__ import annotations

import html
import re
import sys

_H2 = 'style="font-size:21px;margin:46px 0 10px"'
_P = 'style="color:var(--mut);font-size:14px;margin:0 0 12px"'
_CAP = 'style="color:var(--mut);font-size:13px;margin-top:8px"'
_MONO = 'font-family="var(--font-mono, monospace)"'
_UI = 'font-family="var(--font-ui, sans-serif)"'


def _sub(doc: str, anchor: str, addition: str, what: str) -> str:
    if anchor not in doc:
        sys.exit(f"upgrades: the anchor for {what} is not in the design bundle. "
                 f"It was reworded upstream, so this needs updating rather than "
                 f"silently shipping the page without it:\n  {anchor[:90]}")
    if doc.count(anchor) != 1:
        sys.exit(f"upgrades: the anchor for {what} appears {doc.count(anchor)} "
                 "times; it cannot say where the addition goes")
    return doc.replace(anchor, anchor + addition, 1)


ARROW = (
    '<defs><marker id="{id}" viewBox="0 0 10 10" refX="9" refY="5" '
    'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    '<path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>'
)


# ---------------------------------------------------------------- tick chain

def tick_diagram() -> str:
    """The four tick phases, with the two things the prose carries in asides.

    Both are in the `rust/src/market/tick.rs` module docs: the uniform drawn
    in phase 1 is spent in phase 3, and the circuit breaker is applied twice,
    once to the model price and once to the settled print.
    """
    boxes = [
        (20, "Factors", "one normal, one uniform"),
        (230, "Price", "s-process, then breaker"),
        (440, "Volume", "spends phase 1's uniform"),
        (650, "Settlement", "the book prints"),
    ]
    rects = "".join(
        f'<rect x="{x}" y="60" width="170" height="52" rx="6"/>' for x, _, _ in boxes)
    labels = "".join(
        f'<text x="{x + 85}" y="82">{t}</text>'
        f'<text x="{x + 85}" y="99" opacity="0.6" font-size="11">{s}</text>'
        for x, t, s in boxes)
    flow = "".join(
        f'<path d="M {x + 170} 86 L {x + 224} 86"/>' for x, _, _ in boxes[:-1])
    thens = "".join(
        f'<text x="{x + 197}" y="81" font-size="10.5" opacity="0.72">then</text>'
        for x, _, _ in boxes[:-1])
    return f"""
        <figure style="margin:22px 0">
        <svg viewBox="0 0 840 210" role="img" width="100%"
             aria-label="The tick runs in four phases: factors, price, volume, settlement. The uniform drawn in phase one is spent in phase three, and the circuit breaker is applied twice, once to the model price in phase two and once to the settled print in phase four."
             style="max-width:840px;color:currentColor">
          {ARROW.format(id='tick-arrow')}
          <g fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.85">{rects}</g>
          <g fill="none" stroke="currentColor" stroke-width="1.2"
             marker-end="url(#tick-arrow)">{flow}</g>
          <g font-size="12.5" fill="currentColor" text-anchor="middle" {_UI}>{labels}{thens}</g>
          <path d="M 105 60 C 105 22, 525 22, 525 56" fill="none"
                stroke="var(--accent, currentColor)" stroke-width="1.5"
                stroke-dasharray="4 3" marker-end="url(#tick-arrow)"/>
          <text x="315" y="20" font-size="11.5" text-anchor="middle"
                fill="var(--accent, currentColor)" {_UI}>the uniform is stashed, not used</text>
          <path d="M 315 112 C 315 152, 735 152, 735 116" fill="none"
                stroke="var(--accent, currentColor)" stroke-width="1.5"
                marker-end="url(#tick-arrow)"/>
          <text x="525" y="172" font-size="11.5" text-anchor="middle"
                fill="var(--accent, currentColor)" {_UI}>the breaker runs again, on the print</text>
          <text x="20" y="196" font-size="11" fill="currentColor" opacity="0.6" {_MONO}>
            one tick, per company
          </text>
        </svg>
        <figcaption {_CAP}>The order of operations is the model. The uniform drawn
        in phase 1 is not used there; it is consumed in phase 3, so a port that
        drew it where it is used would produce identical draw counts and a
        different stream. The circuit breaker is applied twice, and the second
        application is the one that matters, because the settled print becomes
        the next tick's reference.</figcaption>
        </figure>
"""


# ------------------------------------------------------------- Loop A pipeline

def atlas_pipeline() -> str:
    """Where the survey sits relative to the gates.

    The page explains each method and never shows the order, so a reader
    cannot see that everything above the connector runs at screening
    resolution and nothing above it is believed.
    """
    top = [(20, "axes_for", "name the box"), (200, "survey", "sample it"),
           (380, "sensitivity", "profile, pareto"), (560, "a human picks", "a vector")]
    bot = [(20, "confirm", "disjoint seeds"), (200, "gates", "thirty seeds"),
           (380, "control", "overfitting"), (560, "emit_preset", "a new name")]

    def row(items, y):
        r = "".join(f'<rect x="{x}" y="{y}" width="150" height="46" rx="6"/>'
                    for x, _, _ in items)
        t = "".join(f'<text x="{x + 75}" y="{y + 21}">{a}</text>'
                    f'<text x="{x + 75}" y="{y + 37}" opacity="0.6" font-size="11">{b}</text>'
                    for x, a, b in items)
        f = "".join(f'<path d="M {x + 150} {y + 23} L {x + 194} {y + 23}"/>'
                    for x, _, _ in items[:-1])
        return r, t, f

    r1, t1, f1 = row(top, 40)
    r2, t2, f2 = row(bot, 190)
    return f"""
        <figure style="margin:22px 0">
        <svg viewBox="0 0 740 268" role="img" width="100%"
             aria-label="Loop A runs in two rows. The top row maps the parameter space at screening resolution and ends with a human choosing a vector. The bottom row proves that vector at full resolution, and the connector between them carries the requirement that the seeds are disjoint."
             style="max-width:740px;color:currentColor">
          {ARROW.format(id='atlas-arrow')}
          <g fill="none" stroke="currentColor" stroke-width="1.2" opacity="0.85">{r1}{r2}</g>
          <g fill="none" stroke="currentColor" stroke-width="1.2"
             marker-end="url(#atlas-arrow)">{f1}{f2}</g>
          <g font-size="12.5" fill="currentColor" text-anchor="middle" {_UI}>{t1}{t2}</g>
          <path d="M 635 86 C 635 130, 95 130, 95 186" fill="none"
                stroke="var(--accent, currentColor)" stroke-width="1.6"
                marker-end="url(#atlas-arrow)"/>
          <text x="370" y="126" font-size="12" text-anchor="middle"
                fill="var(--accent, currentColor)" {_UI}>on seeds disjoint from the survey's, or confirm refuses to run</text>
          <text x="20" y="26" font-size="11" fill="currentColor" opacity="0.6" {_MONO}>MAP -- screening resolution</text>
          <text x="20" y="176" font-size="11" fill="currentColor" opacity="0.6" {_MONO}>PROVE -- full resolution</text>
          <g font-size="10.5" fill="currentColor" opacity="0.72" {_UI}>
            <text x="178" y="58">then</text><text x="358" y="58">then</text>
            <text x="538" y="58">then</text>
            <text x="178" y="208">then</text><text x="358" y="208">then</text>
            <text x="538" y="208">then</text>
          </g>
        </svg>
        <figcaption {_CAP}>A survey runs at screening resolution, which is enough
        to rank and describe and not enough to believe. Everything on the second
        row re-measures at full resolution, and the connector is the requirement
        <code style="font-size:12.5px">confirm</code> enforces: the seed blocks
        it is handed must be disjoint from the ones the survey recorded.</figcaption>
        </figure>
"""


# ------------------------------------------------------------- loss role table

def loss_table() -> str:
    """The three roles in `tradefloor.loss`, with membership read from the module."""
    from tradefloor import loss

    rows = [
        ("live target", loss.LIVE_TARGETS,
         "In the loss with a band and a weight. The search moves these."),
        ("constraint", loss.CONSTRAINTS,
         "Measured and penalised outside its band, so an optimiser cannot "
         "break it for free."),
        ("structural", loss.STRUCTURAL,
         "Zero weight in the objective. Membership describes the objective "
         "rather than reachability: the shipped preset holds all five in band "
         "at the certified horizon."),
    ]
    trs = "".join(
        f'<sc-raw-tr>'
        f'<sc-raw-td style="font-family:var(--font-mono);font-size:12.5px;'
        f'white-space:nowrap;vertical-align:top">{role}<div style="font:400 11px '
        f'var(--font-ui);color:var(--mut);margin-top:3px">{len(members)} of 14</div>'
        f'</sc-raw-td>'
        f'<sc-raw-td style="vertical-align:top;font-family:var(--font-mono);'
        f'font-size:12px">{"<br>".join(html.escape(m) for m in members)}</sc-raw-td>'
        f'<sc-raw-td style="vertical-align:top">{html.escape(note)}</sc-raw-td>'
        f"</sc-raw-tr>"
        for role, members, note in rows)
    return f"""
        <h3 style="font-size:16px;margin:26px 0 8px">What each of the fourteen does in the loss</h3>
        <p {_P}>Each statistic has one of three roles, and the roles are one tuple
        each in <code style="font-size:12.5px">tradefloor.loss</code>. The third is
        derived as the complement of the first two, so promoting a statistic is a
        one-tuple edit and a statistic added to
        <code style="font-size:12.5px">facts.REAL_MARKETS</code> is excluded and
        reported by default rather than silently optimised against.</p>
        <sc-raw-table>
          <sc-raw-thead><sc-raw-tr>
            <sc-raw-th>role</sc-raw-th><sc-raw-th>members</sc-raw-th>
            <sc-raw-th>what the role means</sc-raw-th>
          </sc-raw-tr></sc-raw-thead>
          <sc-raw-tbody style="font-size:13.5px">{trs}</sc-raw-tbody>
        </sc-raw-table>
"""


# ---------------------------------------------------------- what never to claim

def never_claim() -> str:
    items = [
        ("Good results here do not predict real returns.",
         "The market is synthetic. A strategy that earns in it has earned in it."),
        ("The realism certification is not an empirical train/test split.",
         "The bands were used both to tune against and to grade against, and "
         "\"held out\" means unseen seeds and rosters rather than withheld "
         "market data."),
        ("One run does not size a scenario.",
         "The expected response is calibrated and the dispersion around it is "
         "not, which is the scenario-magnitude gap."),
        ("Two runs are not comparable without naming the preset.",
         "Every preset is frozen under its own name, and a result recorded "
         "without one does not replay."),
        ("A capture ratio means nothing without its Oracle configuration and "
         "horizon.",
         "Both move the denominator, so both are quoted with any ratio."),
    ]
    lis = "".join(
        f'<li style="margin:0 0 8px"><strong>{html.escape(a)}</strong> '
        f'{html.escape(b)}</li>' for a, b in items)
    return f"""
        <h3 style="font-size:16px;margin:26px 0 8px">What never to claim from a run</h3>
        <ul style="color:var(--mut);font-size:14px;margin:0 0 14px;padding-left:20px">{lis}</ul>
"""


# ------------------------------------------------------------ loop cross-links

#: One sentence per page saying which loop it belongs to, placed under the
#: standfirst. A reader who lands on `atlas` from a search cannot otherwise
#: tell whether Atlas is something they need.
LOOP_NOTES = {
    "atlas": ("Loop A", "calibrating the market model",
              "You do not need this to test a strategy."),
    "trust": ("both loops", "what the calibrated market is measured to "
              "reproduce", "It is what bounds any claim from either."),
    "change": ("Loop A", "the coefficients a calibration produces",
               "Loop B cites a preset rather than changing one."),
    "agents": ("Loop B", "evaluating a strategy against a market that "
               "already exists", "The preset is a fixed input here."),
}


def loop_links(doc: str) -> str:
    for slug, (loop, what, tail) in LOOP_NOTES.items():
        i = doc.find(f'data-page="{slug}"')
        if i < 0:
            sys.exit(f"upgrades: no page div for {slug}")
        m = re.compile(r'<p style="font-size:16px;color:var\(--mut\);'
                       r'margin:14px 0 0">.*?</p>', re.S).search(doc, i)
        if not m:
            sys.exit(f"upgrades: {slug} has no standfirst to place the loop note after")
        note = (f'\n        <p style="color:var(--mut);font-size:13.5px;'
                f'margin:10px 0 0">This page is <strong>{loop}</strong>, {what}. '
                f'{tail} <a href="#/two-loops" style="color:var(--accent)">'
                f'The two loops</a> says which is which.</p>')
        doc = doc[:m.end()] + note + doc[m.end():]
    return doc


def apply(doc: str) -> str:
    doc = _sub(doc,
               f'<h2 {_H2}>How a Price Is Built</h2>',
               tick_diagram(), "the tick-chain diagram")
    doc = _sub(doc,
               f'<h2 {_H2}>The Methods</h2>',
               atlas_pipeline(), "the Loop A pipeline diagram")
    doc = _sub(doc,
               f'<h2 {_H2}>Read This Before You Conclude Anything</h2>',
               never_claim(), "the what-never-to-claim list")
    m = re.search(r'<h2[^>]*font-family:var\(--font-mono\)[^>]*>pretium\.loss</h2>', doc)
    if not m:
        sys.exit("upgrades: the tradefloor.loss heading is not in the design bundle")
    doc = _sub(doc, m.group(0), loss_table(), "the loss role table")
    doc = loop_links(doc)
    return doc
