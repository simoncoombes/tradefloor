//! Economy — the price-relevant chain, ported from `src/lib/engine/economy.ts`.
//!
//! # Scope
//!
//! `economy.ts` is 2,221 lines; roughly 1,250 of them are on the price
//! path. What the price loop actually needs is four scalars —
//! `corporate_bond_yield` (falling back to `federal_funds_rate`),
//! `qe_pe_boost` and `vix` — but producing them requires most of the daily
//! macro step: the Taylor rule needs inflation and unemployment, which need
//! Phillips and Okun, which need the GDP cycle. So the CHAIN is in scope even
//! though the indicator fan-out is not.
//!
//! Deliberately **not** ported, each for a stated reason:
//!
//! | Not ported | Why |
//! |---|---|
//! | `computeDerivedIndicators` (70 fields) | Not on the price-critical path (`SURFACE.md` §0). Verified to consume **zero draws**, which is what makes the omission invisible to the shared stream. |
//! | `SECTOR_SENSITIVITIES`, `calculateSectorEconomicImpact` | Feeds only the discarded `economicImpact` factor. |
//! | `generateEconomicDataRelease`, `generateMonthlyEconomicShock`, `generateSurpriseShock` | Narrative generators. They DO draw, but they are not called from the daily chain — the caller invokes them separately, so they belong to WP5's assembly, not here. |
//! | Announcement / headline / guidance strings | Narrative. **The draws that select them are ported**; see [`central_bank`]. |
//!
//! # WASM is absent by construction
//!
//! Every `isWasmEconomyReady()` branch resolves to the JS side, per decisions
//! D1–D3 and D5 in `docs/rust-port/REMAINING-WORK.md`. Those branches are not
//! ported as runtime conditionals — there is no WASM here to be ready — so
//! the JS formula is inlined and the decision recorded at each site.
//!
//! `wasmCalculateVixTarget` is imported by `economy.ts` and never called: the
//! VIX model has been pure JS since "Cycle 71". An import-driven worklist
//! would invent work there.

pub mod central_bank;
pub mod cycle;
pub mod daily;
mod invariants;
pub mod state;

pub use central_bank::{update_central_bank, Decision, MeetingOutcome};
pub use cycle::{
    check_cycle_transition, cycle_hazard_params, get_cycle_transition_probability, weibull_hazard,
};
pub use daily::{update_economy_daily, DailyInputs};
pub use state::*;
