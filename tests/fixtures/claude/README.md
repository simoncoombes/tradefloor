# Example 08's recording

`example-08.json` is a live Claude run of `examples/08-claude-agent.py`:
twenty days, one answer a day, on the market the example builds. The example
replays it by default, and `tests/test_examples.py` replays it and checks its
`meta`.

It is not a framework-adapter transcript. Its answers are the example's own
`Decision` (weights, driver, reasoning), so the shared checks for the
adapter recordings in `tests/test_integrations.py` do not apply to it.

Make or remake it, which calls Claude twenty times, from the repository root:

```
TRADEFLOOR_LIVE_EXAMPLES=1 ANTHROPIC_API_KEY=... python examples/08-claude-agent.py --record
```
