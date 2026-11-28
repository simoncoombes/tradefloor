"""Generate the cached markets every probe reads.

  short: AUDIT_SHORT_N (60) one-year markets on the certified roster (Universe.random(40, seed=111)),
         seeds from AUDIT_SEED_SHORT (2101)
  var:   AUDIT_SHORT_N one-year markets on a roster that changes with the seed, seeds from AUDIT_SEED_VAR (2201)
  long:  AUDIT_LONG_N (30) 21-year markets (5,292 sessions), roster seed = seed, seeds from AUDIT_SEED_LONG (2301)
The seed variables are the thirteenth registration's (defaults: the held-out screen's first seeds).
"""
import sys
import time

import os
from lib import gen_many
NS = int(os.environ.get('AUDIT_SHORT_N', '60')); NL = int(os.environ.get('AUDIT_LONG_N', '30')); DL = int(os.environ.get('AUDIT_LONG_DAYS', '5292'))
S0, V0, L0 = (int(os.environ.get(k, d)) for k, d in (('AUDIT_SEED_SHORT', '2101'), ('AUDIT_SEED_VAR', '2201'), ('AUDIT_SEED_LONG', '2301')))

def main():
  which = sys.argv[1:] or ["short", "var", "long"]
  t = time.time()
  if "short" in which:
      gen_many([dict(seed=s, useed=111, n=40, days=252) for s in range(S0, S0 + NS)])
      print("short", time.time() - t, flush=True)
  if "var" in which:
      gen_many([dict(seed=s, useed=s, n=40, days=252) for s in range(V0, V0 + NS)])
      print("var", time.time() - t, flush=True)
  if "long" in which:
      gen_many([dict(seed=s, useed=s, n=40, days=DL) for s in range(L0, L0 + NL)])
      print("long", time.time() - t, flush=True)


if __name__ == "__main__":
  main()
