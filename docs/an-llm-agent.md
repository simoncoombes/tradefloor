---
title: An LLM agent
nav_order: 14
rack: connect
short: An LLM agent
---

# An LLM agent

`examples/08-claude-agent.py` drives an agent with the Claude API and scores it
against the reference baselines.

```
pip install "pretium[claude]"
export ANTHROPIC_API_KEY=...        # or: ant auth login
python examples/08-claude-agent.py
```

The interesting part is not that a language model can pick a portfolio. It is
that this harness can check whether it picked one for the right reason.

## Two scores, not one

Every agent gets a P&L. An agent that also implements `explain(day)` gets a
second, separate score: the harness compares the factor the agent names
against the engine's own attribution of what actually moved prices that day.

```
agent                     pnl    impact    why-right
oracle                 155707       ...         100%
claude                    ...       ...          ..%
momentum                -2207       ...            -
```

The oracle and momentum rows are measured, by the same call the example makes:
the reference agents (`pt.reference_agents(seed=3)`) over
`Universe.random(12, seed=7)`, seed 2026, 20 days, `max_leverage=2.0`. Adding
the claude row does not move them, because `evaluate` runs each agent in its
own copy of the market against one shared untraded baseline -- verified by
adding a sixth trading agent and reading the oracle's P&L back unchanged to
the cent. The claude row is left blank deliberately: no published run exists
yet, because every run costs money and one seed would not be worth quoting
anyway. Run it and you get your own numbers.

Momentum's row is negative, and that is not a bad seed. Across seeds
2020-2031 at this configuration the momentum reference agent ends in profit in
3 of 12, and beats buy-and-hold in 6 of 12. It is a baseline, not a strategy
that works here; see the last section for why the market stopped rewarding
it.

The oracle scores 100% on `why-right` because it reads the true mispricing,
which is the same reason it is a reference rather than a competitor.

The `impact` column is left blank for a harder reason: the run prints a
number, and the number is not a measurement. `impact_bps` compares the traded
run's closing prices against the same seed with nobody trading, and fills feed
back into the price process, so the gap between the two runs compounds with
horizon. Re-measured across seeds 2020-2031 at this exact configuration, the
oracle's sign does not fail at a cliff, it erodes: positive in 12 of 12 seeds
at two days, 11 of 12 at three and at five, 10 of 12 at ten, and 5 of 12 at
twenty. The span goes the same way. Over two days the oracle runs +2.4 to
+67.9 bps and momentum +3.0 to +36.9, which reads as what it is, the cost of
trading. Over twenty the oracle runs -451.1 to +418.4, a spread thirteen times
wider than its two-day one, and momentum -2.8 to +91.0. Momentum does keep its
sign at twenty days, 11 of 12, but that is the seed being kind rather than the
number being stable: its eleven positive twenty-day readings already span 20.7
to 91.0 bps among themselves, and at ten days it is positive in only 9 of 12.
A twenty-day impact figure has whatever sign the seed hands it, so this page
stops publishing one. Read `impact_bps` over a day or two, not weeks, and
across seeds.

A model can earn the first score by accident. The second is the one no real
market can produce, because attributing a real price move to mean reversion
rather than order flow is exactly what nobody can do.

So the run answers a question worth asking about any model that claims to
reason about markets: when it explains a move, is it describing the mechanism
or telling a plausible story? Here that has an answer.

### What `why-right` is worth right now

Less than the column suggests, and the shortfall is measurable rather than
philosophical. `explanation_accuracy` compares the agent's answer against the
day's *dominant* factor, which the harness computes by summing each factor's
attribution in absolute value across the whole roster. That sum is almost
always won by `random_noise`. Over seeds 2020-2031 at this configuration it is
the dominant factor on 240 of 240 scored days; on a 40-name, 60-day run over
three seeds it takes 179 days of 180, losing one to `company_news`; and
driving the default `Scenario.vix_shock()` through the run does not disturb
it, 120 of 120 over seeds 2020-2025. An agent that trades nothing and answers
`random_noise` every single day scores 100% on all twelve seeds.

So the oracle's 100% is not evidence that the oracle understands anything, and
a model's would not be either. Read a low score as informative -- the model
named something the engine says was not the largest mover -- and a high one as
weak. The mechanism is real and the scoring is coarse: `engine.attribution`
returns a value per instrument, so a sharper test, per name and per day rather
than one winner for the roster, is available to anyone who wants to write it.
`explanation_accuracy` is not yet that test.

## How it is wired

Claude returns a structured decision rather than prose, so nothing has to be
parsed out of free text.

```python
class Decision(BaseModel):
    weights: dict[str, float]   # -1.0 to 1.0 per ticker
    driver: Factor              # one of the engine's nine factors
    reasoning: str
```

`driver` is a `Literal` of the nine components the engine decomposes each
move into, so the answer is drawn from a closed set and can be checked. It
has to be all nine. The example's list held seven until 0.3.0, omitting
`circuit_breaker` and `jump`, and a closed set narrower than the one the
harness scores against is worse than an open one: on a day the engine
attributes to a missing factor, the model is marked wrong for a choice it
was never offered. `Factor` is now exactly `pt.Engine.FACTORS`, in order.

The call uses `messages.parse`, which validates the response against the
schema:

```python
response = self.client.messages.parse(
    model="claude-opus-5",
    max_tokens=4000,
    system=[{"type": "text", "text": SYSTEM,
             "cache_control": {"type": "ephemeral"}}],
    thinking={"type": "adaptive"},
    output_config={"effort": self.effort},
    messages=[{"role": "user", "content": self._prompt(obs)}],
    output_format=Decision,
)
```

The market's rules go in the system prompt and never change, so they carry a
cache breakpoint. The volatile part -- prices, positions, the book -- goes in
the user turn after it. Putting the market state in the system prompt would
invalidate the cache on every single call.

That breakpoint does nothing at the current size, and the layout is still
right. Claude Opus 5 writes no cache entry below 512 tokens; the `SYSTEM`
string here is 1,155 characters, which would have to average under 2.26
characters per token to reach that floor, and English prose does not. Claude
Haiku 4.5's floor is 4,096 tokens, further out of reach again. So the
breakpoint is inert rather than wrong: it costs nothing to carry, it starts
paying the day the rules grow past the floor, and the ordering it implies --
rules first, market state after -- is what makes that possible at all.

Weights come back rather than share counts. Converting a weight into a
quantity means dividing by price and capping against average daily volume,
which is arithmetic the harness already knows how to do, and asking a model to
do it just adds a place for it to go wrong.

## Two things that will bite you

**The harness steps six times a day.** `steps_per_day` defaults to 6, so an
agent that calls the API in `act` without gating makes six calls a day and six
times the bill. The example holds its position between daily decisions:

```python
if obs.day == self._last_day:
    return {}
self._last_day = obs.day
```

**`explain` takes the day.** The signature is `explain(self, day)`, not
`explain(self)`. Getting it wrong does not raise anywhere you will notice --
the harness records the error per day and reports `explanation_accuracy` as
`None`, so the column reads `-` and looks like the feature is simply off.

## Cost

One call per day, one day per decision. The default run is 20 days over 12
instruments, so 20 calls, and it scales linearly with `days`.

This page does not publish a dollar figure, for the same reason it does not
publish an `impact` number: nobody has run it and billed one. The arithmetic
is yours to do and the inputs are small. Claude Opus 5 bills $5 per million
input tokens and $25 per million output tokens; the observation each call
sends is a twelve-row table, three lines of book and a paragraph; and
`thinking={"type": "adaptive"}` means the reasoning bills as output and
dominates the total. Twenty calls at 2,000 output tokens each is 40,000
tokens, or $1.00; at 5,000 each it is $2.50. Nothing here caches yet, for the
reason above.

The mechanism does not need a frontier model to demonstrate. Pass
`model="claude-haiku-4-5"` for a cheap smoke test and expect worse answers,
particularly on `driver`.

## What this does not tell you

The same warning as everywhere else in these docs, and it applies with more
force to a language model than to a coded strategy. This market has knowable
structure: price is fair value times a log mispricing that reverts toward zero
on a fixed 60-day half-life -- `mispricing_half_life_days` is 60.0 on pt-v12
-- and the system prompt states it outright. A model that reads that and
trades the reversion is exploiting the simulator, not demonstrating skill that
transfers.

Until this release the warning named a different quirk, and what became of it
is worth stating, because the reasoning outlived the correction. An earlier
preset really did hand momentum close to free money: its `return_acf1` read
+0.243 against a real band of -0.08 to +0.06, which
[the realism metrics](realism-metrics.md) record as the single most misleading
defect this project has had. pt-v12's certified `return_acf1` is +0.0239,
inside the band, and momentum now ends in profit in 3 of the 12 seeds swept
above. The quirk moved; the warning did not.
[The realism envelope](realism-envelope.md) lists the five gaps still open.

The `why-right` column is the more durable *kind* of measurement of the two,
because naming the mechanism correctly is a claim about reasoning rather than
about this market's particular quirks. As it is scored today it is also the
coarser of the two, for the reason given above. Either way, run it across
seeds before concluding anything. One market ranks the market.
