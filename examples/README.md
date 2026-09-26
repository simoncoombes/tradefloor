# Examples

Two tiers, and they are different things. `CONTRIBUTING.md` has the rule; the
short version is that the numbers are a curriculum and a directory is a study.

## Start here

**[`rate-shock/counterfactual.py`](rate-shock/counterfactual.py)** is the
canonical demo: one market, one agent, twenty days of shared history, a
checkpoint, a fork into two identical worlds, +200bps in one of them, and a
comparison of what the same agent did next. It takes under ten seconds of
CPU and needs nothing installed beyond the library.

```
python examples/rate-shock/counterfactual.py
```

The five-minute walkthrough is
[**Your first counterfactual experiment**](rate-shock/README.md).
The agent it runs is [`rate-shock/agent.py`](rate-shock/agent.py), which is
a parameter -- swap it for your own and nothing else in the experiment moves.

## The reading order

Eight notebooks and four scripts, numbered in reading order. The committed
notebooks carry their output, so you can read them without running anything.
Start at 00 if you have not used tradefloor before.

| | what it covers |
|---|---|
| [`00-a-year-in-one-market.ipynb`](00-a-year-in-one-market.ipynb) | The shortest useful thing: one year, one company, two crises, and why the price moved |
| [`01-first-simulation.ipynb`](01-first-simulation.ipynb) | Universe, engine, order book, determinism, provenance |
| [`02-evaluating-a-strategy.ipynb`](02-evaluating-a-strategy.ipynb) | Strategy specs, baselines, scores against buy-and-hold, ranking across seeds |
| [`03-why-did-the-price-move.ipynb`](03-why-did-the-price-move.ipynb) | The factor contributions that sum to every move |
| [`04-how-realistic-is-this.ipynb`](04-how-realistic-is-this.ipynb) | The realism panel, the gaps, choosing a preset |
| [`05-training-an-agent.ipynb`](05-training-an-agent.ipynb) | The Gymnasium environment, episodes, what size costs |
| [`06-execution-and-impact.ipynb`](06-execution-and-impact.ipynb) | TCA, the counterfactual run, partial fills, and the same orders in a book a scenario has thinned |
| [`09-a-pandemic-shaped-market.ipynb`](09-a-pandemic-shaped-market.ipynb) | Driving a real 2020-21 macro path, and diagnosing why the first attempt missed. Pinned to `pt-v12`, with the same path run on the default at the end |
| [`07-research-workflow.py`](07-research-workflow.py) | A whole study in one file: sweep, evaluation, TCA, replay |
| [`08-claude-agent.py`](08-claude-agent.py) | An LLM agent scored against the baselines. Needs a key; [`integrations/callable/five_days.ipynb`](integrations/callable/five_days.ipynb) replays a recorded Claude run without one |
| [`10-forking-a-market.py`](10-forking-a-market.py) | Fork a market mid-flight, change the policy rate in one branch, compare |
| [`11-scenario-fork.py`](11-scenario-fork.py) | Read a scenario from YAML, apply it to one branch of a fork, and price what it cost |

Notebooks 05 and 06 cover the two audiences the project is built for, RL
researchers and execution developers.

## The studies

One directory each, self-contained, in no particular order. A study asks one
question, keeps its script and its notebook together because they present the
same experiment two ways, and writes its output to its own git-ignored
`artifacts/`.

| | what it asks |
|---|---|
| [`rate-shock/`](rate-shock/) | Does the agent actually react to macro conditions? Checkpoint, fork, +200bps in one arm, compare. Under ten seconds of CPU, no keys |
| [`integrations/finrobot/`](integrations/finrobot/) | The same experiment with a real [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) agent in place of the native one. Replays a recorded run by default, so it needs no API key |
| [`integrations/`](integrations/) | The same decision loop under a plain function, the OpenAI Agents SDK, PydanticAI and LangGraph. Offline, no keys |
| [`experiments/liquidity-crisis/`](experiments/liquidity-crisis/) | Will a financial AI agent reduce risk in a market crisis? A checkpoint, a two-way fork, and the packaged `liquidity_crisis` scenario on one arm. An executed notebook, replayed from a recording |

## Running them

```
pip install tradefloor jupyter
jupyter lab
```

Notebooks 00, 03 and 09 also need `matplotlib` for their charts, 00 and 09
read the Arrow tables and need `tradefloor[arrow]`, and 05 needs
`tradefloor[rl]` for the Gymnasium environment. The core library has no
dependencies.

The times below are CPU time (user plus system), measured with
`/usr/bin/time` at 0.8.5 and rounded up. Building an engine on pt-v20, the
default, costs about 0.7 seconds of CPU because the preset runs a 755-day
burn-in before day 0, and most examples build several.

`10-forking-a-market.py` and `11-scenario-fork.py` take one to two seconds
each and need nothing extra. `rate-shock/counterfactual.py` takes under ten,
about half of it building five engines. It writes a chart if `matplotlib` is
installed and says so if it is not. `07-research-workflow.py` takes about two
minutes and prints its own wall-clock total on the last line. It needs
`tradefloor[arrow]`, because its realism step reads the daily bars table.
`integrations/finrobot/rate_shock.py` takes about five seconds and also runs
on the core library alone in its default replay mode; `--live` is the one
that needs `tradefloor[finrobot]`, Python 3.11 and an API key. The recording
that replay reads is in the repository's `tests/fixtures/`, so the replay
needs a clone. `08-claude-agent.py` needs `tradefloor[claude]` and an API
key, and spends money per decision, so the test suite never lets it reach a
model.

## How they're kept working

`tests/test_examples.py` checks them. It walks `examples/` rather than
globbing `0*`, so both tiers are covered and a new example cannot arrive
unchecked. The scripts are syntax-checked on every test run, which catches a
rename that missed a reference. `rate-shock/counterfactual.py` gets more than
that: `tests/test_rate_shock_demo.py`
runs it end to end on every test run and checks its claims, not only its exit
code -- that the arms started identical, that nothing diverged before the
intervention, that the experiment reruns to the bit, and that both manifests
reproduce. `integrations/finrobot/rate_shock.py` has the same in
`tests/test_finrobot.py`, which replays its recorded FinRobot run end to end
on every pass. The rest is opt-in, because executing every notebook takes
several minutes:

```
TRADEFLOOR_SLOW_TESTS=1 pytest tests/test_examples.py
```

That executes every notebook, confirms the committed copies carry output,
and runs `07-research-workflow.py` end to end. `08-claude-agent.py` is
checked on every run without a key or a bill: it must refuse readably when
no model answers, offer Claude every factor the harness scores, and ask on
the day's last step, the one its answer is scored against.

Regenerate the committed output with:

```
jupyter nbconvert --to notebook --execute --inplace examples/0*.ipynb
jupyter nbconvert --to notebook --execute --inplace examples/integrations/*/*.ipynb
python examples/experiments/liquidity-crisis/build_notebook.py
```

The second line re-runs the integration notebooks, which replay recorded
model calls. The third rebuilds the liquidity-crisis study from its module.
