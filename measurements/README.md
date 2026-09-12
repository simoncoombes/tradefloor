# measurements

Raw outputs from calibration and realism work. Nothing in the library reads
them, and deleting them wouldn't change a single number the engine produces.
They're kept because the tables they produced are inlined in the source.

| file | what it is | what it backs |
|---|---|---|
| `seed-sd-504.json` | Per-statistic sample standard deviation across seeds 101 to 130 at 504 days | `facts.SEED_SD_504`, whose values are inlined with a `SEED_SD_504_PROVENANCE` block naming this measurement |
| `real-panel.json` | Realism panels for the reference agents, with band verdicts | The envelope's per-statistic intervals |
| `roster.json` | The same panel measured on a balanced roster and a concentrated one | The `roster-concentration` gap, since certification was measured on a sector-balanced roster and no real index is one |

The citable artifact is `docs/envelope.json`, generated from
`tradefloor.envelope.certified()` and guarded by a test against drifting
from the module.
