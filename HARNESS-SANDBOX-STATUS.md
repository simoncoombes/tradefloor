# fix/harness-sandbox: status

Paused on handover. Branched from `origin/release/0.8.5` at 2c8b32b.

## Done

- `python/tradefloor/sandbox.py` (new): `MarketView` (read-only market data),
  `HiddenState` (read-only hidden state for agents with `privileged = True`),
  `PortfolioView`, `SandboxError`, and `TamperGuard`, which compares
  `Engine.state_hash()`, fundamentals, recording counters and portfolio state
  before and after each call into agent code.
- `evaluate` (`harness.py`): sandboxed by default, `trusted_agents=True` opt-in,
  tamper check around `act` and `explain`. The scorecard gains `trusted`,
  `uses_hidden_state` and `tampered`.
- `rank` (`ranking.py`): passes `trusted_agents` through, leaves tampered
  agents out (`Ranking.tampered`, an EXCLUDED line in the report), and marks
  trusted and hidden-state rows.
- `World` and cohorts (`counterfactual.py`): sandboxed per-step observations,
  `trusted_agents` (carried by `fork`), `World.tampered`, flags in `summary()`
  only when set, and `manifest()` records `agent_access`
  (`RunManifest.of(agent_access=...)`, only written when not the default).
- `tca.analyse`: sandboxed; `trusted_agents` opt-in; raises on tampering.
- The Oracle and the `oracle` spec signal read through `obs.hidden`. The
  `_DailyCadence` wrapper passes `hidden` on.
- MCP `_scorecard_row` adds `uses_hidden_state` and `tampered` only when true.
- gym: the policy gets arrays only. `env.engine` belongs to the trainer. No
  change needed. `run_many` has no agents.
- Docs: README section "The agent's view", a `docs/MODEL.md` section "The
  agent's observation", a CHANGELOG detail section under 0.8.5 below the marker
  with a What breaks note, and integration docstrings updated.
- `tests/test_sandbox.py`, 48 tests: both audit exploits refused by default and
  flagged under the opt-in, a write made around the view still flagged, the
  portfolio is read-only, the hidden-state capability works, reference agents
  score identically sandboxed or trusted (pt-v19 and pt-v20), the view surface
  and its refusals, World, tca, rank, and an unchanged integrations payload.

## Test state

- `tests/test_sandbox.py`: 48 passed. Known-answer, prose and README-link
  tests pass.
- `python tests/known_answer.py` digests are identical to the base commit:
  sim 4528d93a, meta 8804ef0e, bonds b8798418, presets f131be87.
- Targeted run of test_harness, test_baselines, test_spec, test_ranking,
  test_tca, test_integrations and test_callable: 314 passed, and the 1 failure
  (callable replay) also fails on the base commit.
- The full suite on the BASE commit gives 21 failed, 4063 passed. The 21
  failures are all recorded-fixture replays refused because pt-v20's dials moved
  (garch_beta, volume_move_response), plus the pt-v20 preset record.
- NOT YET RUN: the full Python suite on this branch, and `cargo test`. The
  cargo run was still going at the pause. No Rust files changed.

## Next steps

1. Run the full suite on this branch and compare it with the base's 21
   failures. Expect no new ones. Watch for pinned scorecard `as_dict` keys,
   pinned rank `as_dict` or report text, and examples under
   `TRADEFLOOR_SLOW_TESTS=1`.
2. Run `cargo test --manifest-path rust/Cargo.toml`.
3. Re-run the audit probe
   (`scratchpad/audit/probe_harness.py`): sandboxed, peek and mutate end with
   SandboxError errors and no gain. Under `trusted_agents=True`, mutate is
   `tampered`.
4. Delete this file before merging.
