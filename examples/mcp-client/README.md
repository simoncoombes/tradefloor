# An MCP client in Python

`client.py` talks to tradefloor's MCP server the way an AI client does, over stdio,
from a plain Python script. It is the starter for writing your own client, or for
checking the server before wiring it into an AI application.

```
pip install "tradefloor[mcp]"
python examples/mcp-client/client.py
```

It needs no key, no account and no network: the server is a subprocess on your
machine. It takes about 18 seconds of CPU on an Apple M-series laptop, most of it
building the pt-v22 market.

## The steps

1. Starts the server with `python -m tradefloor mcp`, the same server
   `tradefloor-mcp` runs, and lists its tools.
2. Calls `open_session` on a seeded market of 8 companies and runs one day with
   `session_step`.
3. Calls `session_fork`, which copies the session as it stands.
4. Runs one more day in both: the control sends no order, and the branch buys
   2,000 shares of `AAA`.
5. Prints `AAA` in both, and closes both sessions.

## Expected output

On tradefloor 0.11.0, preset pt-v22:

```
22 tools: describe_simulator, check_envelope, validate_strategy, evaluate_strategies, rank_strategies, list_scenarios, build_scenario, run_stress_scenario, explain_price_move, explain, build_universe, start_job, check_job, open_session, session_step, session_state, list_contracts, quote_contracts, option_chain, session_fork, session_rewind, close_session

seed 7, 8 companies, forked after one day
AAA at the open                     200.4525
AAA after two days, control         200.9096
AAA after two days, bought 2,000    201.0034
what the order moved it                +4.67 bp
```

The two branches share the first day exactly, so the gap on the second day is the
order and nothing else.

## Configuration

`SERVER` at the top of `client.py` is the whole configuration: a command and its
arguments. An MCP client that launches stdio servers takes the same two values. Claude
Code's version of it is `claude mcp add tradefloor -- tradefloor-mcp`.

`tests/test_mcp_client_example.py` runs this script and checks the printed values.
