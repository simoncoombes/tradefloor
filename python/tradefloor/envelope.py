"""The realism envelope, as data: what tradefloor certifies, and what it does not.

`tradefloor-docs: docs/realism-envelope.md` states the envelope in prose.
This module states it in a form a program can read, so a user does not have
to remember a page to find out whether their question is one this simulator
can answer.

Two things live here, and neither is a score.

**Per-statistic intervals** (`intervals`). Every panel statistic is reported
with the spread it actually has across seeds, not as a bare median. A point
estimate from a stochastic simulator invites a precision it does not have.
`abs_return_acf20` is the example, and the number is read out of `CERTIFIED`
below rather than typed here: it reads about +0.0017 at the shipped preset,
against a thirty-seed spread wide enough that a single seed lands either
side of zero. The band distance is reported in units of that spread, which
is the same weighting `tradefloor.loss` uses -- so "how far out" is
denominated in the model's own noise rather than in the statistic's
arbitrary units.

This paragraph said +0.0087 until 2026-09-14, which is no preset's reading
on the current roster generator and was five defaults old while `CERTIFIED`
a hundred and forty lines below it read 0.0017. The sentence warning against
quoting a stale point estimate was quoting one, which is the plainest case
this module contains for why a published number needs a producer rather than
a careful author (`programme/results/stale-constants.md`).

**A membership check** (`check`). Given a horizon, the statistics a strategy
leans on, and the shape of the roster, it answers whether the question falls
inside the envelope, and when it does not, says which measurement says so.

## Why there is no single confidence number

`tradefloor.loss.compare_to_real_markets` refuses to emit one realism score,
and this module does not reopen that. Aggregation destroys the only
information that matters here: a model is realistic in some respects, at some
measurement scale, and not others. Modesty has nothing to do with it.

There is also a practical failure mode. A scalar travels and a caveat does
not. "87% realistic" is quotable in a way that "volatility memory is no
longer distinguishable from zero by lag 20 and is negative by lag 45, where
real markets stay weakly positive out to lag 60"
is not, so a single number reliably becomes the thing people cite INSTEAD
of the gaps -- which is exactly backwards, because the gaps are what decide
whether a result means anything. A boolean with reasons attached cannot be
quoted without its reasons.

## Provenance

The constants below are measurements, not judgements, and every one is
reproducible from the tooling in `tools/calibration/`. They describe the
shipped default preset, which `PRESET` names.

Nothing here re-checks that at call time. `check` reads these constants and
the question you asked; it never looks at the engine you are about to run.
So if `tradefloor.model_preset()["name"]` is not `PRESET`, this module is
describing a different model than the one you are running and will NOT say
so. This docstring claimed until 2026-08-27 that `check` said so, and it
never did. The comparison is one line and belongs beside any citation of
these numbers:

    tradefloor.model_preset()["name"] == tradefloor.envelope.PRESET
"""

from __future__ import annotations

import math
import statistics
import textwrap
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ._core import ValidationError
from . import facts as _facts
from .facts import (CERTIFIED_HORIZON_DAYS, REAL_MARKETS, SEED_SD,
                    SEED_SD_504, band_distance)

#: The preset these measurements describe.
PRESET = "pt-v19"

#: The measurement horizon the envelope certifies, in trading days.
#: Not a soft preference, and not a band count either. What holds the
#: horizon here is that `CERTIFIED` was MEASURED here, on thirty seeds and
#: two held-out axes. The 504-day table beside it (`MEASURED_504`) is
#: measured and not certified, and at this default one row is outside it:
#: `sector_excess_corr`, 0.10421 against a floor of 0.11.
#:
#: The old reason -- that the thinnest 504-day row cleared its ceiling by
#: only 0.11 -- no longer applies: `annualised_vol_pct` read 33.89 under
#: pt-v12, 30.24 under pt-v14, 28.12 under pt-v16, 25.40 under pt-v18 and
#: 23.39 under pt-v19, against the same 34.0 ceiling throughout. The
#: horizon stays 252 because that is where the certification was measured,
#: not because 504 is fragile.
#:
#: CORRECTED 2026-09-14. This comment read "since pt-v12 all fourteen are
#: in band at 504 days as well, and every default since has held them there
#: with more room again", and gave pt-v19's 504-day `annualised_vol_pct` as
#: 23.81. Both described the pt-v19 of `f317f8d` -- pt-v18 plus four dials,
#: `sector_loading` 0.8 -- and not the vector that ships. 23.8123 was that
#: preset's reading, and it stayed in this prose through four regenerations
#: of `MEASURED_504` beneath it (25.1132, 28.9478, 28.0585 and now 23.3899),
#: which is how a figure measured on one vector comes to be quoted for
#: another. `presets/pt-v19.json` reads 23.3899 and puts one row out.
#:
#: This comment read "three statistics that are in band here leave it by 504
#: days" until 2026-08-27, which described pt-v3. `check` refuses to certify
#: beyond this horizon.
#:
#: Imported from `facts` rather than restated, so the horizon the bands were
#: derived at and the horizon the envelope certifies cannot drift apart: they
#: are one number, and `facts.RULERS_BY_HORIZON` is keyed on it.

#: Measured at the certified horizon: 30 seeds, 40 instruments, 252 days.
#:
#: CORRECTED 2026-09-14, and the correction is about what a count means as
#: much as about its value. This comment read "ALL FOURTEEN in band, at a
#: band-distance loss of 0.0000, and all fourteen again on a held-out
#: 60-name universe", plus "since pt-v12 all fourteen are ALSO in band at
#: 504 days". That is a count quoted as a quality score for the default
#: preset, and the count does not carry that meaning. `facts.REAL_MARKETS`
#: is derived from 2015-2025 windows; the 0.8.0 scoring rule was re-centred
#: on the whole tape and these bands were not, so the count grades a preset
#: against a ruler the project stopped scoring with. A band result is stated
#: as the rows that are out and their distances, never as a bare count.
#:
#: What the rows do at pt-v19, from `presets/pt-v19.json`: `sector_excess_
#: corr` is out at 0.1011 against a floor of 0.11 at 252 days and 0.10421
#: against 0.11 at 504, in all four cells of the record, and no other row is
#: out in any cell. That reading is what `sector_loading` 0.60 was derived
#: to produce against the whole-tape centre 0.1178. The values in this dict
#: WERE NOT pt-v19's until 2026-09-14: all fourteen were the pre-31ef261
#: vector's and disagreed with the record, which is `defect-26`. They are
#: written from `presets/pt-v19.json` by `tools/presets/envelope_tables.py`
#: now. The stale `sector_excess_corr` read 0.1809, inside the band, where
#: the measured 0.1011 is outside it, so the row above became visible here
#: only when the table stopped being stale. As an arithmetic check on the
#: dict below rather than as a score, `envelope.score` returns
#: thirteen of fourteen in band at this default, and the row it counts out
#: is `sector_excess_corr`.
#:
#: This comment read "nine of ten" until 2026-08-26. It described pt-v3, and
#: survived two era boundaries and four statistics being added to the panel
#: because nothing tests a comment. The counts below are what
#: `envelope.score` actually returns.
#:
#: What "held-out" means here, exactly, because the word carries more weight
#: than it earns if left alone. It means simulation seeds the calibration
#: never drew and a roster it never ran, so it tests that the fit generalises
#: across draws rather than sitting on thirty lucky ones. It does NOT mean a
#: withheld sample of real market data: the bands in `facts.REAL_MARKETS` are
#: derived from real-market windows once and used both to tune and to grade,
#: with no empirical train/test split behind them. And the held-out universe
#: comes from the same `Universe.random()` generator as the training one, a
#: different draw rather than a different market -- `GAPS`
#: "roster-concentration" measures what changes when the roster's SHAPE
#: changes, and it changes the count.
#: The SHAPE rows only. The level and crisis rows are held in
#: `CERTIFIED_LEVEL` and `CERTIFIED_CRISIS`, because the two kinds are
#: certified separately: a green panel means the fourteen shape rows are in
#: band, and the level and crisis rows are reported beside them with their
#: own verdicts. Those verdicts were red at every default through pt-v16 and
#: green at pt-v18 and pt-v19; the split is a statement about PROTOCOL, not about
#: failure, and it stays whichever way the verdicts read. A row the default
#: preset fails is never widened to pass and never folded into this count.
CERTIFIED: dict[str, float] = {
    "annualised_vol_pct": 23.5563,
    "excess_kurtosis": 8.5609,
    "return_acf1": -0.0062,
    "abs_return_acf1": 0.0455,
    "abs_return_acf5": 0.0315,
    "abs_return_acf20": 0.0029,
    "cross_sectional_corr": 0.3282,
    "volume_abs_return_corr": 0.4949,
    "leverage_effect": -0.0474,
    "volume_change_acf1": -0.2765,
    "corr_asymmetry": 0.0119,
    "corr_asymmetry_lagged": 0.1424,
    "sector_excess_corr": 0.0956,
    "corr_persistence_acf1": 0.1248,
}

#: The LEVEL rows the default preset reads at the certified horizon,
#: measured as a thirty-seed mean on `facts.LEVEL_PROTOCOL` -- seeds 101 to
#: 130, 252 days, the roster varying WITH the seed, because a level that
#: describes the MODEL cannot be measured on one draw (`facts.AGGREGATE`).
#:
#: Measured 2026-09-14 on the box run `levelproto`, at pin `f3cf10e`, by
#: `tools/presets/level_panel.py` and `level_rows.py`. It replaces a reading
#: from `b4fix10` at `2d83167`, which described the pt-v19 of that day:
#: nineteen of the preset's coefficients moved at 31ef261 and the block was
#: then deleted by a `record.py --panel` regeneration, leaving this table
#: with nothing measured behind it. That is `defect-26`, and the deletion is
#: why `record.py --panel` now carries the block forward or drops it by
#: name.
#:
#: The outgoing default, pt-v18, was measured on the same build, the same
#: protocol and the same seeds in the same run, and reproduced the four
#: constants its own record publishes to all four printed places, so these
#: readings replace those on one ruler rather than beside another. Those
#: four were first measured at `ee22c65` under 0.6.2 and have now reproduced
#: twice across the 0.7.0 and 0.8.0 boundaries.
#: `python/tradefloor/presets/pt-v19.json` carries both under
#: `level_protocol`, and `tests/test_preset_records.py` binds this table to
#: it -- the binding `DECAY_252` below still does not have. The table
#: itself is WRITTEN from that record by `tools/presets/envelope_tables.py`
#: rather than typed, since 0.8.0.
#:
#: The protocol is part of the number and not a detail of it: pt-v16 read
#: -13.6431 with the roster varying against +1.9740 with it held, a gap of
#: 15.6 points on one preset, because a drawn roster opens away from fair
#: value by a draw worth several points of first-year drift.
CERTIFIED_LEVEL: dict[str, float] = {
    # The default preset RETURNS 6.62 per cent a year, inside a band of 2.90
    # to 11.90 at band position 0.41, on a thirty-seed standard error of
    # 1.19 -- so 3.13 standard errors above the floor. pt-v18 read 5.7957 at
    # position 0.32; pt-v16 read -13.6431 and was held red here for three
    # eras, and this row exists because of that. The seed spread is wide
    # against the band: the thirty-seed standard deviation is 6.52, so a
    # single seed's first year says almost nothing about the row.
    "index_drift_pct": 6.2972,
}

#: The CRISIS rows, reserved for the fear gauge and the index tail, measured
#: on the same run and protocol as the level row (`facts.LEVEL_PROTOCOL`).
#:
#: All three are IN BAND at this default. They are still reported apart from
#: the fourteen shape rows, because they are certified on a different
#: protocol and a green shape panel says nothing about them -- not because
#: they are failing. `check` computes each verdict rather than asserting it;
#: it asserted "held red" here until 0.7.0, which was true of every default
#: through pt-v16 and would have been a false statement the day one held.
CERTIFIED_CRISIS: dict[str, float] = {
    # The -1 per cent row reads 1.9359 in a band of 0.39 to 3.03, at band
    # position 0.59, 0.23 ABOVE a centre of 1.71 (recomposed 2026-09-20;
    # 1.7719 at position 0.52 from 2026-09-14). pt-v18 reads 1.5834 at
    # position 0.45 and pt-v16 0.9500 at 0.21.
    #
    # THOSE THREE POSITIONS ARE NEW AND THE ROW'S STORY CHANGED WITH THEM.
    # Until 2026-09-15 this band was the 2015-2025 decade's 0.70 to 4.03,
    # where the same three readings sat at 0.32, 0.27 and 0.09, and the
    # sentence here said they were "below centre on every default this
    # project has shipped", which was the reading that made a count of
    # rows-in-band an insufficient answer. Against the whole tape the
    # default is not low: it is a twentieth of the band's width above
    # centre. The model did not move. The ruler did, under
    # `ruling-longest-tape-per-row`, and the old sentence was measuring the
    # decade rather than the model.
    #
    # WHAT DID NOT CHANGE, and it is the half that still says the row is not
    # settled: `loss.rule_table` still scores this row against the decade
    # centre of 2.66, because moving a centre is a scoring-rule change and
    # not a band change. So the row is mid-band on its band and 3.1 tape
    # errors low on its score at the same time. `facts` carries the split in
    # `REAL_MARKETS_PROVENANCE["fear_gauge_dn1"]`.
    #
    # The -3 per cent row reads 6.4046 (recomposed 2026-09-20; 6.3920 on 52
    # pooled sessions from 2026-09-14), at band position 0.55, a little
    # above the centre of 6.09. pt-v18 read
    # 3.2473 at position 0.09, close to the floor of 2.60; pt-v16 read
    # 1.9557 and was BELOW it. The VIX level identity and the symmetric
    # fall-rate are what moved it, and this is the row they were composed to
    # move. Read the session count beside the value: 52 sessions is thin,
    # and the same row stood on 118 under the pre-31ef261 vector, so the
    # median moved on fewer and deeper falls rather than on more of them.
    "fear_gauge_dn1": 1.8249,
    "fear_gauge_dn3": 5.5230,
    # The index tail row on the same thirty seeds: 79 sessions at or below
    # -3 per cent in 7,530, a pooled rate of 1.0491 per cent against a band
    # of 0.47 to 1.96 and a tape centre of 1.2132. IN band, at band position
    # 0.39, 0.4 of a standard error (0.43) below the tape's centre.
    # Recomposed 2026-09-20: from 2026-09-14 the row read 0.6906 on 52
    # sessions at position 0.15, one standard error below the centre, and
    # the stochastic level was what held it down. pt-v18 read 1.5803 at
    # position 0.75 on the same build and seeds, so this preset has roughly
    # a third of pt-v18's crash sessions. The row is the one that says when
    # there are too many crash sessions; at this default the question is
    # whether there are too few, and the verdict alone does not answer it.
    #
    # The three counts beside it, which the rate cannot see: 19 of 30 seeds
    # hold no such session (the tape's 35 windows hold 13; pt-v18 held 11),
    # 3 of 30 hold five or more (the tape 7, pt-v18 5), and the worst seed
    # holds 24 (the tape 33, pt-v18 39). Much more mass at zero than the
    # tape and a lighter far end.
    #
    # GRADED AND NOT COUNTED at this preset: `cycle_stationary_opening` is
    # 0.0, so every seed opens in expansion at phase age zero and this is
    # year one of a non-stationary opening. `envelope.tail_block` carries
    # that as data beside the verdict. The 504-day reading is NOT measured
    # on this vector: `levelproto` ran 252 days only, and the year-two
    # figure that stood here (1.2989 per cent) was the pre-31ef261 vector's.
    "index_tail_dn3_pct": 0.6773,
}

#: THE STRUCTURAL ROWS: the fourth certification block, and the only one
#: whose rows have no band at all.
#:
#: `CERTIFIED` is the fourteen shape rows, `CERTIFIED_LEVEL` and
#: `CERTIFIED_CRISIS` the rows certified on `facts.LEVEL_PROTOCOL`. All
#: three are graded against a WIDTH: a band that says what a real year could
#: read. This one is not, because its row has no band in this library and
#: `facts.RULED_UNREADABLE` says so at both horizons -- the adoption is
#: ruled and the table entry has never landed.
#:
#: What the row does have is the tape itself: `facts.REAL_VIX_AR1_WINDOWS`
#: is 35 per-window readings at 252 days, median 0.929939 after
#: `debias_ar1`, on a standard error of 0.010997. A centre, an error, and no
#: width is the shape an exact sign test reads, and
#: `facts.structure_verdict` is that test. `certify_structure` runs it on
#: the per-seed panels a certification run already produces.
#:
#: A SECOND GATE BESIDE THE PANEL, NOT A FIFTEENTH PANEL ROW, and that is
#: Simon's ruling and not a convenience. The row enters no `in_band` count,
#: no `misses` list and no mechanism count; it is scored on its own, written
#: onto a record under its own field, and read by a bar of its own.
#: `tools/calibration/preset_panel.py` has measured and reported it,
#: ungraded, since the retain repair, and nothing about that changes: what
#: is new is that a verdict is now taken on it and refused on.
#:
#: THE VALUE IS THE DEFAULT PRESET'S READING, like the three tables above,
#: and this default is REFUSED on it. The recomposed pt-v19 (2026-09-20)
#: reads 0.954280 as the median of thirty seeds at 252 days, 0.0243 above
#: the tape's centre, with k = 25 of 30 seeds above the centre against a
#: cut of 21, and 22 of 30 on the held-out seeds. The 2026-09-14 vector read
#: 0.959419 with k = 21, exactly at the cut, and 28 held out. The outgoing
#: pt-v18 PASSES on both panels, at k = 20 and k = 18. The block is
#: laid down carrying that refusal rather than withheld until it is green:
#: the gate is a non-regression bar, a preset with no prior record has
#: nothing to regress from, and a row nobody can see is a row nobody fixes.
#: A model that repairs it locks the PASS in for every model after it.
CERTIFIED_STRUCTURE: dict[str, float] = {
    "vix_ar1_debiased": 0.948344,
}

#: The default preset's RISE in each structural row from 252 to 504 days,
#: the second gate's one verdict since 2026-09-21 (`facts.REAL_VIX_AR1_RISE`,
#: `facts.structure_rise_verdict`). None until the record carries the block;
#: `test_structure_gate` binds it to the record once it does.
CERTIFIED_STRUCTURE_RISE: dict[str, float | None] = {
    "vix_ar1_debiased": 0.011554,
}

#: Bands re-derived at a 504-day window, from the same reference roster and
#: estimators as `facts.REAL_MARKETS`. Scoring a 504-day measurement against
#: the 252-day bands is the wrong ruler, and it flatters the model on
#: kurtosis while being harsher elsewhere -- these are mostly TIGHTER.
BANDS_504: dict[str, tuple[float, float]] = {
    # The level band is an annualised long-run mean whose width is the
    # centre's own uncertainty, so the same band grades a 504-day reading;
    # the model's resolution at 504 days is finer, and the centre's is not.
    "index_drift_pct": (2.9, 11.9),
    # The fear rows' bands are per-session statistics whose real-side
    # derivation is per 252-session window, and a 504-day reading pools
    # twice the sessions against the same real distribution.
    #
    # THE TWO ROWS DIFFER HERE AND THEY DID NOT BEFORE 2026-09-15. The -1
    # per cent row now carries its OWN 504 band, re-derived on 15 non-crisis
    # 504-session windows of the whole tape rather than reusing the 252 one:
    # the windows exist at both lengths, so reusing would be a choice where
    # a derivation was available. It is TIGHTER than the 252 band at both
    # edges, 2.14 wide against 2.64, because pooling twice the sessions per
    # window narrows the spread across them. The -3 per cent row keeps the
    # 252 band at both horizons, because its windows are conditioned on
    # holding at least five qualifying sessions and re-cutting at 504 would
    # change what the condition selects as well as the window length.
    # `facts.REAL_MARKETS_PROVENANCE` carries both derivations.
    "fear_gauge_dn1": (0.59, 2.73),
    "fear_gauge_dn3": (2.60, 9.58),
    # The index tail row's band is a per-SESSION rate, so a longer window
    # measures the same quantity with more sessions rather than a different
    # one, and the 252-day band grades both horizons. That is a derivation
    # and not a reuse: the same construction and the same anchor rule on
    # seventeen non-overlapping 504-return windows of the same series give a
    # centre of 1.2372 (106 hits in 8,568), an across-window sd of 1.6916, a
    # standard error of 0.4103 and a raw band of [0.4797, 1.9946], which
    # rounds outward to [0.47, 2.00] -- the same band within a twentieth of
    # its own width.
    # `facts.INDEX_TAIL_WINDOWS` carries both window sets and
    # `tests/test_reference_windows.py` re-derives both bands from them.
    # What the longer horizon does change is the MODEL's own resolution,
    # which improves by about a third, and `certify` reports that as `se_m`
    # beside the verdict.
    "index_tail_dn3_pct": (0.47, 1.96),
    "annualised_vol_pct": (16.0, 34.0),
    "excess_kurtosis": (7.1, 22.0),
    "return_acf1": (-0.03, 0.04),
    "abs_return_acf1": (0.04, 0.22),
    "abs_return_acf5": (0.02, 0.10),
    "abs_return_acf20": (-0.02, 0.07),
    "cross_sectional_corr": (0.23, 0.41),
    "volume_abs_return_corr": (0.48, 0.65),
    "leverage_effect": (-0.13, 0.02),
    "volume_change_acf1": (-0.29, -0.21),
    "corr_asymmetry": (-0.04, 0.13),
    "corr_asymmetry_lagged": (-0.10, 0.47),
    "sector_excess_corr": (0.11, 0.22),
    "corr_persistence_acf1": (0.19, 0.49),
}

# So `facts.check_ruler_horizon` can identify this table when it arrives as an
# argument. `facts` cannot name it -- `envelope` imports `facts`, not the
# other way -- and a table the checker cannot identify is a table it cannot
# refuse.
_facts.register_ruler_table(BANDS_504, 504, "envelope.BANDS_504")

# And its BASIS, because this is the table `score` actually grades a 504-day
# panel with and it was the one band table in the library with no entry in
# `facts.BAND_BASIS`. A table that can grade a record and cannot say what it
# is, is the defect that dict exists for; leaving the library's own 504 ruler
# out of it would have been that defect with a smaller blast radius.
_facts.BAND_BASIS["envelope.BANDS_504"] = {
    "era": "2015-07..2025-07",
    "roster": "the certified forty, exactly",
    "n_windows": 5,
    "rule": "spread",
    "tolerance": 0.16800,
    "rows": len(BANDS_504),
    "note": "the fourteen shape rows of facts.REAL_MARKETS_504 plus the "
            "level and crisis rows carrying their 252-day bands, each "
            "argued inline above. It is NOT facts.REAL_MARKETS_504: that "
            "table has fourteen rows and this one has seventeen, and "
            "facts.report and preset_panel grade the same 504-day panel "
            "against the fourteen-row one",
}

#: The seventeen-row ruler per horizon, which is what `score` grades with.
#: `facts.RULERS_BY_HORIZON` holds the fourteen shape rows at 504;
#: `BANDS_504` adds the level and crisis rows carrying their 252-day bands,
#: with the argument for each stated inline above. Keyed on the horizon and
#: looked up rather than chosen by `horizon_days > 252`, which is what let a
#: 756-day or 1,008-day panel be scored against the 504-day bands without
#: anything saying so.
#:
#: The names are module-qualified and they name the table this function
#: ACTUALLY grades with. `score` reported `"REAL_MARKETS_504"` at 504 days
#: while scoring against `BANDS_504`, which is a different table with three
#: more rows -- a small thing, and the same shape as every finding this
#: branch is repairing: a label asserting a provenance the code did not have.
RULERS_BY_HORIZON: dict[int, tuple[dict[str, tuple[float, float]],
                                   dict[str, float], str]] = {
    CERTIFIED_HORIZON_DAYS: (REAL_MARKETS, SEED_SD, "facts.REAL_MARKETS"),
    504: (BANDS_504, SEED_SD_504, "envelope.BANDS_504"),
}

#: The same lookup with the BASIS as its first key, because a horizon does
#: not determine a band table and pretending it does is the defect this
#: block repairs.
#:
#: `shipped` is the 2015-2025 decade set above, unchanged, and it is still
#: what every existing caller gets. `ruled` is the band the project ACTUALLY
#: RULED: `ruling-the-ruler-is-the-universal-band` for the fourteen shape
#: rows and `ruling-longest-tape-per-row` for the two whole-record rows.
#: Until this dict landed the ruled table was registered in
#: `facts._KNOWN_TABLES` and read by nothing, so the release bar was ruled
#: against one object and computed against another.
#:
#: THE DEFAULT IS `ruled` SINCE 2026-09-15. It read `shipped` from the day
#: this dict landed until then, on the argument that flipping it would move
#: every count in forty-odd tools and tests in one commit. That argument
#: was about cost, not about which band is right, and the ruling it was
#: waiting on -- `ruling-the-ruler-is-the-universal-band` -- had already
#: been made. While it read `shipped`, `facts.REAL_MARKETS` was what
#: actually graded every caller that did not know to ask for a basis, which
#: is every caller written before the argument existed. The one name that
#: carries it is `facts.DEFAULT_BAND_BASIS`, imported below; the provenance
#: is stated there.
RULERS_BY_BASIS: dict[str, dict[int, tuple[dict[str, tuple[float, float]],
                                           dict[str, float], str]]] = {
    "shipped": RULERS_BY_HORIZON,
    "ruled": {
        CERTIFIED_HORIZON_DAYS: (_facts.REAL_MARKETS_RULED, SEED_SD,
                                 "facts.REAL_MARKETS_RULED"),
        504: (_facts.REAL_MARKETS_RULED_504, SEED_SD_504,
              "facts.REAL_MARKETS_RULED_504"),
    },
}

#: The basis `score` grades with when a caller names none. RE-EXPORTED, not
#: redefined: `facts` needs the same default for `compare_to_real_markets`
#: and `report`, `envelope` imports `facts` and not the reverse, so the one
#: definition lives there and every producer in the library reads it. Two
#: modules each holding their own "the default" is how the shipped library
#: came to grade a panel two different ways at 504 days.
DEFAULT_BAND_BASIS = _facts.DEFAULT_BAND_BASIS

#: The basis the RELEASE BAR is read on, which is not the same question.
#: `certify` reports this one beside the default and stamps both bases.
BAR_BAND_BASIS = "ruled"

#: The same panel at 504 days, against `BANDS_504`.
#:
#: CORRECTED 2026-09-14. This comment opened "ALL FOURTEEN in band against
#: `BANDS_504`, which pt-v12 was the first preset to manage", after reading
#: "five of ten" until 2026-08-26 (pt-v3, against the ten-statistic panel of
#: the time) and then "thirteen of fourteen, missing only
#: volume_change_acf1" (pt-v10 and pt-v11). Each of those quoted a count as
#: a preset's quality figure, which it is not: `BANDS_504` is derived from
#: 2015-2025 windows and the 0.8.0 scoring rule is not. At pt-v19 the row
#: out at this horizon is `sector_excess_corr`, 0.10421 against a floor of
#: 0.11, and it is the only one. The values below were stale in the same way
#: `CERTIFIED` was (`defect-26`) and were rewritten from the record on
#: 2026-09-14. Two rows changed their verdict when they stopped being stale:
#: `sector_excess_corr` read 0.1829 inside the band against a measured
#: 0.1042 outside it, and `corr_persistence_acf1` read 0.1493 BELOW the
#: floor of 0.19 against a measured 0.3293 inside. The stale table was wrong
#: in both directions.
#:
#: Read the headroom, not just the count. Under pt-v12 this table's thinnest
#: row was `annualised_vol_pct` at 33.89 against a ceiling of 34.0 -- 0.11 of
#: room on a statistic whose seed spread is far wider, so the count was
#: genuine but would have flipped on a change that barely moved the model.
#: pt-v19 reads 23.3899 there, 10.61 of room, having widened it at each of
#: the 0.6.0, 0.7.0 and 0.8.0 boundaries. That figure read 23.81 here until
#: 2026-09-14, which was the four-dial pt-v19 of `f317f8d` and not this one.
#:
#: The count is still MEASURED rather than certified: the certified horizon
#: is 252 because that is where `CERTIFIED` was measured.
MEASURED_504: dict[str, float] = {
    "annualised_vol_pct": 23.4833,
    "excess_kurtosis": 10.0293,
    "return_acf1": -0.0055,
    "abs_return_acf1": 0.0532,
    "abs_return_acf5": 0.0331,
    "abs_return_acf20": 0.0113,
    "cross_sectional_corr": 0.3170,
    "volume_abs_return_corr": 0.5317,
    "leverage_effect": -0.0448,
    "volume_change_acf1": -0.2517,
    "corr_asymmetry": 0.0168,
    "corr_asymmetry_lagged": 0.1365,
    "sector_excess_corr": 0.1074,
    "corr_persistence_acf1": 0.2104,
}

#: |return| autocorrelation at the certified horizon, against real markets.
#: The model reads BELOW real at every measured lag, from 0.0477 against
#: 0.1071 at lag one. It is positive and resolved out to lag 12, it is not
#: distinguishable from zero at lags 20 and 30, and it is negative at about
#: two and a half standard errors by lag 45, where real markets stay weakly
#: positive out to lag 60.
#:
#: MEASURED 2026-09-14 on pt-v19, at thirty seeds on the protocol `CERTIFIED`
#: is measured on -- the roster HELD at `Universe.random(40, seed=111)`,
#: seeds 101 to 130, 252 days -- by `programme/scripts/decay-curve.py` in the
#: design repository. Lags 1, 5 and 20 ARE `CERTIFIED`'s `abs_return_acf1`,
#: `abs_return_acf5` and `abs_return_acf20`: the same per-name estimator on
#: the same bars, so the two tables cannot disagree, and the measured
#: residual between them is 0.0e+00 at all three lags.
#:
#: REPLACED, NOT UPDATED, 2026-09-14, and the distinction is the finding.
#: This table read 0.1413, 0.1063, 0.0897, 0.0496, 0.0371, 0.0173, 0.0082,
#: -0.0052, -0.0120 and -0.0142, published as pt-v14's and carried unchanged
#: across pt-v16, pt-v18 and pt-v19 while every other constant in this module
#: moved at 0.6.0, 0.7.0 and 0.8.0. Re-measuring pt-v14 itself on the
#: protocol above does not reproduce it: lag one reads 0.0721 against the
#: published 0.1413, lag two 0.0535 against 0.1063, lag three 0.0474 against
#: 0.0897, while lag 20 reads 0.0078 against 0.0082 and lag 45 -0.0126
#: against -0.0120. The head was about twice the measured value and the tail
#: sat inside the seed error, which is the shape of a different MEASUREMENT
#: rather than of a different preset -- and the protocol the old curve was
#: taken on is recorded in no file in either repository. So there was no
#: ruler on which the retired values and these are the same quantity, and
#: they were replaced rather than corrected.
#:
#: What must NOT fill this table: `atlas_survey.decay_slope` fits the same
#: quantity through lags 1, 5 and 20 only. Substituting it would put a
#: three-point estimator and a ten-point one under one name, which is the
#: error this project has made three times and documents in
#: `facts.REAL_TAIL3`, `crisisprobe-frontier` and the VIX AR1 row.
#:
#: This table is still TYPED rather than written from the preset record, and
#: that is the remaining half of the defect rather than an accident of
#: formatting: `tools/presets/envelope_tables.py` rewrites
#: `dict[str, float]` literals one row per line and this is a
#: `dict[int, float]`. It is written one lag per line so that a fifth entry
#: in that tool's `TABLES` reaches it. See `stale-constants.md` section 6.
DECAY_252: dict[int, float] = {
    1: 0.0477,
    2: 0.0298,
    3: 0.0293,
    5: 0.0188,
    8: 0.0197,
    12: 0.0155,
    20: 0.0017,
    30: -0.0009,
    45: -0.0076,
    60: -0.0059,
}
REAL_DECAY: dict[int, float] = {
    1: 0.1071, 5: 0.0518, 8: 0.0453, 12: 0.0295, 20: 0.0286,
    30: 0.0179, 60: 0.0054,
}
#: Log-log slope over lags 1, 2, 3, 5, 8, 12 and 20 of `DECAY_252`. Real
#: markets decay hyperbolically and this model decays exponentially, about
#: twice as steeply, because it is built from exponentials. No parameter
#: setting turns one slope into the other.
#:
#: READ THE ERROR BAR, which this constant did not carry until 2026-09-14.
#: On thirty seeds the fit is -0.859 with a bootstrap standard error of
#: 0.199, and on 28 per cent of resamples of those same seeds some lag inside
#: the fit range is non-positive and the log-log slope DOES NOT EXIST. So
#: three decimals overstate what the estimator can deliver, and the ratio
#: against real markets is 1.97x with 2.2x well inside its own error.
#:
#: The value read -0.953 while `DECAY_252` was pt-v14's. Refitting the new
#: curve moved it by less than one standard error, which is the honest
#: reading and not a reason to have left it: the curve moved by a factor of
#: three at lag one and the slope did not, because a slope is a ratio of
#: shape to level and that change was mostly level. Neighbouring defaults
#: refit to -0.7368 (pt-v14), -1.4832 (pt-v16) and -0.6476 (pt-v18), so the
#: quantity is not monotone across defaults and pt-v16's is undefined on
#: half its own resamples.
DECAY_SLOPE = -0.859
REAL_DECAY_SLOPE = -0.436

#: The lag beyond which the model's volatility memory is not merely weak but
#: wrong in sign. A strategy reading volatility over a window longer than
#: this is reading a process that anti-predicts where the market persists.
#:
#: 12 since 2026-09-14, and it read 20 from pt-v14's curve, where lag twenty
#: stood at +0.0082. On pt-v19 lag twenty reads +0.0017 against a thirty-seed
#: bootstrap standard error of 0.0050, so the sign the constant turns on is
#: not resolved there. The last lag positive by more than one standard error
#: is 12, at +0.0155 +/- 0.0041. The change tightens what this module
#: forbids rather than loosening it.
MEMORY_VALID_TO_LAG = 12


@dataclass(frozen=True)
class Gap:
    """One measured way the model departs from real markets.

    `forbids` is the operative field: a gap nobody can act on is trivia.
    """

    id: str
    summary: str
    detail: str
    forbids: str
    statistics: tuple[str, ...] = ()
    #: None when the gap applies at every horizon.
    beyond_days: int | None = None
    #: Selectable presets that bring this gap's statistics into band.
    #:
    #: Empty for a gap nothing closes. A preset named here is NOT a
    #: certification: `CERTIFIED` is measured on the shipped preset, and this
    #: field says only that another one, which a caller has to ask for by
    #: name, does not carry the gap. A reader who needs it closed can select
    #: that preset and give up the certification, and that trade is theirs to
    #: make rather than one this module makes quietly by moving the default.
    closed_by: tuple[str, ...] = ()


def _sector_reading() -> str:
    """`sector_excess_corr`'s own reading and distance, read off the tables.

    The `scenario-magnitude` gap used to TYPE this pair. It said "in band on
    the shipped preset, 0.2081 at 252 days and 0.1817 at 504 against bands
    starting at 0.11" from 0.2.0 until 2026-09-14, and 0.1817 is pt-v13's
    504-day reading to four places. Across five defaults the shipped preset
    walked down to 0.1011 and 0.1042 and left the band, and the sentence did
    not move, so the module published a PASS on a row its own `CERTIFIED`
    table twenty lines away says misses. A gap that restates a number this
    module already publishes will eventually restate a stale one, so this one
    reads `CERTIFIED` and `MEASURED_504`, which
    `tools/presets/envelope_tables.py` writes from `presets/pt-v19.json` and
    `tests/test_preset_records.py` binds to it. The sentence can now only go
    stale if the record does, and that already fails a test.

    The VERDICT is deliberately not stated here, by either name. The band
    basis is under a ruling: on the 2015-2025 bands these tables carry the
    row is outside at both horizons, and on the universal 1987-2025 band its
    floor is 0.04 at 252 and 0.06 at 504 and the same readings are inside at
    both. Picking one so the sentence resolves is the error the sentence
    already made in the other direction.
    """
    v252 = CERTIFIED["sector_excess_corr"]
    v504 = MEASURED_504["sector_excess_corr"]
    b252 = REAL_MARKETS["sector_excess_corr"]
    b504 = BANDS_504["sector_excess_corr"]
    say = [f"{d:.4f} outside {b}" if d else f"inside {b}"
           for d, b in ((band_distance(v252, *b252), b252),
                        (band_distance(v504, *b504), b504))]
    return (f"{v252:.4f} at 252 days ({say[0]}) and {v504:.4f} at 504 days "
            f"({say[1]})")


GAPS: tuple[Gap, ...] = (
    # RETIRED 2026-08-26: the "volume-change" gap. It read that
    # volume_change_acf1 sat about 2.2 seed-sd outside its tighter 504-day
    # band and was the only row of fourteen to miss at that horizon. pt-v12
    # reads -0.2572 at 504 days against a band of -0.29 to -0.21 and -0.2656
    # at 252, comfortably inside both, so the restriction it carried is
    # lifted rather than reworded (§114).
    #
    # Worth remembering what this gap claimed before it was closed. Its first
    # version said the row was UNREACHABLE without spending a passing
    # statistic; that was withdrawn when pt-v10 held both rows at 252 days,
    # and the gap was rewritten as "a horizon problem, not a trade-off". Then
    # the horizon half closed too, and neither closure came from the volume
    # mechanisms three calibration sections were spent on: it came from a
    # literal 4.0 in tick.rs that saturated volume's response to a name's own
    # move at a four percent day. Two confident structural claims about one
    # statistic, both wrong, both refuted by measurement.
    Gap(
        id="horizon",
        summary="the certified horizon is 252 days",
        detail=(
            "Against bands re-derived at the matching window, the shipped "
            "pt-v19 holds all thirteen readable rows at 504 days on the "
            "ruled band; corr_persistence_acf1 is unreadable there, by "
            "name. RECOMPOSED 2026-09-20 -- from 2026-09-14 this gap read "
            "'one row out at 504 days: sector_excess_corr, 0.10421 against "
            "a floor of 0.11', a decade-band verdict on the previous vector. "
            "CORRECTED 2026-09-14 -- this gap read 'the shipped pt-v19 holds "
            "ALL FOURTEEN at 504 days', which was measured on pt-v18 plus "
            "four dials with sector_loading 0.8, before the vector that "
            "ships was composed.\n\n"
            "So why is the horizon still 252? Two reasons, and the band "
            "count is neither. First, headroom -- though this reason has "
            "weakened: under pt-v12 annualised_vol_pct read 33.89 against a "
            "band ending at 34.0, only 0.11 of room on a statistic whose "
            "seed spread is many times that. pt-v19 reads 23.3899 there, "
            "which is 10.61 of room, so that row is no longer "
            "thin. Second and now decisive on its own, "
            "CERTIFIED is what this module certifies and it is measured at "
            "252 days on thirty seeds. The 504-day table is measured, not "
            "certified.\n\n"
            "What remains is a SHAPE problem rather than a level one. "
            "Volatility itself stabilises near 32%, so a long run does not "
            "drift or blow up, and clustering at lags one and five stays "
            "inside its bands. The decay curve is the defect, and the "
            "decay-shape gap carries it: exponential memory imitating "
            "hyperbolic memory holds up over one year and comes apart over "
            "several.\n\n"
            "AND THE LONGER HORIZONS ARE MEASURED NOW. This gap ended "
            "\'nothing beyond 504 has been measured at all\' until "
            "2026-08-27, and pt-v12 made that untrue. "
            "tools/calibration/long_horizon.py runs 756, 1260 and 2520 days "
            "on thirty seeds, and at 2520 days the panel holds 10 of 14 -- "
            "against the 504-day bands, which are the wrong ruler for a "
            "ten-year window and are quoted only because no ten-year bands "
            "have been derived. That nothing RUNS AWAY is settled by a "
            "second measurement needing no band at all: "
            "tools/calibration/memory_vs_drift.py reads annualised "
            "volatility year by year over ten years on twenty seeds, and it "
            "gives 31.5, 35.6, 30.2, 33.5, 33.0, 33.1, 31.3, 32.4, 32.4 and "
            "31.6 percent, which is flat. So a five-year study is "
            "reading numbers that exist and are published. What it does not "
            "have is a band derived at its own horizon, and no committed "
            "tool derives one. That keeps the certification at 252 days."
        ),
        forbids="multi-year backtests, and anything keyed on volatility dynamics beyond one year",
        statistics=("abs_return_acf1", "abs_return_acf5", "return_acf1", "excess_kurtosis"),
        beyond_days=CERTIFIED_HORIZON_DAYS,
    ),
    Gap(
        id="decay-shape",
        summary="volatility memory decays exponentially, not hyperbolically",
        detail=(
            f"Log-log slope over lags 1-20 is {DECAY_SLOPE} +/- 0.199 "
            f"against real markets' {REAL_DECAY_SLOPE}, a ratio of 1.97 "
            f"whose own error covers 2.2, and the model reads BELOW real at "
            f"every measured lag rather than crossing below partway along. "
            f"It is positive and resolved to lag 12, indistinguishable from "
            f"zero at lags 20 and 30, and negative by lag 45, where real "
            f"markets remain weakly positive to lag 60. This is a mechanism "
            f"gap and not a calibration one: the process is built from "
            f"exponentials, and over one year two of them fake a power law "
            f"well enough that no panel statistic objects. A two-component "
            f"mixture was tried and is not sufficient.\n\n"
            f"CORRECTED 2026-09-14. This paragraph read 'about 2.2x steeper, "
            f"and the curve turns NEGATIVE by lag 30'. Both described "
            f"pt-v14's curve, which DECAY_252 carried across three defaults; "
            f"on pt-v19 lag 30 reads -0.0009 against a thirty-seed standard "
            f"error of 0.0033 and settles nothing, and the model no longer "
            f"crosses real anywhere because it starts below it. The DEFECT "
            f"has changed character with the model: the old reading was too "
            f"much short memory decaying too fast, and this default has too "
            f"little memory at every lag, with abs_return_acf1 passing its "
            f"band low rather than high.\n\n"
            f"SHARPENED 2026-08-26. The model already HAS two timescales, "
            f"which had not been established. De-trending |r| by a centred "
            f"252-day rolling mean over 2520 days on twenty seeds and "
            f"re-measuring: 86% of the lag-1 autocorrelation survives, 77% "
            f"of lag-5, 29% of lag-20. Lags 1 and 5 are genuine memory from "
            f"the GJR recursion, whose shock half-life is 3.9 days; lag 20 "
            f"is mostly a slowly-varying variance LEVEL fed by the VIX and "
            f"business-cycle channels. That slow component is not a trend -- "
            f"annualised volatility year by year over ten years is flat, "
            f"+0.6% from the first year to the tenth.\n\n"
            f"The flattering reading is refused. The raw log-log slope at "
            f"2520 days is -0.597, much closer to real markets than the "
            f"-0.847 read at 252 days, and it would be easy to call this gap "
            f"an artefact of a short estimator. Strip the slow level and the "
            f"slope returns to -0.867. The long horizon adds regime "
            f"variation on top of the defect rather than curing it.\n\n"
            f"So the target is specific now: not 'add long memory', which is "
            f"already present and already does its job at lag 20, but make "
            f"the FAST component decay hyperbolically rather than "
            f"exponentially.\n\n"
            f"AND THE SLOPE ALONE IS NOT THE TARGET. Measured 2026-08-26 on "
            f"thirty seeds: turning on the market factor's slow variance "
            f"component improves the log-log slope from -0.716 to -0.504 by "
            f"LOWERING lag-1 autocorrelation from 0.1107 to 0.0693, while "
            f"lag 20 does not move at all. A flatter line through a lower "
            f"point is a better slope and a worse market -- real markets "
            f"have BOTH short-lag clustering, `abs_return_acf1` between 0.02 "
            f"and 0.22, and weakly positive autocorrelation out to lag 60. "
            f"The slope is a ratio of shape to level and can be improved by "
            f"destroying the level.\n\n"
            f"Score work on this gap at lag 20 and beyond WITH LAG 1 HELD, "
            f"never on the slope alone. The same run cost `excess_kurtosis` "
            f"its 504-day band on five arms of six, because a smoother "
            f"variance has thinner tails.\n\n"
            f"Read the scope of that claim precisely. It says no setting of "
            f"THIS model's parameters turns one slope into the other, "
            f"because a sum of exponentials is not a power law. It does not "
            f"say the problem is beyond the project: the volume-change gap "
            f"carried the stronger claim, that its row was structurally "
            f"unreachable, and a new mechanism reached it. A mechanism gap "
            f"is closed by adding mechanism, not by tuning what is here."
        ),
        forbids=(
            f"strategies whose edge depends on volatility memory beyond "
            f"about lag {MEMORY_VALID_TO_LAG} -- vol targeting and risk "
            f"parity on a one-month or longer estimate"
        ),
        statistics=("abs_return_acf20",),
    ),
    Gap(
        id="scenario-magnitude",
        summary="a scenario's size is right on average and unreliable in one run",
        detail=(
            "The expected size of a scenario\'s response is calibrated; "
            "the dispersion around it is not. That is the gap now.\n\n"
            "The steady-state lever -- how much more violent a sustained "
            "crisis is than a calm market -- reads 2.07x on pt-v19 against "
            "real markets\' 6.16x, measured from a held VIX 5 to a held VIX "
            "65 on the certified 40-name roster over 252 days at thirty "
            "seeds (19.19 per cent annualised at the low pin, 39.74 at the "
            "high one). pt-v18 read 6.53x there, pt-v16 6.23x, pt-v14 "
            "6.18x, pt-v10 5.05x, and the default before it 3.07x. "
            "This gap opened by saying the VIX shock response was materially "
            "weaker than the previous preset\'s, and that sentence was "
            "WITHDRAWN when every preset from pt-v11 to pt-v18 read stronger "
            "than the one before it. It is TRUE AGAIN at pt-v19, by "
            "mechanism rather than by accident: the VIX level identity reads "
            "the market\'s variance target against a derived anchor rather "
            "than the dial\'s, so a held VIX 65 is a smaller multiple of it. "
            "pt-v19 sits 66 per cent BELOW real where pt-v18 sat 5.9 per "
            "cent above, so the lever is the row on this panel the new "
            "default reads FURTHEST from real, and it is a large shortfall "
            "rather than a small one.\n\n"
            "CORRECTED 2026-09-14. This paragraph read 5.28x and '14.3 per "
            "cent BELOW real'. 5.28x was measured on pt-v18 plus four dials "
            "with sector_loading 0.8, in the ptv19panel run, and does not "
            "describe the composed vector that ships; presets/pt-v19.json "
            "records 2.42 since the 2026-09-20 recomposition (2.0714 from "
            "2026-09-14). Read the size of the shortfall, not just its "
            "sign: it is more than three times what the withdrawn figure "
            "said. Note also that this lever is read over a window pinned "
            "from day zero with no burn-in, so the numerator and denominator "
            "each average a transient, and every preset\'s recorded ratio is "
            "low by an amount that is a property of the preset.\n\n"
            "'Direction is right' is measured rather than asserted. Driving "
            "the real 2020-21 macro path through the model and correlating "
            "daily returns against each driver, over 504 sessions, against "
            "the same correlations computed on real AAPL over the same "
            "window:\n"
            "  return vs change in VIX             -0.423 (real -0.622)\n"
            "  return vs change in credit yield    -0.496 (real -0.592)\n"
            "  return vs change in valuation       +0.573 (real +0.803)\n"
            "  absolute return vs VIX level        +0.512 (real +0.489)\n\n"
            "All four carry the sign theory fixes in advance, and the "
            "volatility-clustering channel is close to exact.\n\n"
            "This gap used to read those three directional correlations as "
            "response SIZES, and said the model ran at seventy to eighty-five "
            "percent of the real response. That is withdrawn (§81). A "
            "correlation is beta * sd(driver) / sd(return), which is signal "
            "share rather than gain, and measured as gains the three "
            "channels are right: OLS slope of daily return on each driver "
            "over the same 504 sessions gives -0.00461 against real AAPL's "
            "-0.00500 for the VIX, -8.106 against -7.445 for the credit "
            "yield, and +1.226 against +1.272 for valuation, all within ten "
            "percent, with real AAPL inside the model\'s six-seed range on "
            "every channel.\n\n"
            "The denominator is the defect, and it keeps this a gap now "
            "that the lever has arrived. Over the driven window the "
            "model\'s residual sd is 1.565x real on pt-v12, down from 1.76x "
            "at pt-v10 and barely moved from 1.555x at pt-v11. That is the "
            "worst axis in the model. So the expected response to a scenario "
            "is calibrated and the dispersion around it is too wide: one run "
            "understates how much of its own move was the scenario. The "
            "daily-return-sd pair behind the pt-v10 ratio, 0.0355 against "
            "real AAPL\'s 0.0236, has not been re-measured since, so it is "
            "quoted with its era rather than as a current reading.\n\n"
            "An event study over the five sessions after each of six dated "
            "2020-21 events agrees on sign TWO times out of six. This "
            "paragraph said five of six until 2026-08-27; the notebook it "
            "cites prints 2/6. The Fed\'s intermeeting cut of 3 March "
            "2020 goes the wrong way, +9.9% against AAPL\'s -1.4%, because "
            "an announcement-effect channel is absent rather than "
            "miscalibrated; the VIX record close of 16 March misses by "
            "declining to move, +0.3% against -7.4%; and the vaccine result "
            "and Omicron are single-name Apple news, which a run driven only "
            "by a macro path cannot know. The two that agree are the two the "
            "macro path carries. See "
            "examples/09-a-pandemic-shaped-market.ipynb.\n\n"
            "Sector structure was the same shortfall measured a second "
            "way, and whether it is closed now turns on the BAND BASIS "
            "rather than on the model. In calm markets the shipped preset "
            f"reads {_sector_reading()} against the 2015-2025 bands these "
            "tables carry. Against the universal 1987-2025 band of the "
            "design record, whose floor is 0.04 at 252 days and 0.06 at "
            "504, the same two readings are inside at both horizons. This "
            "gap does not pick one. The ruler is under a ruling and the "
            "verdict moves with it, and a reader who needs this row should "
            "read both numbers and the band they are grading against.\n\n"
            "CORRECTED 2026-09-14. This paragraph read 'and is now CLOSED. "
            "In calm markets it is in band on the shipped preset, 0.2081 at "
            "252 days and 0.1817 at 504 against bands starting at 0.11, so "
            "the separate sector-structure gap was retired at 0.2.0'. "
            "0.1817 is pt-v13.json's 504-day reading to four places and "
            "0.2081 matches no committed record on the current roster "
            "generator, so this module published a pass, under the shipped "
            "preset's name, on a row the shipped preset misses at both "
            "horizons on the very band the sentence was citing. The two "
            "figures are now read out of CERTIFIED and MEASURED_504 rather "
            "than typed (`_sector_reading`), which is what stops it "
            "happening again. Under a held VIX 45 pt-v12 reads +0.109 "
            "against a real +0.103, and crisis co-movement reads 0.696 "
            "against a real 0.664 to 0.727; both are pt-v12's and say "
            "so.\n\n"
            "This paragraph read 'industries hold together in a crisis "
            "about a third as tightly as real ones' until 2026-08-26, "
            "measured at +0.035 on pt-v10 and +0.064 on pt-v7. pt-v11's "
            "crisis work closed it and pt-v12 carries that, so the claim "
            "is WITHDRAWN. The crisis shape is right; what remains in "
            "this gap is the dispersion above, which is about sizing a "
            "scenario rather than about structure."
        ),
        forbids="sizing a scenario's impact rather than detecting it",
    ),
    Gap(
        id="macro-range",
        summary="the endogenous macro state cannot reach its own crisis regimes",
        detail=(
            "Left to itself the economy stays in a moderate band, and two "
            "consequences follow that are easy to mistake for defects.\n\n"
            "INFLATION. Measured over thirty seeds and five years, endogenous "
            "inflation peaks at 4.0% on every seed, with sd 1.2 around a mean "
            "of 2.0%; US CPI year-on-year 2015-2025 (FRED CPIAUCSL) has sd "
            "2.18, a peak of 9.0% in June 2022 and monthly AR(1) 0.978 "
            "against the model's 0.958. The cap is the inflation update's "
            "mean reversion, 0.55 of the gap to target each month, a "
            "half-life under a month. That coefficient and the 6.0% clamp "
            "are dials since 0.1.4, `inflation_reversion` and "
            "`inflation_ceiling`, shipped at the old values so every preset "
            "reproduces. Measured (calibration record §65): at reversion "
            "0.15 the endogenous series matches the real mean and sd to the "
            "second decimal (2.85 / 2.10 against 2.87 / 2.18) and then sits "
            "on the clamps; persistence does not move with the dial because "
            "it comes from the cycle, wages and unemployment. No preset takes "
            "either dial yet, because what a real inflation range does to "
            "the equity panel has not been scored, so this gap stands.\n\n"
            "THE CENTRAL BANK'S CRISIS CADENCE. The bank pulls its next "
            "meeting in to 21-30 days when a decision leaves it more than 2pp "
            "behind an inflation rate above 4%. That path is correct and "
            "well exercised, firing in 22.0% of the 11,898 central-bank cases "
            "in the parity corpus, but a default run cannot reach it because "
            "inflation does not get there. It also fires in STAGFLATION "
            "rather than in high inflation as such: at inflation 4.5% with "
            "unemployment 9.0% the bank cuts for the output gap and leaves "
            "itself further behind, so pinning inflation high with "
            "unemployment low will not trigger it however high you pin it.\n\n"
            "So a 2022-style inflation shock has to be driven through a "
            "scenario. It will not arise on its own, and neither will the "
            "policy response to it.\n\n"
            "DRIVING ONE WORKS, and the lever is inflation rather than the "
            "policy rate. Measured on real 2022 data over six seeds, against "
            "a real S&P of -20.0%: a scenario driving `inflation_rate` with "
            "the published CPI path returns a median -23.3%, where the same "
            "run with no scenario at all returns -12.6% and one driving only "
            "`federal_funds_rate` with the real seven-hike path returns "
            "-13.1%, which is the drift and nothing more. Inflation works "
            "because it steers the bank's own reaction into the corporate "
            "bond yield; an externally pinned policy rate does not reproduce "
            "that. Leave `corporate_bond_yield` FREE when doing this, since "
            "pinning it severs the very channel the inflation path is using."
        ),
        forbids="studying inflation regimes or policy crises from the endogenous economy alone",
        statistics=(),
    ),
    Gap(
        id="roster-concentration",
        summary="a concentrated roster holds at one year and comes apart at two",
        detail=(
            "`Universe.random()` assigns sectors round-robin over the twelve "
            "in `sectors.SECTORS`, so a roster is as close to balanced as its "
            "size allows: the certified 40 names put four in each of four "
            "sectors and three in each of the other eight. No real index is "
            "balanced that way -- the S&P is roughly a third technology and "
            "the Nasdaq more so.\n\n"
            "RE-MEASURED 2026-08-26 on pt-v12: thirty seeds, the fourteen-"
            "statistic panel, both horizons. This gap previously carried "
            "'balanced 9, S&P-like 8, all-technology 7', counts out of the "
            "TEN-statistic panel of the pt-v3 era at six seeds, and said so. "
            "Superseded:\n\n"
            "                      252d     504d   out at 504\n"
            "  balanced           14/14    14/14   --\n"
            "  S&P-like           14/14    13/14   annualised_vol_pct\n"
            "  technology-heavy   14/14    11/14   vol, corr_persistence, xs_corr\n"
            "  all-technology     13/13    10/13   vol, corr_persistence, xs_corr\n"
            "  defensive          14/14    14/14   --\n\n"
            "The finding has changed shape. AT THE CERTIFIED HORIZON, "
            "concentration costs nothing: every shape tested holds the whole "
            "panel, so the envelope transfers to a roster shaped like a real "
            "index. This gap used to say part of the certification was an "
            "artifact of balance; on pt-v12 at 252 days that is no longer "
            "measurable.\n\n"
            "What concentration costs is the SECOND year, and the mechanism "
            "is visible rather than mysterious. Cross-sectional correlation "
            "rises monotonically with it -- 0.3797 balanced, 0.3813 S&P-like, "
            "0.4112 technology-heavy, 0.5316 all-technology -- which is the "
            "model behaving CORRECTLY, since names in one industry should "
            "move together more. It rises past the 504-day band's top of "
            "0.41 and annualised volatility follows it out. A band derived "
            "from broad real-market windows is the wrong ruler for a "
            "single-sector portfolio, so part of this is a statement about "
            "the grading rather than about the model.\n\n"
            "`sector_excess_corr` is UNDEFINED on an all-technology roster "
            "rather than out of band: it asks how much a name moves with its "
            "own industry beyond the market, and with one sector those are "
            "the same thing. Hence 13 rather than 14 in that row. Measured by "
            "`tools/calibration/roster_shapes.py`."
        ),
        forbids=(
            "inheriting this envelope for a sector-concentrated roster BEYOND "
            "one year -- at the certified horizon it now transfers"
        ),
        statistics=("cross_sectional_corr", "annualised_vol_pct",
                    "corr_persistence_acf1"),
    ),
)


@dataclass(frozen=True)
class Verdict:
    """The answer `check` returns. Falsy when the question is outside.

    `requested` is the statistics the caller named, and it keeps the printed
    verdict honest. A `check` is conditional on the question asked:
    it consults the horizon, the flags, and the statistics you passed, and it
    says nothing about the eleven you did not. Printing a bare "inside the
    envelope" read as a global all-clear, which it never was, so the head
    line names its own scope.
    """

    inside: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    gaps: tuple[Gap, ...] = field(default=(), repr=False)
    requested: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.inside

    def __str__(self) -> str:
        if not self.inside:
            head = "OUTSIDE the envelope"
        elif self.requested:
            head = "inside the envelope for the statistics you named"
        else:
            head = "inside the envelope for the horizon you named"
        lines = [head]
        lines += [f"  - {r}" for r in self.reasons]
        lines += [f"  ? {w}" for w in self.warnings]
        return "\n".join(lines)


def intervals(
    panels: Sequence[Mapping[str, Any]],
    *,
    seed_sd: Mapping[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-statistic spread across seeds, beside the median and the band.

    `panels` is a sequence of `facts.measure` results, one per seed -- the
    same input `loss.band_distance_loss` takes. Returns, for each statistic:

        median      the point estimate a single panel would report
        low, high   the actual min and max ACROSS SEEDS
        p10, p90    the 10th and 90th percentile, for a less brittle range
        sd          across-seed standard deviation, measured here
        shipped_sd  `facts.SEED_SD`, measured at the shipped baseline
        band        the real-market band
        distance    band distance of the median, zero inside
        sd_out      that distance in units of `shipped_sd`
        extremes_straddle  True when min or max crosses a band edge
        typical_straddles  True when the p10-p90 range crosses one

    `typical_straddles` is the field worth reading. A statistic whose MEDIAN
    sits inside its band while its p10-p90 range crosses an edge is not
    comfortably in band -- it is in band on AVERAGE and out of band on a
    large minority of seeds. That distinction is invisible in a point
    estimate, and a user running one seed meets it.
    `extremes_straddle` uses min and max instead, where a crossing is close
    to expected over thirty draws and is information rather than a finding.

    Refuses fewer than two panels: a spread over one observation is not a
    spread, and reporting it as one would be the false precision this
    function exists to remove.
    """
    if len(panels) < 2:
        raise ValidationError(
            "intervals needs at least two per-seed panels; a spread over one "
            "observation is not a spread"
        )
    scales = SEED_SD if seed_sd is None else seed_sd
    out: dict[str, dict[str, Any]] = {}
    for key, (low, high) in REAL_MARKETS.items():
        values = [p.get(key) for p in panels]
        present = [v for v in values if v is not None]
        if len(present) < 2:
            out[key] = {"median": None, "band": (low, high)}
            continue
        ordered = sorted(present)
        med = statistics.median(present)
        sd = statistics.stdev(present)
        shipped = scales.get(key)
        distance = band_distance(med, low, high)
        out[key] = {
            "median": med,
            "low": ordered[0],
            "high": ordered[-1],
            "p10": _percentile(ordered, 0.10),
            "p90": _percentile(ordered, 0.90),
            "sd": sd,
            "shipped_sd": shipped,
            "band": (low, high),
            "distance": distance,
            "sd_out": (distance / shipped) if (shipped and distance) else 0.0,
            # Two containment tests, because they answer different questions
            # and only one of them is defensible as evidence.
            #
            # `extremes_straddle` uses the min and max, and with thirty draws
            # an extreme crossing an edge is close to expected -- it says
            # "some seed did this", which is worth knowing and is not a
            # finding.
            #
            # `typical_straddles` uses the 10th-90th percentile band, and is
            # the one to read: it says the MIDDLE EIGHTY PERCENT of seeds
            # crosses an edge, so a user running one seed is likely, not
            # merely able, to measure out of band on a statistic whose
            # median is comfortably inside.
            "extremes_straddle": ordered[0] < low or ordered[-1] > high,
            "typical_straddles": (
                _percentile(ordered, 0.10) < low or _percentile(ordered, 0.90) > high
            ),
            "seeds": len(present),
        }
    return out


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def report_intervals(rows: Mapping[str, Mapping[str, Any]]) -> str:
    """`intervals` as a fixed-width table, for reading rather than parsing."""
    head = (
        f"{'statistic':24s} {'median':>9s} {'p10':>9s} {'p90':>9s} "
        f"{'sd':>8s} {'band':>16s}  verdict"
    )
    lines = [head, "-" * len(head)]
    for key, r in rows.items():
        if r.get("median") is None:
            lines.append(f"{key:24s} {'unmeasured':>9s}")
            continue
        lo, hi = r["band"]
        if r["distance"]:
            verdict = f"OUT {r['sd_out']:.1f} sd"
        elif r["typical_straddles"]:
            verdict = "in band on the median; p10-p90 crosses an edge"
        elif r["extremes_straddle"]:
            verdict = "in band (an extreme seed crosses)"
        else:
            verdict = "in band"
        lines.append(
            f"{key:24s} {r['median']:>9.4f} {r['p10']:>9.4f} {r['p90']:>9.4f} "
            f"{r['sd']:>8.4f} {f'({lo}, {hi})':>16s}  {verdict}"
        )
    return "\n".join(lines)


def check(
    *,
    horizon_days: int,
    statistics: Iterable[str] = (),
    sector_concentrated: bool = False,
    scenario_magnitude: bool = False,
    macro_regime: bool = False,
) -> Verdict:
    """Does this question fall inside the envelope?

    `horizon_days` is how long the simulation runs, in trading days.
    `statistics` names the panel statistics the result leans on -- the
    properties of the market the conclusion would change with. Pass the
    keys of `facts.REAL_MARKETS`; unknown names are refused rather than
    ignored, because a silently dropped statistic is a silently granted
    certification.

    `macro_regime` says the result depends on the ECONOMY reaching a
    particular state -- an inflation regime, a policy crisis -- rather than on
    a scenario you drive yourself. It fires `macro-range`, because the
    endogenous economy stays in a moderate band and cannot get to its own
    crisis regimes.

    `sector_concentrated` says the roster is not sector-balanced, which a
    real index never is. `scenario_magnitude` says the result depends on
    the SIZE of a scenario's effect rather than its direction.

    Returns a `Verdict`, which is falsy when the answer is no. Every reason
    names the measurement behind it, so a refusal can be checked rather
    than believed.

    The answer is CONDITIONAL on the question. A verdict consults the
    horizon, the flags, and the statistics named in `statistics`; it says
    nothing about the rest of the panel, so the printed head line names its
    own scope rather than reading as a global all-clear. If your
    conclusion leans on a statistic you did not pass, this function has not
    been asked about it.
    """
    if horizon_days < 1:
        raise ValidationError(f"horizon_days must be positive, got {horizon_days}")
    wanted = tuple(statistics)
    unknown = [s for s in wanted if s not in REAL_MARKETS]
    if unknown:
        raise ValidationError(
            f"unknown statistics {sorted(unknown)}; expected keys of "
            f"facts.REAL_MARKETS: {sorted(REAL_MARKETS)}"
        )

    reasons: list[str] = []
    warnings: list[str] = []
    hit: list[Gap] = []

    def fire(gap: Gap, why: str) -> None:
        hit.append(gap)
        reasons.append(why)

    by_id = {g.id: g for g in GAPS}

    if horizon_days > CERTIFIED_HORIZON_DAYS:
        g = by_id["horizon"]
        # The count is COMPUTED, not written down. This sentence read "13 of
        # 14 ... missing only volume_change_acf1" and stayed that way after
        # pt-v12 brought that row inside its 504-day band, so `check` was
        # telling callers a statistic missed while quoting a number that is
        # plainly inside the band printed beside it (§114).
        out = [k for k, v in MEASURED_504.items()
               if not (BANDS_504[k][0] <= v <= BANDS_504[k][1])]
        held = (f"holds all {len(MEASURED_504)} against horizon-matched bands"
                if not out else
                f"holds {len(MEASURED_504) - len(out)} of {len(MEASURED_504)} "
                f"against horizon-matched bands, missing "
                + ", ".join(f"{k} at {MEASURED_504[k]:.4f} against "
                            f"{BANDS_504[k]}" for k in out))
        fire(g, (
            f"horizon {horizon_days}d exceeds the certified "
            f"{CERTIFIED_HORIZON_DAYS}d. At 504 days the model {held}. The "
            f"thin one is annualised_vol_pct at "
            f"{MEASURED_504['annualised_vol_pct']:.2f} against "
            f"{BANDS_504['annualised_vol_pct']}. Beyond 504 days the panel "
            f"is measured but has no ruler of its own: at 2520 days it holds "
            f"10 of 14 against the 504-day bands "
            f"(tools/calibration/long_horizon.py), and annualised volatility "
            f"is flat year by year across those ten years "
            f"(tools/calibration/memory_vs_drift.py). No bands have been "
            f"derived at a five-year window, so the certification "
            f"stops here"
        ))
        if "excess_kurtosis" in wanted:
            # COMPUTED, for the reason the horizon count above is computed.
            # This read "about 0.3 seed-sd above the floor" until 2026-08-27,
            # which was right for the 8.26 pt-v10 measured at 504 days and
            # wrong for the 7.75 pt-v12 reads there: the room halved and the
            # sentence did not move. Computed since, so none of pt-v14,
            # pt-v16, pt-v18 and pt-v19 moving it needed an edit here.
            room_sd = ((MEASURED_504["excess_kurtosis"]
                        - BANDS_504["excess_kurtosis"][0])
                       / SEED_SD_504["excess_kurtosis"])
            warnings.append(
                f"excess_kurtosis reads {MEASURED_504['excess_kurtosis']:.2f} "
                f"at 504 days against {BANDS_504['excess_kurtosis']}: inside "
                f"it, but {room_sd:.2f} seed-sd above the floor, so a tail "
                f"study at this horizon is reading the low edge of the band"
            )

    for name in wanted:
        # `volume_change_acf1` fired a gap here at any horizon past 252 until
        # pt-v12 brought it inside the 504-day band (-0.2572 against
        # -0.29..-0.21). The arm is deleted rather than made conditional: a
        # gap that no longer exists in GAPS cannot be looked up, and the
        # lookup is what failed when the gap was retired (§114).
        if name == "abs_return_acf20":
            g = by_id["decay-shape"]
            fire(g, (
                f"abs_return_acf20 depends on the decay shape, which is a "
                f"mechanism gap: log-log slope {DECAY_SLOPE} against real "
                f"markets' {REAL_DECAY_SLOPE}, and the curve reads below "
                f"real at every lag, is indistinguishable from zero by lag "
                f"{MEMORY_VALID_TO_LAG + 8} and is negative by lag 45, "
                f"where real markets stay positive to lag 60"
            ))
        elif name not in CERTIFIED:
            # A level or crisis row. The verdict is COMPUTED, for the reason
            # the two counts above are: this arm read the band and then said
            # "held red" without comparing against it. That was true of every
            # default through pt-v16 and false the moment one held the row,
            # and a warning asserting a verdict it did not read is the same
            # defect as a count written down.
            value = CERTIFIED_LEVEL.get(name, CERTIFIED_CRISIS.get(name))
            if value is None:
                warnings.append(
                    f"{name} is graded and its certified value has not been "
                    f"measured on the pinned protocol yet")
            else:
                lo, hi = REAL_MARKETS[name]
                if band_distance(value, lo, hi) == 0:
                    warnings.append(
                        f"{name} is in band at the certified horizon "
                        f"({value:.4f} in {(lo, hi)}, at band position "
                        f"{(value - lo) / (hi - lo):.2f}) -- it is reported "
                        f"apart from the shape rows because it is certified "
                        f"on facts.LEVEL_PROTOCOL, where the roster varies "
                        f"with the seed, and a pass close to an edge is a "
                        f"pass and not a demonstration that the row is right")
                else:
                    warnings.append(
                        f"{name} is held red at the certified horizon "
                        f"({value:.4f} against {(lo, hi)}); a result leaning "
                        f"on it leans on a row the shipped preset does not "
                        f"hold")
        elif horizon_days <= CERTIFIED_HORIZON_DAYS:
            lo, hi = REAL_MARKETS[name]
            if band_distance(CERTIFIED[name], lo, hi) == 0:
                warnings.append(
                    f"{name} is in band at the certified horizon "
                    f"({CERTIFIED[name]:.4f} in {(lo, hi)}) -- but that is a "
                    f"median across 30 seeds; check `intervals` for the "
                    f"spread before relying on one seed"
                )

    if sector_concentrated:
        g = by_id["roster-concentration"]
        fire(g, (
            "the roster is sector-concentrated, and certification was "
            "measured on a sector-balanced one. Re-measured on pt-v12 over "
            "thirty seeds and the fourteen-statistic panel "
            "(tools/calibration/roster_shapes.py): at the certified horizon "
            "that costs nothing any more -- balanced, S&P-like, "
            "technology-heavy and defensive rosters all hold 14 of 14 at 252 "
            "days, and an all-technology roster holds the 13 that are "
            "defined on it, sector_excess_corr having no meaning with one "
            "sector. What concentration costs is the SECOND year: 13 of 14 "
            "S&P-like, 11 of 14 technology-heavy and 10 of 13 "
            "all-technology at 504 days, as cross-sectional correlation "
            "rises past a band derived from broad-market windows. So this "
            "refusal is about the two-year panel and about grading your "
            "roster with the right ruler: re-measure on your own universe"
        ))

    if scenario_magnitude:
        g = by_id["scenario-magnitude"]
        fire(g, (
            "the result depends on the SIZE of a scenario's response. Its "
            "EXPECTED size is calibrated: measured as a regression gain "
            "rather than a correlation, the three driver channels run within "
            "ten percent of real AAPL (§81), and the steady-state volatility "
            "lever from VIX 5 to VIX 65 reads 2.07x on the shipped pt-v19 "
            "against real markets' 6.16x, where pt-v18 read 6.53x -- so the "
            "crisis this model makes is a third of the real one, and that "
            "figure read 5.28x here until 2026-09-14, measured on a "
            "four-dial vector that never shipped. What is "
            "not calibrated is the DISPERSION around that response: over the "
            "driven 2020-21 window the model's residual sd is 1.565x real, "
            "down from 1.76x at pt-v10 and still the worst axis in the "
            "model, so one run understates how much of its own move was the "
            "scenario. Use a scenario to ask WHETHER a strategy breaks, and "
            "read the size as a distribution over seeds"
        ))

    if macro_regime:
        g = by_id["macro-range"]
        fire(g, (
            "the result depends on the economy reaching a regime it does not "
            "reach on its own. Measured over thirty seeds and five years, "
            "endogenous inflation peaks at 4.0% on every seed against a 6.0% "
            "clamp, with sd 1.2 around a mean of 2.0%, where US CPI "
            "year-on-year over 2015-2025 (FRED CPIAUCSL) has sd 2.18 and a "
            "peak of 9.0% in June 2022. So the central bank's own inflation "
            "crisis cadence -- correct, and firing in 22.0% of the parity "
            "corpus -- is unreachable from a default run. Drive the regime "
            "through a scenario, and note that the crisis cadence responds to "
            "STAGFLATION rather than to high inflation alone"
        ))

    if not wanted:
        warnings.append(
            "no statistics named, so only the horizon and roster were "
            "checked; naming what the result leans on gives a sharper answer"
        )

    if not reasons:
        reasons.append(
            f"horizon {horizon_days}d is within the certified "
            f"{CERTIFIED_HORIZON_DAYS}d, and no named statistic meets a "
            f"measured gap"
        )
    return Verdict(
        inside=not hit,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        gaps=tuple(dict.fromkeys(hit)),
        requested=wanted,
    )


def score(panel: Mapping[str, float], *,
          horizon_days: int = CERTIFIED_HORIZON_DAYS,
          basis: str = DEFAULT_BAND_BASIS) -> dict[str, Any]:
    """How a measured panel sits against the bands for its own horizon.

    `panel` maps statistic names to measured values -- what
    `facts.measure()` returns, or a median across seeds.

    The horizon chooses the ruler. That is why this exists as a function
    rather than a comparison anyone can write inline: a
    504-day panel scored against the 252-day bands is the wrong-ruler
    error, and it has been made repeatedly in this project. It flatters the
    model on kurtosis -- the 5.2 that pt-v3 read at 504 days sits
    comfortably inside the 252-day band of 1.6 to 41 and is OUT of the
    horizon-matched 7.1 to 22 -- while being harsher elsewhere. The shipped
    preset reads 7.75 there, inside. That is what grading a 504-day panel
    with `BANDS_504` buys.

    `room_sd` is how far inside its band a statistic sits, in that
    horizon's own seed noise, signed so negative means out. A statistic
    barely inside is one seed away from not being, and the band loss cannot
    see the difference.

    The counts are split by group. `in_band` and `of` total every row
    scored and are kept for readers that predate the split; a gate asks
    `shape_in_band` against `shape_of`, because the level and crisis rows
    are certified on a different protocol and a total that folds them in
    answers a question no protocol asked. Nothing here answers "is the
    panel green" without a group. CORRECTED 2026-09-14: this read "the
    level and crisis rows are held red at the default preset on purpose",
    which described every default through pt-v16. All four are in band at
    pt-v19 -- `CERTIFIED_LEVEL` and `CERTIFIED_CRISIS` carry the values --
    and the split was never about the verdict.

    THE COUNT IS AN ARITHMETIC CHECK ON THIS TABLE AND NOT A QUALITY SCORE,
    recorded 2026-09-14. It is returned because a caller that lists rows
    wants to know it has them all, and because a gate needs one comparison.
    It does not say a preset is good, and it does not compare two presets.
    `facts.REAL_MARKETS` and `BANDS_504` are derived from 2015-2025
    windows, and the 0.8.0 scoring rule was re-centred on 1987-2025 and
    these tables were not, so a count taken here grades a preset against a
    ruler the project stopped scoring with. State a band result as the rows
    that are out and how far: `rows` carries `in_band` and `room_sd` per
    statistic for exactly that, and the preset records carry `misses` by
    name beside every `in_band` count for the same reason.

    A HORIZON WITH NO RULER IS REFUSED. This used to read `far = horizon_days
    > CERTIFIED_HORIZON_DAYS`, so a 756-day panel -- and the 1,008-day runs
    the settling study makes -- scored against the 504-day bands with nothing
    saying so, and 253 scored against the 252-day ones. The lookup below has
    two keys and refuses everything else by name, because a band set derived
    at one window is not an approximate ruler for another window: it is a
    ruler for a different quantity.
    """
    if horizon_days < 1:
        raise ValidationError(
            f"horizon_days must be positive, got {horizon_days}")
    if basis not in RULERS_BY_BASIS:
        raise ValidationError(
            f"{basis!r} is not a band basis; the bases are "
            f"{sorted(RULERS_BY_BASIS)}. A basis is an era, a window count "
            f"and a rule, and `facts.band_basis` states each one")
    if horizon_days not in RULERS_BY_BASIS[basis]:
        raise ValidationError(
            f"no band set has been derived at {horizon_days} days; the "
            f"horizons with a ruler are {sorted(RULERS_BY_HORIZON)}. A "
            f"nearer band set is not an approximation -- the 252-day and "
            f"504-day tables differ on twelve of fourteen rows and their "
            f"noise scales differ by factors from 0.80 to 3.23 -- so this "
            f"refuses rather than picking one.")
    # `loss.STRUCTURAL` names the statistics excluded from the objective by
    # design; imported here rather than at module scope because `loss`
    # imports this module's facts and a top-level import would cycle.
    from .loss import STRUCTURAL

    bands, noise, ruler_name = RULERS_BY_BASIS[basis][horizon_days]

    from .facts import SHAPE, LEVEL, CRISIS, PERSISTENCE

    # THE GATE IS THE LIBRARY'S GRADED ROWS, NOT ONE BAND TABLE'S KEYS.
    #
    # This read `set(REAL_MARKETS)` -- the eighteen-row decade table -- and
    # `facts.PERSISTENCE` sits OUTSIDE that partition by construction, which
    # `facts.SHAPE`'s neighbours say in as many words: `SHAPE + LEVEL +
    # CRISIS` is an exact partition of `REAL_MARKETS` and the persistence
    # row is the one group that is scored and not banded. So a measured
    # panel carrying `vix_ar1_debiased` -- and `facts.measure` emits it on
    # every run -- was REFUSED here, with `unknown statistics`, BEFORE any
    # band lookup ran.
    #
    # That is the same fault this basis machinery was built to end, one row
    # lower down. `REAL_MARKETS_UNIVERSAL` was registered and reachable by
    # no lookup; the nineteenth row is reachable by no GATE. In both cases
    # the ruling names an object the producer cannot address, and in both
    # cases the reason has nothing to do with whether the row has a band.
    #
    # Admitting it moves NO count and NO verdict: the row still has no
    # band in any table here, so it falls through to the UNREADABLE branch
    # below and reports the reason `facts.RULED_UNREADABLE` already records
    # for it. That reason cited `vix-ar1-band-derivation.md` section 9 until
    # 2026-09-18, a note that exists in no commit of either repository, and
    # it now names the ledger entries that do exist. What changes is that
    # adopting that band becomes a table entry on its own, instead of a
    # table entry plus this line, and that the row is now named as
    # unreadable rather than refused as unknown. Absent with a stated reason
    # is a different fact from rejected as a typo.
    graded_rows = frozenset(SHAPE + LEVEL + CRISIS + PERSISTENCE)
    unknown = sorted(set(panel) - graded_rows)
    if unknown:
        raise ValidationError(
            f"unknown statistics {unknown}; the rows this library grades "
            f"are facts.SHAPE + LEVEL + CRISIS + PERSISTENCE: "
            f"{sorted(graded_rows)}")

    unreadable_reasons = _facts.RULED_UNREADABLE.get(horizon_days, {})
    rows: dict[str, Any] = {}
    for name, measured in panel.items():
        band = bands.get(name)
        if band is None:
            # A row this basis has no band for. UNREADABLE, by name and with
            # the reason, rather than dropped or filled from another basis:
            # a cell nobody can grade is a different thing from a cell that
            # passed, and folding the two is how "38 of 38" gets written
            # about a bar that tested 31.
            rows[name] = {
                "measured": measured,
                "band": None, "distance": None, "in_band": None,
                "room_sd": None,
                "structural": name in STRUCTURAL,
                "unreadable": unreadable_reasons.get(
                    name, f"{ruler_name} carries no band for this row"),
            }
            continue
        low, high = band
        sd = noise.get(name)
        rows[name] = {
            "measured": measured,
            "band": (low, high),
            "distance": band_distance(measured, low, high),
            "in_band": band_distance(measured, low, high) == 0,
            "room_sd": (None if not sd
                        else min(measured - low, high - measured) / sd),
            "structural": name in STRUCTURAL,
            "edges": _facts.edge_liveness(name),
        }
    def count(group):
        names = [n for n in rows if n in group and rows[n]["in_band"] is not None]
        return sum(1 for n in names if rows[n]["in_band"]), len(names)

    shape_in, shape_of = count(SHAPE)
    level_in, level_of = count(LEVEL)
    crisis_in, crisis_of = count(CRISIS)
    # 0 of 0 today, because the row has no adopted band at either horizon.
    # It is reported rather than omitted so the count the bar needs exists
    # before the band does: a group with no key is how a row goes missing
    # from a nineteen-row claim without anything disagreeing.
    persistence_in, persistence_of = count(PERSISTENCE)
    for name in rows:
        rows[name]["group"] = ("shape" if name in SHAPE else
                               "level" if name in LEVEL else
                               "crisis" if name in CRISIS else "persistence")
    unreadable = sorted(n for n in rows if rows[n]["in_band"] is None)
    return {
        "horizon_days": horizon_days,
        "ruler": ruler_name,
        # THE BASIS, NOT THE NAME. `ruler` above is a symbol, and swapping
        # that symbol's contents leaves every record asserting the same
        # provenance while every count under it changes. This block is the
        # era, the roster, the window count, the rule and the per-row
        # composition, so a record carries what it was actually graded by.
        "basis": basis,
        "basis_detail": _facts.band_basis(ruler_name),
        "statistics": rows,
        "in_band": sum(1 for r in rows.values() if r["in_band"]),
        "of": sum(1 for r in rows.values() if r["in_band"] is not None),
        # Named, never folded into `of`. Each of these is a ship blocker.
        "unreadable": unreadable,
        "unreadable_of": len(unreadable),
        "edge_form": _facts.published_edge_form(horizon_days)
                     if basis == BAR_BAND_BASIS else None,
        # THE ROW SET `edge_form` COUNTED OVER, WHICH IS NOT THIS PANEL'S.
        # `published_edge_form` counts the HORIZON's whole graded set, and
        # `unreadable_of` above counts the rows this caller handed in, so
        # the two disagree whenever a panel is short of nineteen rows. That
        # is not hypothetical: `certify` grades
        # `aggregate_panels(panels, keys=facts.SHAPE)`, so every certificate
        # the library produces reads `unreadable_of` 0 at 252 beside an edge
        # form saying "3 of 3 unreadable", and 1 beside "4 of 4" at 504.
        # Both numbers are right about different row sets, which is exactly
        # the state `row-value-carries-its-container` refuses, so each count
        # now says what it was taken over.
        "edge_form_rows": (sum(_facts.edge_liveness_counts(
            horizon_days).values()) if basis == BAR_BAND_BASIS else None),
        "panel_rows": len(rows),
        # The split. A gate reads `shape_in_band` against `shape_of`; the
        # level and crisis counts are reported beside it and never added
        # to it.
        "shape_in_band": shape_in, "shape_of": shape_of,
        "level_in_band": level_in, "level_of": level_of,
        "crisis_in_band": crisis_in, "crisis_of": crisis_of,
        "persistence_in_band": persistence_in, "persistence_of": persistence_of,
    }


#: The row `tail_block` reads, and the only `pooled_rate` row there is.
TAIL_ROW = "index_tail_dn3_pct"


def tail_block(panels: Sequence[Mapping[str, Any]], *,
               horizon_days: int = CERTIFIED_HORIZON_DAYS,
               basis: str = DEFAULT_BAND_BASIS,
               stationary_opening: bool | None = None) -> dict[str, Any] | None:
    """The index tail row's certificate line, from the per-seed panels.

    None when the panels do not carry the row's counts, which is what a
    panel measured before the row existed looks like; the certificate then
    says nothing about the tail rather than reporting a rate it cannot
    compute.

    THREE COUNTS, NEVER ONE. The graded value is a pooled rate and a rate
    cannot see its own mixture: thirty seeds at three hits each and a
    mixture of zeros and a crash year have the same mean. So the block
    carries the share of seeds with no hit, the share with five or more and
    the largest single count, beside the tape's own 13 of 35, 7 of 35 and
    33. Those three are REPORTED and not gated, because the tape's are three
    integers with no useful error bar.

    AT THE EDGE. The band's half-width is the TAPE's standard error, which
    is the narrowest it can honestly be; the run has an error of its own,
    `se_m`, and at thirty seeds it is about the same size. A verdict whose
    margin to the nearer band edge is under one `se_m` is therefore a
    verdict this run cannot resolve, and it is flagged rather than reported
    as a clean pass or a clean failure -- the treatment `mechanism_verdict`
    gives a sign count sitting on its cut.

    `stationary_opening` is the run's own answer to whether every seed
    opened at phase age zero. On the current opening year one is
    all-expansion on every seed and year two a synchronised contraction, so
    a rate measured there reads the OPENING and not the model; passed False,
    the block is graded, printed and NOT counted, with the reason carried as
    data. Passed None it says the opening was not stated, which is not the
    same as saying it was stationary.
    """
    from . import facts as _facts

    # Refused rather than answered with the wrong ruler, the same way `score`
    # refuses a horizon with no band set: the row's real windows exist at two
    # lengths and a rate read against the other one's counts is a number that
    # looks plausible and means nothing.
    if basis not in RULERS_BY_BASIS:
        raise ValidationError(
            f"{basis!r} is not a band basis; the bases are "
            f"{sorted(RULERS_BY_BASIS)}. A basis is an era, a window count "
            f"and a rule, and `facts.band_basis` states each one")
    if (horizon_days not in RULERS_BY_BASIS[basis]
            or int(horizon_days) not in _facts.INDEX_TAIL_WINDOWS["windows"]):
        raise ValidationError(
            f"the index tail row has no real windows at {horizon_days} days; "
            f"measured horizons are "
            f"{sorted(_facts.INDEX_TAIL_WINDOWS['windows'])}. Run "
            "tools/calibration/tail_band.py at that horizon and record them")

    hit_key, session_key = _facts.pooled_rate_counts(TAIL_ROW)
    hits = [p.get(hit_key) for p in panels]
    sessions = [p.get(session_key) for p in panels]
    if any(h is None for h in hits) or not sum(n or 0 for n in sessions):
        return None

    bands, _, ruler_name = RULERS_BY_BASIS[basis][horizon_days]
    low, high = bands[TAIL_ROW]
    rate = 100.0 * sum(hits) / sum(sessions)
    rates = [100.0 * h / n for h, n in zip(hits, sessions) if n]
    se_m = (statistics.stdev(rates) / math.sqrt(len(rates))
            if len(rates) > 1 else None)
    centre = _facts.real_centre(TAIL_ROW, horizon_days=horizon_days)
    se_real = _facts.real_centre_se(TAIL_ROW, horizon_days=horizon_days)
    margin = min(rate - low, high - rate)
    tape = _facts.INDEX_TAIL_WINDOWS["windows"][int(horizon_days)]
    tape_counts = [k for _, _, k, _ in tape]
    return {
        "row": TAIL_ROW,
        "horizon_days": horizon_days,
        # THE BASIS, NOT THE NAME, for the same reason `score` stamps both:
        # a rate carrying only a band's symbol asserts the same provenance
        # whatever the symbol now holds, and this row's two bases differ by
        # 0.38 on the ceiling alone.
        "basis": basis,
        "basis_detail": _facts.band_basis(ruler_name),
        "seeds": len(rates),
        "hits": sum(hits),
        "sessions": sum(sessions),
        "rate": rate,
        "band": [low, high],
        "in_band": low <= rate <= high,
        "verdict": ("in" if low <= rate <= high
                    else "HIGH" if rate > high else "LOW"),
        "margin": margin,  # signed: positive inside the band, negative outside
        "se_m": se_m,
        "real_centre": centre,
        "se_real": se_real,
        "z_r": ((rate - centre) / math.sqrt(se_m ** 2 + se_real ** 2)
                if se_m is not None and se_real else None),
        # Within one run standard error of the nearer edge, on EITHER side:
        # the run cannot resolve this verdict. `margin` is signed, positive
        # inside the band and negative outside it, so the flag takes its
        # magnitude -- a reading far outside is resolved, not at the edge.
        "at_the_edge": se_m is not None and abs(margin) < se_m,
        "zero_share": sum(1 for h in hits if h == 0) / len(hits),
        "five_or_more_share": sum(1 for h in hits if h >= 5) / len(hits),
        "max_hits": max(hits),
        "tape": {
            "windows": len(tape_counts),
            "zero_share": sum(1 for k in tape_counts if k == 0) / len(tape_counts),
            "five_or_more_share": sum(1 for k in tape_counts if k >= 5) / len(tape_counts),
            "max_hits": max(tape_counts),
        },
        "counted": stationary_opening,
        "not_counted": (
            None if stationary_opening else
            "year one of a non-stationary opening: every seed opens in "
            "expansion at phase age zero, so the ensemble rate reads the "
            "opening and not the model -- 0.57x to 0.97x of the centre in "
            "year one and 2.06x to 2.19x in year two on the same preset and "
            "the same seeds. Graded and printed, not counted, until the run "
            "carries a stationary opening"
            if stationary_opening is False else
            "the run did not state whether its opening is stationary, and "
            "an unstated opening is not a stationary one: pass "
            "stationary_opening to count this row"),
    }


def certify_structure(panels: Sequence[Mapping[str, float]], *,
                      horizon_days: int = CERTIFIED_HORIZON_DAYS
                      ) -> dict[str, Any]:
    """The STRUCTURAL certificate: `facts.STRUCTURE`, signed against the tape.

    The second gate, beside the panel and mixed into none of it. `certify`'s
    three counts grade the fourteen shape rows against bands; this grades
    the rows that HAVE no band against the tape's own centre, by
    `facts.structure_verdict`. It is called from `certify` so one run
    produces both and they cannot be measured on two different panels, and
    it is a function of its own so `record.py --structure-rows` can build
    the block from a retained artefact's per-seed rows without re-measuring
    anything.

    THE BLOCK IS ALREADY RECORD-SHAPED. `certification_record` exists
    because `certify`'s mechanism answer carries objects a JSON record
    cannot hold; every field here is JSON-safe, so there is no second shape
    to drift from and no trimming function to keep in step.

    A row the panels do not carry is ABSENT rather than passed. The
    per-seed panels of a run that stopped measuring `vix_ar1_debiased` would
    otherwise produce an empty `refused` list, which reads as a clean
    certificate -- the `not_shown`-shrinks failure one gate over, in its own
    spelling. `structure_bar` refuses on `absent` for that reason.
    """
    from . import facts as _facts

    rows: dict[str, Any] = {}
    absent: list[str] = []
    for row in _facts.STRUCTURE:
        values = [p[row] for p in panels if p.get(row) is not None]
        if len(values) < 2:
            absent.append(row)
            continue
        rows[row] = _facts.structure_verdict(values, row,
                                             horizon_days=horizon_days)
    passed = sorted(r for r, v in rows.items() if v["verdict"] == "pass")
    refused = sorted(r for r, v in rows.items() if v["verdict"] == "refused")
    return {
        "horizon_days": horizon_days,
        "seeds": len(panels),
        "counts": {
            "structure_pass": len(passed),
            "structure_of": len(rows),
        },
        "passed": passed,
        "refused": refused,
        "absent": sorted(absent),
        "at_the_cut": sorted(r for r, v in rows.items() if v["at_the_cut"]),
        "rows": rows,
    }


#: The rise block's field on a preset record, beside `STRUCTURE_BAR_PANELS`.
STRUCTURE_RISE_FIELD = "structure_rise"


def certify_structure_rise(panels_252: Sequence[Mapping[str, float]],
                           panels_504: Sequence[Mapping[str, float]]
                           ) -> dict[str, Any]:
    """The second gate's ONE verdict: the rise in each structural row from
    one year to two, on the same seeds, against the tape's rise.

    `certify_structure` at each horizon stays on the record by name; this
    block is what the bar reads since 2026-09-21. A row missing at either
    horizon is ABSENT rather than passed, for `certify_structure`'s reason.
    """
    from . import facts as _facts

    rows: dict[str, Any] = {}
    absent: list[str] = []
    for row in _facts.STRUCTURE:
        a = [p.get(row) for p in panels_252]
        b = [p.get(row) for p in panels_504]
        if len(a) != len(b) or any(v is None for v in a + b) or len(a) < 2:
            absent.append(row)
            continue
        rows[row] = _facts.structure_rise_verdict(a, b, row)
    return {
        "horizons": [CERTIFIED_HORIZON_DAYS, 504],
        "seeds": len(panels_252),
        "matches": sorted(r for r, v in rows.items() if v["verdict"] == "matches"),
        "below": sorted(r for r, v in rows.items() if v["verdict"] == "below"),
        "above": sorted(r for r, v in rows.items() if v["verdict"] == "above"),
        "absent": sorted(absent),
        "rows": rows,
    }


def structure_rise_bar(fresh: Mapping[str, Any] | None,
                       recorded: Mapping[str, Any] | None,
                       *, label: str = STRUCTURE_RISE_FIELD) -> dict[str, Any]:
    """Non-regression on the rise: a row whose record MATCHES the tape's
    rise may not read below or above it again. Same three refusals as
    `structure_bar`: lost, absent, no record."""
    from . import facts as _facts

    def refuse(reason: str, **extra: Any) -> dict[str, Any]:
        out = {"label": label, "passed": False, "rows_matching": [],
               "recorded_matching": [], "lost": [], "absent": [], "gained": [],
               "reason": reason}
        out.update(extra)
        return out

    if recorded is None:
        return refuse(f"{label}: no committed rise certificate to read against; lay one down with `record.py --panel` on an artefact that retains per_seed_504")
    if fresh is None:
        return refuse(f"{label}: the record carries a rise certificate and this run produced none, which is a gate that stopped being run rather than a preset that passed it")
    now = set(fresh.get("matches") or ()); was = set(recorded.get("matches") or ())
    accounted = now | set(fresh.get("below") or ()) | set(fresh.get("above") or ())
    absent = sorted(set(_facts.STRUCTURE) - accounted)
    lost = sorted(r for r in was if r not in now and r not in absent)
    gained = sorted(r for r in now if r not in was)
    reasons = []
    if absent:
        reasons.append(f"{label}: the certificate does not answer " + ", ".join(absent) + " -- the row has left the gate rather than failed it")
    if lost:
        reasons.append(f"{label}: the record MATCHES the tape's rise on " + ", ".join(lost) + " and this certificate reads " + ", ".join(
            f"{r} {fresh['rows'][r]['verdict']} (median rise {fresh['rows'][r]['median_rise']:+.4f} [{fresh['rows'][r]['ci90'][0]:+.4f}, {fresh['rows'][r]['ci90'][1]:+.4f}] against the tape's {fresh['rows'][r]['tape_rise']:+.4f})"
            if (fresh.get("rows") or {}).get(r) else r for r in lost))
    return {"label": label, "passed": not reasons, "rows_matching": sorted(now), "recorded_matching": sorted(was),
            "lost": lost, "absent": absent, "gained": gained,
            "reason": "; ".join(reasons) if reasons else (
                f"{label}: every one of the {len(was)} row(s) the record matches the tape's rise on matches again"
                + (", and " + ", ".join(gained) + " newly matches" if gained else "")
                + ("" if was or gained else "; nothing matched the tape's rise on the record and nothing does now, which is the reading and not a regression"))}


def certify(panels: Sequence[Mapping[str, float]], *,
             horizon_days: int = CERTIFIED_HORIZON_DAYS,
             stationary_opening: bool | None = None) -> dict[str, Any]:
    """The certificate, as THREE counts, from the per-seed panels themselves.

    `score` grades one aggregated panel against the bands and answers one
    question: could a real year read this. `certify` takes the per-seed
    panels the certification run already produces and answers three, because
    one band cannot answer more than one.

      in band          a of 14   FIDELITY. `score`, unchanged, clamps
                                 included. "Could a real year read this."
      mechanism shown  b of N    MECHANISM. An exact sign test of the
                                 per-seed readings against each row's
                                 mechanism-absent reading. "Is a model
                                 without the mechanism excluded."
      at real centre   c of 14   CENTRE. `z_r` against the real median,
                                 diagnostic, never a gate.

    Why the second count has to exist. `facts.BAND_RULE` builds a prediction
    interval for ONE real year and the panel grades a thirty-seed median,
    whose sampling sd is about a quarter of one seed's. The band is
    therefore about five times wider than the resolution of the thing it
    judges, and it contains the mechanism-absent reading on five of the
    fourteen rows -- measured with the shipped preset's own seed noise, a
    null model's graded median passes the band with probability 1.000 on
    three of them. Fourteen of fourteen in band is a true statement about
    fidelity and says nothing at all about whether the mechanisms are
    there. See `facts.NULLS`.

    `N` is the mechanism rows this horizon GRADES: a row named in
    `facts.MECHANISM_DIAGNOSTIC` for the horizon still gets a verdict, is
    printed, and is not counted, because a correct model would fail it here
    and a count that included it would grade the reference rather than the
    model.

    REVERSED is reported separately from NOT SHOWN and by name. A model with
    the sign of a real effect backwards is a different failure from one whose
    effect is too small to see, and the fidelity band cannot tell them apart.
    """
    from . import facts as _facts

    panels = [dict(p) for p in panels]
    if len(panels) < 2:
        raise ValidationError(
            f"a certificate needs at least two per-seed panels; a sign test "
            f"on one seed has no power. Got {len(panels)}")

    graded = _facts.aggregate_panels(panels, keys=_facts.SHAPE)
    # PINNED TO `shipped`, NOT TAKEN FROM THE DEFAULT, since 2026-09-15.
    # This block exists to be the shipped reading BESIDE the ruled one, and
    # its own comment below says so. It read the default, so when
    # `DEFAULT_BAND_BASIS` moved to `ruled` the two blocks became the same
    # count and the certificate lost the comparison it is built around --
    # with `counts.basis` and `counts.basis_ruled` both naming the ruled
    # table and nothing saying the shipped reading had gone. That is the
    # defect family this branch is repairing, one storey up, so `certify`
    # is the one producer in the library that names both of its bases and
    # takes neither from the default.
    fidelity = score(graded, horizon_days=horizon_days, basis="shipped")
    # THE SAME PANEL AGAINST THE BAND THE PROJECT RULED, beside the band it
    # ships. Both, never one: the shipped block keeps its meaning so no
    # reader's number moves underneath them, and the ruled block is the
    # first time anything in this library grades a panel against
    # `ruling-the-ruler-is-the-universal-band`. Which of the two is the bar
    # is Simon's decision and `BAR_BAND_BASIS` names it.
    bar = score(graded, horizon_days=horizon_days, basis=BAR_BAND_BASIS)

    def readings(row: str) -> list[float]:
        return [p[row] for p in panels if p.get(row) is not None]

    mechanism: dict[str, Any] = {}
    centre: dict[str, Any] = {}
    for row in _facts.SHAPE:
        values = readings(row)
        if len(values) < 2:
            continue
        if row in _facts.MECHANISM:
            mechanism[row] = _facts.mechanism_verdict(
                values, row, horizon_days=horizon_days)
        centre[row] = _facts.centre_distance(values, row,
                                             horizon_days=horizon_days)

    counted = {r: v for r, v in mechanism.items() if v["counted"]}
    shown = sorted(r for r, v in counted.items() if v["verdict"] == "shown")
    not_shown = sorted(r for r, v in counted.items() if v["verdict"] == "not shown")
    backwards = sorted(r for r, v in counted.items() if v["verdict"] == "reversed")
    at_the_cut = sorted(r for r, v in counted.items() if v["at_the_cut"])
    determined = {r: v for r, v in centre.items() if v["z_r"] is not None}
    at_centre = sorted(r for r, v in determined.items() if v["at_centre"])

    return {
        "horizon_days": horizon_days,
        "seeds": len(panels),
        "graded": graded,
        "fidelity": fidelity,
        "bar": bar,
        "mechanism": mechanism,
        # THE SECOND GATE, BESIDE THE THREE COUNTS AND IN NONE OF THEM.
        # `facts.STRUCTURE`'s rows have no band, so they are in `fidelity`,
        # in `counts` and in `mechanism` nowhere at all; the ruling is that
        # a structural row goes in a gate of its own rather than into the
        # panel. It is computed here so that one run produces both
        # certificates off ONE set of per-seed panels -- the failure to
        # avoid is a bar reading a structural verdict from a different run
        # than the mechanism verdict beside it.
        #
        # `certification_record` does NOT carry this and must not: the
        # mechanism block is written onto every committed record and folding
        # a structural row into it is the mixing the ruling forbids. It
        # reaches a record through `record.py --structure-rows`, under its
        # own field, exactly as `CERTIFIED_LEVEL` reaches one through
        # `--level-rows`.
        "structure": certify_structure(panels, horizon_days=horizon_days),
        "centre": centre,
        # The index tail row, which is neither a shape row nor a mechanism
        # row: it counts events rather than reading a shape, so it has its
        # own line and its own three counts. None on panels that do not
        # carry it.
        # PINNED TO `BAR_BAND_BASIS`, NOT TAKEN FROM THE DEFAULT, for the
        # same reason `fidelity` and `bar` are pinned above: `certify` names
        # both of its bases and takes neither from the default.
        "tail": tail_block(panels, horizon_days=horizon_days,
                           basis=BAR_BAND_BASIS,
                           stationary_opening=stationary_opening),
        "counts": {
            "in_band": fidelity["shape_in_band"],
            "in_band_of": fidelity["shape_of"],
            # The same count on the ruled band, and the basis each was
            # taken against. Two counts with two bases, because one count
            # with one symbol is what let the bar be ruled against one
            # object and computed against another for a fortnight.
            "in_band_ruled": bar["shape_in_band"],
            "in_band_ruled_of": bar["shape_of"],
            "basis": fidelity["basis_detail"],
            "basis_ruled": bar["basis_detail"],
            "mechanism_shown": len(shown),
            "mechanism_of": len(counted),
            "at_centre": len(at_centre),
            "at_centre_of": len(determined),
        },
        "shown": shown,
        "not_shown": not_shown,
        "reversed": backwards,
        "at_the_cut": at_the_cut,
        "diagnostic": sorted(r for r, v in mechanism.items() if not v["counted"]),
        "off_centre": sorted(r for r, v in determined.items()
                             if not v["at_centre"]),
        "centre_undetermined": sorted(r for r, v in centre.items()
                                      if v["z_r"] is None),
        # A row can be in band and reversed at once, which is the whole
        # finding; naming those rows is cheaper than expecting a reader to
        # intersect two lists.
        "in_band_and_reversed": sorted(
            r for r in backwards
            if fidelity["statistics"].get(r, {}).get("in_band")),
    }


def certification_record(result: Mapping[str, Any]) -> dict[str, Any]:
    """`certify`'s answer, trimmed to what a committed record should carry.

    JSON-safe, and it drops the fidelity block because a preset record
    already carries `panel_252` and `in_band` beside this. What it keeps per
    row is the sign test itself -- the count, the cut, the null and the
    verdict -- plus both effect sizes, so a reader can see how far from the
    cut a verdict sat without re-running anything.

    Defined here rather than in the tool that writes records, so the tool and
    `envelope.CERTIFIED_MECHANISM` cannot drift into two shapes.
    """
    return {
        "horizon_days": result["horizon_days"],
        "seeds": result["seeds"],
        "counts": dict(result["counts"]),
        "shown": list(result["shown"]),
        "not_shown": list(result["not_shown"]),
        "reversed": list(result["reversed"]),
        "at_the_cut": list(result["at_the_cut"]),
        "diagnostic": list(result["diagnostic"]),
        "in_band_and_reversed": list(result["in_band_and_reversed"]),
        "rows": {
            row: {
                "k": m["k"], "n": m["n"], "cut": m["cut"],
                "null": m["null"], "median": m["median"],
                "p": m["p"], "verdict": m["verdict"],
                "at_the_cut": m["at_the_cut"], "counted": m["counted"],
                "z0_normal": m["z0_normal"],
                "z0_bootstrap": m["z0_bootstrap"],
                "tolerance": m["tolerance"],
                "band_windows": m["band_windows"],
            }
            for row, m in result["mechanism"].items()
        },
        "centre": {
            row: {"z_r": c["z_r"], "median": c["median"],
                  "real_centre": c["real_centre"], "se_m": c["se_m"],
                  "se_real": c["se_real"], "at_centre": c["at_centre"],
                  "undetermined": c["undetermined"]}
            for row, c in result["centre"].items()
        },
        # Kept whole: every field is JSON-safe and the three counts are the
        # part a reader cannot recompute from the rate.
        "tail": result.get("tail"),
    }


def mechanism_bar(fresh: Mapping[str, Any] | None,
                  recorded: Mapping[str, Any] | None,
                  *, label: str = "mechanism",
                  horizon_days: int = CERTIFIED_HORIZON_DAYS) -> dict[str, Any]:
    """One certificate against the one a preset committed: the SUBSET rule.

    `certify` has published `mechanism_shown` on every record since the gate
    was built and NOTHING HAS EVER REFUSED ON IT. This is the refusal. It is
    a function rather than an assertion inside a test so that the rule has
    ONE definition and the two things that enforce it -- `record.py`, which
    refuses to overwrite a certificate that shows less, and the `ship_bar`
    test, which refuses the release -- read the same object. The failure to
    avoid is the `vixlaw-ruling` shape: rows that justified a decision and
    then existed in no scorer.

    THE RULE, in one sentence. A preset's `not_shown` must be a SUBSET of
    the `not_shown` its own committed record carries: it may show more
    mechanisms than its record, never fewer, and never a DIFFERENT one at
    the same count.

    WHY A SET AND NOT A COUNT, which is the whole of the choice. Every fixed
    count is either unreachable or arbitrary -- ten of ten fails every
    preset ever shipped, the best of the eighteen being nine, and any
    threshold under it is a number picked because it clears. Non-regression
    on the COUNT is reachable and still blind to the failure worth catching:
    a model that loses `leverage_effect` and gains `corr_asymmetry` reads
    nine of ten on both sides of the change, and a count cannot tell that
    swap from no change at all. The set can, so the bar names rows.

    EACH PRESET AGAINST ITSELF, never against another preset and never
    against a fixed roster. The eighteen committed records read six shown
    through nine, measured on different builds across three eras; one rule
    over all of them would be a rule about the history of the programme. The
    subset form asks only that a model not LOSE what it was recorded doing,
    which is what a regression is.

    THREE REFUSALS, each with its own reason string, because a bare count in
    a failure message would waste the change:

      lost        a row the record shows and this certificate does not,
                  whether it now reads NOT SHOWN or REVERSED.
      reversed    named separately where a lost row went backwards rather
                  than quiet, since `certify` distinguishes them and a sign
                  flip is the louder failure.
      absent      a row in none of the certificate's four lists. It has left
                  the gate rather than failed it, which a subset test on
                  `not_shown` alone reads as a PASS -- `not_shown` shrinks
                  when a row vanishes.

    NO RECORD IS A REFUSAL, not a vacuous pass. Subset is defined against a
    committed set and a preset that has laid none down has nothing to be
    read against; a bar that passed it would be reporting an absence as a
    result. You lay down a record before you ship against it. The tool that
    WRITES the first record is the exception and says so at its own call
    site, because a first measurement cannot regress against itself.

    `fresh` and `recorded` are blocks from `certification_record`, or a
    `certify` result, which carries the same four lists. Both must be the
    same PANEL as well as the same horizon -- 252 against 252, held-out
    against held-out -- and `record_bar` is what pairs them.
    """
    from . import facts as _facts

    def lists(block: Mapping[str, Any]) -> tuple[set[str], set[str], set[str],
                                                 set[str]]:
        return (set(block.get("shown") or ()),
                set(block.get("not_shown") or ()),
                set(block.get("reversed") or ()),
                set(block.get("diagnostic") or ()))

    def refuse(reason: str, **extra: Any) -> dict[str, Any]:
        out = {"label": label, "horizon_days": horizon_days, "passed": False,
               "shown": [], "recorded_shown": [], "lost": [], "reversed": [],
               "absent": [], "gained": [], "reason": reason}
        out.update(extra)
        return out

    if recorded is None:
        return refuse(
            f"{label}: no committed certificate to read against. The bar is "
            "a subset rule and a preset with no record has laid down no set "
            "to be a subset of; lay the record down before shipping against "
            "it")
    if fresh is None:
        return refuse(
            f"{label}: the record carries a certificate and this run "
            "produced none, which is a mechanism gate that stopped being "
            "run rather than a preset that passed it")

    for name, block in (("this run", fresh), ("the record", recorded)):
        days = block.get("horizon_days")
        if days is not None and int(days) != int(horizon_days):
            # Refused rather than read, for `score`'s reason: the rows a
            # horizon GRADES are not the rows another one grades, so a
            # subset taken across two horizons compares sets built from
            # different denominators and means nothing.
            return refuse(
                f"{label}: {name} was certified at {days} days and the bar "
                f"reads {horizon_days}; the rows a horizon counts are set by "
                f"facts.MECHANISM_DIAGNOSTIC and differ between them")

    shown, not_shown, backwards, reported = lists(fresh)
    was_shown, was_not_shown, _, _ = lists(recorded)
    accounted = shown | not_shown | backwards | reported
    absent = sorted(set(_facts.MECHANISM) - accounted)

    # THE SUBSET, TAKEN ON THE SHOWN SIDE and not on `not_shown`, because
    # the two are equivalent only while the roster holds. A row deleted from
    # the certificate leaves `not_shown` SMALLER, so `not_shown <= recorded`
    # passes on exactly the change that removed the row from the gate. Read
    # on the shown side a vanished row is lost, which is what it is.
    lost = sorted(r for r in was_shown if r not in shown and r not in absent)
    lost_backwards = sorted(r for r in lost if r in backwards)
    gained = sorted(r for r in shown if r not in was_shown)

    reasons = []
    if absent:
        reasons.append(
            f"{label}: the certificate does not answer "
            + ", ".join(absent)
            + " -- a mechanism row in none of shown, not_shown, reversed or "
              "diagnostic has left the gate rather than failed it")
    if lost:
        reasons.append(
            f"{label}: the record shows " + ", ".join(lost)
            + " and this certificate does not"
            + (" (" + ", ".join(lost_backwards) + " read REVERSED, which is "
               "a sign flip and not a quiet row)" if lost_backwards else ""))

    return {
        "label": label,
        "horizon_days": horizon_days,
        "passed": not reasons,
        "shown": sorted(shown),
        "recorded_shown": sorted(was_shown),
        "lost": lost,
        "reversed": lost_backwards,
        "absent": absent,
        "gained": gained,
        "reason": "; ".join(reasons) if reasons else (
            f"{label}: every one of the {len(was_shown)} mechanism(s) the "
            f"record shows is shown again"
            + (f", and {len(gained)} more (" + ", ".join(gained) + ")"
               if gained else "")),
    }


#: The two certificates a preset record carries, and the field each is read
#: from. BOTH ARE READ AND EITHER FAILING IS A FAILURE.
#:
#: Not the 252 panel alone: the held-out seeds exist to catch a mechanism
#: visible only on the thirty seeds the preset was measured against, and a
#: bar that read the measured panel only would spend exactly that
#: protection. Not the held-out panel alone either, since 252 is the panel
#: the certificate is published and quoted on.
#:
#: AND EACH AGAINST ITS OWN COUNTERPART, never crossed. The two panels are
#: different seed sets and the committed records disagree between them
#: routinely -- pt-v16 records `corr_asymmetry_lagged` not shown at 252 and
#: shown on the held-out seeds -- so a row shown on one panel is no evidence
#: at all about the other. Crossing them would manufacture both a false
#: refusal and a false pass on the same file.
MECHANISM_BAR_PANELS = ("mechanism_252", "mechanism_heldout_seeds")


def record_bar(fresh: Mapping[str, Any],
               recorded: Mapping[str, Any] | None,
               *, horizon_days: int = CERTIFIED_HORIZON_DAYS) -> dict[str, Any]:
    """`mechanism_bar` on both of a record's certificates, as one verdict.

    Takes two preset RECORDS -- the one this run would write and the one
    committed -- and pairs `MECHANISM_BAR_PANELS` by name. The verdict
    passes only if both panels pass, and its `reason` names every row that
    went missing and the panel it went missing on.
    """
    panels = {}
    for field in MECHANISM_BAR_PANELS:
        panels[field] = mechanism_bar(
            fresh.get(field), (recorded or {}).get(field),
            label=field, horizon_days=horizon_days)
    failed = [v for v in panels.values() if not v["passed"]]
    return {
        "horizon_days": horizon_days,
        "passed": not failed,
        "panels": panels,
        "lost": sorted({r for v in panels.values() for r in v["lost"]}),
        "absent": sorted({r for v in panels.values() for r in v["absent"]}),
        "reason": "; ".join(v["reason"] for v in (failed or panels.values())),
    }


def mechanism_bar_line(verdict: Mapping[str, Any]) -> str:
    """The bar's verdict as one line, to sit beside the three counts."""
    return (f"  mechanism bar    {'PASS' if verdict['passed'] else 'REFUSED'}"
            f"     no mechanism the record shows may go unshown -- "
            + verdict["reason"])


def structure_bar(fresh: Mapping[str, Any] | None,
                  recorded: Mapping[str, Any] | None,
                  *, label: str = "structure",
                  horizon_days: int = CERTIFIED_HORIZON_DAYS) -> dict[str, Any]:
    """The structural certificate against the one a preset committed.

    THE SAME RULE AS `mechanism_bar`, IN THE SAME FORM, on Simon's ruling:
    subset, both panels. A preset must not read REFUSED on a structural row
    its own committed record reads PASS. It may pass MORE rows than its
    record, never fewer, and a row that leaves the certificate is a loss and
    not a shorter list.

    WHAT THIS DOES AND DOES NOT DO TODAY, said here because the block is
    being laid down RED and a reader will otherwise expect it to be
    blocking. pt-v19 reads REFUSED on `vix_ar1_debiased` on both panels and
    it SHIPS, because there is no earlier record to regress from and the bar
    is a non-regression rule rather than a fidelity threshold. What the
    block buys today is visibility -- the row is on every record, by name,
    with its `k`, its cut and its side -- and what it buys tomorrow is the
    ratchet: the first model that repairs the row lays down a PASS, and from
    that record on no model may lose it again.

    WHY NOT A HARD FAIL ON REFUSED, which is the obvious alternative and the
    one the row's own evidence argues for. Two reasons, and the first is
    Simon's ruling and settles it. The second is the form's: the sign test
    has no width, its separation is bought by treating the tape's centre as
    exact, and at thirty seeds the tape's standard error is 1.29 times the
    test's own resolution (`facts.STRUCTURE`, `programme/widthless-design.md`).
    A gate that REFUSED a release on that would be ruling a 0.03 offset a
    ship-stopper on a ruler known to be softer than the offset it is
    measuring. Non-regression asks a question the form can answer: it
    compares two models on one ruler, and an exact ruler is not needed to
    say that one model moved off a point the other sat on.

    THREE REFUSALS, each named, exactly as the mechanism bar names its own:

      lost      a row the record passes and this certificate refuses.
      absent    a row in neither list -- it has left the gate rather than
                failed it, which a subset on `refused` alone reads as a
                PASS because `refused` shrinks when a row vanishes.
      no record a preset that has laid none down. Subset against nothing is
                an absence and an absence is not a result. The tool that
                WRITES the first record is the exception, and it says so at
                its own call site rather than here.

    `fresh` and `recorded` are `certify_structure` blocks. Both must be the
    same PANEL as well as the same horizon; `structure_record_bar` pairs
    them by name.
    """
    from . import facts as _facts

    def lists(b: Mapping[str, Any]) -> tuple[set[str], set[str]]:
        return set(b.get("passed") or ()), set(b.get("refused") or ())

    def refuse(reason: str, **extra: Any) -> dict[str, Any]:
        out = {"label": label, "horizon_days": horizon_days, "passed": False,
               "rows_passed": [], "recorded_passed": [], "lost": [],
               "absent": [], "gained": [], "reason": reason}
        out.update(extra)
        return out

    if recorded is None:
        return refuse(
            f"{label}: no committed structural certificate to read against. "
            f"The bar is a subset rule and a preset with no record has laid "
            f"down no set to be a subset of; lay the record down with "
            f"`record.py --structure-rows` before shipping against it")
    if fresh is None:
        return refuse(
            f"{label}: the record carries a structural certificate and this "
            f"run produced none, which is a gate that stopped being run "
            f"rather than a preset that passed it")

    for name, b in (("this run", fresh), ("the record", recorded)):
        days = b.get("horizon_days")
        if days is not None and int(days) != int(horizon_days):
            return refuse(
                f"{label}: {name} was certified at {days} days and the bar "
                f"reads {horizon_days}; a structural row's tape centre is "
                f"per horizon (facts.REAL_VIX_AR1_WINDOWS) and a subset "
                f"taken across two of them signs against two different points")

    now_pass, now_refused = lists(fresh)
    was_pass, _ = lists(recorded)
    accounted = now_pass | now_refused
    absent = sorted(set(_facts.STRUCTURE) - accounted)

    # THE SUBSET, TAKEN ON THE PASSING SIDE, for `mechanism_bar`'s reason
    # one row over: a row deleted from the certificate leaves `refused`
    # SMALLER, so `refused <= recorded` passes on exactly the change that
    # removed the row from the gate.
    lost = sorted(r for r in was_pass if r not in now_pass and r not in absent)
    gained = sorted(r for r in now_pass if r not in was_pass)

    reasons = []
    if absent:
        reasons.append(
            f"{label}: the certificate does not answer "
            + ", ".join(absent)
            + " -- a structural row in neither passed nor refused has left "
              "the gate rather than failed it")
    if lost:
        reasons.append(
            f"{label}: the record PASSES " + ", ".join(lost)
            + " and this certificate REFUSES "
            + ", ".join(
                f"{r} (k {fresh['rows'][r]['k']} of {fresh['rows'][r]['n']} "
                f"{fresh['rows'][r]['side']} the tape centre "
                f"{fresh['rows'][r]['real_centre']:.6f}, cut "
                f"{fresh['rows'][r]['cut']})"
                if (fresh.get("rows") or {}).get(r) else r
                for r in lost))

    return {
        "label": label,
        "horizon_days": horizon_days,
        "passed": not reasons,
        "rows_passed": sorted(now_pass),
        "recorded_passed": sorted(was_pass),
        "lost": lost,
        "absent": absent,
        "gained": gained,
        "reason": "; ".join(reasons) if reasons else (
            f"{label}: every one of the {len(was_pass)} structural row(s) "
            f"the record passes passes again"
            + (f", and {len(gained)} more (" + ", ".join(gained) + ")"
               if gained else "")),
    }


#: The two structural certificates a preset record carries, and the field
#: each is read from. BOTH ARE READ AND EITHER FAILING IS A FAILURE, for
#: `MECHANISM_BAR_PANELS`' reasons exactly: the two panels are different
#: seed sets, the held-out seeds exist to catch what the measured thirty
#: cannot, and the shipped default already reads k = 21 of 30 at 252 and
#: k = 28 of 30 held-out -- one at the cut and one nowhere near it, on the
#: same preset and the same build. A bar reading either panel alone would
#: spend exactly the protection the second panel is there to give.
STRUCTURE_BAR_PANELS = ("structure_252", "structure_heldout_seeds")


def structure_record_bar(fresh: Mapping[str, Any],
                         recorded: Mapping[str, Any] | None,
                         *, horizon_days: int = CERTIFIED_HORIZON_DAYS
                         ) -> dict[str, Any]:
    """`structure_bar` on both of a record's structural certificates.

    Takes two preset RECORDS and pairs `STRUCTURE_BAR_PANELS` by name. The
    verdict passes only if both panels pass, and its `reason` names every
    row that was lost AND the panel it was lost on -- which is why the
    per-panel bar is labelled with the field rather than with the word
    "structure".
    """
    panels = {}
    for field in STRUCTURE_BAR_PANELS:
        panels[field] = structure_bar(
            fresh.get(field), (recorded or {}).get(field),
            label=field, horizon_days=horizon_days)
    # THE RISE, since 2026-09-21, read whenever the record carries it. A
    # record written before the block existed has laid down no rise to
    # regress from, and that is said in the verdict rather than refused: the
    # seventeen presets that will never be re-measured are not thereby
    # failing a gate that did not exist when they were recorded.
    if (recorded or {}).get(STRUCTURE_RISE_FIELD) or fresh.get(STRUCTURE_RISE_FIELD):
        if (recorded or {}).get(STRUCTURE_RISE_FIELD) is None:
            panels[STRUCTURE_RISE_FIELD] = {
                "label": STRUCTURE_RISE_FIELD, "passed": True, "first": True,
                "rows_matching": [], "recorded_matching": [], "lost": [],
                "absent": [], "gained": [],
                "reason": f"{STRUCTURE_RISE_FIELD}: the record predates the rise certificate and lays down none to regress from; this run lays one down"}
        else:
            panels[STRUCTURE_RISE_FIELD] = structure_rise_bar(
                fresh.get(STRUCTURE_RISE_FIELD), recorded.get(STRUCTURE_RISE_FIELD))
    failed = [v for v in panels.values() if not v["passed"]]
    return {
        "horizon_days": horizon_days,
        "passed": not failed,
        "panels": panels,
        "lost": sorted({r for v in panels.values() for r in v["lost"]}),
        "absent": sorted({r for v in panels.values() for r in v["absent"]}),
        "reason": "; ".join(v["reason"] for v in (failed or panels.values())),
    }


def structure_bar_line(verdict: Mapping[str, Any]) -> str:
    """The structural bar's verdict as one line, beside the mechanism's."""
    return (f"  structure bar    {'PASS' if verdict['passed'] else 'REFUSED'}"
            f"     no structural row the record passes may go refused -- "
            + verdict["reason"])


def certification_report(result: Mapping[str, Any]) -> str:
    """`certify`'s three counts as text, each saying what it answers.

    The sentences are not decoration. "Fourteen of fourteen in band" has
    been read as "this model reproduces real markets" for three eras, and
    it is a statement about whether a real YEAR could read these numbers.
    """
    counts = result["counts"]
    lines = [
        f"certification: {result['seeds']} seeds, {result['horizon_days']} days",
        "",
        f"  in band          {counts['in_band']:2d} of {counts['in_band_of']}"
        "   fidelity: could a real year read this",
        f"  mechanism shown  {counts['mechanism_shown']:2d} of "
        f"{counts['mechanism_of']}"
        "   is a model WITHOUT the mechanism excluded",
        f"  at real centre   {counts['at_centre']:2d} of "
        f"{counts['at_centre_of']}"
        "   diagnostic, never a gate",
        "",
        f"{'row':24s} {'median':>10s} {'null':>9s} {'k':>7s} {'p':>7s}  "
        f"{'z_0 norm':>8s} {'z_0 boot':>8s}  {'z_R':>6s}  verdict",
    ]
    for row, m in result["mechanism"].items():
        c = result["centre"].get(row, {})
        z_r = c.get("z_r")
        band = result["fidelity"]["statistics"].get(row, {})
        marks = [m["verdict"]]
        if m["at_the_cut"]:
            marks.append("AT THE CUT")
        if not m["counted"]:
            marks.append("diagnostic, not counted")
        if not band.get("in_band", True):
            marks.append("OUT OF BAND")
        def effect(value: float | None) -> str:
            # None where the seeds do not scatter at all, which makes the
            # standard error zero and the ratio undefined. The GATE still has
            # an answer there -- it counts sides and needs no estimator --
            # so a dash in an effect-size column is not a missing verdict.
            return f"{value:>+8.2f}" if value is not None else f"{'--':>8s}"

        lines.append(
            f"{row:24s} {m['median']:>10.4f} {m['null']:>9.4f} "
            f"{m['k']:>3d}/{m['n']:<3d} {m['p']:>7.3f}  "
            f"{effect(m['z0_normal'])} {effect(m['z0_bootstrap'])}  "
            + (f"{z_r:>+6.2f}" if z_r is not None else f"{'--':>6s}")
            + "  " + ", ".join(marks))
    for row, c in result["centre"].items():
        if row in result["mechanism"]:
            continue
        z_r = c["z_r"]
        kind = ("equivalence: its real value IS its null"
                if row in ("return_acf1",) else "level-only: no mechanism null")
        lines.append(
            f"{row:24s} {c['median']:>10.4f} {'--':>9s} {'--':>7s} {'--':>7s}  "
            f"{'--':>8s} {'--':>8s}  "
            + (f"{z_r:>+6.2f}" if z_r is not None else f"{'--':>6s}")
            + "  " + kind)
    tail = result.get("tail")
    if tail:
        marks = [tail["verdict"]]
        if tail["at_the_edge"]:
            marks.append("AT THE EDGE")
        if not tail["counted"]:
            marks.append("graded, not counted")
        lines += [
            "",
            f"{tail['row']:24s} {tail['rate']:>10.4f} "
            f"{'--':>9s} {'--':>7s} {'--':>7s}  "
            f"{'--':>8s} {'--':>8s}  "
            + (f"{tail['z_r']:>+6.2f}" if tail["z_r"] is not None
               else f"{'--':>6s}")
            + "  " + ", ".join(marks),
            f"  {tail['hits']} hits in {tail['sessions']} sessions over "
            f"{tail['seeds']} seeds, pooled; band "
            f"{tail['band'][0]:.2f} to {tail['band'][1]:.2f}, centre "
            f"{tail['real_centre']:.3f} (se {tail['se_real']:.3f}); "
            + (f"run se {tail['se_m']:.3f}, " if tail["se_m"] is not None
               else "")
            + f"margin to the nearer edge {tail['margin']:+.3f}",
            # The mixture, which the rate cannot see. Printed beside the
            # tape's own three so a reader can tell "the right rate" from
            # "the right rate for the right reason".
            f"  seeds at zero {tail['zero_share']:.2f} (tape "
            f"{tail['tape']['zero_share']:.2f}), at five or more "
            f"{tail['five_or_more_share']:.2f} (tape "
            f"{tail['tape']['five_or_more_share']:.2f}), most in one seed "
            f"{tail['max_hits']} (tape {tail['tape']['max_hits']}); "
            "reported, not gated",
        ]
        if tail["not_counted"]:
            lines += textwrap.wrap(tail["not_counted"], 76,
                                   initial_indent="  ", subsequent_indent="  ")
    if result["reversed"]:
        lines += [
            "",
            "REVERSED, which the band cannot see: "
            + ", ".join(result["reversed"]),
        ]
        if result["in_band_and_reversed"]:
            lines.append(
                "  and in band while reversed: "
                + ", ".join(result["in_band_and_reversed"])
                + " -- the model has the sign of a real effect backwards and "
                "the fidelity count reads it as a pass")
    if result["at_the_cut"]:
        lines += ["", "at the cut, so undetermined at this seed count: "
                  + ", ".join(result["at_the_cut"])]
    if result["diagnostic"]:
        lines += ["", "reported and NOT counted at this horizon:"]
        for row in result["diagnostic"]:
            lines += textwrap.wrap(
                f"{row}: {result['mechanism'][row]['diagnostic']}", 76,
                initial_indent="  ", subsequent_indent="  ")
    if result["centre_undetermined"]:
        lines += ["", "no centre distance: "
                  + ", ".join(result["centre_undetermined"])]
    return "\n".join(lines)


def regressions(panel: Mapping[str, float], *,
                horizon_days: int = CERTIFIED_HORIZON_DAYS,
                basis: str = DEFAULT_BAND_BASIS) -> list[str]:
    """Statistics the SHIPPED preset holds in band and this panel does not.

    BOTH SIDES ON ONE RULER, and that had to be repaired on 2026-09-15 when
    the default basis moved. The candidate side came from `score` at
    whatever basis was live while the baseline side read
    `REAL_MARKETS[name]` by name, so a ruled-basis candidate was compared
    with a decade-band baseline and a row could be reported lost because
    the two sides were graded by different tables. `basis` now picks one
    table and both sides read it.

    A row the basis cannot read is SKIPPED rather than reported lost. Its
    `in_band` is None, `not None` is True, and this function decides
    whether a candidate becomes the default, so the difference between "the
    candidate lost this row" and "nobody graded this row" is the whole
    answer.

    The reconciliation this module was missing. The calibration objective
    sums two horizons; the envelope certifies one. A search can therefore
    improve its own score by spending the certification, and the resulting
    candidate looks like a straightforward win until somebody measures the
    whole panel by hand.

    That is not hypothetical. `pt-v4` halves the dual-horizon loss and is
    the first vector ever to close the thin-tails gap, retired at 0.2.0 when
    the shipped preset closed it too -- and it surrenders
    `return_acf1` at the certified horizon, on training seeds, held-out
    seeds and a held-out universe alike. It was called a win twice before
    anyone counted (CALIBRATION-FOLLOWUPS §33).

    The trade pt-v4 pays was later shown to be a wiring accident rather
    than a law. A jump landed on `mispricing_s` after the momentum roll had
    already recorded the pre-jump level, so herding read the jump as a
    re-rating and continued it: fattening the tail and adding return
    continuation were the same write. `pt-v5` separates them and holds both,
    nine of the original ten at the certified horizon with the 504-day tail
    closed (§38,
    §45). That does not soften the policy below. pt-v5 passes the controls
    and is still not the default, because passing §8 is not certification
    and `CERTIFIED` is measured on the shipped preset.

    So the count is a function now rather than a judgement. An empty list
    means the candidate certifies at least as well as what ships; a
    non-empty one names exactly what it costs, and the policy that follows
    is simple: **a candidate that regresses the certified horizon does not
    become the default, whatever the objective says.** The objective is the
    search's proxy. The envelope is the contract.

    Only meaningful at the certified horizon, where a shipped baseline
    exists to compare against; `CERTIFIED` is measured there.
    """
    if horizon_days != CERTIFIED_HORIZON_DAYS:
        raise ValidationError(
            f"regressions compares against CERTIFIED, which is measured at "
            f"{CERTIFIED_HORIZON_DAYS} days, not {horizon_days}. Use "
            f"`score` to read another horizon on its own ruler.")
    theirs = score(panel, horizon_days=horizon_days, basis=basis)["statistics"]
    # The shipped preset's own panel, graded by the SAME table, so the
    # comparison is between two readings and not between two rulers.
    ours = score(CERTIFIED, horizon_days=horizon_days,
                 basis=basis)["statistics"]
    lost = []
    for name, row in theirs.items():
        # Being outside the calibration objective is not a licence to lose
        # the row. Until 0.2.0 every `structural` statistic was skipped here,
        # on the reasoning that the shipped preset did not hold them either;
        # pt-v10 holds all fourteen at the certified horizon, so that
        # reasoning expired and the skip with it. The condition below is the
        # one that always did the work: a row the shipped preset does not
        # hold in band cannot be lost by a candidate.
        # A level or crisis row is certified on a different protocol, so it
        # is absent from `CERTIFIED` and skipped. CORRECTED 2026-09-14: this
        # read "is held red at the shipped preset", which was true through
        # pt-v16 and is not true of pt-v19 -- all four of its level and
        # crisis rows are in band (`CERTIFIED_LEVEL`, `CERTIFIED_CRISIS`).
        # The skip never depended on the verdict, only on the protocol.
        if name not in CERTIFIED:
            continue
        # `is False` and `is True`, never truthiness: a row this basis
        # cannot read is None on both sides and belongs in neither list.
        if ours.get(name, {}).get("in_band") is True and \
                row["in_band"] is False:
            lost.append(name)
    return sorted(lost)


def certified(basis: str = DEFAULT_BAND_BASIS) -> dict[str, Any]:
    """The envelope as a plain mapping, for serialising into a manifest.

    The statistics carry their group. The shape rows are what a green panel
    certifies; the level and crisis rows are reported with their own
    verdicts, computed here from the band rather than assumed, and a level
    or crisis row whose certified value has not been measured yet is listed
    under ``unmeasured`` rather than given a number.

    THE BASIS IS AN ARGUMENT, since 2026-09-15. This read `REAL_MARKETS`
    directly, which is the shipped decade table and the one ruler a caller
    could not ask it for anything else. That made this function the third
    band path in the library, after `loss._band_of` and `tail_block`, and
    the one a manifest is serialised from -- so a record could carry a
    verdict on a ruler the ruling had superseded and say nothing about it.
    A row the basis has no adopted band for now reports `band` and
    `in_band` as None and is named in ``unreadable``, which is what `score`
    does, rather than raising or borrowing another basis's band.
    """
    from .facts import SHAPE, LEVEL, CRISIS
    from . import facts as _facts
    if basis not in RULERS_BY_BASIS:
        raise ValidationError(
            f"{basis!r} is not a band basis; the bases are "
            f"{sorted(RULERS_BY_BASIS)}. A basis is an era, a window count "
            f"and a rule, and `facts.band_basis` states each one")
    if CERTIFIED_HORIZON_DAYS not in RULERS_BY_BASIS[basis]:
        raise ValidationError(
            f"{basis!r} has no band set at the certified horizon of "
            f"{CERTIFIED_HORIZON_DAYS} days; it carries "
            f"{sorted(RULERS_BY_BASIS[basis])}")
    bands, _, ruler_name = RULERS_BY_BASIS[basis][CERTIFIED_HORIZON_DAYS]
    statistics: dict[str, Any] = {}
    for table, group in ((CERTIFIED, "shape"), (CERTIFIED_LEVEL, "level"),
                         (CERTIFIED_CRISIS, "crisis")):
        for k, v in table.items():
            band = bands.get(k)
            statistics[k] = {
                "measured": v,
                "band": list(band) if band is not None else None,
                "in_band": (band_distance(v, *band) == 0
                            if band is not None else None),
                "group": group,
            }
    unmeasured = [k for k in LEVEL + CRISIS if k not in statistics]
    unreadable = sorted(k for k, s in statistics.items()
                        if s["in_band"] is None)
    return {
        "preset": PRESET,
        "certified_horizon_days": CERTIFIED_HORIZON_DAYS,
        # THE BASIS, NOT THE NAME, for the reason `score` stamps both: a
        # manifest carrying only a ruler's symbol asserts the same
        # provenance whatever that symbol now holds.
        "band_basis": basis,
        "band_basis_detail": _facts.band_basis(ruler_name),
        "statistics": statistics,
        "unreadable": unreadable,
        "groups": {"shape": list(SHAPE), "level": list(LEVEL), "crisis": list(CRISIS)},
        "unmeasured": unmeasured,
        "gaps": [
            {
                "id": g.id,
                "summary": g.summary,
                "detail": g.detail,
                "forbids": g.forbids,
                "statistics": list(g.statistics),
                "beyond_days": g.beyond_days,
                # A reader who cites the artifact and needs a gap closed
                # should be able to find out whether anything closes it,
                # without reading the source.
                "closed_by": list(g.closed_by),
            }
            for g in GAPS
        ],
    }
