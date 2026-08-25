"""Read the survey for the decay exponent: what moves it, and what it costs.

§55 to §57 approached this one hand-picked vector at a time, and §57 showed
nine points cannot separate a real trade from statistics jittering across
band edges. This asks the survey, now that decay_slope_504 is one of its
recorded outputs (§56a).

Real markets read -0.436. pt-v6 reads -0.917. Closer to zero is better.
"""
import json, sys
from pretium.atlas import Survey

TARGET = "decay_slope_504"
s = Survey.load(sys.argv[1])
outs = s.outputs()
print(f"  outputs: {len(outs)}   decay present: {[o for o in outs if 'decay' in o]}")
if TARGET not in outs:
    raise SystemExit("  no decay_slope_504 recorded")

def show(label, obj, n=1400):
    print(f"\n=== {label} ===")
    print("  " + json.dumps(obj, default=str, indent=1)[:n].replace("\n", "\n  "))

show(f"what moves {TARGET}", s.sensitivity(TARGET))
show("frontier: slope toward zero against lowest loss",
     s.pareto({TARGET: "max", "loss": "min"}))
show("moves nothing", s.unidentified([TARGET, "loss"]))
