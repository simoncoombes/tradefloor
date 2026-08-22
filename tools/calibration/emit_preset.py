"""Turn a calibration certificate into the Rust preset it certifies.

The vector a search found and the vector a build ships have to be the same
sixty-four bits, and the way that goes wrong is mundane: somebody reads a
number off a report and types it into a source file. This script removes
the typing. It reads a `calibrate.py` certificate, emits the `const fn`
body for `rust/src/params.rs`, and emits a bit-pattern assertion for the
test beside it — the repo's existing convention for pinning a float that
must not drift (`params.rs` already pins `mispricing_phi` by
`to_bits()`), applied to the preset a calibration produced.

Decimal is used for the literal and hex for the assertion on purpose:
`repr` of a Python float is the shortest decimal that round-trips, and
Rust's `f64` parser is correctly rounded, so the literal reconstructs the
same bits — and the `to_bits()` test is what proves that claim on every
build rather than trusting it once.

    .venv/bin/python tools/calibration/emit_preset.py \
        --certificate results/calibrate-pt-v2-2026-08-22.json --name pt_v2
"""

from __future__ import annotations

import argparse
import json
import struct


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--name", default="pt_v2")
    parser.add_argument("--vector", default=None,
                        help="for an evaluate_axes.py certificate: which "
                             "named vector to emit")
    args = parser.parse_args()

    with open(args.certificate, encoding="utf-8") as handle:
        cert = json.load(handle)

    if args.vector:
        # An evaluate_axes.py certificate: named vectors, each carrying the
        # full override set and its moves against pt-v1. The shipped vector
        # is not always the search's own optimum — see CALIBRATION-PTV2.md
        # on the band-edge margin — so the emitter reads whichever vector
        # the report names rather than assuming.
        row = cert["vectors"][args.vector]
        moves = sorted(
            ({"parameter": key, "pt_v1": value["pt_v1"],
              "candidate": value["value"]}
             for key, value in row["moves"].items()),
            key=lambda m: m["parameter"])
        moved = [m for m in moves if m["candidate"] != m["pt_v1"]]
    else:
        moves = sorted(cert["moves"], key=lambda m: m["parameter"])
        moved = [m for m in moves if m["candidate"] != m["pt_v1"]]

    print(f"    pub const fn {args.name}() -> ModelParams {{")
    print("        let mut p = ModelParams::pt_v1();")
    for move in moved:
        bits = struct.unpack("<Q", struct.pack("<d", move["candidate"]))[0]
        print(f"        p.{move['parameter']} = {move['candidate']!r};"
              f"  // 0x{bits:016X}")
    print("        p")
    print("    }")

    print()
    print(f"const {args.name.upper()}_BITS: &[(&str, u64)] = &[")
    for move in moved:
        bits = struct.unpack("<Q", struct.pack("<d", move["candidate"]))[0]
        hexed = f"{bits:016X}"
        grouped = "_".join(hexed[i:i + 4] for i in range(0, 16, 4))
        print(f'    ("{move["parameter"]}", 0x{grouped}u64),')
    print("];")

    print()
    print(f"    // unchanged from pt-v1: "
          f"{len(moves) - len(moved)} of {len(moves)} searched")
    for move in moves:
        if move["candidate"] == move["pt_v1"]:
            print(f"    //   {move['parameter']}")


if __name__ == "__main__":
    main()
