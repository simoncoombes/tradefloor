# Examples

Four notebooks, in order. Each runs end to end and the committed copies
carry their real output, so you can read them without running anything.

| notebook | what it answers |
|---|---|
| [`01-first-simulation.ipynb`](01-first-simulation.ipynb) | What does a market look like, and what did I just run? |
| [`02-evaluating-a-strategy.ipynb`](02-evaluating-a-strategy.ipynb) | Is my strategy any good, and how would I know? |
| [`03-why-did-the-price-move.ipynb`](03-why-did-the-price-move.ipynb) | Ground truth: the seven factors that sum to every move |
| [`04-how-realistic-is-this.ipynb`](04-how-realistic-is-this.ipynb) | What is this certified to reproduce, and where does it fail? |

## Running them

```
pip install pretium jupyter matplotlib
jupyter lab
```

Only notebook 3 needs `matplotlib`, and only for its final chart. The core
library has no dependencies.

## Scripts

- **`research_workflow.py`** — universe, 20-seed sweep, five-agent
  evaluation and TCA in about five seconds. The whole shape of a study in
  one file.
- **`claude_agent.py`** — drives an agent with the Claude API and scores it
  against the reference baselines, including whether it named the right
  factor for a move. Needs `pip install "pretium[claude]"` and an API key,
  and costs money per decision.

## Kept honest

These notebooks are executed as part of the release check, so a committed
notebook whose code no longer runs is a failing build rather than something
a reader discovers. Regenerate with:

```
jupyter nbconvert --to notebook --execute --inplace examples/0*.ipynb
```
