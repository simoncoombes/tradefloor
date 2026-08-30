"""The realism envelope, as data: what tradefloor certifies, and what it does not.

`tradefloor-docs: docs/realism-envelope.md` states the envelope in prose.
This module states it in a form a program can read, so a user does not have
to remember a page to find out whether their question is one this simulator
can answer.

Two things live here, and neither is a score.

**Per-statistic intervals** (`intervals`). Every panel statistic is reported
with the spread it actually has across seeds, not as a bare median. A point
estimate from a stochastic simulator invites a precision it does not have:
`abs_return_acf20` reads +0.0096 at the shipped preset, and the across-seed
range is wide enough that a single seed can read either side of zero. The
band distance is reported in units of that spread, which is the same
weighting `tradefloor.loss` uses -- so "how far out" is denominated in the
model's own noise rather than in the statistic's arbitrary units.

**A membership check** (`check`). Given a horizon, the statistics a strategy
leans on, and the shape of the roster, it answers whether the question falls
inside the envelope, and when it does not, says which measurement says so.

## Why there is no single confidence number

`tradefloor.loss.compare_to_real_markets` refuses to emit one realism score,
and this module does not reopen that. Aggregation destroys the only
information that matters here: a model is realistic in some respects, at some
measurement scale, and not others. Modesty has nothing to do with it.

There is also a practical failure mode. A scalar travels and a caveat does
not. "87% realistic" is quotable in a way that "volatility memory turns
negative by lag 30, where real markets stay weakly positive out to lag 60"
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

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ._core import ValidationError
from .facts import REAL_MARKETS, SEED_SD, SEED_SD_504, band_distance

#: The preset these measurements describe.
PRESET = "pt-v16"

#: The measurement horizon the envelope certifies, in trading days.
#: Not a soft preference, though the reason is no longer a band count: since
#: pt-v12 all fourteen are in band at 504 days as well (`MEASURED_504`), and
#: pt-v16 holds them there with more room again. What holds the horizon here is
#: that `CERTIFIED` was MEASURED here, on thirty seeds and two held-out axes.
#:
#: The old reason -- that the thinnest 504-day row cleared its ceiling by
#: only 0.11 -- no longer applies: `annualised_vol_pct` read 33.89 under
#: pt-v12, 30.24 under pt-v14, and 28.12 under pt-v16, against the same
#: 34.0 ceiling throughout. The
#: horizon stays 252 because that is where the certification was measured,
#: not because 504 is fragile.
#:
#: This comment read "three statistics that are in band here leave it by 504
#: days" until 2026-08-27, which described pt-v3. `check` refuses to certify
#: beyond this horizon.
CERTIFIED_HORIZON_DAYS = 252

#: Measured at the certified horizon: 30 seeds, 40 instruments, 252 days.
#: ALL FOURTEEN in band, at a band-distance loss of 0.0000, and all fourteen
#: again on a held-out 60-name universe measured at the same resolution.
#:
#: Since pt-v12 (2026-08-26) all fourteen are ALSO in band at 504 days, which
#: is the first time this project has measured that: pt-v3 held seven there
#: and pt-v10 held thirteen. See `MEASURED_504` and the `horizon` gap, which
#: no longer carries a missing row.
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
CERTIFIED: dict[str, float] = {
    "annualised_vol_pct": 24.2377,
    "excess_kurtosis": 9.7510,
    "return_acf1": -0.0044,
    "abs_return_acf1": 0.0413,
    "abs_return_acf5": 0.0139,
    "abs_return_acf20": 0.0030,
    "cross_sectional_corr": 0.2825,
    "volume_abs_return_corr": 0.5158,
    "leverage_effect": -0.0141,
    "volume_change_acf1": -0.2798,
    "corr_asymmetry": 0.0175,
    "corr_asymmetry_lagged": -0.0015,
    "sector_excess_corr": 0.1604,
    "corr_persistence_acf1": 0.1724,
}

#: Bands re-derived at a 504-day window, from the same reference roster and
#: estimators as `facts.REAL_MARKETS`. Scoring a 504-day measurement against
#: the 252-day bands is the wrong ruler, and it flatters the model on
#: kurtosis while being harsher elsewhere -- these are mostly TIGHTER.
BANDS_504: dict[str, tuple[float, float]] = {
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

#: The same panel at 504 days. ALL FOURTEEN in band against `BANDS_504`,
#: which pt-v12 was the first preset to manage. This comment read "five of
#: ten" until 2026-08-26 (pt-v3, against the ten-statistic panel of the
#: time), then "thirteen of fourteen, missing only volume_change_acf1"
#: (pt-v10 and pt-v11). `volume_move_cap` closed that row.
#:
#: Read the headroom, not just the count. Under pt-v12 this table's thinnest
#: row was `annualised_vol_pct` at 33.89 against a ceiling of 34.0 -- 0.11 of
#: room on a statistic whose seed spread is far wider, so the count was
#: genuine but would have flipped on a change that barely moved the model.
#: pt-v16 reads 28.12 there, 5.88 of room, having widened it again at
#: the 0.6.0 boundary.
#:
#: The count is still MEASURED rather than certified: the certified horizon
#: is 252 because that is where `CERTIFIED` was measured.
MEASURED_504: dict[str, float] = {
    "annualised_vol_pct": 28.1221,
    "excess_kurtosis": 9.4598,
    "return_acf1": 0.0114,
    "abs_return_acf1": 0.1037,
    "abs_return_acf5": 0.0643,
    "abs_return_acf20": 0.0217,
    "cross_sectional_corr": 0.3524,
    "volume_abs_return_corr": 0.5942,
    "leverage_effect": -0.0274,
    "volume_change_acf1": -0.2567,
    "corr_asymmetry": -0.0065,
    "corr_asymmetry_lagged": -0.0232,
    "sector_excess_corr": 0.1579,
    "corr_persistence_acf1": 0.2865,
}

#: |return| autocorrelation at the certified horizon, against real markets.
#: The model crosses below real around lag 8 and goes NEGATIVE by lag 30,
#: where real markets stay weakly positive out to lag 60.
DECAY_252: dict[int, float] = {
    1: 0.1413, 2: 0.1063, 3: 0.0897, 5: 0.0496, 8: 0.0371,
    12: 0.0173, 20: 0.0082, 30: -0.0052, 45: -0.0120, 60: -0.0142,
}
REAL_DECAY: dict[int, float] = {
    1: 0.1071, 5: 0.0518, 8: 0.0453, 12: 0.0295, 20: 0.0286,
    30: 0.0179, 60: 0.0054,
}
#: Log-log slope over lags 1-20. Real markets decay hyperbolically; this
#: model decays exponentially, about 2.2x steeper, because it is built from
#: exponentials. No parameter setting turns one slope into the other.
DECAY_SLOPE = -0.953
REAL_DECAY_SLOPE = -0.436

#: The lag beyond which the model's volatility memory is not merely weak but
#: wrong in sign. A strategy reading volatility over a window longer than
#: this is reading a process that anti-predicts where the market persists.
MEMORY_VALID_TO_LAG = 20


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
            "pt-v16 holds ALL FOURTEEN at 504 days, as pt-v14 and pt-v12 did "
            "before it. pt-v12 was the first to manage it: pt-v3 held 7 "
            "there and pt-v10 held 13.\n\n"
            "So why is the horizon still 252? Two reasons, and the band "
            "count is neither. First, headroom -- though this reason has "
            "weakened: under pt-v12 annualised_vol_pct read 33.89 against a "
            "band ending at 34.0, only 0.11 of room on a statistic whose "
            "seed spread is many times that. pt-v16 reads 28.12 there, "
            "which is 5.88 of room, so the fourteenth row is no longer "
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
            f"Log-log slope over lags 1-20 is {DECAY_SLOPE} against real "
            f"markets' {REAL_DECAY_SLOPE}, about 2.2x steeper, and the curve "
            f"turns NEGATIVE by lag 30 where real markets remain weakly "
            f"positive to lag 60. This is a mechanism gap, not a calibration "
            f"one: the process is built from exponentials, and over one year "
            f"two of them fake a power law well enough that no panel "
            f"statistic objects. A two-component mixture was tried and is "
            f"not sufficient.\n\n"
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
            "crisis is than a calm market -- reads 6.23x on pt-v16 against "
            "real markets\' 6.16x, measured from a held VIX 5 to a held VIX "
            "65 on the certified 40-name roster over 252 days at thirty "
            "seeds. pt-v14 read 6.18x there, pt-v10 5.05x, and the default "
            "before it 3.07x. "
            "This gap opened by saying the VIX shock response was materially "
            "weaker than the previous preset\'s; on pt-v16 it is stronger "
            "than any preset before it and within two percent of real, so "
            "that sentence is WITHDRAWN.\n\n"
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
            "way, and is now CLOSED. In calm markets it is in band on "
            "the shipped preset, 0.2081 at 252 days "
            "and 0.1817 at 504 "
            "against bands starting at 0.11, so the separate "
            "sector-structure gap was retired at 0.2.0. Under a held VIX "
            "45 pt-v12 reads +0.109 against a real +0.103, and crisis "
            "co-movement reads 0.696 against a real 0.664 to 0.727.\n\n"
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
            # sentence did not move. Computed since, so neither pt-v14 nor
            # pt-v16 moving it needed an edit here.
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
                f"markets' {REAL_DECAY_SLOPE}, and the curve is negative by "
                f"lag 30 where real markets stay positive to lag 60"
            ))
        elif CERTIFIED.get(name) is not None and horizon_days <= CERTIFIED_HORIZON_DAYS:
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
            "lever from VIX 5 to VIX 65 reads 6.23x on the shipped pt-v16 "
            "against real markets' 6.16x, where pt-v14 read 6.18x. What is "
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
          horizon_days: int = CERTIFIED_HORIZON_DAYS) -> dict[str, Any]:
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
    """
    if horizon_days < 1:
        raise ValidationError(
            f"horizon_days must be positive, got {horizon_days}")
    # `loss.STRUCTURAL` names the statistics excluded from the objective by
    # design; imported here rather than at module scope because `loss`
    # imports this module's facts and a top-level import would cycle.
    from .loss import STRUCTURAL

    far = horizon_days > CERTIFIED_HORIZON_DAYS
    bands = BANDS_504 if far else REAL_MARKETS
    noise = SEED_SD_504 if far else SEED_SD

    unknown = sorted(set(panel) - set(REAL_MARKETS))
    if unknown:
        raise ValidationError(
            f"unknown statistics {unknown}; expected keys of "
            f"facts.REAL_MARKETS: {sorted(REAL_MARKETS)}")

    rows: dict[str, Any] = {}
    for name, measured in panel.items():
        low, high = bands[name]
        sd = noise.get(name)
        rows[name] = {
            "measured": measured,
            "band": (low, high),
            "distance": band_distance(measured, low, high),
            "in_band": band_distance(measured, low, high) == 0,
            "room_sd": (None if not sd
                        else min(measured - low, high - measured) / sd),
            "structural": name in STRUCTURAL,
        }
    return {
        "horizon_days": horizon_days,
        "ruler": "REAL_MARKETS_504" if far else "REAL_MARKETS",
        "statistics": rows,
        "in_band": sum(1 for r in rows.values() if r["in_band"]),
        "of": len(rows),
    }


def regressions(panel: Mapping[str, float], *,
                horizon_days: int = CERTIFIED_HORIZON_DAYS) -> list[str]:
    """Statistics the SHIPPED preset holds in band and this panel does not.

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
    theirs = score(panel, horizon_days=horizon_days)["statistics"]
    lost = []
    for name, row in theirs.items():
        # Being outside the calibration objective is not a licence to lose
        # the row. Until 0.2.0 every `structural` statistic was skipped here,
        # on the reasoning that the shipped preset did not hold them either;
        # pt-v10 holds all fourteen at the certified horizon, so that
        # reasoning expired and the skip with it. The condition below is the
        # one that always did the work: a row the shipped preset does not
        # hold in band cannot be lost by a candidate.
        low, high = REAL_MARKETS[name]
        if band_distance(CERTIFIED[name], low, high) == 0 and not row["in_band"]:
            lost.append(name)
    return sorted(lost)


def certified() -> dict[str, Any]:
    """The envelope as a plain mapping, for serialising into a manifest."""
    return {
        "preset": PRESET,
        "certified_horizon_days": CERTIFIED_HORIZON_DAYS,
        "statistics": {
            k: {
                "measured": v,
                "band": list(REAL_MARKETS[k]),
                "in_band": band_distance(v, *REAL_MARKETS[k]) == 0,
            }
            for k, v in CERTIFIED.items()
        },
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
