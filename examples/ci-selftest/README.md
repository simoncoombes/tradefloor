# The evaluation workflow's self-test

Two agents that `.github/workflows/evaluate-selftest.yml` hands to the reusable
workflow `.github/workflows/evaluate.yml` on every pull request that touches
`tradefloor ci`, either workflow or this directory. Both are free baselines, so the
test needs no key and makes no model call.

| file | agent |
|---|---|
| `baseline.py` | buy-and-hold |
| `candidate.py` | one-day momentum, the top four names |

The workflow runs them twice on seeds 1 to 3, ten days, eight companies. The first
run sets `max-errors: "0"`, which holds. The second sets `max-drawdown: "0"`, which
every run breaches, with `fail-on-threshold: false`, and a third job checks that the
first passed and the second reported `passed=false`. Both runs build tradefloor from
the commit under test (`install-from-source: true`), because `tradefloor ci` is not
in a release yet.

`tests/test_ci.py::test_the_self_test_agents_hold_one_threshold_and_breach_the_other`
makes the same two runs locally.
