"""tpieces.py IN OUT: the traded stage's records (traded-SEED.json) reduced to MG2's, XD1's and XD2's per-history
pieces (tpieces-SEED.json, passprob_futures.traded_pieces), for the gate."""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "longrun"))
sys.path.insert(0, str(HERE))
import ptv22 as P  # noqa: E402
from passprob_futures import traded_pieces  # noqa: E402

src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
for f in sorted(src.glob("traded-*.json")):
    h = P._revive(json.load(open(f)))
    json.dump(traded_pieces(h), open(dst / f"tpieces-{h['seed']}.json", "w"))
print("reduced", len(list(dst.glob("tpieces-*.json"))))
