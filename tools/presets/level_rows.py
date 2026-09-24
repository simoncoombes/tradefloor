"""The level and crisis blocks, from a target arm and a CONTROL arm.

    python tools/presets/level_rows.py --target out/j-level-pt-v19.json \
        --control out/j-level-pt-v18.json --out out/level-rows.json

`envelope.CERTIFIED_LEVEL` and `CERTIFIED_CRISIS` are certified on
`facts.LEVEL_PROTOCOL`, where the roster varies with the seed. `level_panel.py`
measures one preset on that protocol; this aggregates two of those arms into
the artefact `record.py --level-rows` writes onto a preset record, and
`envelope_tables.py` then prints into the module.

    envelope.CERTIFIED_LEVEL     index_drift_pct
    envelope.CERTIFIED_CRISIS    fear_gauge_dn1, fear_gauge_dn3,
                                 index_tail_dn3_pct

Each row is aggregated by `facts.aggregate_panels`, which applies the row's
OWN estimator -- a mean across seeds for the level row, the median of the
pooled samples for `fear_gauge_dn3`, the ratio of two summed counts for the
tail rate. The numbers are produced by the library that grades them rather
than by an estimator chosen here.

THE CONTROL IS THE POINT. Reading the target alone cannot separate a moved
model from a moved instrument: six mechanisms and two rulers can land
between one measurement and the next, and a block written across that gap
publishes a difference nobody can attribute. So the run also measures the
preset whose level block is already published -- on the same build, the same
protocol and the same seeds -- and checks it against the constants that
preset's own record carries. If the control does not reproduce them, the
target's readings are not on the ruler they replace and this exits non-zero.

The artefact is written FIRST and the refusal comes after, because the
readings are worth keeping either way; it is publishing them that the
verdict has to gate, and that gate lives in `record.py --level-rows`.

WHICH PUBLISHED BLOCK THE CONTROL IS CHECKED AGAINST is the control's own
record, not `envelope.CERTIFIED_LEVEL`. The envelope publishes whichever
preset it currently certifies, so once the envelope has moved to the target
the envelope's block is the TARGET's and a control checked against it fails
by construction -- which it did, on ptv19final, and was read as a failure of
the model. When the control IS the envelope's preset the two are the same
numbers by a different route.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The published precision. `envelope.py` prints four places, so four places
#: is the claim, and a control that agrees to four places has reproduced it.
PLACES = 4
TOL = 10.0 ** -PLACES


def load(path: str) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def record_path(name: str) -> pathlib.Path:
    """The committed record for a preset, from the INSTALLED package.

    The installed wheel first: on a box the source tree has no `_core`, and
    putting `python/` at the front of the path made `import tradefloor`
    resolve to the source package and fail on `from . import _core`.
    """
    try:
        import tradefloor as tf
    except ImportError:
        sys.path.insert(0, str(ROOT / "python"))
        import tradefloor as tf
    return pathlib.Path(tf.__file__).resolve().parent / "presets" / (name + ".json")


def published_block(control: str, keys: list[str]) -> tuple[dict, str]:
    """The constants the control arm has to reproduce, and where they came from."""
    from tradefloor import envelope

    if control == envelope.PRESET:
        block = dict(envelope.CERTIFIED_LEVEL)
        block.update(envelope.CERTIFIED_CRISIS)
        missing = [k for k in keys if k not in block]
        if missing:
            raise SystemExit(
                "REFUSED: the envelope certifies %s and its published block is "
                "missing %s" % (control, ", ".join(missing)))
        return block, "envelope.CERTIFIED_LEVEL + CERTIFIED_CRISIS"

    path = record_path(control)
    if not path.exists():
        raise SystemExit(
            "REFUSED: the control %s is not the envelope's preset and has no "
            "record at %s, so there is nothing to check it against"
            % (control, path))
    blk = load(str(path)).get("level_protocol")
    if not blk:
        raise SystemExit(
            "REFUSED: %s's record carries no level_protocol block, so the "
            "control cannot be checked on these rows. A control without a "
            "published ruler is not a control." % control)
    own = dict(blk.get("certified_level") or {})
    own.update(blk.get("certified_crisis") or {})
    missing = [k for k in keys if k not in own]
    if missing:
        raise SystemExit("REFUSED: %s's own record block is missing %s"
                         % (control, ", ".join(missing)))
    return own, "%s.json level_protocol" % control


def check_arm(doc: dict, name: str, role: str) -> None:
    """What an arm has to be before its readings mean anything."""
    if doc.get("preset") != name:
        raise SystemExit("REFUSED: the %s arm names preset %r and was read as "
                         "%r" % (role, doc.get("preset"), name))
    if not doc.get("roster_per_seed"):
        raise SystemExit(
            "REFUSED: the %s arm (%s) was measured with the roster HELD, and "
            "these rows are certified on facts.LEVEL_PROTOCOL where it varies "
            "with the seed" % (role, name))
    off = doc.get("off_protocol")
    if off:
        raise SystemExit(
            "REFUSED: the %s arm (%s) was measured off protocol on %s, so its "
            "readings do not carry a certified verdict"
            % (role, name, ", ".join(off)))
    if doc.get("model_fingerprint") not in (None, name):
        raise SystemExit(
            "REFUSED: the %s arm names %s but ran fingerprint %s, so the "
            "artefact would label a measurement it did not make"
            % (role, name, doc["model_fingerprint"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True,
                    help="level_panel.py arm for the preset the block is written for")
    ap.add_argument("--control", required=True,
                    help="level_panel.py arm for a preset whose level block is "
                         "already published, on the same build and seeds")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        import tradefloor as tf
    except ImportError:
        sys.path.insert(0, str(ROOT / "python"))
        import tradefloor as tf
    from tradefloor import envelope, facts

    level_keys = list(envelope.CERTIFIED_LEVEL)
    crisis_keys = list(envelope.CERTIFIED_CRISIS)
    keys = level_keys + crisis_keys

    tdoc, cdoc = load(args.target), load(args.control)
    target, control = tdoc["preset"], cdoc["preset"]
    check_arm(tdoc, target, "target")
    check_arm(cdoc, control, "control")
    if target == control:
        raise SystemExit("REFUSED: the target and the control are both %s, so "
                         "the control checks nothing" % target)

    # SAME BUILD, SAME SEEDS, or the difference between the arms is not the
    # preset. This is the comparison the whole design rests on and it used to
    # be an assumption in a jobs script.
    for field in ("package_version", "commit", "days", "seeds", "protocol"):
        if tdoc.get(field) != cdoc.get(field):
            raise SystemExit(
                "REFUSED: the arms disagree on %s (%r against %r). The control "
                "separates a moved model from a moved instrument only when it "
                "is the same instrument." % (field, tdoc.get(field), cdoc.get(field)))

    published, source = published_block(control, keys)
    if control != envelope.PRESET:
        print("NOTE: the control is %s and the envelope certifies %s, so the "
              "control is checked against its OWN record block" % (control, envelope.PRESET))
    print("      ruler: %s" % source)

    tvals = facts.aggregate_panels(tdoc["rows"], keys)
    cvals = facts.aggregate_panels(cdoc["rows"], keys)

    print("\n%-22s %12s %12s %12s  %s"
          % ("row", "published", "control", "delta", "verdict"))
    moved = []
    for k in keys:
        pub, got = published[k], cvals.get(k)
        if got is None:
            moved.append(k)
            print("%-22s %12.4f %12s %12s  ABSENT" % (k, pub, "-", "-"))
            continue
        delta = got - pub
        if abs(delta) >= TOL:
            moved.append(k)
        print("%-22s %12.4f %12.4f %+12.4f  %s"
              % (k, pub, got, delta, "MOVED" if abs(delta) >= TOL else "reproduced"))

    print("\n%-22s %12s %12s %12s  %s" % ("row", "band", target, "position", ""))
    for k in keys:
        got = tvals.get(k)
        lo, hi = facts.REAL_MARKETS.get(k, (float("nan"), float("nan")))
        inside = got is not None and lo <= got <= hi
        print("%-22s %12s %12.4f %12s  %s"
              % (k, "%.2f-%.2f" % (lo, hi),
                 got if got is not None else float("nan"),
                 "%.3f" % ((got - lo) / (hi - lo)) if got is not None else "-",
                 "IN" if inside else "OUT"))

    stationary = bool(tf.ModelParams.from_preset(target).to_dict()
                      .get("cycle_stationary_opening", 0.0))
    cert = envelope.certify(tdoc["rows"], stationary_opening=stationary)

    out = {
        "target": target,
        "control": control,
        "envelope_preset": envelope.PRESET,
        "package_version": tdoc.get("package_version"),
        "commit": tdoc.get("commit"),
        "protocol": tdoc.get("protocol"),
        "seeds": tdoc.get("seeds"),
        "days": tdoc.get("days"),
        "certified_level": {k: tvals.get(k) for k in level_keys},
        "certified_crisis": {k: tvals.get(k) for k in crisis_keys},
        # The vector the target ran, so the record writer can refuse a block
        # measured on a preset whose coefficients have since moved. Without
        # it a stale block is indistinguishable from a fresh one.
        "target_coefficients": tdoc.get("coefficients"),
        "control_values": {k: cvals.get(k) for k in keys},
        "published_values": published,
        "published_source": source,
        "control_reproduced": not moved,
        "control_moved_rows": moved,
        "certification_record": envelope.certification_record(cert),
    }
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("\nwrote", path)

    if moved:
        print("\nREFUSED: the control (%s) does not reproduce the constants "
              "%s publishes for it on %s. The instrument or the model has "
              "moved since those were measured, so %s's readings above cannot "
              "be compared with anything published."
              % (control, source, ", ".join(moved), target))
        return 1
    print("\nOK: the control reproduces every published row to %d places, so "
          "%s's readings are on the same ruler as the ones they replace."
          % (PLACES, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
