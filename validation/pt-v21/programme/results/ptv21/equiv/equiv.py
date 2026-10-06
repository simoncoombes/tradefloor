"""equiv.py byname|dials ARM_LINE OUT.json -- trajectory digests for the pt-v21 equivalence check.

  byname   tf.ModelParams.from_preset("pt-v21")                 (the release engine, rel/0.10-ptv21 931ed3d4)
  dials    tf.ModelParams.from_preset("pt-v20", **R21E1's dials) (ptv21/macro 2024f633, custom-2dc32068)

Five untraded seeds (201-205) for two years (504 sessions) and one traded seed (206, 504 sessions, an agent buying
500 of the first equity at each open and selling it back before each close). Each session: open_market,
run_session(9, 30, 3, 390), close_market. The digest of a seed is sha256 over, per session in order: every
instrument's price after the open, after the session and after the close (float64 bytes), the published macro
fields (sorted keys, floats as exact hex), the economy's phase and the central bank's policy rate from the state snapshot,
the session's volume column, and for the traded seed the agent's fills (sorted JSON). Two builds that agree
bit for bit print the same digests.
"""
import hashlib, json, sys
import numpy as np
import tradefloor as tf

DAYS = 504


def model(mode, arm_line):
    if mode == "byname":
        return tf.ModelParams.from_preset("pt-v21")
    body = arm_line.split(":", 1)[1]
    dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
    return tf.ModelParams.from_preset("pt-v20", **dials)


def run(m, seed, traded):
    u = tf.Universe.random(40, seed=111, bonds=True)
    e = tf.Engine(seed=seed, universe=u, model=m)
    h = hashlib.sha256()
    tick = list(e.tickers)
    specs = {x["ticker"] for x in tf.rate_specs()}
    eq0 = next(t for t in tick if t not in specs)
    f = lambda: h.update(bytes(e.prices()))
    nfills = 0
    for d in range(DAYS):
        e.open_market(); f()
        if traded:
            e.submit("equiv", eq0, 500.0)
        e.run_session(9, 30, 3, 390); f()
        if traded:
            e.submit("equiv", eq0, -500.0)
        e.close_market(); f()
        mf = e.macro_fields
        h.update(json.dumps({k: (float(v).hex() if isinstance(v, float) else v) for k, v in mf.items()},
                            sort_keys=True, default=str).encode())
        s = e.state_snapshot()
        h.update(json.dumps([s["economy"].get("phase"), s["central_bank"].get("policy_rate")], default=str).encode())
        h.update(bytes(e.column("volume")))
        if traded:
            fl = e.take_fills("equiv")
            nfills += len(fl)
            h.update(json.dumps(sorted(fl, key=lambda x: json.dumps(x, sort_keys=True, default=str)),
                                sort_keys=True, default=str).encode())
    return h.hexdigest(), nfills


def main():
    mode, arm_line, out = sys.argv[1], sys.argv[2], sys.argv[3]
    m = model(mode, arm_line)
    res = {"mode": mode, "tradefloor": tf.version(), "fingerprint": str(m.fingerprint), "days": DAYS, "seeds": {}}
    for seed, traded in ((201, False), (202, False), (203, False), (204, False), (205, False), (206, True)):
        dg, nf = run(m, seed, traded)
        res["seeds"][str(seed)] = {"traded": traded, "digest": dg, **({"fills": nf} if traded else {})}
        print(mode, seed, "traded" if traded else "", dg, nf if traded else "", flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
