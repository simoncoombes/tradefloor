"""Rewrite `envelope.py`'s value tables from a committed preset record.

    python tools/presets/envelope_tables.py --record python/tradefloor/presets/pt-v19.json
    python tools/presets/envelope_tables.py --record ... --write

At 0.6.0 `envelope.CERTIFIED` was re-typed by hand from a measurement
artefact and `DECAY_252` beside it was not, and nothing failed.
`tests/test_preset_records.py` now binds four tables in `envelope.py` to
the record for the preset the module says it certifies -- but the binding
catches a table that is WRONG, not one that was TYPED. This closes the
other half: the four tables are written from the record by a program, so
the module's numbers and the record's are one measurement printed twice
rather than one measurement and one transcription.

It touches exactly five things and nothing else:

    PRESET               the record's preset name
    CERTIFIED            record["panel_252"]
    MEASURED_504         record["panel_504"]
    CERTIFIED_LEVEL      record["level_protocol"]["certified_level"]
    CERTIFIED_CRISIS     record["level_protocol"]["certified_crisis"]

Every `"row": value,` line inside those four dict literals is replaced by
the record's value at the module's published precision (four places) and
every other line -- the comments that say what a number means -- is left
where it is. Prose that quotes a figure by hand stays prose: this tool
prints the figures that prose usually quotes (band positions, the 504-day
headroom, the crisis lever against real) so they are computed rather than
recalled, and it is the caller's job to make the sentences agree.

REFUSES a record with no `level_protocol` block. The level and crisis
tables are certified on `facts.LEVEL_PROTOCOL`, which no panel measures;
writing the shape tables from one run and leaving the level tables from
another is how `DECAY_252` came to describe a preset nobody was running.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENVELOPE = ROOT / "python" / "tradefloor" / "envelope.py"

#: The published precision. Four places is the claim `envelope.py` makes and
#: the tolerance `test_preset_records.py` binds at.
PLACES = 4

TABLES = (
    ("CERTIFIED", ("panel_252",)),
    ("MEASURED_504", ("panel_504",)),
    ("CERTIFIED_LEVEL", ("level_protocol", "certified_level")),
    ("CERTIFIED_CRISIS", ("level_protocol", "certified_crisis")),
)

ROW = re.compile(r'^(\s*)"([A-Za-z0-9_]+)": (-?[0-9]+\.[0-9]+),\s*$')


def dig(record: dict, path: tuple[str, ...]) -> dict:
    node = record
    for key in path:
        node = node[key]
    return node


def rewrite(text: str, name: str, values: dict[str, float]) -> tuple[str, list]:
    """Replace the value lines of one `NAME: dict[str, float] = {` literal.

    Returns the new text and a list of (row, old, new) for every row whose
    printed value moved. Refuses a table whose row set differs from the
    record's: a row added to or dropped from the panel is a schema change,
    not a re-measurement, and needs a hand.
    """
    head = re.compile(rf"^{re.escape(name)}: dict\[str, float\] = \{{$", re.M)
    m = head.search(text)
    if m is None:
        raise SystemExit(f"REFUSED: no `{name}: dict[str, float] = {{` in {ENVELOPE}")
    start = m.end()
    end = text.index("\n}", start)
    body = text[start:end]
    seen, moved, out = [], [], []
    for line in body.split("\n"):
        r = ROW.match(line)
        if not r:
            out.append(line)
            continue
        indent, row, old = r.group(1), r.group(2), float(r.group(3))
        if row not in values:
            raise SystemExit(f"REFUSED: {name} has a row `{row}` the record does not; "
                             f"that is a schema change, not a re-measurement")
        seen.append(row)
        new = round(values[row], PLACES)
        if new != old:
            moved.append((row, old, new))
        out.append(f'{indent}"{row}": {new:.{PLACES}f},')
    missing = [k for k in values if k not in seen]
    if missing:
        raise SystemExit(f"REFUSED: the record has rows {missing} that {name} does not; "
                         f"that is a schema change, not a re-measurement")
    return text[:start] + "\n".join(out) + text[end:], moved


def figures(record: dict) -> list[str]:
    """The numbers prose quotes, computed from the record and the rulers."""
    sys.path.insert(0, str(ROOT / "python"))
    from tradefloor import facts

    lines = []
    lp = record["level_protocol"]
    rows = dict(lp["certified_level"])
    rows.update(lp["certified_crisis"])
    for row, value in rows.items():
        lo, hi = facts.REAL_MARKETS[row]
        pos = (value - lo) / (hi - lo)
        verdict = "IN" if lo <= value <= hi else "OUT"
        lines.append(f"  {row:<22} {value:9.4f}  band {lo:.2f}-{hi:.2f}  position {pos:.2f}  {verdict}")
    v504 = record["panel_504"]["annualised_vol_pct"]
    hi504 = facts.REAL_MARKETS_504["annualised_vol_pct"][1]
    lines.append(f"  annualised_vol_pct at 504: {v504:.2f} against a ceiling of {hi504:.1f}, "
                 f"{hi504 - v504:.2f} of room")
    cl = record["crisis_lever"]
    ratio, real = cl["ratio"], cl["real"]
    lines.append(f"  crisis lever {ratio:.2f}x against real {real:.2f}x: "
                 f"{100.0 * (ratio - real) / real:+.1f} per cent of real "
                 f"(vol {cl['vol_at_vix_5']:.2f} at VIX 5, {cl['vol_at_vix_65']:.2f} at VIX 65)")
    ib = record["in_band"]
    lines.append(f"  in band: 252 {ib['252']}, 504 {ib['504']}, held-out universe "
                 f"{ib['heldout_universe']}, held-out seeds {ib['heldout_seeds']}")
    c = record["mechanism_252"]["counts"]
    lines.append(f"  mechanism shown {c['mechanism_shown']} of {c['mechanism_of']}, "
                 f"at centre {c['at_centre']} of {c['at_centre_of']}")
    cert = lp["certification"]
    lines.append(f"  level-protocol certificate: in band {cert['counts']['in_band']} of "
                 f"{cert['counts']['in_band_of']}, shown {cert['counts']['mechanism_shown']} of "
                 f"{cert['counts']['mechanism_of']}, at centre {cert['counts']['at_centre']} of "
                 f"{cert['counts']['at_centre_of']}; reversed {cert['reversed'] or '-'}")
    tail = cert.get("tail")
    if tail:
        lines.append(f"  tail block: rate {tail.get('rate', float('nan')):.4f}, hits {tail.get('hits')} "
                     f"in {tail.get('sessions')} sessions, se_m {tail.get('se_m', float('nan')):.4f}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", required=True, help="a committed preset record")
    ap.add_argument("--envelope", default=str(ENVELOPE))
    ap.add_argument("--write", action="store_true",
                    help="rewrite the module; without it, report only")
    args = ap.parse_args()

    record = json.loads(pathlib.Path(args.record).read_text(encoding="utf-8"))
    if "level_protocol" not in record:
        print(f"REFUSED: {args.record} carries no level_protocol block, so "
              f"CERTIFIED_LEVEL and CERTIFIED_CRISIS have nothing measured to "
              f"be written from. Write it with record.py --level-rows first.",
              file=sys.stderr)
        return 1
    path = pathlib.Path(args.envelope)
    text = path.read_text(encoding="utf-8")

    preset = record["preset"]
    m = re.search(r'^PRESET = "([^"]+)"$', text, re.M)
    if m is None:
        raise SystemExit(f"REFUSED: no `PRESET = \"...\"` line in {path}")
    was = m.group(1)
    text = text[:m.start()] + f'PRESET = "{preset}"' + text[m.end():]
    print(f"PRESET {was!r} -> {preset!r}" if was != preset else f"PRESET {preset!r} unchanged")

    total = 0
    for name, where in TABLES:
        text, moved = rewrite(text, name, dig(record, where))
        total += len(moved)
        print(f"{name}: {len(moved)} row(s) moved")
        for row, old, new in moved:
            print(f"  {row:<22} {old:9.4f} -> {new:9.4f}  ({new - old:+.4f})")

    print("figures the prose quotes, from the record:")
    for line in figures(record):
        print(line)

    if args.write:
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}: {total} value(s) moved")
    else:
        print(f"{total} value(s) would move; pass --write to rewrite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
