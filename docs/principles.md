---
title: The principles
nav_order: 26
rack: reference
short: Principles
---

# The principles

Twelve rules this project works under. Each one is argued somewhere in the
docs, justifying a local decision. Collected here with the failure that
produced it, they are the argument for why a result from this simulator is
worth anything.

Six of the twelve were bought with a specific loss. Those say so.

## 1. Determinism is the product

Anything that would make two runs differ is a correctness bug rather than a
performance trade. That is `PRODUCT.md`'s third product principle, and
it is why the determinism workflow compares known-answer digests across five
build targets on every release.

## 2. Measure rather than assert

Every claim in the documentation is a number that was produced by running
something. `tools/remeasure` re-runs the stated method behind each published
figure and reports every number the current build no longer produces.

## 3. State the limitation next to the capability

Never in a footnote. The realism page publishes fourteen statistics inside
their bands and five named gaps on the same page, and each gap ends in a rule
about what it forbids you to conclude.

## 4. A changed model has a different name

[Model presets](model-presets.md) states the rule as "a changed model has a
different name". Changing a coefficient is allowed. Reporting the result under
the shipped preset's name is not: change any settable coefficient and the
fingerprint reads `custom-7f290e34` rather than `pt-v1`.

## 5. Whatever sits outside the objective is free

[Atlas](atlas.md) states this as the reason it exists. **The failure:** six
consecutive calibration searches were rejected, each for the same underlying
reason. A scalar objective collapses everything you care about into one
number and the optimiser sells whatever is not in that number. Fixing one
blind spot moved the selling to the next one.

## 6. Discovery seeds and validation seeds must be disjoint

`atlas.Survey.confirm` re-measures on seed blocks disjoint from the survey's
and refuses to run on overlapping seeds. **The failure:** a candidate was
declared shippable on a 13% improvement. Measured on fresh seeds it read
+0.1297 where it was found, and -0.0315, +0.0209 and +0.0233 elsewhere,
reversing sign once. Discovery and validation had used the same thirty seeds,
so re-measuring reproduced the same fluctuation exactly.

## 7. The ruler must match the horizon

A statistic measured over 504 days is scored against bands re-derived at 504
days, not against the 252-day bands. `loss.dual_horizon_loss` scores `L_real`
at both horizons and refuses to run on one, and
[the realism metrics](realism-metrics.md) carries the reasoning.

## 8. A number invites scepticism and a sentence does not

[Atlas](atlas.md) states this where it explains why a summary says outright
that an additivity check failed rather than quietly presenting an unchecked
number. [The realism metrics](realism-metrics.md) states the same thing about
scores: a scalar travels and a caveat does not, which is why the library
publishes fourteen statistics with bands rather than one realism score.

## 9. One seed is not an answer

[Agents and evaluation](agents-and-evaluation.md) measures the cost of
believing one: on the twelve-market grid a single seed picks the pooled
leader eight times in twelve, and what a seed says momentum is worth runs
from -0.503 to +0.909 depending only on which market it drew. The paired sign
test is reported beside the pooled number for this reason.

## 10. Strategies are data, never code

A `StrategySpec` is declarative, versioned and hashable, which is what lets
one travel through [the MCP server](mcp.md) without executing anything a
caller sent.

## 11. Absence differs from zero, and invalid input raises

[Conventions](conventions.md): `corporate_bond_yield=None` falls through to
the default and where a column cannot carry `None`, absence is `NaN`, because
zero is a real rate. Nothing is silently clamped, because a simulator that
repairs your inputs gives you a market you did not specify.

## 12. The reader's job leads

Both audiences reach their own path quickly. That is why the docs are racked
by task rather than by module, and why [the two loops](two-loops.md) exists:
a reader who cannot tell which loop they are in cannot tell which pages are
theirs.

## The one that was wrong twice

`volume_change_acf1` is the autocorrelation of day-to-day volume changes, and
the realism page called it structurally unreachable twice.

The first claim was that reaching its band costs `volume_abs_return_corr`,
because the engine's common log-volume state adds volume variance unrelated
to any name's own moves. That trade was real, and it was priced on the pt-v3
era base. On the pt-v10 base both one-year bands became reachable together, in
a window about 0.03 wide in the innovation sigma.

The second claim was that the two-year half needed more volume memory. What
closed it was `volume_move_cap`, a hard-coded literal 4.0 in `tick.rs` that
saturated a name's volume response at a 4% daily move. Lifting it to 12.0
reads -0.2656 at 252 days and -0.2572 at 504, inside both bands.

Every number in both claims was right. The mechanism each asserted was
wrong, and the second was wrong in the same direction as the first: it
described the limit as a property of the model rather than of the value one
constant happened to hold.
