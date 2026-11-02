"""merge_out.py: put per-arm shard outputs back into one box folder.

    python merge_out.py DEST SHARD_OUT [SHARD_OUT ...]

A screen box runs each (arm, stage) as its own shard, so a stage that
writes one file for every arm (edge.json, xsec.json, c10.json, ...) writes
one per shard. The desk graders read one box folder, laid out as the grade
job lays it out, so the shards are merged:

* a file only one shard wrote is copied;
* a file several wrote identically is copied once;
* JSON is merged key by key: the arm-keyed dictionaries (`arms`,
  `per_seed`, `per_arm`, and top-level arm names as c10.json has them) take
  the union, the per-history lists `histories` and `opening` are joined,
  and any other value must agree or it is reported;
* logs and text are joined, each under a header naming its shard;
* any other file that differs (an .npz under a shared name) is reported and
  the first copy kept.

The merge never writes over a value silently: every disagreement is listed
in DEST/merge-conflicts.json, and a file that conflicts is named there.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

LISTS_JOINED = ("histories", "opening", "arm", "shards")
#: Files every shard writes about itself (its seed plan check, its digest
#: gate). They differ by arm and path by construction; the first is kept
#: and the rest are not conflicts.
STAMPS = ("seedplan.json", "digests.json")
#: A stage's own scratch: numbered job scripts each shard writes under one
#: name (aoph-jobs/1.sh, ...) and the recorded command-line arguments of a
#: tool. Kept from the first shard, never a conflict.
SCRATCH_DIRS = ("aoph-jobs", "aopool-jobs")
SCRATCH_KEYS = ("args",)
TEXT = (".log", ".txt", ".err", "")


def merge_json(a, b, path: str, conflicts: list) -> object:
    if path.split(".")[-1] in SCRATCH_KEYS:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge_json(a[k], v, f"{path}.{k}", conflicts) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list) and path.split(".")[-1] in LISTS_JOINED:
        return a + [x for x in b if x not in a]
    if a != b:
        conflicts.append({"where": path, "kept": _short(a), "dropped": _short(b)})
    return a


def _short(v) -> str:
    s = json.dumps(v, default=str)
    return s if len(s) < 160 else s[:157] + "..."


def merge(dest: pathlib.Path, shards: list[pathlib.Path]) -> list[dict]:
    dest.mkdir(parents=True, exist_ok=True)
    conflicts: list[dict] = []
    for sh in shards:
        for f in sorted(p for p in sh.rglob("*") if p.is_file()):
            rel = f.relative_to(sh)
            to = dest / rel
            if not to.exists():
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, to)
                continue
            if to.read_bytes() == f.read_bytes():
                continue
            if f.name in STAMPS or rel.parts[0] in SCRATCH_DIRS or rel.name == "mc-joblist":
                continue
            if f.suffix == ".json":
                try:
                    a = json.loads(to.read_text(encoding="utf-8"))
                    b = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    conflicts.append({"where": str(rel), "kept": "first", "dropped": str(sh)})
                    continue
                local: list[dict] = []
                merged = merge_json(a, b, str(rel), local)
                for c in local:
                    c["shard"] = sh.parent.name
                conflicts += local
                to.write_text(json.dumps(merged, indent=1, default=str), encoding="utf-8")
            elif f.suffix in TEXT:
                with to.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n=== shard {sh.parent.name}\n")
                    fh.write(f.read_text(encoding="utf-8", errors="replace"))
            else:
                conflicts.append({"where": str(rel), "kept": "first", "dropped": sh.parent.name})
    (dest / "merge-conflicts.json").write_text(json.dumps(conflicts, indent=1), encoding="utf-8")
    return conflicts


if __name__ == "__main__":
    out = merge(pathlib.Path(sys.argv[1]), [pathlib.Path(p) for p in sys.argv[2:]])
    print(f"merged {len(sys.argv) - 2} shard(s) into {sys.argv[1]}; {len(out)} conflict(s)")
