# Examples

Two tiers, and they are different things. `CONTRIBUTING.md` has the rule; the
short version is that the numbers are a curriculum and a directory is a study.

## Start here

**[`rate-shock/counterfactual.py`](rate-shock/counterfactual.py)** is the
canonical demo: one market, one agent, twenty days of shared history, a
checkpoint, a fork into two identical worlds, +200bps in one of them, and a
comparison of what the same agent did next. It runs in about two seconds and
needs nothing installed beyond the library.

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
| [`02-evaluating-a-strategy.ipynb`](02-evaluating-a-strategy.ipynb) | Strategy specs, baselines, capture ratio, ranking across seeds |
| [`03-why-did-the-price-move.ipynb`](03-why-did-the-price-move.ipynb) | The nine factor contributions that sum to every move |
| [`04-how-realistic-is-this.ipynb`](04-how-realistic-is-this.ipynb) | The realism panel, the gaps, choosing a preset |
| [`05-training-an-agent.ipynb`](05-training-an-agent.ipynb) | The Gymnasium environment, episodes, what size costs |
| [`06-execution-and-impact.ipynb`](06-execution-and-impact.ipynb) | TCA, the counterfactual run, partial fills |
| [`09-a-pandemic-shaped-market.ipynb`](09-a-pandemic-shaped-market.ipynb) | Driving a real 2020-21 macro path, and diagnosing why the first attempt missed |
| [`07-research-workflow.py`](07-research-workflow.py) | A whole study in one file: sweep, evaluation, TCA, replay |
| [`08-claude-agent.py`](08-claude-agent.py) | An LLM agent scored against the baselines |
| [`10-forking-a-market.py`](10-forking-a-market.py) | Fork a market mid-flight, change the policy rate in one branch, compare |
| [`11-scenario-fork.py`](11-scenario-fork.py) | Read a scenario from YAML, apply it to one branch of a fork, and price what it cost |

Notebooks 05 and 06 cover the two audiences the project is built for, RL
researchers and execution developers.

## The studies

One directory each, self-contained, no reading order. A study asks one
question, keeps its script and its notebook together because they are the same
experiment in two presentations, and writes its output to its own git-ignored
`artifacts/`.

| | what it asks |
|---|---|
| [`rate-shock/`](rate-shock/) | Does the agent actually react to macro conditions? Checkpoint, fork, +200bps in one arm, compare. Two seconds, no keys |
| [`finrobot/`](finrobot/) | The same experiment with a real [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) agent in place of the native one. Replays a recorded run by default, so it needs no API key |

## Running them

```
pip install tradefloor jupyter
jupyter lab
```

Notebooks 00, 03 and 09 also need `matplotlib` for their charts, and 05 needs
`tradefloor[rl]` for the Gymnasium environment. The core library has no
dependencies.

`rate-shock/counterfactual.py` runs in about two seconds,
`07-research-workflow.py` in about five and `10-forking-a-market.py` in about
two, and none of them needs anything extra. The first writes a chart if
`matplotlib` is installed and says so if it is not. `finrobot/rate_shock.py`
also runs on the core library alone in its default replay mode; `--live` is
the one that needs `tradefloor[finrobot]`, Python 3.11 and an API key.
`08-claude-agent.py` needs `tradefloor[claude]` and an API key, and spends
money per decision, so it's the one file here that isn't run automatically.

## How they're kept working

`tests/test_examples.py` checks them. It walks `examples/` rather than
globbing `0*`, so both tiers are covered and a new example cannot arrive
unchecked. The scripts are syntax-checked on every test run, which catches a
rename that missed a reference. `rate-shock/counterfactual.py` gets more than
that: `tests/test_rate_shock_demo.py`
runs it end to end on every test run and checks its claims, not only its exit
code -- that the arms started identical, that nothing diverged before the
intervention, that the experiment reruns to the bit, and that both manifests
reproduce. `finrobot/rate_shock.py` has the same in
`tests/test_finrobot.py`, which replays its recorded FinRobot run end to end
on every pass. The rest is opt-in, because executing every notebook takes
about a minute:

```
TRADEFLOOR_SLOW_TESTS=1 pytest tests/test_examples.py
```

That executes every notebook, confirms the committed copies carry output,
runs `07-research-workflow.py` end to end, and checks `08-claude-agent.py`
refuses readably when its extra is missing rather than raising a traceback.

Regenerate the committed output with:

```
jupyter nbconvert --to notebook --execute --inplace examples/0*.ipynb
jupyter nbconvert --to notebook --execute --inplace examples/*/*.ipynb
```
