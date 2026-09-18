"""Build the committed record for a preset, from a measured panel.

    python tools/presets/record.py --panel out/preset-panel.json
    python tools/presets/record.py --panel out/preset-panel.json --check

A preset's published figures were spread across `envelope.py`, prose in three
modules, a README, several test comments and the changelog, and every release
that moved the default re-typed them by hand. Two survived 0.6.0 unmeasured
because nobody could tell they were stale: `envelope.DECAY_252` and
`DECAY_SLOPE` still describe pt-v14.

A record is one JSON file per preset holding what was measured, what it was
measured with, and when. `python/tradefloor/presets/<name>.json`, generated
here, committed, and shipped in the wheel so the documentation site and
`tradefloor.preset_record` read the same bytes. `git log` on one of those
files is the audit trail for that preset.

The schema is versioned and additive. A reader that knows `schema: 1` keeps
working when a later release adds a field, so the docs build does not have to
move in lockstep with a measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "python" / "tradefloor" / "presets"

#: Bumped only when a reader that knows the previous number would be WRONG,
#: rather than merely incomplete. Adding a field does not bump it.
SCHEMA = 1

#: Which release made each preset the default. A record says what a preset
#: IS; this says what it was to the project, and it is the question a reader
#: holding an old result actually asks.
DEFAULT_SINCE = {
    "pt-v3": "0.1.0", "pt-v10": "0.2.0", "pt-v12": "0.3.0",
    "pt-v14": "0.4.0", "pt-v16": "0.6.0", "pt-v18": "0.7.0", "pt-v19": "0.8.0",
}


def moved_values(was: dict[str, float], now: dict[str, float]) -> list[str]:
    """The names both vectors carry at DIFFERENT values.

    `restamp.py`'s rule, in one place: a name ADDED to `ModelParams` changes
    every record's digest and moves no trajectory, and a name whose VALUE
    moved is a different model wearing the same label. Only the second
    invalidates a measured panel.
    """
    return sorted(k for k in set(was) & set(now) if was[k] != now[k])


def coefficient_digest(values: dict[str, float]) -> str:
    """A citable identity for the coefficient vector itself.

    The fingerprint is a NAME and a preset could in principle be edited
    under it, which is the failure `the_three_presets_are_three_different_
    models` exists to catch on the Rust side. This is the same guard for a
    record: two files claiming one name and disagreeing here is a defect,
    not a matter of interpretation.
    """
    body = "\n".join(f"{k}={values[k]!r}" for k in sorted(values))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def mechanism_set(values: dict[str, float]) -> dict[str, dict]:
    """The mechanism set with doses, beside the coefficient vector.

    Additive (schema 1 stays 1): a reader that knows the coefficient
    vector keeps working, and one that knows this field can say which
    mechanisms a preset runs, at which specification and which doses.
    The specification digest comes from `tools/mechanism`, the doses are
    the preset's own coefficients for the mechanism's dials. The preset's
    `fingerprint` is untouched.
    """
    for sub in ("mechanism", "mechanism/mechanisms"):
        path = str(ROOT / "tools" / sub)
        if path not in sys.path:
            sys.path.insert(0, path)
    from emit import shipped

    out = {}
    for mech in shipped():
        out[mech.name] = {
            "spec": mech.digest(),
            "stream": mech.stream,
            "doses": {d.name: values.get(d.name, d.default) for d in mech.dials},
        }
    return out


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def build(name: str, panel: dict, values: dict[str, float]) -> dict:
    """One preset's record, from the panel that measured it."""
    p = panel["presets"][name]
    measured = {
        # The panel names the version and the box it ran on. A figure without
        # the build that produced it cannot be re-derived, which is the whole
        # reason `RunManifest` exists for runs.
        "tradefloor_version": panel["pretium_version"],
        # The panel's own commit first. `git rev-parse HEAD` here names the
        # checkout writing the file, which is the measuring one only when the
        # record is written on the box that measured it.
        "commit": panel.get("commit") or git("rev-parse", "HEAD") or None,
        "workers": panel.get("workers"),
        "wall_seconds": round(panel.get("wall_s", 0.0), 1),
        "method": panel["method"],
    }
    return {
        "schema": SCHEMA,
        "preset": name,
        "fingerprint": name,
        "coefficient_digest": coefficient_digest(values),
        "coefficients": {k: values[k] for k in sorted(values)},
        "mechanisms": mechanism_set(values),
        "default_since": DEFAULT_SINCE.get(name),
        "measured": measured,
        "panel_252": p["panel_252"],
        "panel_504": p["panel_504"],
        # The mechanism certificate beside the band panel, because "14 of 14
        # in band" answers fidelity alone: on five rows the band contains the
        # reading of a model WITHOUT the mechanism the row is named for. See
        # `envelope.certify`. Additive, so schema 1 stays 1 and a record
        # written before this field existed keeps working.
        "mechanism_252": p["mechanism_252"],
        "mechanism_heldout_seeds": p["mechanism_heldout_seeds"],
        "in_band": {
            "252": p["in_band_252"],
            "504": p["in_band_504"],
            "heldout_universe": p["in_band_heldout_universe"],
            "heldout_seeds": p["in_band_heldout_seeds"],
        },
        "misses": {
            "252": p["misses_252"],
            "504": p["misses_504"],
            "heldout_universe": p["misses_heldout_universe"],
            "heldout_seeds": p["misses_heldout_seeds"],
        },
        # THE DENOMINATOR, WHICH `in_band` ABOVE DOES NOT CARRY. A basis does
        # not band every row: `facts.REAL_MARKETS_RULED_504` holds thirteen of
        # the fourteen, because `corr_persistence_acf1` is held out of the
        # ruled band at 504. So pt-v19 under that basis reads 14 at 252 and 13
        # at 504 with NO miss at either -- 13 of 13, not 13 of 14 -- and a
        # bare 13 beside a bare 14 in the same dict reads as one short.
        # `preset_panel._count_in_band` names the rows it could not read and
        # this is where they reach the record; without it the count cannot be
        # told from a failure. Additive, so schema 1 stays 1.
        "unreadable": {
            "252": p.get("unreadable_252"),
            "504": p.get("unreadable_504"),
            "heldout_universe": p.get("unreadable_heldout_universe"),
            "heldout_seeds": p.get("unreadable_heldout_seeds"),
        },
        "crisis_lever": {
            "ratio": p["crisis_lever"],
            "vol_at_vix_5": p["vol_at_vix_5"],
            "vol_at_vix_65": p["vol_at_vix_65"],
            "real": panel["method"].get("real_crisis_lever"),
        },
    }


def carry_level_protocol(record: dict, path: pathlib.Path) -> str:
    """Carry an existing `level_protocol` block onto a rebuilt record.

    THE BLOCK CANNOT BE REBUILT HERE AND MUST NOT BE DROPPED HERE. `build`
    assembles a record from a panel measured with the roster HELD at
    `Universe.random(40, seed=111)`, and `level_protocol` is measured on
    `facts.LEVEL_PROTOCOL`, where the roster varies with the seed -- a
    different run on a different protocol. A fresh `--panel` therefore
    returns a dict with no such key, and writing it over the committed file
    silently deletes a thirty-seed measurement that cost a box.

    That is not hypothetical. pt-v18's block went at d4cfe22 and pt-v19's at
    31ef261, both of them `--panel` regenerations by somebody fixing
    something else, and the result was defect-26: `envelope.CERTIFIED_LEVEL`
    and `CERTIFIED_CRISIS` left with nothing measured behind them,
    `envelope_tables.py` refusing to run, and the published level rows
    frozen on a preset that no longer existed.

    So the block is carried, and carrying it is only honest while the
    preset's coefficient VALUES have not moved. If they have, the block
    describes a different model and is dropped -- LOUDLY, naming the tool
    that replaces it, which is the difference between this and what
    happened.
    """
    if not path.exists():
        return ""
    was = json.loads(path.read_text(encoding="utf-8")).get("level_protocol")
    if not was:
        return ""
    stamped = was.get("coefficients")
    if stamped is None:
        record["level_protocol"] = was
        return ("carried level_protocol forward UNCHECKED: it was written "
                "before the block stamped the vector it ran on, so nothing "
                "here can tell whether the preset has moved under it")
    changed = moved_values(stamped, record["coefficients"])
    if changed:
        return ("DROPPED level_protocol: %d coefficient(s) moved since it was "
                "measured (%s), so it describes a different %s. Re-measure "
                "with tools/presets/level_panel.py and level_rows.py, then "
                "record.py --level-rows."
                % (len(changed), ", ".join(changed[:6]), record["preset"]))
    record["level_protocol"] = was
    added = sorted(set(record["coefficients"]) - set(stamped))
    return ("carried level_protocol forward"
            + ("; +%d dial(s) added inert since (%s)" % (len(added), ", ".join(added))
               if added else ""))


def mechanism_bar(fresh: dict, committed: dict | None) -> dict:
    """`envelope.record_bar`, with the FIRST LAY-DOWN named as its own case.

    The bar is a SUBSET rule -- no mechanism a record shows may go unshown --
    and a preset with no committed record has laid down no set to be a subset
    of. `envelope.mechanism_bar` refuses that, correctly, because a ship bar
    that passed an absence would be reporting one as a result. The tool that
    WRITES the first record is the one place where it is not a refusal, and
    it is separated here rather than inside the library so that the exception
    sits where it applies and cannot be inherited by a reader of the rule.
    """
    from tradefloor import envelope

    if committed is None:
        return {"passed": True, "first": True, "lost": [], "absent": [],
                "reason": "no committed record: this run LAYS ONE DOWN, and "
                          "a first measurement cannot regress against itself"}
    return dict(envelope.record_bar(fresh, committed), first=False)


def count_block_gradings(block: dict) -> dict[str, dict]:
    """One count block's published count, recomputed at every basis.

    MEASURED, NOT ASSERTED, and that distinction is the whole of this
    function. The block retains in `centre` the median it graded for each of
    the fourteen shape rows, so the count it published can be recomputed
    against each band table the library knows and compared with what the
    block says. A basis that reproduces the published count is a candidate
    for the ruler this block was read against; one that does not is ruled
    out. Nothing is stamped here that does not re-derive from the block's own
    retained numbers.

    Desk cost: fourteen band lookups per basis, no panel, no box. The level
    block was thought to need a re-measurement to be re-scored because the
    per-seed panels behind it were never retained -- they were not, and they
    are not needed, because the aggregate it graded is the thing `centre`
    keeps.
    """
    from tradefloor import envelope

    centre = block.get("centre") or {}
    published = (block.get("counts") or {}).get("in_band")
    days = block.get("horizon_days")
    panel = {row: c["median"] for row, c in centre.items()
             if c.get("median") is not None}
    if not panel or published is None or not days:
        return {}
    out = {}
    for basis in sorted(envelope.RULERS_BY_BASIS):
        try:
            s = envelope.score(panel, horizon_days=days, basis=basis)
        except Exception:                      # a basis this horizon has no table for
            continue
        out[basis] = {
            "in_band": s["shape_in_band"], "of": s["shape_of"],
            "ruler": s["ruler"], "basis_detail": s["basis_detail"],
            "reproduces": s["shape_in_band"] == published,
        }
    return out


def stamp_band_ruler(block: dict, label: str) -> str:
    """Name the ruler one count block was graded by, and add the bar's.

    THE DEFECT THIS CLOSES. `level_protocol.certification` publishes its own
    `in_band`, its own `at_centre`, eleven mechanism row verdicts and a tail
    verdict, and it names no band anywhere -- not a basis, not even a symbol.
    The top-level `in_band` beside it is regraded on every `--panel` run at
    whatever `--band-basis` the panel was measured at. Regenerate pt-v19 at
    the basis the ship bar is ruled against and the file publishes
    `in_band["252"] = 14` from one path and `level_protocol...in_band = 13`
    from the other, in the same file, with nothing saying they are counts of
    different things. They are: a different roster, thirty different seeds,
    and now a different ruler.

    So the block is made to say which. `counts.protocol` names the roster
    protocol, `counts.band_basis` and `counts.basis` name the ruler the
    published count re-derives at, and `counts.in_band_ruled` carries the
    same panel against the band the bar is scored at, stamped
    `counts.basis_ruled`. Two counts that no longer collide: at the bar's own
    basis they agree with the top-level count, and where they differ the
    field names say why.

    REFUSES rather than guessing. If the published count reproduces at no
    basis the library knows, the block was graded by something not on the
    shelf and this says so instead of stamping a ruler onto it. If it
    reproduces at several -- which is the ordinary case for a preset in band
    everywhere -- no single ruler is claimed; the candidates are listed, and
    that the count does not move between them is a stronger statement than
    picking one.
    """
    from tradefloor import envelope

    counts = block.get("counts")
    if not isinstance(counts, dict) or counts.get("in_band") is None:
        return ""
    if counts.get("basis") and counts.get("basis_ruled"):
        return ""                # written by a certify that already stamps
    graded = count_block_gradings(block)
    if not graded:
        return ("; %s counts NOT STAMPED: the block retains no graded "
                "medians, so the ruler behind its count cannot be re-derived "
                "and must not be guessed" % label)
    repro = sorted(b for b, v in graded.items() if v["reproduces"])
    if not repro:
        return ("; %s counts NOT STAMPED: in_band %s of %s reproduces at none "
                "of %s (%s), so it was graded by a table this build does not "
                "carry"
                % (label, counts["in_band"], counts.get("in_band_of"),
                   ", ".join(sorted(graded)),
                   "; ".join("%s gives %d" % (b, graded[b]["in_band"])
                             for b in sorted(graded))))
    counts["protocol"] = _PROTOCOL_OF.get(label, label)
    if len(repro) == 1:
        counts["band_basis"] = repro[0]
        counts["basis"] = graded[repro[0]]["basis_detail"]
    else:
        counts["band_basis"] = None
        counts["band_basis_candidates"] = repro
        counts["basis"] = {
            "note": "this count is %d of %s at every basis this build carries "
                    "(%s), so no one ruler is claimed for it"
                    % (counts["in_band"], counts.get("in_band_of"),
                       ", ".join(repro)),
        }
    bar = envelope.BAR_BAND_BASIS
    if bar in graded:
        counts["in_band_ruled"] = graded[bar]["in_band"]
        counts["in_band_ruled_of"] = graded[bar]["of"]
        counts["basis_ruled"] = graded[bar]["basis_detail"]
    return ("; %s counts stamped %s, and the bar's basis %r reads %s of %s on "
            "the same medians"
            % (label, "/".join(repro), bar,
               counts.get("in_band_ruled"), counts.get("in_band_ruled_of")))


#: Every place a committed record publishes a band-derived `in_band` count,
#: and what a reader has to be able to tell them apart by. Three blocks, three
#: rosters or seed sets, and until 2026-09-15 none of them named a ruler.
_COUNT_BLOCKS = (
    ("mechanism_252", ("mechanism_252",)),
    ("mechanism_heldout_seeds", ("mechanism_heldout_seeds",)),
    ("level_protocol.certification", ("level_protocol", "certification")),
)

#: The run behind each count, in one line, because the ruler is only half of
#: what tells two of these counts apart. `mechanism_252` and
#: `level_protocol.certification` are both 252-day thirty-seed counts and they
#: are of different rosters; naming only the band would leave a reader
#: thinking a difference between them was a disagreement.
_PROTOCOL_OF = {
    "mechanism_252": "the certified roster held at Universe.random(40, "
                     "seed=111), training seeds",
    "mechanism_heldout_seeds": "the certified roster held at Universe.random("
                               "40, seed=111), held-out seeds",
    "level_protocol.certification": "facts.LEVEL_PROTOCOL, roster varying "
                                    "with the seed",
}


def stamp_band_rulers(record: dict) -> str:
    """Every band-derived count block in the record, made to name its ruler."""
    note = ""
    for label, path in _COUNT_BLOCKS:
        block = record
        for key in path:
            block = (block or {}).get(key) or {}
        if isinstance(block, dict) and block:
            note += stamp_band_ruler(block, label)
    return note


def unnamed_band_counts(record: dict) -> list[str]:
    """Band-derived counts in this record that name no ruler.

    A record publishes four of them and they are counts of different things:
    `in_band` on the held roster at two horizons and two seed sets, and
    `level_protocol.certification.counts.in_band` on the varying-roster
    protocol. Two of those can disagree honestly. What they may not do is
    disagree in silence, which is what happens when the ruler is a symbol in
    one place and absent in the other.
    """
    bad = []
    method = (record.get("measured") or {}).get("method") or {}
    if not method.get("band_basis"):
        bad.append("measured.method names no band_basis, so the top-level "
                   "in_band and misses say nothing about which table graded "
                   "them")
    for label, path in _COUNT_BLOCKS:
        block = record
        for key in path:
            block = (block or {}).get(key) or {}
        counts = block.get("counts") or {}
        if counts.get("in_band") is None:
            continue
        if counts.get("basis") or counts.get("band_basis_candidates"):
            continue
        bad.append("%s publishes in_band %s of %s and names no basis, so a "
                   "reader cannot tell it from the top-level count beside it"
                   % (label, counts.get("in_band"), counts.get("in_band_of")))
    return bad


def place_level_protocol(record: dict) -> dict:
    """`level_protocol` after the mechanism blocks, wherever it was set.

    One spelling of the field order, so a record written by `--panel` and one
    written by `--level-rows` cannot end up with the same fields in two
    different orders and a diff that reads as a change.
    """
    if "level_protocol" not in record:
        return record
    ordered = {}
    for key, value in record.items():
        if key == "level_protocol":
            continue
        ordered[key] = value
        if key == "mechanism_heldout_seeds":
            ordered["level_protocol"] = record["level_protocol"]
    if "level_protocol" not in ordered:      # a record written before that field
        ordered["level_protocol"] = record["level_protocol"]
    return ordered


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", required=False,
                    help="a preset_panel.py artefact")
    ap.add_argument("--check", action="store_true",
                    help="compare against the committed records and report "
                         "every difference, writing nothing")
    ap.add_argument("--mechanisms", action="store_true",
                    help="rewrite only the mechanism set of every committed "
                         "record from the build's coefficients; no panel needed")
    ap.add_argument("--mechanism-gate", metavar="PANEL",
                    help="write ONLY the mechanism certificate onto the "
                         "committed records the given panel names, leaving "
                         "every other field byte for byte as it is. For the "
                         "case this field was added in: the certificate is a "
                         "new measurement on a protocol whose per-seed panels "
                         "nobody kept before, and re-deriving `panel_252` "
                         "from a fresh run at the same time would silently "
                         "fold in every model and roster change since the "
                         "record was written")
    ap.add_argument("--level-rows", metavar="ROWS",
                    help="write ONLY the LEVEL_PROTOCOL block onto the record "
                         "the given level_rows.py artefact names. "
                         "envelope.CERTIFIED_LEVEL and CERTIFIED_CRISIS are "
                         "certified on a protocol no panel measures, so they "
                         "reach a record through here or not at all")
    ap.add_argument("--default-since", action="store_true",
                    help="rewrite only `default_since` on every committed "
                         "record from the DEFAULT_SINCE table; no panel "
                         "needed. For the release that moves the default")
    ap.add_argument("--coefficients", action="store_true",
                    help="rewrite only the coefficient vector and its digest "
                         "on every committed record, from the build; no panel "
                         "needed. For an era that ADDS a dial, where every "
                         "preset gains the new name at its inert default and "
                         "no measurement has changed")
    args = ap.parse_args()
    if args.mechanism_gate:
        return write_mechanism_gate(args.mechanism_gate)
    if args.level_rows:
        return write_level_protocol(args.level_rows)
    if args.default_since:
        return write_default_since()
    if args.mechanisms:
        return write_mechanisms()
    if args.coefficients:
        return write_coefficients()

    import tradefloor

    if args.panel is None:
        ap.error("--panel is required unless --mechanisms is given")
    panel = json.loads(pathlib.Path(args.panel).read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    drift = []
    for name in sorted(panel["presets"]):
        values = tradefloor.ModelParams.from_preset(name).to_dict()
        record = build(name, panel, values)
        path = OUT / f"{name}.json"
        note = carry_level_protocol(record, path)
        # After the carry, whichever way the carry went: a block carried
        # UNCHECKED needs the ruler named just as much as a checked one, and
        # more, since nothing else about it has been verified.
        note += stamp_band_rulers(record)
        record = place_level_protocol(record)
        text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists():
                drift.append(f"{path.name} is missing")
            else:
                have = json.loads(path.read_text(encoding="utf-8"))
                # THE SUBSET BAR, BEFORE THE FIELD DIFF AND NOT INSTEAD OF
                # IT. A `differs` line says the block moved and says nothing
                # about WHICH WAY: a preset that gained a mechanism and one
                # that lost `leverage_effect` read the same there, and at an
                # unchanged count of nine of ten the field diff is the only
                # thing that fires at all. `envelope.record_bar` names the
                # rows and the panel, so a loss cannot be read as drift.
                bar = mechanism_bar(record, have)
                if not bar["passed"]:
                    drift.append(f"{path.name}: MECHANISM LOST -- "
                                 + bar["reason"])
                # The measurement block carries a commit and a wall time, so
                # comparing it would report drift on every re-run. What has
                # to agree is the SCIENCE.
                for field in ("coefficient_digest", "mechanisms", "panel_252",
                              "panel_504", "in_band", "misses", "crisis_lever",
                              # The denominator is science, not bookkeeping: a
                              # count of 13 means one thing beside an empty
                              # `unreadable` and another beside a named row.
                              "unreadable",
                              "mechanism_252", "mechanism_heldout_seeds",
                              # A block this run would DROP is drift and the
                              # loudest kind: it is a measurement about to be
                              # deleted by a tool that cannot remake it.
                              "level_protocol"):
                    if have.get(field) != record.get(field):
                        drift.append(f"{path.name}: {field} differs")
        else:
            # THE SAME BAR ON THE WRITE SIDE, because `--check` is advisory
            # and this is the path that overwrites a measurement. A record
            # is the only place a preset's mechanism set is written down, so
            # a run that loses `leverage_effect` and writes anyway destroys
            # the evidence that it did.
            bar = mechanism_bar(record, json.loads(path.read_text(
                encoding="utf-8")) if path.exists() else None)
            if not bar["passed"]:
                print(f"REFUSED: {path.name} would overwrite its mechanism "
                      f"certificate with one that shows less. " + bar["reason"]
                      + ". A preset may show MORE mechanisms than its record "
                        "and never fewer; if the loss is intended, the record "
                        "it is measured against has to be retired on purpose",
                      file=sys.stderr)
                drift.append(f"{path.name}: MECHANISM LOST -- " + bar["reason"])
                continue
            # THE LAST GATE BEFORE TWO COUNTS GO INTO ONE FILE. A record's
            # top-level `in_band` is regraded here at the panel's basis while
            # `level_protocol` is carried forward from a run at whichever
            # basis was live when it was measured. Writing both without
            # naming either is how pt-v19 would have shipped a 14 and a 13 of
            # the same-looking quantity, and the sweep that found it refused
            # to write the eighteen records rather than do that. The refusal
            # belongs here, at the write, where it can be answered.
            unnamed = unnamed_band_counts(record)
            if unnamed:
                print(f"REFUSED: {path.name} would publish "
                      f"{len(unnamed)} band-derived count(s) with no ruler "
                      f"named: " + "; ".join(unnamed)
                      + ". Re-measure or re-stamp the block rather than "
                        "writing a count nobody can attribute",
                      file=sys.stderr)
                drift.append(f"{path.name}: {len(unnamed)} unnamed band count(s)")
                continue
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"  wrote {path.relative_to(ROOT)}"
                  + (f"  ({note})" if note else ""))

    if args.check:
        for d in drift:
            print(f"  {d}")
        print(f"{len(drift)} differences")
        return 1 if drift else 0
    # A refusal in the write path leaves `drift` non-empty, and a run that
    # refused to write a record must not exit 0. The other records are still
    # written: the fault is per record and stopping at the first one leaves
    # the rest stale for a reason that has nothing to do with them, which is
    # the lesson `write_coefficients` already learned.
    return 1 if drift else 0


def write_coefficients() -> int:
    """Rewrite the coefficient vector and its digest, and nothing else.

    Adding a settable parameter changes the SHAPE of every preset's
    coefficient vector, because the digest is taken over the whole of
    `settable_names()`. Every record therefore goes stale at once, on a
    field that has nothing to do with what the preset was measured to do,
    and `--panel` would demand a fresh measurement to fix a bookkeeping
    change.

    So this touches two fields and leaves the measured blocks byte for
    byte as they are. What it must NOT be used for is a preset whose
    coefficients actually moved: that is a new preset, and a new preset
    needs the panel.

    A REFUSAL SKIPS ONE RECORD AND DOES NOT ABORT THE RUN, which it used to.
    The refusal is per record and the bookkeeping change is global, so
    stopping at the first one left the records after it in the sort order
    stale for a reason that had nothing to do with them -- half a rewrite,
    which is worse than either end of it. The run still fails (this returns
    1) and the refused record is still not written; what changed is that the
    other seventeen are not collateral. This bit on 2026-09-11, when
    `crash_amplifier_conditional_sigma` was added while `pt-v19.json` was
    deliberately un-regenerated for `vix_target_shock_cap`.
    """
    import tradefloor

    refused = []
    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        # `to_dict()` whole, exactly as `build` passes it, `name` included:
        # the committed digest was taken over that dict and filtering any
        # key here would move the digest for a second, invisible reason.
        values = tradefloor.ModelParams.from_preset(record["preset"]).to_dict()
        before = record["coefficients"]
        moved = sorted(k for k in set(before) & set(values)
                       if before[k] != values[k])
        if moved:
            print(f"  REFUSED {path.name}: {len(moved)} existing coefficient(s) "
                  f"moved, which is a new preset and needs --panel: {moved}")
            refused.append(path.name)
            continue
        record["coefficient_digest"] = coefficient_digest(values)
        record["coefficients"] = {k: values[k] for k in sorted(values)}
        text = json.dumps(record, indent=2, ensure_ascii=False) + chr(10)
        path.write_text(text, encoding="utf-8", newline=chr(10))
        added = sorted(set(values) - set(before))
        print(f"  wrote {path.relative_to(ROOT)}"
              + (f"  (+{', '.join(added)})" if added else ""))
    if refused:
        print(f"{len(refused)} record(s) not written: {', '.join(refused)}")
        return 1
    return 0


def write_mechanism_gate(panel_path: str) -> int:
    """Set the mechanism certificate on every record the panel names.

    Two fields and nothing else. A preset's `panel_252` was measured on the
    build and the roster generator of its own day, and the certificate is
    measured today: writing both from one run would move the published band
    figures for a reason that has nothing to do with the mechanism question,
    which is the drift this whole file exists to stop. So the certificate
    carries its OWN provenance -- the commit, the version and the method that
    produced it -- and a reader can see that the two blocks were measured on
    different builds because each says which.
    """
    panel = json.loads(pathlib.Path(panel_path).read_text(encoding="utf-8"))
    measured = {
        "tradefloor_version": panel["pretium_version"],
        "commit": git("rev-parse", "HEAD") or None,
        "method": panel["method"],
    }
    written = 0
    refused = 0
    for name in sorted(panel["presets"]):
        path = OUT / f"{name}.json"
        if not path.exists():
            print(f"  skipped {name}: no committed record to write onto")
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        p = panel["presets"][name]
        # THE BAR, READ BEFORE THE BLOCKS ARE REPLACED. This mode's whole job
        # is to write a fresh certificate over a committed one, so it is the
        # shortest path in the tool from a lost mechanism to a record that
        # says the mechanism was never there. Refused per preset, never per
        # run: one preset that regressed is not a reason to leave the other
        # seventeen carrying a stale certificate.
        bar = mechanism_bar(p, record)
        if not bar["passed"]:
            print(f"  REFUSED {name}: " + bar["reason"]
                  + ". The certificate on disk shows a mechanism this panel "
                    "does not, and writing would erase the comparison",
                  file=sys.stderr)
            refused += 1
            continue
        for field in ("mechanism_252", "mechanism_heldout_seeds"):
            block = dict(p[field])
            block["measured"] = measured
            record[field] = block
        ordered = {}
        for key, value in record.items():
            if key in ("mechanism_252", "mechanism_heldout_seeds"):
                continue
            ordered[key] = value
            if key == "panel_504":
                ordered["mechanism_252"] = record["mechanism_252"]
                ordered["mechanism_heldout_seeds"] = \
                    record["mechanism_heldout_seeds"]
        ordered = place_level_protocol(ordered)
        path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        counts = record["mechanism_252"]["counts"]
        print(f"  wrote {path.relative_to(ROOT)}  mechanism shown "
              f"{counts['mechanism_shown']} of {counts['mechanism_of']}"
              + (f", REVERSED {record['mechanism_252']['reversed']}"
                 if record["mechanism_252"]["reversed"] else ""))
        # The bar's own verdict beside the count it is taken on, so a run
        # that PASSED says so in the same place a run that refused says why.
        # A gate that is only visible when it fires is a gate a reader has
        # no reason to believe is running.
        from tradefloor import envelope
        print("  " + envelope.mechanism_bar_line(bar).strip())
        written += 1
    # A refusal must not exit 0, and it must not be reported as "nothing to
    # write" either: the two are the same integer and opposite facts.
    if refused:
        print(f"  {refused} preset(s) REFUSED: a committed certificate shows "
              f"a mechanism this panel does not", file=sys.stderr)
        return 1
    return 0 if written else 1


def write_level_protocol(rows_path: str) -> int:
    """Set the LEVEL_PROTOCOL block on the record the measurement names.

    The panel this file is otherwise built from holds `Universe.random(40,
    seed=111)` and measures the fourteen SHAPE rows. `envelope.CERTIFIED_LEVEL`
    and `CERTIFIED_CRISIS` are certified on `facts.LEVEL_PROTOCOL`, where the
    roster varies with the seed, so no panel can produce them and nothing
    bound them to a record. That is how `envelope.DECAY_252` came to describe
    pt-v14 under a pt-v16 default while its neighbours moved: a published
    constant with nothing to compare it against goes stale in silence, and
    the module's own docstring promises it describes the shipped preset.

    Its own provenance, like the mechanism certificate's, because it is a
    different run on a different protocol and a reader must be able to see
    that rather than infer it.

    REFUSES on a measurement whose control did not reproduce. The control arm
    exists to separate a moved model from a moved instrument, and a block
    written over a failed control is a published number nobody can compare.
    The refusal belongs here, at the write, rather than at the measurement:
    the readings are worth keeping either way, and it is publishing them that
    the verdict has to gate.

    REFUSES, the same way, a measurement of a preset whose coefficient VALUES
    have moved since -- `restamp.py`'s rule, which it applies to the panel
    blocks and this now applies to the level block. A name ADDED to
    `ModelParams` is inert and carries; a value that moved is a different
    model wearing the same name, and the readings were taken on the old one.
    The vector is stamped INTO the block so `--panel` can make the same
    judgement later instead of deleting what it cannot rebuild.
    """
    doc = json.loads(pathlib.Path(rows_path).read_text(encoding="utf-8"))
    name = doc["target"]
    path = OUT / f"{name}.json"
    if not path.exists():
        print(f"REFUSED: no committed record at {path} to write the "
              f"level block onto; write the panel record first",
              file=sys.stderr)
        return 1
    if not doc.get("control_reproduced"):
        print(f"REFUSED: the control {doc['control']} did not reproduce "
              f"the constants this build publishes for it "
              f"({', '.join(doc.get('control_moved_rows') or [])}), so "
              f"{name}'s readings are not on the ruler they replace",
              file=sys.stderr)
        return 1

    record = json.loads(path.read_text(encoding="utf-8"))
    ran = doc.get("target_coefficients")
    if ran is None:
        print(f"  NOTE: {pathlib.Path(rows_path).name} does not carry the "
              f"vector it ran, so the block is written UNCHECKED against "
              f"{name}'s coefficients. level_rows.py has stamped it since "
              f"2026-09-14.")
    else:
        changed = moved_values(ran, record["coefficients"])
        if changed:
            print(f"REFUSED: {len(changed)} of {name}'s coefficients have "
                  f"moved since this measurement ({', '.join(changed[:6])}), "
                  f"so it describes a different model under the same name. "
                  f"Re-measure with tools/presets/level_panel.py.",
                  file=sys.stderr)
            return 1

    record["level_protocol"] = {
        "certified_level": doc["certified_level"],
        "certified_crisis": doc["certified_crisis"],
        "certification": doc["certification_record"],
        # The vector the readings were taken on, so a later `--panel` can tell
        # a block that still describes this preset from one that does not.
        "coefficients": (dict(sorted(ran.items())) if ran is not None else None),
        "control": {
            "preset": doc["control"],
            "reproduced": True,
            "values": doc["control_values"],
            "published": doc["published_values"],
        },
        "measured": {
            "tradefloor_version": doc.get("package_version"),
            "commit": doc.get("commit"),
            "protocol": doc.get("protocol"),
            "seeds": doc.get("seeds"),
            "days": doc.get("days"),
        },
    }
    # The same stamp the carried path gets, so a block written here and one
    # carried forward by `--panel` cannot end up saying different amounts
    # about the ruler behind the same count. A block from a `certify` that
    # already stamps its basis is left alone.
    #
    # THE LEVEL BLOCK ONLY, because this mode promises to write the level
    # block and leave the rest of the record where it is. The two mechanism
    # blocks need the same stamp and they get it from `--panel`, which is the
    # mode that owns them.
    stamped_note = stamp_band_ruler(record["level_protocol"]["certification"],
                                    "level_protocol.certification")
    ordered = place_level_protocol(record)
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    if stamped_note:
        print(f" {stamped_note.lstrip(';')}")
    rows = dict(doc["certified_level"])
    rows.update(doc["certified_crisis"])
    print(f"  wrote {path.relative_to(ROOT)}  level_protocol: "
          + ", ".join(f"{k}={v:.4f}" for k, v in rows.items()))
    return 0


def write_default_since() -> int:
    """Set `default_since` on every committed record, from the table above.

    Not a measurement: it says which release made a preset the default, which
    is a fact about the project and lives in `DEFAULT_SINCE`. It gets its own
    mode because the alternative at an era boundary is re-running a 96-core
    panel to change one string, or editing seventeen JSON files by hand --
    and hand-editing a generated record is how `envelope.DECAY_252` came to
    describe a preset nobody was running.
    """
    written = 0
    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        was = record.get("default_since")
        record["default_since"] = DEFAULT_SINCE.get(record["preset"])
        if was != record["default_since"]:
            print(f"  {path.name}: default_since {was!r} -> "
                  f"{record['default_since']!r}")
            written += 1
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    print(f"  {written} record(s) changed")
    return 0


def write_mechanisms() -> int:
    """Set the mechanism field on every committed record, from the build."""
    import tradefloor

    for path in sorted(OUT.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        values = tradefloor.ModelParams.from_preset(record["preset"]).to_dict()
        record["mechanisms"] = mechanism_set(values)
        ordered = {}
        for key, value in record.items():
            ordered[key] = value
            if key == "coefficients":
                ordered["mechanisms"] = record["mechanisms"]
        path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"  {path.name}: mechanisms {sorted(record['mechanisms'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
