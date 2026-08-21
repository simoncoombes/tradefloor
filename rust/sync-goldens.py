#!/usr/bin/env python3
"""Sync the golden vectors from mc-port into this repo, and verify them.

The goldens are the parity contract: they are generated from the TypeScript
engine by the 15 generators in `mc-port/scripts/rust-port/`, which import the
live TS source and therefore cannot move here. So mc-port produces them and
pretium consumes them, and this script is the seam.

Two rules it enforces, both learned the hard way:

1. **Verify against index.json's SHA-256 manifest, always.** A partially
   copied or truncated vector set is otherwise a silent pass -- the tests read
   fewer cases and report success. Copying 135 MB by hand and trusting it is
   exactly how that happens.

2. **Never sync in the other direction.** The goldens are generated FROM
   TypeScript and are the thing Rust is checked against. Regenerating them
   from Rust would make the tests self-confirming: they would assert that Rust
   agrees with Rust, and pass forever regardless of correctness.

Usage:
    python sync-goldens.py            # copy from mc-port, then verify
    python sync-goldens.py --verify   # verify what is already here
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "goldens"
SOURCE = HERE.parent.parent / "mc-port" / "docs" / "rust-port" / "goldens"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path) -> int:
    index = root / "index.json"
    if not index.exists():
        print(f"  no index.json in {root} - cannot verify", file=sys.stderr)
        return 1

    manifest = json.loads(index.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if not files:
        print("  index.json lists no files - refusing to report success",
              file=sys.stderr)
        return 1

    bad = 0
    for entry in files:
        target = root / entry["file"]
        if not target.exists():
            print(f"  MISSING  {entry['file']}")
            bad += 1
            continue
        actual = sha256(target)
        if actual != entry["sha256"]:
            print(f"  CORRUPT  {entry['file']}")
            print(f"           expected {entry['sha256'][:16]}...")
            print(f"           actual   {actual[:16]}...")
            bad += 1

    total = len(files)
    if bad:
        print(f"\n  {bad} of {total} golden files failed verification.")
        return 1
    print(f"  {total}/{total} golden files verified against index.json.")
    return 0


def main() -> int:
    if "--verify" in sys.argv:
        return verify(DEST)

    if not SOURCE.exists():
        print(f"  source not found: {SOURCE}", file=sys.stderr)
        print("  The goldens are generated in mc-port; that checkout must be",
              file=sys.stderr)
        print("  adjacent to this one.", file=sys.stderr)
        return 1

    print(f"  copying goldens\n    from {SOURCE}\n    to   {DEST}")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(SOURCE, DEST)
    return verify(DEST)


if __name__ == "__main__":
    raise SystemExit(main())
