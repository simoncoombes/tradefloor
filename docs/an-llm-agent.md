---
title: An LLM agent
nav_order: 14
rack: connect
short: An LLM agent
---

# An LLM agent

`examples/claude_agent.py` drives an agent with the Claude API and scores it
against the reference baselines.

```
pip install "pretium[claude]"
export ANTHROPIC_API_KEY=...        # or: ant auth login
python examples/claude_agent.py
```

The interesting part is not that a language model can pick a portfolio. It is
that this harness can check whether it picked one for the right reason.

## Two scores, not one

Every agent gets a P&L. An agent that also implements `explain(day)` gets a
second, separate score: the harness compares the factor the agent names
against the engine's own attribution of what actually moved prices that day.

```
agent                     pnl    impact    why-right
oracle                  42650      87.5         100%
claude                     ...       ...          ..%
momentum                 -221      20.7            -
```

The oracle and momentum rows are measured. The claude row is left blank
deliberately: no published run exists yet, because every run costs money and
one seed would not be worth quoting anyway. Run it and you get your own
numbers. The oracle scores 100% on `why-right` because it reads the true
mispricing, which is the same reason it is a reference rather than a
competitor.

A model can earn the first score by accident. The second is the one no real
market can produce, because attributing a real price move to mean reversion
rather than order flow is exactly what nobody can do.

So the run answers a question worth asking about any model that claims to
reason about markets: when it explains a move, is it describing the mechanism
or telling a plausible story? Here that has an answer.

## How it is wired

Claude returns a structured decision rather than prose, so nothing has to be
parsed out of free text.

```python
class Decision(BaseModel):
    weights: dict[str, float]   # -1.0 to 1.0 per ticker
    driver: Factor              # one of the engine's seven factors
    reasoning: str
```

`driver` is a `Literal` of the seven components the engine decomposes each
move into, so the answer is drawn from a closed set and can be checked. The
call uses `messages.parse`, which validates the response against the schema:

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
instruments, so 20 calls, which lands around $0.30 to $0.60 at Opus 5 rates
with the system prompt cached. It scales linearly with `days`.

The mechanism does not need a frontier model to demonstrate. Pass
`model="claude-haiku-4-5"` for a cheap smoke test and expect worse answers,
particularly on `driver`.

## What this does not tell you

The same warning as everywhere else in these docs, and it applies with more
force to a language model than to a coded strategy. This market has knowable
structure: returns are positively autocorrelated at lag one where real
equities are not, and the system prompt tells the model so. A model that reads
that and leans into momentum is exploiting the simulator, not demonstrating
skill that transfers.

The `why-right` column is the more durable measurement of the two, because
naming the mechanism correctly is a claim about reasoning rather than about
this market's particular quirks. Even so, run it across seeds before
concluding anything. One market ranks the market.
