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
it and ask questions in English: *does a momentum strategy beat buy-and-hold
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

### If the client cannot find it

This is the usual first failure, and it is not a pretium problem: a desktop
MCP client does not inherit your shell's `PATH`, so `"command":
"pretium-mcp"` resolves only if the script sits somewhere the client already
looks. If you installed into a virtual environment, which you should, give
the absolute path instead:

```json
{
  "mcpServers": {
    "pretium": {
      "command": "/path/to/.venv/bin/pretium-mcp"
    }
  }
}
```

To find that path, ask the interpreter you installed into:

```
python -c "import sys, pathlib; print(pathlib.Path(sys.prefix) / ('Scripts' if sys.platform == 'win32' else 'bin') / 'pretium-mcp')"
```

Derived from `sys.prefix` rather than looked up on `PATH` deliberately.
`shutil.which` is the obvious thing to reach for and it returns `None`
unless the environment happens to be activated, which is exactly the
situation someone hits this problem in.

Running `pretium-mcp` in a terminal is a useful check on its own. With the
extra missing it says so and names the fix; with everything present it
waits silently for a client to speak to it over stdin, which looks like a
hang and is the server working.

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
| `check_envelope` | Is my question inside the certified envelope, *before* I spend anything |
| `validate_strategy` | Is this spec well-formed, and what is its fingerprint |
| `build_universe` | What roster shall I run against: generated, concentrated, or hand-authored |
| `build_scenario` | What macro path shall I run through, and what does it look like day by day |
| `evaluate_strategies` | How do these strategies do on one identical market |
| `rank_strategies` | Which is really better, across seeds, with a paired sign test |
| `run_stress_scenario` | What a shock does, always against the same market unshocked |
| `explain_price_move` | Why did this price move, via the nine factors that sum to it |
| `start_job` | Run something too slow to answer inline, including a full certified year |
| `check_job` | Is it done, and what did it find |

Start with `describe_simulator`. Everything else reads better afterwards.

The three `build_*` and `validate_*` tools are all cheap preview steps: they
compose a thing as data, check it, and hand back a document the run tools
accept. A model gets a grammar wrong several times before it gets it right,
and each of those attempts should cost a parse rather than a simulation.

## Strategies are data, never code

There is no way to submit Python to this server. A tool call cannot carry a
callable, and accepting a string of code would make this a remote code
execution endpoint with a market simulator attached.

Instead the server speaks [strategy specs](strategy-specs.md), the same
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

## Universes and scenarios are data too

A strategy is not the only thing you compose. `build_universe` and
`build_scenario` do the same job for the market itself.

**Rosters.** By default a run uses a generated, sector-balanced roster from
`(size, seed)`. Two other shapes matter:

```json
{"size": 20, "seed": 111, "sectors": ["technology", "financial_services"]}
```

A sector-**concentrated** roster is not a convenience setting. "Certification
was measured on a sector-balanced roster, which no real index is" is one of
the six named gaps in [the realism envelope](realism-envelope.md), and
`envelope.check` already takes `sector_concentrated` as an argument. So
concentrating a roster is the honest way to ask a real question, and the
answer comes back labelled uncertified, automatically, in the caveats of
every result that used it.

You can also hand-author the roster outright, passing `instruments` with
explicit tickers, sectors, prices and betas. Unknown fields are refused by
name rather than ignored, because a silently dropped field produces a roster
you did not describe.

Either way you get back a `universe` document. Pass it to any run tool as
`universe` and it supersedes that tool's inline arguments, so a roster is
composed once and reused rather than re-specified per call, and the
provenance records the document rather than just the fingerprint. A fingerprint
identifies a roster to someone who already has it; a document reconstructs
it.

**Scenarios.** Three presets exist by name (`rate_shock`, `vix_shock` and
`vol_shock`) but you are not limited to them. `build_scenario` composes one
from `hold`, `ramp` and `step` instructions over the seven macro fields
(`vix`, `federal_funds_rate`, `corporate_bond_yield`, `inflation_rate`,
`qe_pe_boost`, `fear_greed_index`, `cycle`):

```json
{"steps": [
  {"kind": "ramp", "field": "vix", "start": 15, "end": 55, "over": 8},
  {"kind": "step", "field": "federal_funds_rate",
   "before": 0.025, "after": 0.06, "at": 4}]}
```

It returns the day-by-day table so you can see the path before you spend
anything, and a document `run_stress_scenario` takes directly.

An earlier version of this server exposed only the three presets, on the
grounds that free-form building would let a model pin macro states nobody
calibrated. That reasoning does not survive contact with the rest of the
design: this server's whole approach is to **allow and label**, not to
forbid, and the envelope already carries a `scenario_magnitude` gap for
exactly that risk. An authored scenario is allowed, and every result from
one says plainly that a shock's *direction* is certified while its *size* is
not.

## Long runs, and the horizon they unlock

A direct tool call is capped at 60 days, because 40 names with the baselines
takes about 95 seconds at 252 days and a tool call has to answer inside a
conversation.

That cap had a consequence worth stating plainly: **every result a direct
call can produce is a SHORT WINDOW** on a market whose realism is certified
over 252 days. The statistics that make this market credible, meaning
volatility, autocorrelation and cross-sectional co-movement, are annual
measurements.

`start_job` is what removes that ceiling:

```
start_job(tool="evaluate_strategies",
          arguments={"strategies": {...}, "days": 252})
→ {"job_id": "job-1", "estimated_seconds": 96}

check_job("job-1")
→ {"status": "running", "elapsed_seconds": 42}
→ {"status": "done", "result": {...}}
```

A job may run to 252 days, the certified horizon itself, and a result at
that horizon correctly does **not** carry the SHORT WINDOW caveat, because
it is no longer a slice. This is the point of having jobs at all; the
convenience of not blocking is secondary.

Jobs run inside the server process. Two run at once (a third would slow both
without finishing sooner), finished results are kept for later collection,
and **nothing survives a server restart**. There is no queue and no
database, and `check_job` says so rather than leaving you to infer it. The
`estimated_seconds` figure comes from measured cost and is an estimate, not
a promise: a long overrun usually means the machine is loaded, not that the
job has hung.

## Every result carries its own caveats

This is the part worth understanding before you trust anything the server
tells you.

A person calling `pt.evaluate` has the docstrings and this documentation in
reach. A model calling `evaluate_strategies` has the tool result and nothing
else, and it will summarise that result to a human who has even less. Hand
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
and re-measuring it across the README's own published method (40 names,
universe seed 111, 252 days, sim seeds 1 to 6) gives a median of `0.0485`.
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

Read `pooled_capture`, which is total P&L over the reference's total, rather than
`seeds_first`, which counts league positions among all entrants and splits
arbitrarily on a tie. A submitted momentum spec compared against the
momentum baseline comes back as all ties and zero paired seeds, which is the
correct answer for two identical strategies rather than a coin-flip winner.

## Limits, and why they exist

| Limit | Value |
|---|---|
| `max_days` (direct call) | 60 |
| `max_days` (background job) | 252, the certified horizon |
| `max_universe` | 120 |
| `max_strategies` | 8 |
| `max_seeds` | 12 |
| concurrent jobs | 2 |

These are wall-clock decisions rather than modelling ones, and the library
imposes none of them. Forty names with the baselines takes about 0.5s at 5 days,
20s at 60, and 95s at 252, and a tool call has to answer inside a
conversation.

That last figure is why a direct call is capped below the certified 252-day
horizon, and why every directly-returned result carries the SHORT WINDOW
caveat. Use `start_job` to reach the certified horizon without leaving the
server.

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

- [Strategy specs](strategy-specs.md), the grammar the server speaks
- [An LLM agent](an-llm-agent.md), a model trading *inside* the market
- [The realism envelope](realism-envelope.md), where the caveats come from
- [Agents and evaluation](agents-and-evaluation.md), the library API underneath
