"""ptv22_pieces.py ADIR OUT [I/N]: each `chain` history's pieces (`ptv22.chain_pieces`), one gzip pickle per history
(`OUT/chain-<seed>.pkl.gz`), for `ptv22_passprob.py`. A history's pieces are a few hundred kilobytes where its
record is 150 MB, so a box sends these back and keeps the records. I/N takes every N-th history from the I-th,
so N copies share the work."""
import gzip
import pathlib
import pickle
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ptv22 as P  # noqa: E402

adir, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
shard, of = map(int, (sys.argv[3] if len(sys.argv) > 3 else "0/1").split("/"))
for f in sorted(adir.glob("chain-*.json"))[shard::of]:
    h = P._revive(__import__("json").load(open(f)))
    with gzip.open(out / f"{f.stem}.pkl.gz", "wb") as g:
        pickle.dump(P.chain_pieces(h), g, protocol=4)
    print(f.stem, flush=True)
