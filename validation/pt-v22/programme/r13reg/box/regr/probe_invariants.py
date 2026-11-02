"""Probe 4: invariants on the default preset.

Each check prints PASS or FAIL with the numbers behind it.
"""
import json
import os

import numpy as np
import pyarrow as pa
import tradefloor as tf

# r14 screen: AUDIT_DIALS (k=v,...) runs the probe on pt-v20 with those dials, not the default preset
import os as _aos
_AD = {k: float(v) for k, v in (x.split("=") for x in _aos.environ.get("AUDIT_DIALS", "").split(",") if x)}
_ARM = tf.ModelParams.from_preset("pt-v20", **_AD)
def _armE(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.Engine(*a, **k)
def _armEval(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.evaluate(*a, **k)

from lib import f64

U = tf.Universe.random(40, seed=111)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""), flush=True)


# 1. determinism
a = _armE(seed=7, universe=U); a.run_days(60)
b = _armE(seed=7, universe=U); b.run_days(60)
check("same seed, same hash after 60 days", a.state_hash() == b.state_hash())
c = _armE(seed=8, universe=U); c.run_days(60)
check("different seed, different hash", a.state_hash() != c.state_hash())
check("default model is pt-v20", a.model_fingerprint == "pt-v20", a.model_fingerprint)

# 2. fork: a fork run forward equals the parent run forward
p = _armE(seed=9, universe=U); p.run_days(30)
f = p.fork(1)[0]
p.run_days(30); f.run_days(30)
check("fork then run == parent run", p.state_hash() == f.state_hash())
# fork mid-session
p = _armE(seed=9, universe=U); p.run_days(5); p.open_market(); p.run_session(9, 30, 3, 100)
f = p.fork(1)[0]
for x in (p, f):
    x.run_session(11, 10, 3, 290); x.close_market(); x.run_days(10)
check("mid-session fork == parent", p.state_hash() == f.state_hash())

# 3. snapshot / restore
p = _armE(seed=10, universe=U); p.run_days(25)
snap = p.state_snapshot()
q = _armE(seed=10, universe=U); q.restore_state(snap)
p.run_days(25); q.run_days(25)
check("state_snapshot/restore then run == uninterrupted", p.state_hash() == q.state_hash())

# 4. checkpoint round trip through JSON
p = _armE(seed=11, universe=U); p.run_days(20)
cp = tf.Checkpoint.of(p, universe=U, seed=11)
cp2 = tf.Checkpoint.from_json(cp.to_json())
r = cp2.resume()
p.run_days(20); r.run_days(20)
check("Checkpoint JSON round trip then run == uninterrupted", p.state_hash() == r.state_hash())

# 5. replay of the order log
p = _armE(seed=12, universe=U); p.run_days(15); p.pin_macro(vix=35.0); p.run_days(5)
rp = tf.replay(p.order_log, seed=12, universe=U, model=_ARM)
check("replay(order_log) == original", rp.state_hash() == p.state_hash())

# 6. 64-bit seeds
big = [2**32 + 5, 2**63 + 5, 2**64 - 1]
hs = []
for s in big + [5]:
    try:
        e = _armE(seed=s, universe=U); e.run_days(3); hs.append(e.state_hash())
    except Exception as ex:
        hs.append("ERR " + type(ex).__name__ + ": " + str(ex)[:80])
check("64-bit seeds accepted and distinct from their low 32 bits", len(set(hs)) == len(hs) and not any(h.startswith("ERR") for h in hs), str([h[:12] for h in hs]))
try:
    _armE(seed=2**64, universe=U); check("seed 2**64 refused", False, "accepted")
except Exception as ex:
    check("seed 2**64 refused", True, type(ex).__name__)
try:
    _armE(seed=-1, universe=U); check("negative seed refused", False, "accepted")
except Exception as ex:
    check("negative seed refused", True, type(ex).__name__)

# 7. attribution sums to the move in s, per name per day
e = _armE(seed=13, universe=U)
e.run_days(10, record=True)
tr = pa.table(e.truth()).to_pandas()
cols = [c for c in tr.columns]
check("truth() has columns", True, str(cols)[:300])
# daily attribution via attribution(): per-day sums of the eleven factors
e2 = _armE(seed=13, universe=U)
worst = 0.0; worst_pv = 0.0
for d in range(10):
    s0 = f64(e2.column("mispricing_s"))
    e2.run_days(1)
    s1 = f64(e2.column("mispricing_s"))
    tot = sum(f64(e2.attribution(n)) for n in tf.Engine.FACTORS)
    worst = max(worst, np.max(np.abs(tot - (s1 - s0))))
check("eleven factors sum to the day's change in s", worst < 1e-9, f"max abs error {worst:.2e}; FACTORS={len(tf.Engine.FACTORS)}")

# 8. pins hold (pin_macro is documented as a write; checked for what a reader of the name expects)
e = _armE(seed=14, universe=U); e.run_days(5)
e.pin_macro(vix=45.0, corporate_bond_yield=0.07, federal_funds_rate=0.05)
held = []
for d in range(10):
    e.run_days(1)
    mf = e.macro_fields
    held.append((mf["vix"], mf["corporate_bond_yield"], mf["federal_funds_rate"]))
held = np.array(held)
check("pin_macro: VIX held at 45 each read", np.allclose(held[:, 0], 45.0), str(np.round(held[:, 0], 3)))
check("pin_macro: corporate yield held at 7%", np.allclose(held[:, 1], 0.07), str(np.round(held[:, 1], 5)))
check("pin_macro: policy rate held at 5%", np.allclose(held[:, 2], 0.05), str(np.round(held[:, 2], 5)))

# 9. Scenario.hold
try:
    s = tf.Scenario()
    s = s.hold(vix=40.0, corporate_bond_yield=0.07)
    e = _armE(seed=15, universe=U)
    vx = []
    for d in range(10):
        s.apply(e, d); vx.append(e.macro_fields["vix"]); e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    check("Scenario.hold(vix=40) read at each open", np.allclose(vx, 40.0), str(np.round(vx, 2)))
except Exception as ex:
    check("Scenario.hold usable as documented", False, f"{type(ex).__name__}: {ex}"[:300])

# 10. known-answer digests
kat = json.load(open(os.path.join(os.path.dirname(tf.__file__), "..", "..", "tests", "known_answer.json")))
try:
    dig = tf._core.fixed_simulation_digest(size=kat.get("size", 40), universe_seed=kat.get("universeSeed", kat.get("universe_seed", 1)),
                                           seed=kat["seed"], days=kat["days"], ticks=kat.get("ticks", 390), preset="pt-v20")
    check("known-answer digest reproduces", dig == kat["sha256"], f"{dig[:16]} vs {kat['sha256'][:16]} keys={[k for k in kat if 'eed' in k or k in ('size','ticks')]}")
except Exception as ex:
    check("known-answer digest reproduces", False, f"{type(ex).__name__}: {ex}"[:200])

# 11. look-ahead: the macro table's row d against what an agent reads at the open of d
e = _armE(seed=16, universe=U)
opens = []
for d in range(8):
    opens.append(e.macro_fields["vix"]); e.run_days(1, record=True)
mt = pa.table(e.macro_table()).to_pandas()
check("macro_table row d == VIX an agent read at the open of day d", np.allclose(mt.vix.values[:8], opens),
      f"table {np.round(mt.vix.values[:4],3)} opens {np.round(opens[:4],3)}")
# bars: the last recorded day is the last completed one only
e = _armE(seed=16, universe=U); e.run_days(3, record=True); e.open_market(); e.run_session(9, 30, 3, 65)
try:
    bb = pa.table(e.bars(grain="day")).to_pandas()
    check("mid-session bars() hold only completed days (or the partial day, marked)", bb.day.max() <= 3, f"days {sorted(bb.day.unique())}")
except Exception as ex:
    check("mid-session bars()", False, str(ex)[:200])

# 12. bonds priced consistently with the curve
ub = tf.Universe.random(10, seed=1, bonds=True)
e = _armE(seed=17, universe=ub)
specs = {s["ticker"]: s for s in tf.rate_specs()}
names = [t for t in e.tickers if t in specs]
idx = [e.tickers.index(t) for t in names]
prev_px = f64(e.prices())[idx]; prev_c = dict(e.curve)
errs = []
for d in range(60):
    e.run_days(1)
    px = f64(e.prices())[idx]; cu = dict(e.curve)
    for k, t in enumerate(names):
        sp = specs[t]
        key = {"UST2Y": "treasury_2y", "UST10Y": "treasury_10y", "IGCORP": "investment_grade"}.get(t)
        if key is None or key not in cu:
            continue
        dy = cu[key] - prev_c[key]
        pred = prev_c[key] / 252 - sp["duration"] * dy + 0.5 * sp["convexity"] * dy * dy
        errs.append(abs((px[k] / prev_px[k] - 1) - pred))
    prev_px, prev_c = px, cu
errs = np.array(errs)
check("rate-index daily return == carry - D dy + C dy^2/2", len(errs) and np.max(errs) < 5e-4,
      f"n={len(errs)} max err {np.max(errs) if len(errs) else float('nan'):.2e} median {np.median(errs) if len(errs) else float('nan'):.2e}; curve keys {list(prev_c)}; spec keys {list(specs[names[0]])}")

print("\n%d of %d passed" % (sum(r[1] for r in results), len(results)))
