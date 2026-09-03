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

use crate::engine::{Engine, SessionBuffer, SessionRequest};
use crate::economy::{create_initial_economy_state, create_initial_central_bank_state, InitialEconomyOptions};
use crate::market::GameTime;
use crate::market::TickCompany;
use crate::rng::{stream, DrawKind};

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

    // ── Draw surgery ─────────────────────────────────────────────────

    /// An independent copy of the current state: same prices, same macro
    /// state, same generator positions, same installed patches.
    ///
    /// One clone per call rather than a batch -- `Vec<Sim>` is not a shape
    /// wasm-bindgen is trusted to return here (see the design note this
    /// binding set comes from), and a page forks one arm at a time.
    ///
    /// The new `Sim` gets a fresh, empty session buffer rather than a
    /// copy of the parent's: the buffer holds the LAST session's per-tick
    /// output for reading, not simulation state, so nothing about the
    /// copy's own future trajectory depends on what is in it.
    #[wasm_bindgen(js_name = fork)]
    pub fn fork(&self) -> Sim {
        Sim {
            inner: self.inner.clone(),
            buffer: SessionBuffer::new(),
            tickers: self.tickers.clone(),
            day_count: self.day_count,
        }
    }

    /// Install substitutions: flat `(stream, kind, index, value)`
    /// quadruples, `entries.len()` a multiple of four.
    ///
    /// `stream` is one of the seven ids [`crate::rng::stream`] declares
    /// (`0..=6`); `kind` is `0.0` for a uniform or `1.0` for a normal --
    /// [`crate::rng::DrawKind`]'s own discriminants, so the page and this
    /// binding read the same two numbers the same way. The generator
    /// still advances at every patched address; only the value the
    /// consumer receives there changes --
    /// [`crate::engine::Engine::patch_draw`] documents the contract this
    /// wraps.
    ///
    /// A quadruple naming a negative or out-of-range stream or kind, or a
    /// negative index, is refused rather than silently dropped or
    /// clamped to the nearest valid one, which would install a patch at
    /// an address the caller never named. This is the one binding on
    /// this surface that returns `Result` rather than the plain value
    /// the design note's signature shows: every OTHER binding here that
    /// can fail already does, for the reason this file's own module docs
    /// give -- a Rust panic reaches JavaScript as an unlabelled trap --
    /// and a malformed flat array is exactly the input a caller can send
    /// by mistake.
    #[wasm_bindgen(js_name = patchDraws)]
    pub fn patch_draws(&mut self, entries: &[f64]) -> Result<(), JsError> {
        let parsed = parse_patch_entries(entries).map_err(|e| JsError::new(&e))?;
        for (stream_id, kind, index, value) in parsed {
            self.inner.patch_draw(stream_id, kind, index, value);
        }
        Ok(())
    }

    /// The installed overlay, in the same flat `(stream, kind, index,
    /// value)` form `patchDraws` takes -- installing `drawPatches()`'s
    /// result on an empty `Sim` reproduces the overlay.
    ///
    /// Ordered by stream id, then by kind and index within a stream (the
    /// order [`crate::rng::DrawOverlay`]'s table already keeps), so the
    /// result is one exact list a test can pin rather than a set a
    /// caller must sort first.
    #[wasm_bindgen(js_name = drawPatches)]
    pub fn draw_patches(&self) -> Vec<f64> {
        let mut out = Vec::new();
        for id in 0..=stream::VOLUME_IDIO {
            if let Some(overlay) = self.inner.draw_overlay(id) {
                for (&(kind, index), &value) in &overlay.table {
                    out.push(id as f64);
                    out.push(kind as u8 as f64);
                    out.push(index as f64);
                    out.push(value);
                }
            }
        }
        out
    }

    /// `(uniforms, normals)` taken so far on each stream, flattened in
    /// stream-id order `0..=6`: fourteen values, a pair per stream, the
    /// address the next draw of each kind on that stream would take.
    #[wasm_bindgen(js_name = streamPositions)]
    pub fn stream_positions(&self) -> Vec<f64> {
        self.inner
            .stream_positions()
            .iter()
            .flat_map(|&(u, n)| [u as f64, n as f64])
            .collect()
    }

    /// The jumps stream's uniform index for the market jump of `day`.
    ///
    /// The arithmetic `python/tradefloor/counterfactual.py`'s
    /// `World._jump_address` uses: the stream's current uniform
    /// position, plus `1 + companies` per day from here to `day` -- one
    /// market uniform and one per active company, the market's first,
    /// each day the jumps stream sees. Valid while the active roster does
    /// not change between here and `day`; nothing on this wasm surface
    /// lists or delists a company.
    ///
    /// `day` at or before the days already run saturates the "how many
    /// days ahead" term at zero rather than underflowing a `u64`
    /// subtraction, since this binding's frozen signature returns a
    /// plain `f64` rather than a `Result`: a caller asking about a day
    /// already closed gets the stream's current position back rather
    /// than a trap or a wrapped-around, meaninglessly large number. It is
    /// the caller's job to ask about a day at or ahead of [`Sim::day`],
    /// the way the page does.
    #[wasm_bindgen(js_name = jumpAddress)]
    pub fn jump_address(&self, day: u32) -> f64 {
        let (uniforms, _normals) =
            self.inner.stream_positions()[stream::JUMPS as usize];
        let per_day = 1 + self.tickers.len() as u64;
        let ahead = day.saturating_sub(self.day_count) as u64;
        (uniforms + ahead * per_day) as f64
    }
}

/// Parse and validate a flat `patchDraws` argument into `(stream, kind,
/// index, value)` quadruples.
///
/// Pure Rust, no wasm-bindgen type in its signature, deliberately: a
/// `JsError` is itself a handle onto a JS `Error` object, so
/// `JsError::new` calls into an imported JS function and panics on a
/// non-wasm target with "cannot call wasm-bindgen imported functions on
/// non-wasm targets" -- which took the first version of this file's own
/// test suite by surprise, on the three tests that exercised the error
/// path. Keeping validation here, and turning its `Err(String)` into a
/// `JsError` only at [`Sim::patch_draws`]'s boundary, is what lets those
/// three run under a plain host `cargo test` alongside the rest.
/// A quadruple's stream, kind or index field, checked before the `as`
/// cast that follows it rather than after.
///
/// `f64 as u32` / `f64 as u64` saturates rather than panicking or
/// wrapping: a negative value casts to `0`, and so does NaN. Casting
/// first and range-checking the result, this function's first version,
/// let a negative stream, kind or index land at `0` -- MARKET, Uniform
/// or index `0` -- silently, exactly the "clamped to the nearest valid
/// one" outcome `patchDraws`'s own doc comment says this binding
/// refuses. Found by review, measured directly against the real
/// `Sim::patch_draws`, not argued from reading the cast.
///
/// `!(value >= 0.0)` rather than `value < 0.0`: the negated form also
/// rejects NaN, the same idiom `lib.rs` documents for this crate's
/// guards generally.
fn non_negative(value: f64, field: &str) -> Result<f64, String> {
    if !(value >= 0.0) {
        return Err(format!(
            "{field} must be non-negative, got {value}"));
    }
    Ok(value)
}

fn parse_patch_entries(entries: &[f64]) -> Result<Vec<(u32, DrawKind, u64, f64)>, String> {
    if entries.len() % 4 != 0 {
        return Err(format!(
            "patchDraws takes (stream, kind, index, value) quadruples; \
             got {} values, not a multiple of four",
            entries.len()));
    }
    let mut out = Vec::with_capacity(entries.len() / 4);
    for quad in entries.chunks_exact(4) {
        let stream_id = non_negative(quad[0], "stream")? as u32;
        if stream_id > stream::VOLUME_IDIO {
            return Err(format!(
                "unknown stream id {stream_id}; expected 0..={}",
                stream::VOLUME_IDIO));
        }
        let kind = match non_negative(quad[1], "kind")? as u32 {
            0 => DrawKind::Uniform,
            1 => DrawKind::Normal,
            other => return Err(format!(
                "unknown draw kind {other}; 0 (uniform) or 1 (normal)")),
        };
        let index = non_negative(quad[2], "index")? as u64;
        out.push((stream_id, kind, index, quad[3]));
    }
    Ok(out)
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

#[cfg(test)]
mod tests {
    use super::*;

    // A small, fast roster and preset shared by most tests here: big
    // enough that `jump_address`'s `1 + companies` term is not
    // accidentally 1, small enough that a handful of days costs nothing.
    // `wasm-bindgen`'s macro expands to plain Rust on any target, so
    // these run under the host `cargo test --features wasm` and need no
    // wasm32 target or JS runtime -- they exercise `Sim` exactly as a
    // page would, minus the FFI marshalling.
    fn sim() -> Sim {
        Sim::new(5, 11, 3, "pt-v3").expect("a valid roster and preset")
    }

    #[test]
    fn fresh_sim_has_no_patches_and_zero_positions() {
        let sim = sim();
        assert!(sim.draw_patches().is_empty());
        assert_eq!(sim.stream_positions(), vec![0.0; 14]);
    }

    #[test]
    fn patch_draws_round_trips_through_draw_patches() {
        let mut sim = sim();
        // Two streams and both kinds, so the round trip cannot pass by
        // one field happening to be right: market/uniform/0, then
        // jumps/normal/2, in stream-id order -- the order draw_patches
        // documents.
        let entries = vec![
            stream::MARKET as f64, 0.0, 0.0, 0.25,
            stream::JUMPS as f64, 1.0, 2.0, -1.5,
        ];
        sim.patch_draws(&entries).expect("both quadruples are well-formed");
        assert_eq!(sim.draw_patches(), entries);
    }

    // The three tests below exercise `parse_patch_entries`, the pure
    // function `patch_draws` validates through, rather than
    // `Sim::patch_draws` itself: `JsError::new` calls into an imported JS
    // function and panics with "cannot call wasm-bindgen imported
    // functions on non-wasm targets" under a plain host `cargo test`, so
    // a test that needs `patch_draws` to actually RETURN an `Err` cannot
    // run here. `patch_draws_round_trips_through_draw_patches` above
    // already proves the success path through the real method, and
    // `patch_draws` calls `parse_patch_entries` in full before installing
    // anything on `self.inner`, so a parse failure here means the real
    // method installs nothing either.

    #[test]
    fn patch_draws_refuses_a_length_not_a_multiple_of_four() {
        let err = parse_patch_entries(&[0.0, 0.0, 0.0]);
        assert!(err.is_err(), "three values is not a whole quadruple");
    }

    #[test]
    fn patch_draws_refuses_an_unknown_stream() {
        // 7 is one past volume_idio (6), the highest valid stream id.
        let err = parse_patch_entries(&[7.0, 0.0, 0.0, 0.5]);
        assert!(err.is_err(), "stream id 7 does not name a stream");
    }

    #[test]
    fn patch_draws_refuses_an_unknown_kind() {
        let err = parse_patch_entries(&[stream::MARKET as f64, 2.0, 0.0, 0.5]);
        assert!(err.is_err(), "2 names neither uniform (0) nor normal (1)");
    }

    // `f64 as u32` / `f64 as u64` saturates a negative value to 0 rather
    // than panicking, and the first version of parse_patch_entries cast
    // before checking, so a negative stream, kind or index silently
    // landed at 0 -- MARKET, Uniform or index 0 -- instead of being
    // refused. Found by review; these three pin the fix, one field at a
    // time, so a regression in any one of them is unambiguous about
    // which check broke.

    #[test]
    fn patch_draws_refuses_a_negative_stream() {
        let err = parse_patch_entries(&[-1.0, 0.0, 0.0, 0.5]);
        assert!(err.is_err(),
                "-1 must not silently become stream 0 (MARKET)");
    }

    #[test]
    fn patch_draws_refuses_a_negative_kind() {
        let err = parse_patch_entries(&[stream::MARKET as f64, -1.0, 0.0, 0.5]);
        assert!(err.is_err(),
                "-1 must not silently become kind 0 (Uniform)");
    }

    #[test]
    fn patch_draws_refuses_a_negative_index() {
        let err = parse_patch_entries(&[stream::MARKET as f64, 0.0, -5.0, 0.5]);
        assert!(err.is_err(), "-5 must not silently become index 0");
    }

    #[test]
    fn patch_draws_refuses_nan_in_stream_kind_or_index() {
        // NaN also saturates to 0 under `as u32`/`as u64`, and NaN
        // compared with `>=` is false either way its operands are
        // written, so the same non_negative guard catches it without a
        // separate is_nan check -- worth pinning as its own case since
        // it is a different reason to reach the same cast.
        let nan = f64::NAN;
        assert!(parse_patch_entries(&[nan, 0.0, 0.0, 0.5]).is_err());
        assert!(parse_patch_entries(&[stream::MARKET as f64, nan, 0.0, 0.5]).is_err());
        assert!(parse_patch_entries(&[stream::MARKET as f64, 0.0, nan, 0.5]).is_err());
    }

    #[test]
    fn a_fork_is_independent() {
        let mut parent = sim();
        let mut child = parent.fork();
        child.patch_draws(&[stream::JUMPS as f64, 0.0, 0.0, 1.0])
            .expect("a well-formed quadruple");
        assert!(parent.draw_patches().is_empty(),
                "patching the fork installed an overlay on the parent");
        assert_eq!(child.draw_patches().len(), 4);

        // And the other direction: a patch installed on the parent AFTER
        // the fork was taken does not reach the fork either.
        parent.patch_draws(&[stream::MARKET as f64, 0.0, 0.0, 0.1])
            .expect("a well-formed quadruple");
        assert_eq!(child.draw_patches().len(), 4,
                   "the fork must be unaffected by the parent's later patch");
    }

    #[test]
    fn jump_address_predicts_the_stream_position_it_names() {
        let mut sim = sim();
        let day = 3u32;
        let addr = sim.jump_address(day);
        sim.run_days(day as usize, 10).expect("ticks > 0");
        // Stream id JUMPS's uniform half sits at the even slot of its
        // pair in the flattened streamPositions layout.
        let jumps_uniforms = sim.stream_positions()[(stream::JUMPS as usize) * 2];
        assert_eq!(addr, jumps_uniforms,
                   "jumpAddress(day) must equal the position the jumps \
                    stream actually reaches after running that many days");
    }

    #[test]
    fn jump_address_of_a_day_already_run_does_not_panic() {
        let mut sim = sim();
        sim.run_days(2, 10).expect("ticks > 0");
        // day 0 is behind sim.day (2): saturating_sub floors the "how
        // many days ahead" term at zero instead of wrapping a u64
        // subtraction into a huge, meaningless address.
        let addr = sim.jump_address(0);
        assert!(addr.is_finite());
        let jumps_uniforms = sim.stream_positions()[(stream::JUMPS as usize) * 2];
        assert_eq!(addr, jumps_uniforms,
                   "a day already run saturates to the stream's current \
                    position rather than a wrapped-around one");
    }

    #[test]
    fn patching_a_market_normal_changes_the_trajectory() {
        // market_factor_z is the market stream's first normal, drawn
        // unconditionally on every tick (rng.rs Site::MarketFactorZ) --
        // unlike the jump uniform's comparison against an intensity, so
        // this cannot land on a day nothing was going to do anyway.
        let mut control = sim();
        let mut shock = control.fork();
        shock.patch_draws(&[stream::MARKET as f64, 1.0, 0.0, 6.0])
            .expect("a well-formed quadruple");
        control.run_days(1, 10).expect("ticks > 0");
        shock.run_days(1, 10).expect("ticks > 0");
        assert_ne!(control.prices(), shock.prices(),
                   "patching the market stream's first normal to 6.0 must \
                    move at least one price");
    }

    #[test]
    fn patching_the_jump_uniform_does_not_change_the_draw_count() {
        // "The generator still advances at every patched address; only
        // the value the consumer receives changes" (Engine::patch_draw).
        // A patch that also shifted the draw count would desynchronise
        // every later address from the one a caller computed for it.
        let mut control = sim();
        let mut shock = control.fork();
        let addr = shock.jump_address(2);
        shock.patch_draws(&[stream::JUMPS as f64, 0.0, addr, 1.0])
            .expect("a well-formed quadruple");
        control.run_days(3, 10).expect("ticks > 0");
        shock.run_days(3, 10).expect("ticks > 0");
        assert_eq!(control.stream_positions(), shock.stream_positions(),
                   "installing a patch must not change how many draws a \
                    stream takes");
    }

    #[test]
    fn nothing_in_this_file_can_mutate_the_roster() {
        // jumpAddress's "1 + companies" term (self.tickers.len(), cached
        // at construction and never touched again) assumes the active
        // roster does not change between the call and the target day.
        // Measured rather than argued: this greps the file rather than
        // trusting the binding list to stay that way, the same
        // discipline rng.rs's mathx enforcement uses. Engine::
        // add_company and Engine::remove_company exist and are what
        // python_engine.rs's list_instrument/delist call; nothing here
        // calls either, and there is no other way to reach them from a
        // Sim, so the roster is fixed for its whole life. If a future
        // binding changes that, this fails and points at jumpAddress.
        //
        // Scoped to the code above this test module: include_str! pulls
        // in this very assertion's own text, which contains the literal
        // string being searched for and would make the check pass on
        // itself. The first version of this test did exactly that and
        // failed on its own source, caught immediately by running it.
        let source = include_str!("wasm.rs")
            .split("mod tests {")
            .next()
            .expect("this file has a test module");
        assert!(!source.contains(".add_company("),
                "a call to add_company appeared in this file; \
                 jumpAddress's roster-is-fixed assumption no longer holds");
        assert!(!source.contains(".remove_company("),
                "a call to remove_company appeared in this file; \
                 jumpAddress's roster-is-fixed assumption no longer holds");
    }

    #[test]
    fn a_forks_fresh_session_buffer_does_not_change_the_next_days_prices() {
        // fork() gives the copy an empty SessionBuffer rather than a
        // clone of the parent's, on the claim that the buffer is
        // write-only output scratch space: run_session only ever writes
        // to it (buffer.write_tick, after resizing to the CURRENT
        // request's shape) and close_day does not receive it as a
        // parameter at all, so nothing reads it as an input.
        //
        // Measured across a day boundary rather than argued from
        // reading engine.rs: continuing THE SAME Sim into day 2 (whose
        // buffer already holds day 1's output, non-empty and the right
        // shape already) must produce identical prices to forking that
        // Sim right after day 1 (a fresh, empty buffer) and running day
        // 2 on the fork. Both start from identical engine state; the
        // buffer's prior contents are the only difference between them.
        let mut warm = sim();
        warm.run_day(10).expect("ticks > 0"); // day 1: warm's buffer is now non-empty
        let mut fresh = warm.fork();           // same engine state, fresh buffer

        warm.run_day(10).expect("ticks > 0");  // day 2 on the warm buffer
        fresh.run_day(10).expect("ticks > 0"); // day 2 on the fresh buffer

        assert_eq!(warm.prices(), fresh.prices(),
                   "a fork's fresh session buffer changed day 2's prices \
                    versus continuing the parent's already-used one");
    }
}
