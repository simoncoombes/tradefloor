#!/usr/bin/env python3
"""Sync the golden vectors from the reference checkout, and verify them.

The goldens are the parity contract: they are produced by the reference
implementation's generators, which import that implementation directly and
therefore live with it rather than here. This script is the seam between where
they are produced and where they are consumed.

Point it at the reference checkout with PRETIUM_GOLDENS_SRC, or pass --from.

Two rules it enforces, both learned the hard way:

1. **Verify against index.json's SHA-256 manifest, always.** A partially
   copied or truncated vector set is otherwise a silent pass -- the tests read
   fewer cases and report success. Copying 135 MB by hand and trusting it is
   exactly how that happens.

2. **Never sync in the other direction.** The goldens come FROM the reference
   implementation and are the thing this crate is checked against.
   Regenerating them from this crate would make the tests self-confirming:
   they would assert that the crate agrees with itself, and pass forever
   regardless of correctness.

Usage:
    PRETIUM_GOLDENS_SRC=/path/to/reference/goldens python sync-goldens.py
    python sync-goldens.py --from /path/to/goldens
    python sync-goldens.py --verify   # verify what is already here
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = HERE / "goldens"


def _source() -> Path | None:
    """Where the reference goldens live. No default: this is deliberate.

    Guessing a sibling checkout would bake one machine's layout into the tool
    and, worse, would name the reference implementation in a repository that
    does not otherwise mention it.
    """
    if "--from" in sys.argv:
        return Path(sys.argv[sys.argv.index("--from") + 1]).expanduser().resolve()
    env = os.environ.get("PRETIUM_GOLDENS_SRC")
    return Path(env).expanduser().resolve() if env else None


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

    source = _source()
    if source is None:
        print("  no source given.", file=sys.stderr)
        print("  Set PRETIUM_GOLDENS_SRC, or pass --from <path>.", file=sys.stderr)
        return 1
    if not source.exists():
        print(f"  source not found: {source}", file=sys.stderr)
        return 1

    print(f"  copying goldens\n    from {source}\n    to   {DEST}")
    if DEST.exists():
        shutil.rmtree(DEST)
    shutil.copytree(source, DEST)
    return verify(DEST)


if __name__ == "__main__":
    raise SystemExit(main())
