# FinRobot, evaluated in a controlled market

**How does a FinRobot agent respond to a sudden +200bps rate shock?**

FinRobot builds the financial AI agent. Tradefloor is the controlled
environment for measuring what that agent does when the world changes.

```
                    SHARED HISTORY
                         |
                      FinRobot
                         |
                    CHECKPOINT
                         |
                +--------+--------+
                |                 |
             CONTROL          +200BPS
              4.0%              6.0%
                |                 |
             FinRobot          FinRobot
                |                 |
                +--------+--------+
                         |
                      COMPARE
```

Twenty simulated days of shared history with a real FinRobot agent managing a
$50m book. Then a checkpoint, a fork into two worlds proved identical field by
field, and one changed variable: policy rate and corporate bond yield rise 200
basis points in one of them. Both worlds run twenty more days with the same
agent under the same mandate, and the run reports where they came apart.

This is an agent-EVALUATION experiment. One seed cannot say which arm was the
better investor. It can say exactly what the same agent holding the same book
did differently when one number changed. No historical dataset answers that
question, because history ran once.

| | |
|---|---|
| [`rate_shock.py`](rate_shock.py) | The experiment. Run it. |
| [`rate_shock.ipynb`](rate_shock.ipynb) | The same experiment, read rather than run |
| [`../../python/tradefloor/integrations/finrobot.py`](../../python/tradefloor/integrations/finrobot.py) | The adapter. The source of truth; the notebook imports it |
| [`../../tests/fixtures/finrobot/`](../../tests/fixtures/finrobot/) | The recorded run both of them replay |

## Running it

```bash
python examples/finrobot/rate_shock.py
```

That replays a genuine recorded FinRobot run. It needs no API key, no network
and no FinRobot install. The market is deterministic, so re-executing it
against the recorded agent responses reproduces the experiment exactly. About
fifteen seconds.

```bash
pip install "tradefloor[finrobot]"
export ANTHROPIC_API_KEY=...
python examples/finrobot/rate_shock.py --live
```

That calls the real thing. Sixty decisions -- twenty days of shared history
plus twenty in each arm, one decision a day -- so it costs sixty API calls and
takes a few minutes. Add `--record` to overwrite the replay fixture with the
run you just did.

`tradefloor[finrobot]` is a large extra and needs **Python 3.11 exactly**:
FinRobot declares `>=3.10, <3.12` and Tradefloor needs `>=3.11`. See the
comment on the extra in `pyproject.toml` for why each pin is there.

## What the adapter does, and refuses to do

FinRobot owns interpretation, the portfolio decision and a one-line rationale.
Tradefloor owns everything else: the market, the macro path, execution, the
book, fills, accounting, the checkpoint, the fork, the intervention and the
comparison.

Two boundaries are worth reading the adapter for.

**FinRobot never touches engine state.** It returns JSON. The adapter parses
it, validates every action against the listed universe and the supported
sides, refuses anything it cannot execute, and hands share deltas to the same
execution path every other agent uses.

**FinRobot is never shown the answer key.** The observation is an allowlist,
written out field by field. Fair value, the nine-way factor attribution of
every price move, each company's mispricing and the macro path the run has
not reached yet all stay on the Tradefloor side of the line.
`tests/test_finrobot.py` proves it twice: once by running the mapping against
an engine that raises if the forbidden surface is touched, and once by
computing the hidden values and scanning the text FinRobot receives for them.

The agent is never told which arm it is in.

## What comes out

Artifacts land in `artifacts/`, git-ignored because they are output:
the checkpoint, the comparison, the run manifest, the agent provenance, and
one file per arm holding every decision it took.

The provenance file is the one that matters for citing a result. Tradefloor's
market is deterministic; an LLM agent is not, and the two must not blur
together. It records the framework and version, the entry point, the provider,
the model, the generation parameters, the mandate version and the decision
cadence: everything a replay cannot reconstruct from the market alone.

---

FinRobot is a project of the [AI4Finance
Foundation](https://github.com/AI4Finance-Foundation/FinRobot), Apache-2.0.
This is a Tradefloor integration for FinRobot. AI4Finance neither maintains
nor endorses it. It is no part of FinRobot's own interface.

Everything reported here is ground truth about this simulated market. Treat it
as a controlled synthetic experiment. It predicts nothing about how real
securities would react to a real rate rise.
