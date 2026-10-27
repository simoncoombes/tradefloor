# pt-v22's grade: not run yet

This folder will hold the grade pt-v22 ships on, laid out as `../pt-v21/` is:
the registered rows, the scripts the grading machines ran with
`scripts-as-run.txt`, what they wrote (less the raw histories), the verdict the
preset record carries, and `run-box.sh`. Until the grade has run it holds only
this file.

pt-v22 is in the engine already, so its preset, its record and its known
answer can be checked before the grade. Its values come from a STAND-IN arm:
`tools/presets/arms/pt-v22.txt`, whose `@status` line says `stand-in`. Those
are not the values pt-v22 will ship.

## On release day

1. **Swap in the graded arm**, from the repository's root:

   ```
   python tools/presets/arm_preset.py --arm <the graded arm> --graded
   ```

   It writes the arm file and `ModelParams::pt_v22`, builds the engine, checks
   that pt-v22 is the arm, writes the record's identity (fingerprint,
   coefficients, mechanisms, the arm) and pt-v22's known-answer row, and brings
   the pt-v22 values in `provenance.py` in line. It lists any provenance entry
   that needs a person: a measured value that moved, or a dial the graded arm
   brings into scope or takes out. `python tools/presets/arm_preset.py --check`
   then exits 0.

2. **Publish the grade here**, as `../pt-v21/` is published.

3. **Fill the record's slots.** `python/tradefloor/presets/pt-v22.json` holds
   each block the grade measures as `{"slot": ..., "filled_by": ...}`, and
   `tf.preset_record("pt-v22")` refuses to load it while any slot is empty.
   `tools/presets/record.py` fills them:

   | Slot | Filled by |
   |---|---|
   | `measured`, `panel_252`, `panel_504`, `mechanism_252`, `mechanism_heldout_seeds`, `in_band`, `misses`, `unreadable`, `absent`, `dispersion`, `crisis_lever` | `record.py --panel <the grade's preset_panel.py artefact>` |
   | `structure_252`, `structure_heldout_seeds`, `structure_rise` | `record.py --panel`, or `--structure-rows` when the artefact keeps no per-seed rows |
   | `level_protocol` | `record.py --level-rows <the grade's level_rows.py artefact>` |
   | `long_run` | `record.py --long-run <the grade's criteria.py verdict>` |

4. **Fill the named values the tests hold.** Each is empty until the grade has
   run, and `tests/test_validation.py` fails if some are filled and others are
   not:

   | Where | Name | What |
   |---|---|---|
   | `tools/presets/record.py` | `PT_V22_GRADE_BOXES` | the boxes that ran the grade |
   | `tests/test_validation.py` | `GRADE22["boxes"]` | the same boxes |
   | | `GRADE22["engine_commit"]` | the engine commit the boxes ran |
   | | `GRADE22["package"]` | the package version that commit builds |
   | | `GRADE22["scripts"]` | per box, how many files it unpacked and the sha256 of its archive |
   | | `GRADE22["verdict"]` | the verdict's file under this folder, its result, rows passed and rows graded |

If pt-v22 becomes the default in that release, that is a separate change:
`DEFAULT_PRESET_NAME`, `DEFAULT_SINCE` and `record.py --default-since`, as
pt-v21's move to the default was.
