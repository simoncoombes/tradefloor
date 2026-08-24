# measurements

Raw outputs from calibration and realism work. **Provenance records, not
inputs** — nothing in the library reads these files, and deleting them would
not change a single number the engine produces.

They are here because the tables they produced are inlined in the source,
and a table without its measurement is an assertion.

| file | what it is | what it backs |
|---|---|---|
| `seed-sd-504.json` | Per-statistic sample standard deviation across seeds 101–130 at 504 days | `facts.SEED_SD_504`, whose values are inlined with a `SEED_SD_504_PROVENANCE` block naming this measurement |
| `real-panel.json` | Realism panels for the reference agents, with band verdicts | The envelope's per-statistic intervals |
| `roster.json` | The same panel measured on a balanced roster and a concentrated one | The `roster-concentration` gap — certification was measured on a sector-balanced roster, which no real index is |

The citable artifact is `docs/envelope.json`, which is generated from
`pretium.envelope.certified()` and guarded by a test against drifting from
the module. These are the workings behind it.
