# Examples

Eight notebooks and three scripts, numbered in reading order. The committed
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

Notebooks 05 and 06 cover the two audiences the project is built for, RL
researchers and execution developers.

## Running them

```
pip install tradefloor jupyter
jupyter lab
```

Notebooks 00, 03 and 09 also need `matplotlib` for their charts, and 05 needs
`tradefloor[rl]` for the Gymnasium environment. The core library has no
dependencies.

`07-research-workflow.py` runs in about five seconds and
`10-forking-a-market.py` in about two, and neither needs anything extra. `08-claude-agent.py` needs `tradefloor[claude]` and an API key, and
spends money per decision, so it's the one file here that isn't run
automatically.

## How they're kept working

`tests/test_examples.py` checks them. The scripts are syntax-checked on every
test run, which catches a rename that missed a reference. The rest is opt-in,
because executing eight notebooks takes about a minute:

```
PRETIUM_SLOW_TESTS=1 pytest tests/test_examples.py
```

That executes every notebook, confirms the committed copies carry output,
runs `07-research-workflow.py` end to end, and checks `08-claude-agent.py`
refuses readably when its extra is missing rather than raising a traceback.

Regenerate the committed output with:

```
jupyter nbconvert --to notebook --execute --inplace examples/0*.ipynb
```
