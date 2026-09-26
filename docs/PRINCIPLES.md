# Engineering principles

tradefloor is built under the twelve rules below. Where code or a test
enforces a rule, its section names it. Two of the rules were written after a
specific failure, and their sections say what went wrong.

## Determinism

Anything that makes two runs with the same inputs differ is a bug.

Every `exp`, `log`, `pow`, `sin` and `cos` in the engine goes through
`rust/src/mathx.rs`, which calls the pure-Rust `libm` crate. The math
libraries on Windows, Linux and macOS don't return identical bits, and a
difference in the last bit grows into a visibly different market within a
simulated year. A test in `rust/tests/mathx_parity.rs` fails if the standard
library's versions appear anywhere else in the source.

For every tagged release, CI builds the wheel on five targets (Linux on
x86-64 and ARM, macOS on ARM and Intel, Windows on x86-64), runs one fixed
simulation on each and fails the release check if any two digests differ.
The expected digests are committed in `tests/known_answer.json`.

## Measured claims

Every claim in the documentation is a number that came from running
something. `tools/remeasure/remeasure.py` re-runs the method behind each
figure it tracks and reports any figure the current build no longer
reproduces.

The same rule applies to what the project says about itself. It has no
testimonials, customer list, competitor benchmarks, pricing or adoption
figures, and the docs don't invent any.

## Limits next to capabilities

A limitation is stated next to the capability it limits, in the main text.
The README lists five limits beside what the simulator does, and each of the
five gaps in `tf.envelope.GAPS` says which conclusions it rules out.
`tf.envelope.check()` tests a planned study against them:

```python
import tradefloor as tf

verdict = tf.envelope.check(horizon_days=504)
print(verdict.inside, [gap.id for gap in verdict.gaps])
print(verdict.gaps[0].forbids)
```

```
False ['horizon']
multi-year backtests, and anything keyed on volatility dynamics beyond one year
```

## A new name for a changed model

A preset is a named, frozen set of coefficients. You can change any settable
coefficient, but the result then can't be reported under the preset's name.
The fingerprint hashes every coefficient, so a vector that matches a shipped
preset reports that preset's name and anything else reports a custom one:

```python
import tradefloor as tf

params = tf.ModelParams.from_preset("pt-v1").to_dict()
print(tf.ModelParams.from_dict(params).fingerprint)

params["garch_alpha"] = 0.12
print(tf.ModelParams.from_dict(params).fingerprint)
```

```
pt-v1
custom-57d34290
```

A published preset never changes, because changing it would alter results
that other people have already cited. A better coefficient ships as a new
preset with a new name. Every shipped preset can still be selected by name,
and each has its own known-answer digest in `tests/known_answer_presets.json`,
fixed when it first shipped and checked on every release.

## No single objective

A single-number objective squeezes everything you care about into one score,
and an optimizer will make anything outside the score worse whenever that
raises the score. `tradefloor.atlas` surveys the parameter space and reports
every statistic at each point it samples, with no score to optimize.

This rule came from six calibration searches in a row that were rejected for
that reason. Each fix closed one blind spot, and the next search then traded
away a statistic the objective couldn't see.

## Validation on unseen seeds

A result found on one set of seeds (the numbers that fix a run's random
draws) has to be measured again on seeds it never saw.
`atlas.Survey.confirm` does this, and refuses to run if a confirmation seed
was used by the survey or appears in two blocks.

A candidate was once declared ready to ship on a 13% improvement, a gap of
+0.1297 on the seeds where it was found. On three fresh blocks of seeds the
same gap read -0.0315, +0.0209 and +0.0233, so its sign reversed once.
Because the search and its check had used the same thirty seeds, the check
had reproduced the same random fluctuation exactly.

## Matching horizons

A statistic measured over 504 trading days is scored against bands derived
at 504 days, and a one-year statistic against one-year bands.
`tradefloor.loss.dual_horizon_loss` scores both horizons, each against its
own bands and its own seed noise, and raises an error if either is missing.

## Numbers with their caveats

A single number gets quoted on its own, and its caveats get left behind. So
the library publishes no overall realism score. Each statistic comes with
its real-market band, and the gaps are listed next to them.

Explanations carry their caveats too. When `Survey.attribution` says why one
parameter vector beats another, its summary prints how much the additivity
assumption missed, or says the assumption is unchecked when there is nothing
to check it against.

## More than one market

One market is one sample, and the winner on one seed can be luck. On pt-v20,
over twelve ten-day markets on the same 30 companies, mean reversion beat
momentum on 7 and momentum won the other 5. Each market on its own names a
winner, but the paired sign test over all twelve gives p = 0.77, which
doesn't separate the two. `tf.rank` runs agents across many seeds, and its
`separation` method gives that paired sign test for any two of them.

## Strategies as data

A `StrategySpec` is declarative, versioned and hashable. It round-trips
through JSON, and its fingerprint can be cited beside the seed and the
universe. The MCP server accepts strategies only in this form, so it never
runs code a caller sent. A strategy the spec can't express, such as one with
a stop loss, needs a Python agent and the library.

## Missing and invalid input

tradefloor doesn't repair invalid input. It raises an error, because a
repaired input gives you a market you didn't ask for. Rates are decimals, so
`federal_funds_rate=5.2` raises a `ValidationError` that says it looks like a
percent.

A missing value is `None`. `corporate_bond_yield=None` falls back to the
policy rate, and 0.0 is used as a real yield. A table column can't hold
`None`, so a missing value there is NaN, because zero is a real value for a
return or a market maker's inventory.

## Docs organized by task

tradefloor is used for two kinds of work. Calibrating the market model
against real data is one, and it happens in this repository. Testing a
strategy or training an agent against a market that already exists is the
other, and most readers only need that one. The docs at
<https://docs.tradefloor.dev> are written for the second kind of work and
ordered by task, starting with a first run. The model, its statistics and
these rules are in `docs/MODEL.md`, `docs/STATISTICS.md` and this file.

Results from testing strategies never feed back into calibration. If the
market were tuned on a strategy's results, it would build that strategy's
edge into the market that then measures it.

## A limit misdiagnosed twice

The realism envelope twice said that `volume_change_acf1`, the
autocorrelation of day-to-day changes in volume, could not reach its band
because of how the model is built. Both claims quoted correct numbers, and
both blamed the model's design for a limit that parameter values caused.

The first claim was that bringing it into its band would push
`volume_abs_return_corr` out of its own, because the shared log-volume state
adds volume variance unrelated to any stock's own moves. That trade-off was
real on the pt-v3 base. On pt-v10 both statistics sat inside their one-year
bands at once.

The second claim was that the two-year reading needed more volume memory.
The cause turned out to be a literal 4.0 in the tick engine, which capped a
stock's volume response at a 4% daily move. It became the setting
`volume_move_cap` in 0.3.0, and pt-v12 raised it to 12.0. That brought the
statistic inside its band at one year and at two.
