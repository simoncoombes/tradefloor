---
title: Scenario recipes
nav_order: 8.2
rack: experiment
short: Scenario recipes
---

# Scenario recipes

`pretium` has no `.financial_crisis()`, no `.pandemic()`, no `.war()`, and it
is not going to grow one. The reason is recorded in the design decisions and
is worth restating here, because it is also the reason this page exists: a
named event has to encode somebody's opinion about what that event does to
markets, and once encoded the opinion becomes invisible. Run `.war()`,
publish the output, and you have published the library author's assumption as
if it were the model's finding.

So the library scripts macro paths and leaves the naming to you. That is the
right split, but on its own it is not much help to somebody who actually
wants to know how their strategy behaves through something 2008-shaped. This
page is the other half: worked configurations for recognisable macro
episodes, written so that the assumption stays on the page where you can
argue with it.

Every recipe below carries four things, and the order matters:

1. **The config**, runnable and run.
2. **What it is anchored on**, meaning the historical figures behind each number,
   with sources, and an explicit list of the numbers that are *not* anchored
   on anything.
3. **What it does not claim.** A recipe is a macro path. It is not a theory of
   the episode it is named after, and it is certainly not evidence about one.
4. **What it measures in this model**, with the method, so you can see what
   the configuration actually produces here rather than what it ought to.

Copy a recipe, change the numbers you disagree with, and the disagreement is
now visible in your methods section instead of buried in a verb.

## Before you compose anything: one trap and three rules

Exactly one thing on this page can still hand you a plausible wrong answer
without saying a word, and it is the first item below. The other three used to
behave that way too; the `Scenario` surface now refuses each of them by name
and says what to write instead, so they have stopped being traps and become
rules. Read the rules anyway, because they are what makes a composed timeline
expressible at all, and every recipe below is shaped or sized by one of them.

### The trap: a policy-only rate path does nothing before day 45

A policy-only rate path moves nothing until the first central-bank meeting,
because the corporate bond yield, the rate equities actually discount off,
is only recomputed at meetings, and the first one is scheduled 45 days out.
Then it transmits, hard.

This is the one conflict on this page that no library check can catch, which
is why it leads. The scenario is well-formed, the run is well-formed, and the
answer is a confident zero that is arithmetically correct and analytically
worthless. `compare()` refuses a shock that begins *after* the horizon ends
(rule 3), but a shock that begins on day 5 of a 40-day run is inside the
horizon by every test the library can apply. It simply has nothing to transmit
through yet.

This is documented in full on [Scenarios](scenarios.html), with the
measurement: a policy-rate `ramp` from 2.5% to 5% over thirty days moves every
price by exactly 0.00% over 40 days, and a median −4.29% once the run is long
enough to cross day 45. It is not restated here; go and read it, because
every rate-bearing recipe below is sized around it.

**Read the heading precisely.** It says a *policy-only rate path*, and the
qualifier is the whole content. Most of the macro surface transmits the day you
move it: a VIX shock on day 5 has moved prices by a median 39% at day 25, and
`qe_pe_boost` and `corporate_bond_yield` both act immediately. Only
`federal_funds_rate` and `inflation_rate` wait for a meeting, because both work
by steering the corporate bond yield and nothing else. The per-field table is
on [Scenarios](scenarios.html).

The consequence for this page is a rule: **run rate and inflation recipes for
at least 90 days.** A 30-day study of a hiking cycle measures nothing and
says so silently. Recipe 2 shows the same trap arriving through inflation
rather than the policy rate, which is worth seeing because it is not the case
the existing documentation warns about.

### Rule 1: pins on one field layer as consecutive segments

A `Scenario` holds a *list* of pins per field, and they lie end to end:

- each pin owns from its own start day until the next one begins;
- the last pin owns the rest of the run, however long the run turns out to be;
- the **first** pin also owns every day before its own start, which is what
  keeps a lone `ramp` or `step` defined on day zero.

```python
import pretium as pt
from pretium import Scenario

# calm at 15, a jump to 48 on day 60, a plateau, then a decay to 22.
segments = (Scenario("spike, plateau, decay")
            .step("vix", before=15.0, after=48.0, at=60)
            .ramp("vix", start=48.0, end=22.0, over=45, begin=75))

for d in (0, 59, 60, 74, 75, 97, 120):
    print(f"day {d:3d}: {segments.at(d)['vix']:.1f}")
```

```
day   0: 15.0
day  59: 15.0
day  60: 48.0
day  74: 48.0
day  75: 48.0
day  97: 35.3
day 120: 22.0
```

All three clauses are visible there. The `step` is the first pin, so its
`before=15.0` covers days 0 to 59. It owns days 60 to 74 as well, the
plateau, because that is where the next pin begins. And the `ramp` does **not** hold
its `start` value over the days before day 75, the way a lone ramp would:
those days already belong to the step. A ramp's pre-`begin` hold is the
first-pin clause, not a property of ramps.

That last point is the one worth internalising, because it is what turns
`hold` then `ramp` into a jump followed by a decay, which is the shape most
macro episodes actually have.

**Start days must strictly increase within a field.** The two other orderings
are refused by name, with both calls reconstructed as you wrote them, because
"two pins on 'vix'" is not something anybody can act on. Two pins beginning on
the same day:

```python
import textwrap

try:
    (Scenario("looks right, is not")
     .step("vix", before=15.0, after=48.0, at=60)
     .ramp("vix", start=48.0, end=18.0, over=40, begin=60))
except pt.ValidationError as exc:
    print(textwrap.fill(str(exc), 76))
```

```
two pins on 'vix' both begin on day 60: step('vix', before=15.0, after=48.0)
at day 60 and ramp('vix', start=48.0, end=18.0, over=40) from day 60. Pins
on one field layer as consecutive segments, so the first one's values from
day 60 onward could never be reached -- one of these two calls would do
nothing, silently. Say the level and the episode separately: .hold(vix=<the
level before day 60>) then .ramp('vix', start=..., end=..., over=...,
begin=60) -- a ramp starts AT its start value, so that is a jump on day 60
and then the path.
```

Both calls claim day 60, so whichever was written first can never be read from
day 60 onward: one of those two lines is dead code its author believes is
running. The refusal's own advice is the form to reach for, and it is worth
running:

```python
spike = (Scenario("spike and decay")
         .hold(vix=15.0)
         .ramp("vix", start=48.0, end=18.0, over=40, begin=60))

print([f"day {d}: {spike.at(d)['vix']:.1f}" for d in (0, 30, 59, 60, 80, 100)])
```

```
['day 0: 15.0', 'day 30: 15.0', 'day 59: 15.0', 'day 60: 48.0', 'day 80: 33.0', 'day 100: 18.0']
```

Calm until day 60, a jump to 48, a decay reaching 18 on day 100, which is the
path the two-pins-on-day-60 version was reaching for. The `hold` states the
level and the `ramp` states the episode, each exactly once. Moving the ramp to
`begin=61` composes as well and is a legitimate answer, but it buys the
composition with a path one day out of step with the arithmetic you wrote;
saying the level separately is the form that means what it says.

The other refused ordering is a pin declared *after* one that begins later,
as in `ramp(..., begin=75)` and then `step(..., at=60)`. That message says the pins
`are out of order`, that they `must be declared in the order they happen`, and
names the swap. Declared that way round the earlier pin would have to back-fill
days the later one already owns, which is a whole run under the wrong path.

**And check before you run.** `Scenario.table(days)` exists for this, and one
glance at day zero is still the cheapest check on this page. It will not catch
an ordering mistake any more, since those raise, but it catches a `begin` off by a
factor of ten, or a level you meant to change and did not:

```python
for row in spike.table(5):
    print(row)
```

```
{'day': 0, 'vix': 15.0}
{'day': 1, 'vix': 15.0}
{'day': 2, 'vix': 15.0}
{'day': 3, 'vix': 15.0}
{'day': 4, 'vix': 15.0}
```

### Rule 2: the shape constructors build a whole scenario

`Scenario.rate_shock`, `Scenario.vix_shock`, `Scenario.vol_shock` and
`Scenario.from_json` are constructors. Each returns a complete scenario, and
each looks perfectly chainable, so `Scenario().hold(...).vix_shock(...)` reads
like it adds a VIX leg to what you already had. It does not: the receiver
would be discarded and you would get back a scenario driving only `vix`. That
call is refused, naming the fields the receiver was driving and giving the
composing form.

Two forms compose. Build the shape **first** and chain the extra fields onto
it, which is safe because they are different fields:

```python
right = (Scenario.vix_shock(calm=15.0, peak=60.0, at=10, over=30)
         .hold(inflation_rate=0.06, fear_greed_index=20.0))
print(right.fields)
```

```
('fear_greed_index', 'inflation_rate', 'vix')
```

Or write the shape out, which is what rule 1 makes possible: `vix_shock` is a
`hold` at the calm level followed by a `ramp` back down to it, and a ramp
starting at its `start` value is the jump.

```python
same = (Scenario()
        .hold(inflation_rate=0.06, fear_greed_index=20.0)
        .hold(vix=15.0)
        .ramp("vix", start=60.0, end=15.0, over=30, begin=10))
print(same.table(120) == right.table(120))
```

```
True
```

The constructors are convenience rather than capability. `rate_shock` is two
ramps held apart by `credit_spread`; `vix_shock` is the two lines above. Use
them where they say what you mean, and write the segments out where they do
not.

### Rule 3: `compare()` needs a scenario that moves

`compare()` defaults to `baseline=Scenario().hold(**scenario.at(0))`, the
scenario's own day-zero values held flat. That is the right default for a
*path*, because it isolates the movement from the level it started at. For a
scenario that never moves inside the horizon it *is* the scenario, and every
instrument comes back at exactly 0.00% by construction rather than by
measurement. A clean, confident, entirely meaningless zero, which reads as
"the shock did nothing" rather than as "no shock was applied". It is refused
rather than reported.

Three shapes reach that refusal and your next move differs:

- a `hold`-only scenario, or a `step(field, ..., at=0)` whose day-zero value
  is already the *after* value: there is no path to isolate, only a level, so
  name the world **without** it and pass that as `baseline=`;
- a shock whose start day falls at or after `days`: the path is real, but the
  run ends before it begins. Run it longer, and the message names the day the
  shock starts and the run length it needs;
- a scenario driving nothing at all.

An explicit baseline that realises the same path as the scenario is refused on
the same grounds: two runs of one world, differenced, is a zero either way.

```python
from pretium.scenario import compare

u = pt.Universe.random(20, seed=4)
held = Scenario("held crisis").hold(vix=45.0)

r = compare(held, seed=5, universe=u, days=30,
            baseline=Scenario("calm").hold(vix=15.0))
print(f"explicit baseline: median {r['median_pct']:+.2f}%")
```

```
explicit baseline: median +29.72%
```

**Whenever a recipe pins levels rather than paths, pass `baseline=`
explicitly.** Recipe 4 does, and says so at the point of use.

There is a second lesson in that +29.72%, and it is not that a crisis makes
prices go up. Run the same comparison across sim seeds 1 to 8 and the median
move spans **−26.56% to +29.72%, negative on 2 of the 8**. A price delta on a
single seed is simply the wrong instrument for a volatility scenario: what a
VIX pin changes is the *variance* of the shared market factor, and one seed's
realisation of a higher variance is a coin flip with a wide edge. Measure
volatility scenarios with `pt.facts.measure`, as recipe 3 does. Measure rate
and credit scenarios, which move fair value directionally, with
`compare()`.

## How the measured effects below were produced

Every "in this model" figure on this page was measured on engine commit
`9b485a0`, pretium 0.1.0, under model preset `pt-v1`, which was the default
at the time and is not any more. The shipped default is now `pt-v3`, so
these figures describe an earlier era. Every one of them is an inventory row
in the re-measurement harness under the group `recipes`:

```
.venv/bin/python tools/remeasure/remeasure.py --only recipes
```

That is deliberate, because the two halves of this page age at completely
different rates. The configs and their historical anchors are durable. The
Federal Reserve raised its target range eleven times between March 2022 and
July 2023 regardless of what this engine does next week. The measured effects
are this build's answer, and a calibration change moves all of them. If you
are reading a number below and the harness reports it `MOVED`, trust the
harness.

Two conventions, both borrowed from [Scenarios](scenarios.html) so that one
set of habits covers both pages:

- **price effects** use `Universe.random(20, seed=4)` at sim seed 5, via
  `compare()`, reported as the median across the twenty instruments;
- **volatility effects** use `Universe.random(20, seed=11)` at sim seed 3,
  via `pt.facts.measure` over 120 days.

Single seeds, stated. Nothing on this page is a distribution, and the
dispersion across seeds is generally larger than the effects.

## Recipe 1: A rate-hiking cycle

```python
import pretium as pt
from pretium import Scenario
from pretium.scenario import compare

hiking = Scenario.rate_shock(start=0.00125, end=0.0538, over=90,
                             credit_spread=0.02)

u = pt.Universe.random(20, seed=4)
r = compare(hiking, seed=5, universe=u, days=120)
print(f"median {r['median_pct']:+.2f}%  worst {r['worst_pct']:+.2f}%  "
      f"best {r['best_pct']:+.2f}%  exact={r['exact']}")
```

```
median -9.26%  worst -13.21%  best +0.26%  exact=True
```

### What it is anchored on

The 2022-23 US tightening cycle. The Federal Reserve raised its target range
from 0-0.25% to 5.25-5.50% across eleven increases between 17 March 2022 and
27 July 2023, including four consecutive 75bp moves in June, July, September
and November 2022, the fastest stretch of the cycle. The start and end
values here are the midpoints of the first and last ranges: 0.125% and 5.375%,
which is where `start=0.00125` and `end=0.0538` come from. Both are read
directly off the Fed's own record of open market operations.

`over=90` is **not** anchored. The real cycle ran roughly sixteen months,
about 340 trading sessions; ninety compresses it to a bit over a quarter of
that, which makes this a considerably more violent cycle than the one that
happened. That was a deliberate trade for a runnable horizon, and it is the
single number in this recipe most worth changing. If you want the real pace,
use `over=340` and run 400 days.

`credit_spread=0.02` is the constructor's default and is **not** anchored to
2022-23. It holds the corporate yield exactly 200bp above the policy rate for
the whole path, which means this recipe has spreads *constant* through a
tightening cycle. Real spreads generally widen into one. The effect below is
therefore an understatement, and the `rate_shock` docstring says so too.

### What it does not claim

This is not what the 2022-23 hiking cycle did, and running it does not
produce evidence about that cycle. It is one defensible way to represent a
tightening of that magnitude: a linear walk, at four times the real pace, with
a frozen credit spread. Each of those three is a choice, and a reader who
disagrees with any of them is disagreeing with me rather than with the model.
In particular the linearity is wrong in a specific and knowable way, since
the real cycle front-loaded its 75bp moves and decelerated to 25bp, and recipe 5 shows
how to express a path with genuine structure if that matters to you.

### What it measures in this model

The median instrument reprices **−9.26%** over 120 days, the most rate-sensitive
name **−13.21%**, and the least sensitive one **+0.26%**, essentially unmoved.
That dispersion is the point of running a cross-section at all: a scenario that
moved every name identically would tell a stock-picking strategy nothing.

`exact=True` means the two worlds consumed identical market-noise draws, so the
difference is the macro path and nothing else.

Note the horizon. At 120 days the run crosses two central-bank meetings; the
same config measured at 40 days would report roughly nothing, for the reason
in the meeting trap.

## Recipe 2: An inflation shock

```python
import pretium as pt
from pretium import Scenario
from pretium.scenario import compare

inflation = Scenario("inflation shock").ramp(
    "inflation_rate", start=0.014, end=0.091, over=100, begin=5)

u = pt.Universe.random(20, seed=4)
early = compare(inflation, seed=5, universe=u, days=40)
late = compare(inflation, seed=5, universe=u, days=120)
print(f"40 days:  largest absolute move {max(abs(x) for x in early['move_pct']):.2f}%")
print(f"120 days: median {late['median_pct']:+.2f}%")
```

```
40 days:  largest absolute move 0.26%
120 days: median -10.01%
```

### What it is anchored on

The 2021-22 US inflation episode. CPI-U rose 1.4% over the twelve months to
January 2021 and peaked at 9.1% over the twelve months to June 2022, the
largest twelve-month increase since the period ending November 1981, per the
Bureau of Labor Statistics. `start=0.014` and `end=0.091` are those two
prints.

`over=100` compresses roughly seventeen months into a hundred sessions, the
same kind of compression as recipe 1 and the same caveat. `begin=5` just
leaves a few calm days at the front so `compare()`'s default baseline has a
sensible level to hold; it carries no meaning.

### What it does not claim

It does not claim that inflation rose linearly, which it did not. The path
from 1.4% to 9.1% had a visible acceleration through late 2021 and a plateau
either side of the peak. It does not claim that this is what an inflation
shock does to equities. And it very specifically does not model the thing that
actually transmitted inflation to equity prices in 2022, which was the policy
response; here the engine's own reaction function decides that, and the
engine's reaction function is not calibrated to the FOMC.

### What it measures in this model

This recipe exists mainly to show that **the meeting trap is not about the
policy rate.** `inflation_rate` never reaches fair value directly. The valuation
takes earnings, growth, the corporate bond yield, the policy rate and the QE
multiple adjustment, and inflation is not among them. Inflation transmits only
by steering the macro chain into a central-bank reaction, and that reaction
lands at meetings.

So the largest absolute price move anywhere in the cross-section after **40
days is 0.26%**, against a median of **−10.01%** at 120 days. That is not
quite the exact 0.00% the policy-rate trap produces, so an inflation study
that stops early sees a *small* effect rather than a literally absent one,
which is arguably worse: a small effect looks like a finding. It is a
thirty-eight-fold understatement of where the path ends up.

## Recipe 3: A liquidity crisis

```python
import pretium as pt
from pretium import Scenario

crisis = (Scenario.vix_shock(calm=18.0, peak=80.0, at=20, over=60)
          .ramp("corporate_bond_yield", start=0.055, end=0.095,
                over=40, begin=20))

u = pt.Universe.random(20, seed=11)
shocked = pt.facts.measure(seed=3, universe=u, days=120, scenario=crisis)
calm = pt.facts.measure(seed=3, universe=u, days=120,
                        scenario=Scenario("calm").hold(vix=18.0))
print(f"annualised vol: {calm['annualised_vol_pct']:.2f}% calm -> "
      f"{shocked['annualised_vol_pct']:.2f}% crisis")
print(f"cross-sectional corr: {calm['cross_sectional_corr']:.3f} -> "
      f"{shocked['cross_sectional_corr']:.3f}")
```

```
annualised vol: 61.76% calm -> 82.16% crisis
cross-sectional corr: 0.493 -> 0.636
```

Note the shape of the construction. `vix_shock` says "a spike that subsides"
in one line, which is the only reason it is here: by rule 2 it is exactly
`.hold(vix=18.0).ramp("vix", start=80.0, end=18.0, over=60, begin=20)`, and
writing that instead changes nothing about the run. It has to come **first**,
though, because it is a constructor. The credit leg chains on after it, safely,
because `corporate_bond_yield` is a different field.

### What it is anchored on

2008. The VIX reached an intraday high of **89.53 on 24 October 2008** and its
highest close of the crisis, **80.86, on 20 November 2008**; `peak=80.0` is
that closing high, lightly rounded. Credit is anchored on option-adjusted
spreads, which both set records in **December 2008**: the ICE BofA US
Corporate (investment grade) index at **6.56%** and the US High Yield index at
**21.82%**.

`calm=18.0` is a judgement call rather than a measurement: a plausible
pre-crisis level rather than the engine's endogenous mean of 15.

The corporate yield leg is the weakest-sourced number here and I want to be
precise about why. **I could not retrieve a corporate bond *yield* series.**
FRED's series pages and CSV endpoints were unreachable from this environment,
and searches returned the series' landing pages without values. So
`start=0.055` and `end=0.095` are not read off anything. They are constructed:
an investment-grade spread moving toward its 6.56% record over a Treasury base
of roughly 3%, which is the right order for 2008 but is *my arithmetic on one
sourced spread and one assumed base*, not an observation. Treat those two
numbers as the most arguable on this page.

`at=20` and `over=60` are unanchored pacing choices.

### What it does not claim

It does not claim this is what 2008 was. 2008 was a solvency crisis with a
funding run inside it, and this configuration contains neither. It is a
volatility path and a discount-rate path, which is a *shape*, not a mechanism.
Nothing here models a bank failing, a money-market fund breaking the buck, or
a counterparty vanishing. It does not claim the VIX peak and the spread peak
coincided; they were about a month apart, and this recipe overlaps them
because separating them by a month on a 120-day horizon is a decision I did
not have grounds to make precisely.

Most importantly: reproducing a volatility level is not reproducing a crisis.

### What it measures in this model

Annualised realised volatility rises from **61.76% to 82.16%**, an uplift of
**20.40 percentage points**, and mean pairwise correlation of daily log returns
rises from **0.493 to 0.636**. The second number is the one worth having.
Above VIX 25.5 the model blends idiosyncratic sector factors toward the shared
market factor, so a crisis VIX is a correlation regime as well as a volatility
regime, and diversification measurably stops working, which is what a real
crisis does to a portfolio.

The response saturates: the factor's variance is clamped at 8× its baseline,
so pushing `peak` from 80 to 120 buys almost nothing. Recipe 3 sits near the
top of the usable range already.

Measured on price instead, the same config reports a median **−8.29%** and a
worst name at **−13.07%** on sim seed 5, but per the seed band under rule 3,
do not lean on that. The price consequence of a volatility regime is seed-dependent in
sign; the volatility and correlation numbers are the claim.

## Recipe 4: A contraction regime

```python
import pretium as pt
from pretium import Scenario
from pretium.scenario import compare

contraction = (Scenario("contraction")
               .hold(cycle="contraction")
               .step("fear_greed_index", before=50.0, after=25.0, at=5))

# The baseline MUST be explicit here -- see rule 3. `compare`'s default would
# hold this scenario's own day-zero values, which for a scenario that is only
# a level is the scenario itself, so calling it without a baseline raises
# rather than reporting the zero it would otherwise measure.
baseline = (Scenario("expansion baseline")
            .hold(cycle="expansion")
            .hold(fear_greed_index=50.0))

u = pt.Universe.random(20, seed=4)
r = compare(contraction, seed=5, universe=u, days=120, baseline=baseline)
print(f"median {r['median_pct']:+.2f}%  exact={r['exact']}")
```

```
median +2.85%  exact=True
```

### What it is anchored on

The duration and shape of US contractions as dated by the NBER: the 2007-09
recession ran December 2007 to June 2009 (eighteen months, the longest of the
post-war period), the 2001 recession March to November 2001, and the 2020
recession February to April 2020, which at two months is the shortest on
record.

A 120-day horizon at 252 sessions a year is about six months, which sits
between the 2020 and 2001 durations and well short of 2007-09. `cycle` is
held for the whole run rather than entered and exited, which is a
simplification: the recipe represents being *inside* a contraction, not a
cycle turning.

The `fear_greed_index` step from 50 to 25 is **not anchored on anything**. It
is a plausible sentiment reading, chosen because 50 is the field's neutral
value and 25 is clearly fearful. There is a widely-quoted index of this name
whose historical values I did not attempt to source, and this number should
not be read as referring to it.

### What it does not claim

It does not claim to be a recession. Neither `cycle` nor `fear_greed_index`
reaches fair value directly, because the valuation function does not take
either, so this recipe cannot and does not model falling earnings, which is the main
thing a contraction does to equities. What it models is a contraction's effect
on the *macro chain*, and nothing else.

If you want a contraction that damages earnings, you have to move the thing
that damages them, and the macro surface will not do it for you. That is a
real limit of this scenario surface and not a defect of the recipe.

### What it measures in this model

The headline is **+2.85%**, and a recipe that reports a contraction *raising*
prices needs its mechanism on the page rather than a shrug. Reading the
recorded macro at day 119 in both worlds:

| | policy rate | corporate yield | inflation |
|---|---|---|---|
| contraction | 2.00% | 6.41% | 1.00% |
| expansion | 2.75% | 4.91% | 3.09% |

Both channels are live and they pull against each other. The contraction
world's central bank **eases**, 2.00% against the expansion world's 2.75%,
and inflation falls to 1.00% against 3.09%, both of which support valuations.
Against that, the contraction widens the credit spread hard: the corporate
yield ends 150bp *higher* at 6.41%, and that is the rate equities discount
off.

The net on this seed is a small positive. Do not read a sign off one seed.
The mechanism table is the durable finding here; the +2.85% is close to noise
against the seed dispersion, and I did not measure a seed band for it.

## Recipe 5: A compound episode

This is the one where the surface earns itself. Four fields move on four
different schedules, the policy rate takes three levels of its own, and the
credit leg blows out and then retraces. Rule 1 means all of that is
chainable, ten pins in start-day order and four fields deep, and the
equivalence is demonstrated below rather than asserted.

The path is nevertheless built as data and loaded through
`Scenario.from_json`, for a reason that has nothing to do with what chaining
can express. Every leg here is arithmetic: interpolations between dated
levels, written once as a function you can read, change and diff. Ten
chained calls say the same thing with the arithmetic spread across their
arguments. And `from_json` reads whatever produced the numbers, so the same
recipe works when the path comes out of a spreadsheet instead of a loop:

```python
import json
import pretium as pt
from pretium import Scenario
from pretium.scenario import compare


def pandemic_shape(days=120):
    rows = []
    for d in range(days):
        if d < 15:                      # calm
            vix = 15.0
        elif d < 60:                    # spike to the record close, decaying
            vix = 82.0 + (18.0 - 82.0) * (d - 15) / 45
        else:
            vix = 18.0

        if d < 18:                      # policy: two cuts, ten days apart
            ff = 0.0155
        elif d < 28:
            ff = 0.01125
        else:
            ff = 0.00125

        if d < 18:                      # credit: blow-out, then facilities
            corp = 0.036
        elif d < 40:
            corp = 0.036 + (0.105 - 0.036) * (d - 18) / 22
        elif d < 90:
            corp = 0.105 + (0.045 - 0.105) * (d - 40) / 50
        else:
            corp = 0.045

        qe = 0.0 if d < 40 else min(0.10, 0.10 * (d - 40) / 30)
        rows.append({"day": d, "vix": vix, "federal_funds_rate": ff,
                     "corporate_bond_yield": corp, "qe_pe_boost": qe})

    return Scenario.from_json(json.dumps(
        {"schema": 1, "label": "compound: pandemic shape",
         "days": days, "path": rows}))


compound = pandemic_shape()
u = pt.Universe.random(20, seed=4)
r = compare(compound, seed=5, universe=u, days=120)
print(f"median {r['median_pct']:+.2f}%  worst {r['worst_pct']:+.2f}%  "
      f"best {r['best_pct']:+.2f}%  exact={r['exact']}")
```

```
median +9.79%  worst -0.67%  best +12.04%  exact=True
```

`from_json` holds each field's final value beyond the recorded horizon, so a
longer run is defined rather than an `IndexError`, so `pandemic_shape(120)`
queried at day 500 still returns the day-119 policy rate. It is worth checking
the path before running it, as always:

```python
for d in (0, 17, 30, 119, 500):
    print(d, {k: round(v, 5) for k, v in compound.at(d).items()})
```

```
0 {'corporate_bond_yield': 0.036, 'federal_funds_rate': 0.0155, 'qe_pe_boost': 0.0, 'vix': 15.0}
17 {'corporate_bond_yield': 0.036, 'federal_funds_rate': 0.0155, 'qe_pe_boost': 0.0, 'vix': 79.15556}
30 {'corporate_bond_yield': 0.07364, 'federal_funds_rate': 0.00125, 'qe_pe_boost': 0.0, 'vix': 60.66667}
119 {'corporate_bond_yield': 0.045, 'federal_funds_rate': 0.00125, 'qe_pe_boost': 0.1, 'vix': 18.0}
500 {'corporate_bond_yield': 0.045, 'federal_funds_rate': 0.00125, 'qe_pe_boost': 0.1, 'vix': 18.0}
```

### The same path, chained

Rule 1 is easiest to believe on something this size, so here is the whole
episode written as segments. Ten pins, four fields, each field's pins in
start-day order, and a `hold` in front of every field that does not begin
moving on day zero:

```python
chained = (Scenario("compound: chained")
           .hold(vix=15.0)
           .ramp("vix", start=82.0, end=18.0, over=45, begin=15)
           .hold(federal_funds_rate=0.0155)
           .step("federal_funds_rate", before=0.0155, after=0.01125, at=18)
           .step("federal_funds_rate", before=0.01125, after=0.00125, at=28)
           .hold(corporate_bond_yield=0.036)
           .ramp("corporate_bond_yield", start=0.036, end=0.105, over=22, begin=18)
           .ramp("corporate_bond_yield", start=0.105, end=0.045, over=50, begin=40)
           .hold(qe_pe_boost=0.0)
           .ramp("qe_pe_boost", start=0.0, end=0.10, over=30, begin=40))

print(chained.table(120) == compound.table(120))
```

```
True
```

Identical on all 120 days, so the two spellings are the same scenario and
either would produce the figures below. Every `before=` on those two `step`
calls is inert, because the pin ahead of each one already owns the days it
names. That is the segment rule stated in the least convenient possible way, and a
good reason to prefer the `from_json` version for a path this long.

### What it is anchored on

March 2020, which is a good compound test because its legs genuinely moved on
different clocks and in different directions.

- **VIX**: `peak=82.0` is the record closing high of **82.69 on 16 March
  2020**, the highest close in the index's history, above 2008's 80.86.
- **Policy**: two cuts, ten sessions apart, mirroring the Federal Reserve's
  two intermeeting moves: **50bp on 3 March 2020** taking the target range to
  1.00-1.25%, then **100bp on 15 March 2020** taking it to 0-0.25%. The three
  levels `0.0155 → 0.01125 → 0.00125` are the midpoints of 1.50-1.75%,
  1.00-1.25% and 0-0.25%.
- **Credit**: the blow-out-then-compression shape follows the US High Yield
  option-adjusted spread, which peaked around **10.9% on 23 March 2020**
  before the Fed's corporate credit facilities were announced, and then
  retraced through the rest of the year.

The `corp` numbers need a caveat of their own. The engine has a single
`corporate_bond_yield` standing in for an entire credit curve, so a recipe has
to choose which part of that curve it represents. This one represents the
**stressed end**: a 10.5% peak is a high-yield-like discount rate, roughly the
sourced 10.9% spread over a near-zero Treasury base, and it is deliberately
not an investment-grade yield, which stayed far lower. Sources also differ on
the 2020 high-yield peak depending on whether a daily observation or a monthly
average is quoted. I found both ~10.9% and ~8.8%, and I used the daily
figure.

`qe_pe_boost` ramping to **0.10 is not anchored on anything at all.** It is a
bare assumption that large-scale asset purchases supported equity multiples by
about ten percent, and it is doing a great deal of work in the result below.
The `cycle` field is left unpinned so the chain evolves on its own.

### What it does not claim

Everything above, and one thing more that matters more than the rest.

This recipe produces a market that **ends higher than it started**, which is
also what happened in 2020. That agreement is not evidence of anything. It is
a consequence of the `qe_pe_boost=0.10` I typed in. I assumed policy support
lifted multiples, and the model duly lifted multiples. Take that one line out
and the result changes character completely.

That is the whole argument for this page existing rather than a
`.pandemic()` method, made concrete. With the assumption written down as a
line of config, you can see that the conclusion was installed rather than
discovered. Behind a method name you could not have.

### What it measures in this model

Median **+9.79%** at 120 days, best name **+12.04%**, worst **−0.67%**, a
narrow cross-section, because the dominant channels here (the policy rate, the
QE multiple) move every name in the same direction, unlike recipe 1's
rate-sensitivity dispersion.

Realised volatility over the run, measured on the volatility convention, is
**80.79%**, against recipe 3's 82.16%, a comparable stress despite the very
different price outcome, which is the point of measuring both. The two
recipes describe markets that were about equally violent and ended in
completely different places.

`exact=True`: identical market draws across both worlds, so the whole
difference is the path.

## Citing a recipe

Do not cite the recipe. Cite the path:

```python
print(compound.to_json(days=120)[:120], "...")
```

```
{"days":120,"label":"compound: pandemic shape","path":[{"corporate_bond_yield":0.036,"day":0,"federal_funds_rate":0.0155 ...
```

`to_json` serialises the realised values rather than the constructor call,
which is the honest direction: the path is reproducible whatever any later
version of `pandemic_shape` does, and `Scenario.from_json` will replay it
exactly. Publish that alongside your seed and universe fingerprint, and a
reader can disagree with your assumptions and re-run them at the same time,
which is the entire point of keeping the assumption and the finding in
separate columns.

## Sources

Historical figures cited above, with what was actually retrieved:

- Federal Reserve, [Open Market Operations](https://www.federalreserve.gov/monetarypolicy/openmarket.htm)
  the 2022-23 target-range changes and the two March 2020 intermeeting cuts.
- Bureau of Labor Statistics, [Consumer prices up 9.1 percent over the year ended June 2022](https://www.bls.gov/opub/ted/2022/consumer-prices-up-9-1-percent-over-the-year-ended-june-2022-largest-increase-in-40-years.htm)
  the June 2022 peak and the comparison to November 1981.
- Bureau of Labor Statistics, [CPI news release, 10 February 2021](https://www.bls.gov/news.release/archives/cpi_02102021.htm)
  the January 2021 1.4% twelve-month figure.
- [Macroption, VIX all-time highs](https://www.macroption.com/vix-all-time-high/)
  and [CNBC, 16 March 2020](https://www.cnbc.com/2020/03/16/wall-streets-fear-gauge-hits-highest-level-ever.html)
  the 89.53 intraday high of 24 October 2008, the 80.86 close of 20 November
  2008, and the 82.69 record close of 16 March 2020.
- [Trading Economics / FRED BAMLH0A0HYM2](https://tradingeconomics.com/united-states/bofa-merrill-lynch-us-high-yield-option-adjusted-spread-fed-data.html)
  and [BAMLC0A0CM](https://tradingeconomics.com/united-states/bofa-merrill-lynch-us-corporate-master-option-adjusted-spread-fed-data.html)
  the December 2008 record spreads of 21.82% and 6.56%, and the March 2020
  high-yield peak.
- [NBER business cycle dating](https://www.nber.org/research/business-cycle-dating/business-cycle-dating-committee-announcements)
  the 2001, 2007-09 and 2020 recession dates.

**Numbers I could not source, and did not pretend to.** No corporate bond
*yield* series was retrievable from this environment: FRED's series pages and
CSV endpoint both refused the request, and searches returned landing pages
without values. Every `corporate_bond_yield` level on this page is therefore
constructed from a sourced *spread* plus an assumed Treasury base, and is
flagged as such in the recipe that uses it. The 2022 ten-year Treasury peak
was likewise not sourced and is not used. `fear_greed_index` values,
`qe_pe_boost`, all `calm` VIX levels, and every pacing parameter (`over`,
`at`, `begin`) are unanchored choices, each named as one where it appears.

And a final one that applies to the whole page: **none of the engine's
internal mappings are calibrated to any of these series.** Feeding a
historically accurate VIX path in does not make the volatility that comes out
historically accurate. The anchors justify the *inputs*; they say nothing
about the outputs.
