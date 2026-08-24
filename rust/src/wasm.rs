//! The browser surface: the same engine, compiled to WebAssembly.
//!
//! `lib.rs` opens by saying the price model is "compiled once and consumed
//! twice: as WebAssembly inside a browser, and as a Python extension module
//! for backtesting". This is the first half, and it is deliberately thin.
//!
//! ## What is NOT here, and why that is the point
//!
//! No day loop. No initial-state mapping. No macro step. Every one of those
//! is a modelling decision, and every one lives in the core --
//! [`crate::engine::Engine::close_day`],
//! [`crate::universe::InstrumentInit::to_tick_company`] — precisely so that
//! this file cannot make them differently from the Python binding.
//!
//! That constraint is the reason this module exists at all rather than a
//! TypeScript port maintained beside it. The crate header puts it plainly:
//! two models that quietly disagree about the same prices are worse than
//! having no second binding. A wasm binding that re-implemented the day loop
//! would be that same fork one layer down, and the drift would be invisible
//! until a whole simulated market had come apart.
//!
//! So the rule for anything added here: if it decides something about the
//! market, it belongs in the core and this file calls it.
//!
//! ## Determinism
//!
//! WebAssembly specifies IEEE-754 exactly for add, subtract, multiply,
//! divide and square root, and this crate ships its own `exp`, `log`, `sin`
//! and `cos` rather than calling the platform libm — which is the usual
//! reason a browser build disagrees with a native one. Between them the main
//! sources of divergence are removed.
//!
//! That is an argument, not a measurement. [`price_digest`] exists so a
//! browser can produce a number that is comparable with the native build's,
//! and the claim can be checked rather than believed.

use wasm_bindgen::prelude::*;

use crate::engine::{Engine, SessionBuffer, SessionRequest};
use crate::economy::{create_initial_economy_state, create_initial_central_bank_state, InitialEconomyOptions};
use crate::market::GameTime;
use crate::market::TickCompany;

/// The library version, so a page can report what it is running.
#[wasm_bindgen]
pub fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Every preset name this build carries.
#[wasm_bindgen]
pub fn preset_names() -> Vec<String> {
    crate::params::ModelParams::preset_names()
        .iter()
        .map(|s| (*s).to_string())
        .collect()
}

/// One simulated market.
///
/// Construction mirrors the Python surface: a generated roster from
/// `(size, universe_seed)` and a simulation seed that is independent of it,
/// so "same universe, different draws" is expressible — the standard shape
/// for variance estimation, and the one a daily-challenge page wants when it
/// holds the roster fixed and varies the market.
#[wasm_bindgen]
pub struct Sim {
    inner: Engine,
    buffer: SessionBuffer,
    tickers: Vec<String>,
    day_count: u32,
}

#[wasm_bindgen]
impl Sim {
    /// Build a market of `size` generated instruments.
    ///
    /// `preset` names a shipped coefficient set; an unknown name is an error
    /// rather than a silent fallback, because a market running coefficients
    /// nobody chose would still report a preset's name.
    #[wasm_bindgen(constructor)]
    pub fn new(size: usize, universe_seed: u32, seed: u32, preset: &str)
               -> Result<Sim, JsError> {
        if size < 2 {
            return Err(JsError::new(
                "a universe needs at least two instruments"));
        }
        let params = crate::params::ModelParams::preset(preset).ok_or_else(
            || JsError::new(&format!(
                "unknown preset {preset:?}; this build has {:?}",
                crate::params::ModelParams::preset_names())))?;

        let generated = crate::universe::random_universe(size, universe_seed);
        let tickers: Vec<String> =
            generated.iter().map(|g| g.ticker.clone()).collect();
        let companies: Vec<TickCompany> = generated
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect();

        Ok(Sim {
            inner: Engine::with_params(
                seed,
                companies,
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
                params,
            ),
            buffer: SessionBuffer::new(),
            tickers,
            day_count: 0,
        })
    }

    /// Roster order, which is contractual: the engine draws in index order,
    /// so a reordered universe is a different market from the same seed.
    #[wasm_bindgen(getter)]
    pub fn tickers(&self) -> Vec<String> {
        self.tickers.clone()
    }

    /// Current price per instrument, in roster order.
    #[wasm_bindgen(getter)]
    pub fn prices(&self) -> Vec<f64> {
        self.inner.prices()
    }

    /// Days closed so far.
    #[wasm_bindgen(getter)]
    pub fn day(&self) -> u32 {
        self.day_count
    }

    /// The coefficient set's fingerprint — a sha256 over the canonical
    /// serialisation, so a page can cite exactly what it ran.
    #[wasm_bindgen(getter, js_name = modelFingerprint)]
    pub fn model_fingerprint(&self) -> String {
        self.inner.params().fingerprint()
    }

    /// The macro state's VIX, the one number a trading page always wants.
    #[wasm_bindgen(getter)]
    pub fn vix(&self) -> f64 {
        self.inner.economy().vix
    }

    /// Advance one trading day: open, trade, close, step the macro chain.
    ///
    /// The close is `Engine::close_day` — the same call the Python binding
    /// makes — so a day here and a day there are the same day.
    #[wasm_bindgen(js_name = runDay)]
    pub fn run_day(&mut self, ticks: usize) -> Result<(), JsError> {
        if ticks == 0 {
            return Err(JsError::new("ticks must be greater than zero"));
        }
        self.inner.open_market();
        self.inner.run_session(
            &SessionRequest {
                // The reference's opening bell. `day_of_week: 3` matches the
                // Python surface's default, so the same (size, seed, days)
                // is the same market on both.
                start: GameTime { hour: 9, minute: 30, day_of_week: 3 },
                ticks,
                volatility_multiplier: 1.0,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
                // The close is `Engine::close_day` below, which also steps
                // the macro chain. Letting the session close would settle
                // the day without that step.
                close_at_end: false,
                // The day is opened above, once. `reopen: true` would reset
                // the attribution accumulator and re-anchor the daily open
                // mid-day, which is what agent stepping wants and a
                // one-session day does not.
                reopen: false,
                daily_innovations: &[],
                sector_base_variances: &[],
                stop: None,
            },
            &mut self.buffer,
        );
        self.day_count += 1;
        self.inner.close_day(i64::from(self.day_count));
        Ok(())
    }

    /// Advance `days` trading days.
    #[wasm_bindgen(js_name = runDays)]
    pub fn run_days(&mut self, days: usize, ticks: usize)
                    -> Result<(), JsError> {
        for _ in 0..days {
            self.run_day(ticks)?;
        }
        Ok(())
    }
}

/// The cross-binding determinism probe.
///
/// Delegates to [`crate::engine::fixed_simulation_digest`] so the browser
/// and the Python surface hash the same thing the same way. A digest
/// rebuilt independently on each side would be a fork of the check itself.
#[wasm_bindgen(js_name = priceDigest)]
pub fn price_digest(size: usize, universe_seed: u32, seed: u32,
                    days: usize, ticks: usize, preset: &str)
                    -> Result<String, JsError> {
    crate::engine::fixed_simulation_digest(
        size, universe_seed, seed, days, ticks, preset)
        .ok_or_else(|| JsError::new(&format!("unknown preset {preset:?}")))
}
