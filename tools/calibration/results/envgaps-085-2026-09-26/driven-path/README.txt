The driven 2020-21 window re-run on notebook 09's new path, on a laptop
(macOS arm64, 2026-09-26), with this worktree's build of
integration/0.8.5-envgaps (sim 72485a9f). Notebook 09 moved its credit leg
from the HYG-converted high-yield proxy to Moody's Baa (FRED DBAA) and added
the NBER phases as the cycle in commit 2681878 on integration/0.8.5; the data
file it reads (examples/data/covid-2020-2021.json with the baa column) was
taken from that commit, sha256 prefix in covid-data-sha256.txt.

  driven_baa.py          Baa credit and the phases (notebook 09's path)
  driven_baa_nocycle.py  Baa credit, no phases
  driven_hyg_cycle.py    the HYG credit of ../scripts/driven.py, with phases

Each is ../scripts/driven.py with only the path changed, run as
  REPO=<dir holding examples/data> python driven_baa.py pt-v20,pt-v19 \
      2020,101,102,103,104,105,106 driven-baa.json 8
(the two controls on pt-v20 alone). The logs are their printed summaries.
