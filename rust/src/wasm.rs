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
//! reference implementation maintained beside it. The crate header puts it plainly:
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
//! Measured on 2026-08-24: one fixed simulation produces
//! `2b2f3141...042cfd8f5` under `wasm32-unknown-unknown` on node and under
//! native macos-arm64, identically. [`price_digest`] is how that is
//! re-checked rather than believed.
//!
//! Two residual looseness points, both handled rather than hoped about:
//!
//! - **NaN payload bits are not specified by wasm.** Hashing one would
//!   compare a pattern two engines may legally choose differently.
//!   `fixed_simulation_digest` refuses to hash a non-finite value, so that
//!   becomes a visible failure instead of a wrong "identical" verdict.
//! - **Relaxed SIMD is non-deterministic by design.** It is off by default
//!   and must stay off; do not add `-C target-feature=+relaxed-simd`.
//!
//! ## What `unknown-unknown` means for a consumer
//!
//! The triple is `<arch>-<vendor>-<os>`, and both unknowns are literal:
//! there is no operating system underneath. No filesystem, no sockets, no
//! clock, no threads, no environment, no process. A browser supplies host
//! services through JavaScript, not through a POSIX layer, which is why
//! this target and not `wasm32-wasip1`.
//!
//! The core is unaffected because it asks for none of them -- its only
//! dependencies are `libm` and `sha2`, and the single `std::fs` call in the
//! crate is `#[cfg(test)]`. That is not luck; it is what made this binding a
//! day's work rather than a port.
//!
//! One ergonomic consequence worth knowing: a Rust panic compiles to an
//! `unreachable` trap, which reaches JavaScript as
//! `RuntimeError: unreachable executed` with no message. This surface
//! returns `Result` rather than panicking, so a caller sees real errors --
//! but a consumer debugging their own integration will want
//! `console_error_panic_hook`, which is one dependency and one call, and is
//! deliberately not imposed here.

use wasm_bindgen::prelude::*;

use crate::engine::{Engine, SessionBuffer};
use crate::economy::{create_initial_economy_state, create_initial_central_bank_state, InitialEconomyOptions};
use crate::market::TickCompany;

/// Read a seed from JavaScript: a Number that is a safe integer, or a BigInt.
///
/// Seeds are `u64` from 0.8.5, and a JavaScript Number holds integers
/// exactly only up to `2^53 - 1` (`Number.MAX_SAFE_INTEGER`). So both are
/// taken. A Number keeps working for every seed a page already passes, and
/// a BigInt reaches the whole range, `0n` to `2n ** 64n - 1n`. A Number
/// above the safe range is refused rather than rounded: `2 ** 63 + 12345`
/// written as a Number is already `2 ** 63 + 12288` before this sees it,
/// and running that would be a different market from the one the caller
/// wrote down. A bare `u64` parameter would take a BigInt only and throw
/// on every Number, which is why this is not one.
fn seed_from_js(value: &JsValue, name: &str) -> Result<u64, JsError> {
    const RANGE: &str = "an integer from 0 to 2**64 - 1: a Number up to \
                         Number.MAX_SAFE_INTEGER, or a BigInt for the whole range";
    if let Some(n) = value.as_f64() {
        if n.fract() == 0.0 && (0.0..=9_007_199_254_740_991.0).contains(&n) {
            return Ok(n as u64);
        }
        return Err(JsError::new(&format!("{name} must be {RANGE}, got {n}")));
    }
    if value.is_bigint() {
        return u64::try_from(value.clone()).map_err(|_| JsError::new(&format!(
            "{name} must be {RANGE}, got a BigInt outside it")));
    }
    let kind = value.js_typeof().as_string().unwrap_or_default();
    Err(JsError::new(&format!("{name} must be {RANGE}, got a value of type {kind}")))
}

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
    ///
    /// `universe_seed` and `seed` are each a Number up to
    /// `Number.MAX_SAFE_INTEGER` or a BigInt up to `2n ** 64n - 1n`.
    #[wasm_bindgen(constructor)]
    pub fn new(size: usize,
               #[wasm_bindgen(unchecked_param_type = "number | bigint")]
               universe_seed: JsValue,
               #[wasm_bindgen(unchecked_param_type = "number | bigint")]
               seed: JsValue,
               preset: &str)
               -> Result<Sim, JsError> {
        let universe_seed = seed_from_js(&universe_seed, "universe_seed")?;
        let seed = seed_from_js(&seed, "seed")?;
        Sim::build(size, universe_seed, seed, preset).map_err(|e| JsError::new(&e))
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
        self.inner.model_fingerprint().to_string()
    }

    /// The macro state's VIX as published, the one number a trading page
    /// always wants: the state itself unless `vix_stress_premium` is set
    /// (`Engine::published_vix`).
    #[wasm_bindgen(getter)]
    pub fn vix(&self) -> f64 {
        self.inner.published_vix()
    }

    /// The price index's level (`index_level_listed`), on the prices as they
    /// stand: `undefined` with the switch off, which it is on every shipped
    /// preset. A page reaches the switch only through a preset, as it
    /// reaches every dial.
    #[wasm_bindgen(getter, js_name = indexLevel)]
    pub fn index_level(&self) -> Option<f64> {
        self.inner.index_level().map(|i| i.level)
    }

    /// The front contract of each listed futures family, in the order
    /// `Engine::contracts` lists the families (index, VIX, policy rate, term
    /// rate, oil), each quoted as `Engine::quote` quotes it. Empty on a
    /// model that lists none, which is every shipped preset.
    #[wasm_bindgen(getter, js_name = frontFutures)]
    pub fn front_futures(&self) -> Vec<FutureQuote> {
        front_future_quotes(&self.inner)
    }

    /// Advance one trading day: number it, open, trade, close, step the
    /// macro chain.
    ///
    /// The day is the core's numbered day, which calls
    /// [`Engine::set_current_day`] before the open as the Python binding's
    /// `run_days` does. The
    /// close is `Engine::close_day`, the same call the Python binding makes,
    /// so a day here and a day there are the same day. Through 0.10.x this
    /// opened the market without numbering the day, so on pt-v21 the
    /// earnings and dividend calendars never moved off day zero.
    #[wasm_bindgen(js_name = runDay)]
    pub fn run_day(&mut self, ticks: usize) -> Result<(), JsError> {
        if ticks == 0 {
            return Err(JsError::new("ticks must be greater than zero"));
        }
        self.inner.run_numbered_day(i64::from(self.day_count), ticks, &mut self.buffer);
        self.day_count += 1;
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

/// One front future's quote, as `Sim.frontFutures` lists it: a read of
/// [`Engine::quote`] and nothing decided here.
#[wasm_bindgen]
#[derive(Debug, Clone)]
pub struct FutureQuote {
    symbol: String,
    root: String,
    expiry: i64,
    price: f64,
    fair: f64,
    bid: Option<f64>,
    ask: Option<f64>,
    mark: Option<f64>,
    basis_bp: f64,
    sessions_to_expiry: f64,
    multiplier: f64,
}

#[wasm_bindgen]
impl FutureQuote {
    /// The canonical symbol, `IDX.F0119` for the index future expiring at
    /// session 119.
    #[wasm_bindgen(getter)]
    pub fn symbol(&self) -> String {
        self.symbol.clone()
    }

    /// The family's root: `IDX`, `VIX`, `FF`, `TR3` or `OIL`.
    #[wasm_bindgen(getter)]
    pub fn root(&self) -> String {
        self.root.clone()
    }

    /// The session it expires at, counted from 0.
    #[wasm_bindgen(getter)]
    pub fn expiry(&self) -> f64 {
        self.expiry as f64
    }

    /// The futures price now.
    #[wasm_bindgen(getter)]
    pub fn price(&self) -> f64 {
        self.price
    }

    /// The fair value the price is quoted around.
    #[wasm_bindgen(getter)]
    pub fn fair(&self) -> f64 {
        self.fair
    }

    /// The book's best bid, `undefined` on an empty side.
    #[wasm_bindgen(getter)]
    pub fn bid(&self) -> Option<f64> {
        self.bid
    }

    /// The book's best ask, `undefined` on an empty side.
    #[wasm_bindgen(getter)]
    pub fn ask(&self) -> Option<f64> {
        self.ask
    }

    /// The last close's settlement mark, `undefined` before the contract's
    /// first close.
    #[wasm_bindgen(getter)]
    pub fn mark(&self) -> Option<f64> {
        self.mark
    }

    /// `price - fair` in basis points.
    #[wasm_bindgen(getter, js_name = basisBp)]
    pub fn basis_bp(&self) -> f64 {
        self.basis_bp
    }

    /// Sessions from now to the expiry.
    #[wasm_bindgen(getter, js_name = sessionsToExpiry)]
    pub fn sessions_to_expiry(&self) -> f64 {
        self.sessions_to_expiry
    }

    /// Dollars per point.
    #[wasm_bindgen(getter)]
    pub fn multiplier(&self) -> f64 {
        self.multiplier
    }
}

/// The front contract of each listed family, quoted. A family whose
/// contracts carry no front flag yet has none here.
fn front_future_quotes(engine: &Engine) -> Vec<FutureQuote> {
    engine
        .contracts()
        .into_iter()
        .filter(|c| c.front)
        .filter_map(|c| {
            let q = engine.quote(&c.symbol)?;
            Some(FutureQuote {
                symbol: c.symbol,
                root: c.root,
                expiry: q.expiry,
                price: q.price,
                fair: q.fair,
                bid: q.bid,
                ask: q.ask,
                mark: q.mark,
                basis_bp: q.basis_bp,
                sessions_to_expiry: q.sessions_to_expiry,
                multiplier: q.multiplier,
            })
        })
        .collect()
}

/// The cross-binding determinism probe.
///
/// Delegates to [`crate::engine::fixed_simulation_digest`] so the browser
/// and the Python surface hash the same thing the same way. A digest
/// rebuilt independently on each side would be a fork of the check itself.
#[wasm_bindgen(js_name = priceDigest)]
pub fn price_digest(size: usize,
                    #[wasm_bindgen(unchecked_param_type = "number | bigint")]
                    universe_seed: JsValue,
                    #[wasm_bindgen(unchecked_param_type = "number | bigint")]
                    seed: JsValue,
                    days: usize, ticks: usize, preset: &str)
                    -> Result<String, JsError> {
    let universe_seed = seed_from_js(&universe_seed, "universe_seed")?;
    let seed = seed_from_js(&seed, "seed")?;
    crate::engine::fixed_simulation_digest(
        size, universe_seed, seed, days, ticks, preset)
        .ok_or_else(|| JsError::new(&format!("unknown preset {preset:?}")))
}

impl Sim {
    /// The constructor's work in plain Rust, with the seeds already read, so
    /// a native test can build a `Sim`: `JsError` and the `JsValue` seeds
    /// call into JavaScript, which panics off wasm.
    fn build(size: usize, universe_seed: u64, seed: u64, preset: &str) -> Result<Sim, String> {
        if size < 2 {
            return Err("a universe needs at least two instruments".to_string());
        }
        let params = crate::params::ModelParams::preset(preset).ok_or_else(|| {
            format!(
                "unknown preset {preset:?}; this build has {:?}",
                crate::params::ModelParams::preset_names()
            )
        })?;
        Ok(Sim::with_params(size, universe_seed, seed, params))
    }

    /// A `Sim` on any coefficient vector. Not exported: a page names a
    /// shipped preset, and only a native test builds one off a preset, to
    /// reach the switches no shipped preset sets yet.
    fn with_params(size: usize, universe_seed: u64, seed: u64, params: crate::params::ModelParams) -> Sim {
        let generated = crate::universe::random_universe(size, universe_seed);
        let tickers: Vec<String> =
            generated.iter().map(|g| g.ticker.clone()).collect();
        let companies: Vec<TickCompany> = generated
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect();

        Sim {
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
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `Sim` numbers its days, so pt-v21's calendars move: through 0.10.x
    /// it never did, every day was day zero, and a name whose report or
    /// ex-dividend date fell on it reported or went ex at every session
    /// while every other name never did.
    #[test]
    fn a_sim_numbers_its_days_and_its_calendars_advance() {
        let mut sim = Sim::build(24, 7, 11, "pt-v21").unwrap();
        let days = 130usize;
        let n = sim.tickers().len();
        let mut reports = vec![0usize; n];
        let mut report_days = std::collections::BTreeSet::new();
        let mut ex = vec![0usize; n];
        let mut ex_days = std::collections::BTreeSet::new();
        for d in 0..days {
            sim.run_day(390).unwrap();
            assert_eq!(sim.day(), d as u32 + 1);
            assert_eq!(sim.inner.elapsed_days(), d as i64);
            for (i, m) in sim.inner.earnings_moves().iter().enumerate() {
                if *m != 0.0 {
                    reports[i] += 1;
                    report_days.insert(d);
                }
            }
            for (i, x) in sim.inner.dividends_today().iter().enumerate() {
                if *x != 0.0 {
                    ex[i] += 1;
                    ex_days.insert(d);
                }
            }
        }
        // Every name reports once a quarter of 63 sessions: two or three
        // times in 130 sessions, on days spread over the quarter.
        assert!(reports.iter().all(|&r| (2..=3).contains(&r)), "{reports:?}");
        assert!(report_days.len() > 10, "{report_days:?}");
        // A dividend payer goes ex once a quarter as well, never daily.
        assert!(ex.iter().any(|&k| k > 0), "{ex:?}");
        assert!(ex.iter().all(|&k| k <= 3), "{ex:?}");
        assert!(ex_days.len() > 5, "{ex_days:?}");
    }

    /// Every shipped preset lists no futures and keeps no index, so a page
    /// sees `undefined` and an empty list; the getters change nothing.
    #[test]
    fn on_a_shipped_preset_there_is_no_index_and_no_future() {
        let mut sim = Sim::build(12, 7, 3, "pt-v21").unwrap();
        sim.run_days(2, 65).unwrap();
        let before = sim.inner.state_hash(2, false);
        assert_eq!(sim.index_level(), None);
        assert!(sim.front_futures().is_empty());
        assert_eq!(sim.inner.state_hash(2, false), before);
    }

    /// With the phase 1 switches on, the index and each family's front
    /// contract read through, as `Engine::index_level` and `Engine::quote`
    /// give them, and reading them moves nothing.
    #[test]
    fn with_the_switches_on_the_index_and_each_front_future_read_through() {
        let mut p = crate::params::PT_V21;
        for (name, value) in [
            ("index_level_listed", 1.0),
            ("vix_intraday_live", 1.0),
            ("forecast_horizon_sessions", 252.0),
            ("futures_index_listed", 1.0),
            ("futures_vix_listed", 1.0),
            ("futures_rates_listed", 1.0),
            ("futures_oil_listed", 1.0),
        ] {
            p = p.with_override(name, value).unwrap();
        }
        let mut sim = Sim::with_params(12, 7, 3, p);
        sim.run_days(2, 65).unwrap();
        let before = sim.inner.state_hash(2, false);
        let level = sim.index_level().unwrap();
        assert_eq!(level, sim.inner.index_level().unwrap().level);
        let fronts = sim.front_futures();
        let roots: Vec<String> = fronts.iter().map(|q| q.root()).collect();
        assert_eq!(roots, ["IDX", "VIX", "FF", "TR3", "OIL"]);
        for q in &fronts {
            let quoted = sim.inner.quote(&q.symbol()).unwrap();
            assert_eq!(q.price(), quoted.price);
            assert_eq!(q.fair(), quoted.fair);
            assert_eq!(q.mark(), quoted.mark);
            assert_eq!(q.expiry(), quoted.expiry as f64);
        }
        assert_eq!(sim.inner.state_hash(2, false), before);
    }

    /// The day the core numbers is the one the digest probe numbers, so the
    /// browser's `Sim` and `priceDigest` run the same loop.
    #[test]
    fn a_sim_and_the_digest_run_the_same_days() {
        let mut sim = Sim::build(12, 7, 3, "pt-v21").unwrap();
        sim.run_days(3, 65).unwrap();
        let mut engine = Sim::build(12, 7, 3, "pt-v21").unwrap().inner;
        let mut buffer = SessionBuffer::new();
        for day in 0..3 {
            engine.run_numbered_day(day, 65, &mut buffer);
        }
        assert_eq!(sim.inner.state_hash(3, false), engine.state_hash(3, false));
    }
}
