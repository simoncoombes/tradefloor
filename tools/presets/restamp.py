"""Restamp the committed preset records' COEFFICIENT VECTOR, and nothing else.

    python tools/presets/restamp.py [--check]

`record.py` builds a record from a measured panel. That is the right tool when
a preset's trajectory has moved and the panel has to be re-measured. It is the
wrong tool for the other thing that invalidates a record, which happens more
often: a dial is ADDED to `ModelParams` at a value that changes no trajectory,
and every record's `coefficient_digest` -- a hash over the whole vector -- goes
stale at once while every measured panel in them is still exactly right.

That has now happened twice without being noticed, because the records are
checked by a test and the boxes that would have run it were gated on the
known-answer digest instead. `market_vol_alpha_excursion` landed inert and
took all eighteen records with it; the slow variance level took them again.

So this rewrites `coefficients`, `coefficient_digest` and `mechanisms` from
the preset the build actually ships, and leaves `measured`, `panel_252`,
`panel_504`, `mechanism_252` and `mechanism_heldout_seeds` untouched. It is
only honest under one condition, and the condition is checkable: the added
dials must not move a trajectory. Run `python tests/known_answer.py` first and
confirm the `sim` digest has not moved. If it HAS moved, this tool is the
wrong one and `record.py` with a fresh panel is the right one.

`--check` reports what would change and writes nothing.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "python" / "tradefloor" / "presets"

sys.path.insert(0, str(ROOT))
from tools.presets.record import coefficient_digest, mechanism_set  # noqa: E402

import tradefloor  # noqa: E402


def main() -> int:
    check = "--check" in sys.argv
    paths = sorted(OUT.glob("*.json"))
    if not paths:
        print("no preset records under %s" % OUT)
        return 1
    moved = 0
    refused = []
    for path in paths:
        rec = json.loads(path.read_text(encoding="utf-8"))
        name = rec["preset"]
        values = tradefloor.ModelParams.from_preset(name).to_dict()
        digest = coefficient_digest(values)
        if digest == rec["coefficient_digest"]:
            continue
        added = sorted(set(values) - set(rec["coefficients"]))
        dropped = sorted(set(rec["coefficients"]) - set(values))
        changed = sorted(k for k in set(values) & set(rec["coefficients"])
                         if values[k] != rec["coefficients"][k])
        # A CHANGED value is a different model wearing the same name, which is
        # the case this tool refuses to paper over: the panels beside it were
        # measured on the old number.
        if changed:
            print("REFUSED %s: %d coefficient(s) have different VALUES, not just "
                  "new names: %s. The panels in this record were measured on the "
                  "old vector; re-measure with record.py."
                  % (path.name, len(changed), ", ".join(changed[:6])))
            refused.append(path.name)
            continue
        moved += 1
        print("%-12s +%d added %s%s" % (
            path.name, len(added), ", ".join(added),
            "  -%d dropped %s" % (len(dropped), ", ".join(dropped)) if dropped else ""))
        if check:
            continue
        rec["coefficient_digest"] = digest
        rec["coefficients"] = {k: values[k] for k in sorted(values)}
        rec["mechanisms"] = mechanism_set(values)
        # `record.py`'s exact spelling -- indent 2, no ASCII escaping, LF --
        # so the diff is the three keys that moved and not the whole file.
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + chr(10),
                        encoding="utf-8", newline=chr(10))
    print("%d of %d record(s) %s" % (moved, len(paths),
                                     "would move" if check else "restamped"))
    if refused:
        # Non-zero, and the OTHER records are still done. A refused record is a
        # preset whose published panel describes a different model, which is a
        # measurement to schedule rather than a reason to leave seventeen
        # correct files stale beside it.
        print("%d record(s) need a re-measurement, not a restamp: %s"
              % (len(refused), ", ".join(refused)))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
