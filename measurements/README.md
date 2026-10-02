# measurements

Raw outputs from calibration and realism work. These are provenance records
rather than inputs. Nothing in the library reads them, and deleting them
wouldn't change a single number the engine produces.

They're kept because the tables they produced are inlined in the source, and
a table without its measurement is an assertion.

| file | what it is | what it backs |
|---|---|---|
| `seed-sd-504.json` | Per-statistic sample standard deviation across seeds 101 to 130 at 504 days | `facts.SEED_SD_504`, whose values are inlined with a `SEED_SD_504_PROVENANCE` block naming this measurement |
| `decay-curve-504.json` | Real-market volatility-clustering decay: per-name absolute-return autocorrelation at lags 1 to 120, per 504-day window | `envelope.REAL_DECAY` and `REAL_DECAY_SLOPE`, checked by `tests/test_envelope.py` |
| `real-panel.json` | Realism panels for the reference agents, with band verdicts | The envelope's per-statistic intervals |
| `roster.json` | The ten-statistic panel of 0.1.0 measured on a balanced roster and three concentrated ones | The `roster-concentration` gap's oldest counts, balanced 9, S&P-like 8 and all-technology 7, kept as history |
| `roster-shapes-pt-v19.json` | `tools/calibration/roster_shapes.py` on pt-v19: five sector mixes, seeds 101 to 130, 252 and 504 days, as collected from an unpublished cloud run (engine pin `ed15e73`, known-answer digest `1e683b96`) | `envelope.ROSTER_SHAPE_ROWS` and `ROSTER_INDEX_DRIFT`, which `check` reads for a sector-concentrated roster on pt-v19 (it refuses one on any other preset, the default pt-v20 included), and which `tests/test_envelope.py` re-scores from this file |
| `roster-shapes-pt-v20.json` | The same tool, mixes, seeds and horizons on pt-v20, run on a Mac at 0.8.6 (known-answer digest `72485a9f`). The balanced mix reproduces pt-v20's certified panels to four places | The `roster-concentration` gap's statement that the mixes do not hold on pt-v20: at 504 days the S&P-like and technology-heavy mixes read `volume_abs_return_corr` at 0.6367 and 0.6332 against a ceiling of 0.63. `check` reads nothing from it, and `tests/test_envelope.py` re-scores it |

The citable artifact is `docs/envelope.json` in the `tradefloor-docs`
repository, generated from `tradefloor.envelope.certified()`. These are the
workings behind it.
