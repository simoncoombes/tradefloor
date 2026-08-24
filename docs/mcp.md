---
title: The MCP server
nav_order: 13.5
rack: connect
short: MCP server
---

# The MCP server

**Give a coding agent the simulator as a set of tools.**

`pretium` is a Python library, so its usual audience is a person writing
code. The MCP server adds an audience that cannot: a model that calls tools
and reads back JSON. Point Claude Code, Claude Desktop or any MCP client at
it and ask questions in English — *does a momentum strategy beat buy-and-hold
here, and is the difference real?*

```
pip install "pretium[mcp]"
```

Then register it with your client. The command is `pretium-mcp`, it takes no
arguments, and it speaks MCP over stdio:

```json
{
  "mcpServers": {
    "pretium": {
      "command": "pretium-mcp"
    }
  }
}
```

In Claude Code, `claude mcp add pretium -- pretium-mcp` does the same thing.

## This is the opposite of "An LLM agent"

Two pages, two directions, and they are easy to confuse:

- [An LLM agent](an-llm-agent.md) puts a model **inside** the market. It
  trades, it gets a P&L, and it is scored against the baselines.
- **This page** puts the market inside the **model's** toolbox. Nothing
  trades on the model's behalf; it composes strategies as data and asks the
  simulator to run them.

## The tools

| Tool | What it answers |
|---|---|
| `describe_simulator` | What is this, what is it certified to reproduce, what can it not do |
| `check_envelope` | Is my question inside the certified envelope — *before* I spend anything |
| `validate_strategy` | Is this spec well-formed, and what is its fingerprint |
| `evaluate_strategies` | How do these strategies do on one identical market |
| `rank_strategies` | Which is really better, across seeds, with a paired sign test |
| `run_stress_scenario` | What does a rate, VIX or volatility shock do to them |
| `explain_price_move` | Why did this price move — the seven factors that sum to it |
| `describe_universe` | What roster would a run use |

Start with `describe_simulator`. Everything else reads better afterwards.

## Strategies are data, never code

There is no way to submit Python to this server. A tool call cannot carry a
callable, and accepting a string of code would make this a remote code
execution endpoint with a market simulator attached.

Instead the server speaks [strategy specs](strategy-specs.md) — the same
declarative grammar that makes a result citable:

```json
{
  "signal": {"kind": "momentum", "lookback_days": 1.0},
  "portfolio": {"top_k": 5, "gross": 1.0}
}
```

`spec_version` is optional here and supplied for you, because a model
composing a spec in conversation will omit it every time and the round trip
is wasted. A spec that *names* a version keeps it, so a version newer than
this build understands is still refused rather than read partially.

What the grammar cannot express, the server cannot run: path dependence,
conditional logic, custom signals. Those need a Python agent and the
library. `describe_simulator` states this rather than leaving you to find
it.

## Every result carries its own caveats

This is the part worth understanding before you trust anything the server
tells you.

A person calling `pt.evaluate` has the docstrings and this documentation in
reach. A model calling `evaluate_strategies` has the tool result and nothing
else — and it will summarise that result to a human who has even less. Hand
a model `{"return_pct": 88.7}` and it will report that the strategy made
88.7%.

So every result carries a `caveats` list beside its numbers:

```
caveats:
  - The price process is a known model, not a forecast...
  - ONE SEED. A single seed measures that seed as much as the strategy...
  - SHORT WINDOW: 5 trading days against a realism certification
    measured over 252...
  - This strategy trades a return-continuation signal, so its edge depends
    on the simulator's return autocorrelation: return_acf1 measures 0.0375
    against a real-market band of -0.08 to 0.06 (in band) at the certified
    252-day horizon.
```

Two properties make this more than a disclaimer.

**They are earned.** Each caveat fires on a property of your specific call.
A multi-seed run does not carry the single-seed warning; a strategy with no
momentum component is not told about return autocorrelation. A list that
always says the same thing stops being read.

**They are computed, not typed.** Every number in them is read from the
realism envelope at call time. This rule paid for itself immediately: while
this server was being written, `PRODUCT.md` and `README.md` were both found
still asserting a return autocorrelation of `+0.219` and `+0.249` from a
superseded preset. The shipped `pt-v3` certifies `return_acf1` at `0.0375`,
and re-measuring it across the README's own published method — 40 names,
universe seed 111, 252 days, sim seeds 1 to 6 — gives a median of `0.0485`.
Both are comfortably *inside* the real-market band of −0.08 to 0.06, and
both are a fifth of the figure the prose still quotes. A hardcoded caveat is
how a caveat becomes false, and a false caveat told to a model is worse than
none.

A summary of a pretium result that drops the caveats is a misreport.

## Provenance on every result

Every successful call returns what someone else needs to re-run it: the
pretium version, the model preset and its fingerprint, the seed, the
universe and its fingerprint, and the horizon.

A number from a simulator without its seed is not a measurement. It is an
anecdote, and a model summarising it cannot tell the difference.

## One seed is not an answer

`evaluate_strategies` runs one market. It is fast and it is the right first
look, but a verdict from a single seed measures the seed as much as the
strategy.

`rank_strategies` is the honest version. It runs many seeds and reports
`paired_sign_tests`: both entrants traded the **same** market on each seed,
so pairing removes the market from the question.

Read `pooled_capture` — total P&L over the reference's total — rather than
`seeds_first`, which counts league positions among all entrants and splits
arbitrarily on a tie. A submitted momentum spec compared against the
momentum baseline comes back as all ties and zero paired seeds, which is the
correct answer for two identical strategies rather than a coin-flip winner.

## Limits, and why they exist

| Limit | Value |
|---|---|
| `max_days` | 60 |
| `max_universe` | 120 |
| `max_strategies` | 8 |
| `max_seeds` | 12 |

These are wall-clock decisions, not modelling ones — the library imposes
none of them. Forty names with the baselines takes about 0.5s at 5 days,
20s at 60, and 95s at 252, and a tool call has to answer inside a
conversation.

That last figure is why `max_days` sits below the certified 252-day horizon,
and why **every** scored result from this server carries the SHORT WINDOW
caveat. The statistics that make this market credible are annual
measurements. A five-day run is a sample of that market, not a description
of it. If you need the certified horizon, use the library.

## What is deliberately not exposed

**Atlas.** A [response-surface survey](atlas.md) is thousands of simulations
and runs for hours. A tool call that cannot return inside a conversation is
not a tool.

**Model coefficients.** Presets are selectable by name. The 54 coefficients
behind them are not, because improvised coefficients produce a market nobody
calibrated, reported with the authority of a named preset.

**Writes.** Every tool is read-only and pure: same arguments, same bytes, on
every platform.

## Related

- [Strategy specs](strategy-specs.md) — the grammar the server speaks
- [An LLM agent](an-llm-agent.md) — a model trading *inside* the market
- [The realism envelope](realism-envelope.md) — where the caveats come from
- [Agents and evaluation](agents-and-evaluation.md) — the library API underneath
