//! The engine — WP5. Where the port becomes something you can run.
//!
//! # What this owns, and what it deliberately does not
//!
//! It owns the seeded generator, the per-company price state, the economy and
//! the central bank. That is all.
//!
//! Events, AI decisions, whale trades, corporate actions and earnings stay in
//! the embedder and cross this boundary as **data** — news impulses and
//! impact-queue entries, not behaviour. That split is not a simplification;
//! it is what makes the boundary tractable. Whale events, for instance, reach
//! the price only through the generic news channels, so there is nothing to
//! port for them.
//!
//! # The stream is shared, and that is the hard part
//!
//! Every one of those TypeScript-side subsystems draws from the **same** MAIN
//! PCG32 stream this engine owns. So an embedder cannot simply run its own
//! generator alongside: the two would interleave differently and every price
//! would diverge. [`Engine::draw_uniform`] and [`Engine::draw_normal`] exist
//! for exactly that reason — the embedder asks THIS engine for its draws, so
//! there is one stream with one position.
//!
//! That also means Box-Muller's spare cache is shared. An embedder that takes
//! an odd number of normals leaves a spare waiting that this engine's next
//! normal will consume, which is correct and is why the cache lives in
//! `GameRng` rather than being reset per call.
//!
//! # Columnar access
//!
//! State is held internally as `Vec<TickCompany>` — array-of-structs — because
//! that is what WP4's gated tick operates on, and re-shaping it would
//! invalidate 18,720 verified values for a layout preference.
//!
//! The FFI surface is columnar instead ([`Engine::prices`],
//! [`Engine::write_prices`]): one contiguous `f64` slice per field, which
//! crosses a WASM boundary as a single view rather than 108 marshalled
//! objects. Conversion happens when the embedder reads, not per tick, so the
//! cost is paid once per boundary crossing rather than 390 times a day.

use crate::economy::{
    check_cycle_transition, update_central_bank, update_economy_daily, CentralBankState,
    DailyInputs, EconomicShock, EconomyState,
};
use crate::market::{
    close_day, get_market_status, intraday_fraction, reset_daily_prices, simulate_market_tick,
    AvgVolumePolicy, CloseInputs, GameTime, MarketStatus, NewsEvent, NewsImpactEntry, OrderVolume,
    TickCompany, TickInputs,
};
use crate::rng::{GameRng, Rng};

/// The main stream's sequence, from `rng.ts:32`. Not 0 and not 1 — both are
/// different streams from the same seed, and picking the wrong one produces a
/// plausible market that matches nothing.
pub const MAIN_STREAM: u32 = 99;

/// What the embedder supplies for one tick.
#[derive(Debug, Clone)]
pub struct TickRequest<'a> {
    pub time: GameTime,
    /// Difficulty-driven noise multiplier.
    pub volatility_multiplier: f64,
    /// News reduced to the four fields the factor model reads.
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Aggregated pending order volume, keyed by ticker.
    pub order_volumes: &'a [(String, OrderVolume)],
}

/// What one tick produced.
#[derive(Debug, Clone, PartialEq)]
pub struct TickOutcome {
    pub market_status: MarketStatus,
    /// Indices of the companies that were active, in processing order.
    pub active_indices: Vec<usize>,
    /// Fair value per active company — the book's anchor, NOT a price.
    pub fair_values: Vec<f64>,
    pub volumes: Vec<f64>,
    /// Draws consumed by this tick. Zero when the market was closed.
    pub draws_consumed: usize,
}

/// What the embedder supplies at the close of a simulated day.
#[derive(Debug, Clone)]
pub struct DayCloseRequest<'a> {
    /// Per company, the day's accumulated `randomNoise` from factor
    /// attribution. `None` falls back to the day's total return.
    pub daily_innovations: &'a [Option<f64>],
    /// Per company, `sectorBaseDailyVariance(sector)`.
    pub sector_base_variances: &'a [f64],
    /// How the close treats `avg_volume`. `AvgVolumePolicy::Hold` -- the
    /// shipped default -- unless you are replaying a reference tape; the
    /// argument for the divergence lives on the policy itself.
    pub avg_volume: AvgVolumePolicy,
}

/// What the embedder supplies for the daily macro step.
#[derive(Debug, Clone)]
pub struct DayAdvanceRequest<'a> {
    pub volatility: f64,
    pub active_shocks: &'a [EconomicShock],
    pub market_return_pct: f64,
    pub game_day: i64,
    /// Game timestamp in minutes, for the central bank's meeting calendar.
    pub timestamp: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DayAdvanceOutcome {
    pub phase_changed: bool,
    pub meeting_held: bool,
    pub draws_consumed: usize,
}

pub struct Engine {
    rng: GameRng,
    companies: Vec<TickCompany>,
    economy: EconomyState,
    central_bank: CentralBankState,
    sector_keys: Vec<String>,
    /// Per-company attribution, accumulated across the current day.
    ///
    /// Four entries per company -- company_news, order_flow_impact,
    /// short_squeeze_effect, random_noise -- summed tick by tick and reset at
    /// `open_market`.
    attribution: Vec<[f64; 7]>,
    /// This tick's ground truth, per company slot.
    ///
    /// `attribution` above sums across the day, which is what a scorer wants
    /// and is useless as a dataset: it can say order flow moved a price today
    /// but not WHEN, and a label that cannot be aligned to a bar is not a
    /// label. The per-tick figures were computed and thrown away; these keep
    /// them.
    ///
    /// Three different quantities that are easy to confuse, so they are named
    /// apart: `fundamental` is the valuation, `anchor` is
    /// `fundamental * exp(s)` -- the price the model wanted before the book
    /// touched it -- and the printed price is what the book actually settled.
    tick_components: Vec<[f64; 7]>,
    tick_fundamental: Vec<f64>,
    tick_anchor: Vec<f64>,
    /// Cumulative MAIN-stream draws, including any the embedder took through
    /// [`Engine::draw_uniform`]. The single most useful number for diagnosing
    /// a cross-language divergence: if these differ, nothing downstream is
    /// worth comparing.
    draws: usize,
}

impl Engine {
    /// Seed the MAIN stream and take ownership of the state.
    ///
    /// # Both orderings are CONTRACTUAL
    ///
    /// `companies` is an ordered SEQUENCE, never a set or a mapping. The tick
    /// walks it in index order and draws per company as it goes, so reordering
    /// the roster produces a different market from the same seed. A
    /// well-meaning `sort_by(|a, b| a.ticker.cmp(&b.ticker))` anywhere upstream
    /// is a silent, total divergence — nothing will fail, the numbers will just
    /// be different ones.
    ///
    /// `sector_keys` must likewise be in `Object.keys(SECTOR_CONFIGS)` order:
    /// one normal is drawn per key, per tick, in that order.
    ///
    /// Roster SIZE is contractual too, and for the same reason. Adding a
    /// company does not append a name to an otherwise-unchanged market — draws
    /// scale with `n`, so every subsequent draw shifts and the whole market
    /// changes. A 30-name universe and a 100-name universe from one seed have
    /// nothing to do with each other.
    pub fn new(
        seed: u32,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
    ) -> Self {
        let companies_len = companies.len();
        Self {
            rng: GameRng::new(seed, MAIN_STREAM),
            companies,
            economy,
            central_bank,
            attribution: vec![[0.0; 7]; companies_len],
            tick_components: vec![[0.0; 7]; companies_len],
            // NaN, not zero: a company that has never ticked has no valuation,
            // and zero is a real one that would silently read as "worthless"
            // rather than as "not yet computed".
            tick_fundamental: vec![f64::NAN; companies_len],
            tick_anchor: vec![f64::NAN; companies_len],
            sector_keys,
            draws: 0,
        }
    }

    // ── Draw delegation ───────────────────────────────────────────────────

    /// Take one uniform from the shared stream, on the embedder's behalf.
    ///
    /// The embedder's own subsystems — events, corporate actions, earnings —
    /// draw from this same stream in the TypeScript. Routing them through here
    /// keeps one stream with one position; running a second generator
    /// alongside would interleave differently and diverge every price.
    pub fn draw_uniform(&mut self) -> f64 {
        self.draws += 1;
        self.rng.next_f64()
    }

    /// Take one normal from the shared stream. See [`Engine::draw_uniform`];
    /// note this also shares the Box-Muller spare.
    pub fn draw_normal(&mut self) -> f64 {
        self.draws += 1;
        self.rng.next_normal()
    }

    /// Cumulative draws taken since construction.
    /// The generator's exact position, as three numbers.
    ///
    /// The half of a checkpoint that cannot be reconstructed from the roster.
    /// Every other piece of engine state -- prices, GARCH variance, maker
    /// inventory, the mispricing carry -- is observable through `column()`, so
    /// an embedder that persists its own instruments already has it. The
    /// stream position is not observable that way, and without it a restored
    /// market continues from a different sequence while looking correct.
    pub fn rng_state(&self) -> crate::rng::RngState {
        self.rng.snapshot()
    }

    /// Put the generator back to a captured position.
    ///
    /// Deliberately narrow: it restores the STREAM and nothing else. Company
    /// state is the caller's to restore, because the caller is the one that
    /// persisted it. A method that pretended to restore everything would have
    /// to be kept in step with every field ever added to a company, and would
    /// fail silently the first time it was not.
    pub fn set_rng_state(&mut self, state: crate::rng::RngState) {
        self.rng = GameRng::restore(state);
    }

    pub fn draws_consumed(&self) -> usize {
        self.draws
    }

    // ── The tick ──────────────────────────────────────────────────────────

    /// Run one simulated minute.
    ///
    /// A closed market costs zero draws and changes nothing — the guard is
    /// inside [`simulate_market_tick`] and precedes every draw site, which
    /// matters because most of the clock is closed.
    pub fn tick(&mut self, request: &TickRequest) -> TickOutcome {
        // The generator is moved out, used, and moved back. `tick_with` takes
        // `&mut self`, so it cannot also borrow `self.rng` — and swapping is
        // clearer than duplicating the tick body for the two cases.
        let mut rng = std::mem::replace(&mut self.rng, GameRng::new(0, MAIN_STREAM));
        let mut counting = Counting {
            inner: &mut rng,
            count: 0,
        };
        let mut outcome = self.tick_with(request, &mut counting);
        let consumed = counting.count;
        self.rng = rng;
        self.draws += consumed;
        outcome.draws_consumed = consumed;
        outcome
    }

    /// Run one simulated minute against an EXTERNAL draw source.
    ///
    /// This is what a replay harness needs: the engine's own generator cannot
    /// reproduce a recorded TypeScript stream, because `next_normal` routes
    /// through `cos` and diverges on 1.545% of draws. Feeding recorded draws
    /// separates the arithmetic under test from the generator that is known to
    /// differ.
    ///
    /// `draws_consumed` in the returned outcome is 0 here — the caller owns
    /// the source and already knows what it handed over.
    pub fn tick_with(&mut self, request: &TickRequest, rng: &mut impl Rng) -> TickOutcome {
        let status = get_market_status(request.time);

        let outcome = simulate_market_tick(
            &mut self.companies,
            &TickInputs {
                economy: &self.economy,
                market_status: status,
                intraday_t: intraday_fraction(request.time),
                volatility_multiplier: request.volatility_multiplier,
                news: request.news,
                news_impact_queue: request.news_impact_queue,
                order_volumes: request.order_volumes,
                sector_keys: &self.sector_keys,
            },
            rng,
        );

        // Accumulate the APPLIED contributions, which is the same quantity the
        // `truth` table reports per tick -- so the day total of a column here
        // equals that column summed over the day, and the two surfaces cannot
        // disagree about what drove a price.
        //
        // It used to accumulate the RAW factors, and that was wrong in a way
        // that mattered. The three drift factors are divided by 390 before
        // they reach `s` and the noise term is multiplied by the intraday
        // volatility curve, so raw sums overstate news, flow and squeeze by
        // ~390x relative to noise. Anything ranking factors by magnitude --
        // "was this agent right for the right reasons" -- therefore answered
        // `company_news` on days that were overwhelmingly noise. Measured on
        // one session: raw called it news at 6.0e0 against noise at 6.0e-2;
        // applied calls it noise at 7.8e-1 against news at 1.5e-2.
        // Zeroed rather than left stale. A company that did not tick did not
        // move, so every component contributed exactly zero to its `s` -- which
        // is true, and keeps the columns summing to a Δs of zero. Carrying the
        // previous tick's values forward would invent activity.
        for slot in self.tick_components.iter_mut() {
            *slot = [0.0; 7];
        }
        for (n, slot) in outcome.active_indices.iter().enumerate() {
            if let (Some(acc), Some(computed)) = (
                self.attribution.get_mut(*slot),
                outcome.s_components.get(n),
            ) {
                for (k, value) in computed.iter().enumerate() {
                    acc[k] += value;
                }
            }
            if let (Some(row), Some(computed)) = (
                self.tick_components.get_mut(*slot),
                outcome.s_components.get(n),
            ) {
                *row = *computed;
            }
            // Levels, unlike contributions, PERSIST between ticks: a company
            // that did not trade still has the valuation and anchor it last
            // had, and blanking them would make the columns unusable for
            // exactly the join they exist for.
            if let Some(v) = outcome.fundamental_values.get(n) {
                if let Some(slot_v) = self.tick_fundamental.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.fair_values.get(n) {
                if let Some(slot_v) = self.tick_anchor.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
        }

        TickOutcome {
            market_status: status,
            active_indices: outcome.active_indices,
            fair_values: outcome.fair_values,
            volumes: outcome.volumes,
            draws_consumed: 0,
        }
    }

    // ── Day boundaries ────────────────────────────────────────────────────

    /// Market-open reset. Zero draws.
    ///
    /// This is what anchors the circuit-breaker band to today's open, which is
    /// what makes it a SESSION band and leaves the overnight gap outside it by
    /// design (D6).
    /// Attribution for the current day, four values per company:
    /// `[company_news, order_flow_impact, short_squeeze_effect, random_noise]`.
    ///
    /// # Four, not the reference's six
    ///
    /// The reference declares six attribution keys, but three of them --
    /// `earningsRevision`, `multipleChange` and `sentiment` -- belong to
    /// factors the live flags discard, and its `shortSqueezeEffect` is folded
    /// into `orderFlowImpact` for display rather than reported separately.
    ///
    /// Reporting six columns here would mean shipping three columns of
    /// structural zeros, which is the "knobs wired to nothing" documentation
    /// lie this port has already had to correct once. So the four live
    /// components are reported, and the squeeze is kept separate because it is
    /// genuinely a distinct mechanism.
    pub fn attribution(&self) -> &[[f64; 7]] {
        &self.attribution
    }

    /// This tick's `s` decomposition per company slot, in
    /// [`crate::market::factors::S_COMPONENT_KEYS`] order. Zero for a company
    /// that did not tick.
    pub fn tick_components(&self) -> &[[f64; 7]] {
        &self.tick_components
    }

    /// The valuation each company was last measured at. NaN before its first
    /// tick.
    pub fn tick_fundamental(&self) -> &[f64] {
        &self.tick_fundamental
    }

    /// The book anchor each company was last given: `fundamental * exp(s)`.
    pub fn tick_anchor(&self) -> &[f64] {
        &self.tick_anchor
    }

    /// Restore the per-day accumulators that a column snapshot does not hold.
    ///
    /// The columns are per-COMPANY state -- price, variance, inventory, the
    /// mispricing carry. These four are per-DAY, live beside them, and were
    /// missing from the snapshot, which made a mid-day fork diverge:
    /// `attribution` is fed to GARCH as the day's innovation at the close, so
    /// a fork that lost it closed on a different variance and priced
    /// differently from the parent it was supposed to be identical to.
    ///
    /// Lengths are checked rather than truncated. A short slice would restore
    /// a market correct for its first companies and stale for the rest.
    pub fn restore_day_state(
        &mut self,
        attribution: &[f64],
        components: &[f64],
        fundamental: &[f64],
        anchor: &[f64],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len, want) in [
            ("attribution", attribution.len(), n * 7),
            ("tick_components", components.len(), n * 7),
            ("tick_fundamental", fundamental.len(), n),
            ("tick_anchor", anchor.len(), n),
        ] {
            if len != want {
                return Err(format!("{name} has {len} values, expected {want}"));
            }
        }
        self.attribution = attribution
            .chunks_exact(7)
            .map(|c| [c[0], c[1], c[2], c[3], c[4], c[5], c[6]])
            .collect();
        self.tick_components = components
            .chunks_exact(7)
            .map(|c| [c[0], c[1], c[2], c[3], c[4], c[5], c[6]])
            .collect();
        self.tick_fundamental = fundamental.to_vec();
        self.tick_anchor = anchor.to_vec();
        Ok(())
    }

    /// One attribution column across all companies, by index 0..7.
    pub fn attribution_column(&self, factor: usize) -> Vec<f64> {
        self.attribution
            .iter()
            .map(|a| a.get(factor).copied().unwrap_or(f64::NAN))
            .collect()
    }

    pub fn open_market(&mut self) {
        // Attribution is per DAY. Resetting here rather than at close means a
        // caller can still read yesterday's decomposition after the close has
        // run, which is when they would actually want it.
        self.attribution.clear();
        self.attribution.resize(self.companies.len(), [0.0; 7]);
        self.tick_components.clear();
        self.tick_components.resize(self.companies.len(), [0.0; 7]);
        self.tick_fundamental.clear();
        self.tick_fundamental.resize(self.companies.len(), f64::NAN);
        self.tick_anchor.clear();
        self.tick_anchor.resize(self.companies.len(), f64::NAN);

        reset_daily_prices(&mut self.companies);
    }

    /// Close-of-day bookkeeping. Zero draws.
    ///
    /// Must run BEFORE any earnings shock the embedder applies that evening:
    /// the momentum roll reads `s` as it stands at the close, and an earnings
    /// gap applied first would be counted again as next-day herding. The
    /// TypeScript's earnings path patches `sPrevClose` by the shock for the
    /// same reason.
    pub fn close_market(&mut self, request: &DayCloseRequest) {
        assert_eq!(
            request.daily_innovations.len(),
            self.companies.len(),
            "one innovation per company"
        );
        assert_eq!(
            request.sector_base_variances.len(),
            self.companies.len(),
            "one sector base variance per company"
        );
        for (i, company) in self.companies.iter_mut().enumerate() {
            close_day(
                company,
                &CloseInputs {
                    daily_innovation: request.daily_innovations[i],
                    sector_base_daily_variance: request.sector_base_variances[i],
                    avg_volume: request.avg_volume,
                },
            );
        }
    }

    /// The daily macro step: economy, cycle roll, then the central bank.
    ///
    /// The order is the TypeScript's and is load-bearing — the rates and VIX
    /// the factor model reads on the first tick of a new day are already the
    /// day's NEW values, not yesterday's.
    pub fn advance_day(&mut self, request: &DayAdvanceRequest) -> DayAdvanceOutcome {
        let mut rng = std::mem::replace(&mut self.rng, GameRng::new(0, MAIN_STREAM));
        let mut counting = Counting {
            inner: &mut rng,
            count: 0,
        };
        let mut outcome = self.advance_day_with(request, &mut counting);
        let consumed = counting.count;
        self.rng = rng;
        self.draws += consumed;
        outcome.draws_consumed = consumed;
        outcome
    }

    /// The daily macro step against an EXTERNAL draw source. See
    /// [`Engine::tick_with`].
    pub fn advance_day_with(
        &mut self,
        request: &DayAdvanceRequest,
        rng: &mut impl Rng,
    ) -> DayAdvanceOutcome {
        let phase_before = self.economy.cycle_phase;

        self.economy = update_economy_daily(
            &self.economy,
            &DailyInputs {
                volatility: request.volatility,
                active_shocks: request.active_shocks,
                market_return_pct: request.market_return_pct,
                game_day: request.game_day,
            },
            rng,
        );
        self.economy = check_cycle_transition(&self.economy, rng);

        let meeting =
            update_central_bank(&self.central_bank, &self.economy, request.timestamp, rng);
        let meeting_held = meeting.decision.is_some();
        self.central_bank = meeting.central_bank;
        self.economy = meeting.economy;

        DayAdvanceOutcome {
            phase_changed: self.economy.cycle_phase != phase_before,
            meeting_held,
            draws_consumed: 0,
        }
    }

    // ── Day-chunked stepping ──────────────────────────────────────────────

    /// Run a whole session in one call, writing per-tick output into a
    /// caller-owned buffer.
    ///
    /// # Why this is core rather than a wrapper convenience
    ///
    /// A caller looping over [`Engine::tick`] from Python crosses the FFI
    /// boundary roughly 98,000 times per simulated year — and worse in
    /// practice, because every attribute read on a returned object is another
    /// crossing. No binding layer can fix that from the outside: if the only
    /// advancement primitive is one tick, the loop is in the host language and
    /// the crossings are unavoidable. So day-chunking lives here.
    ///
    /// # Nothing accumulates beyond one day
    ///
    /// Output goes into [`SessionBuffer`], which the caller sizes once and
    /// drains between days. At tick grain a year of 100 names is ~9.8M rows
    /// per column, so accumulating Rust-side and returning at the end does not
    /// scale — one day is ~312 KB and is reused.
    ///
    /// Buffers are `f64` throughout with no `f32` option, deliberately: the
    /// known-answer files and the cross-platform release gate hash these
    /// buffers, so a half-precision path would be a silent parity break
    /// dressed as a performance switch.
    pub fn run_session(
        &mut self,
        request: &SessionRequest,
        buffer: &mut SessionBuffer,
    ) -> SessionOutcome {
        buffer.resize(request.ticks, self.companies.len());

        if request.reopen {
            self.open_market();
        }

        let mut draws = 0usize;
        let (mut hour, mut minute) = (request.start.hour, request.start.minute);
        let mut halted_at = None;

        for t in 0..request.ticks {
            let outcome = self.tick(&TickRequest {
                time: GameTime {
                    hour,
                    minute,
                    day_of_week: request.start.day_of_week,
                },
                volatility_multiplier: request.volatility_multiplier,
                news: request.news,
                news_impact_queue: request.news_impact_queue,
                order_volumes: request.order_volumes,
            });
            draws += outcome.draws_consumed;
            buffer.write_tick(
                t,
                &self.companies,
                &self.tick_components,
                &self.tick_fundamental,
                &self.tick_anchor,
            );

            if let Some(stop) = &request.stop {
                if stop.triggered(t, &self.companies) {
                    halted_at = Some(t);
                    buffer.ticks_written = t + 1;
                    break;
                }
            }

            minute += 1;
            if minute >= 60 {
                minute = 0;
                hour += 1;
            }
        }

        if request.close_at_end && halted_at.is_none() {
            // The engine's OWN accumulated noise, where the caller left the
            // slot empty. It cannot be the caller's job to fill it here: the
            // request is built BEFORE the session runs, and the value being
            // asked for is the noise this session is about to accumulate.
            //
            // So on this path an absent innovation is not a choice, it is the
            // only thing an embedder can supply -- and `None` falls through to
            // the day's total return, which is a different quantity (measured
            // elsewhere: median factor 0.82, tenth percentile 0.22, ninetieth
            // 3.20). A parameter that cannot be supplied correctly is a trap,
            // not a feature.
            let own = self.attribution_column(random_noise_index());
            let innovations: Vec<Option<f64>> = request
                .daily_innovations
                .iter()
                .enumerate()
                .map(|(i, supplied)| supplied.or_else(|| own.get(i).copied()))
                .collect();
            self.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: request.sector_base_variances,
                avg_volume: AvgVolumePolicy::default(),
            });
        }

        SessionOutcome {
            draws_consumed: draws,
            halted_at,
        }
    }

    // ── State access ──────────────────────────────────────────────────────

    pub fn economy(&self) -> &EconomyState {
        &self.economy
    }

    /// Mutable access to the macro state, for driving a scenario.
    ///
    /// A scenario is a PATH, not a feature: a rate shock is `federal_funds_rate`
    /// stepping from 2.5 to 5 over N days, supplied by whoever is running the
    /// study. Expressing that needs the embedder to be able to write the macro
    /// state between days, which is what this is for.
    ///
    /// Writing it MID-SESSION is legal and is not checked, because the engine
    /// cannot tell a deliberate intraday shock from a mistake. It is worth
    /// knowing that the tick reads these fields as it goes, so a write between
    /// two ticks of the same session takes effect immediately and for the rest
    /// of that session — which is either exactly what was wanted or a
    /// surprise, depending on who wrote it.
    pub fn economy_mut(&mut self) -> &mut EconomyState {
        &mut self.economy
    }
    pub fn central_bank(&self) -> &CentralBankState {
        &self.central_bank
    }
    /// Mutable access to the central bank, for restoring a snapshot.
    ///
    /// Exists for the same reason as [`Engine::economy_mut`]: now that the
    /// macro chain advances between days, a fork that did not carry the
    /// bank's meeting calendar would hold a meeting its parent never held.
    pub fn central_bank_mut(&mut self) -> &mut CentralBankState {
        &mut self.central_bank
    }
    pub fn companies(&self) -> &[TickCompany] {
        &self.companies
    }
    pub fn companies_mut(&mut self) -> &mut [TickCompany] {
        &mut self.companies
    }
    pub fn len(&self) -> usize {
        self.companies.len()
    }
    pub fn is_empty(&self) -> bool {
        self.companies.is_empty()
    }

    // ── Roster mutation ───────────────────────────────────────────────────
    //
    // A listed universe is not static: companies IPO in, go bankrupt, and are
    // acquired. An engine that could not represent that would force the
    // embedder to choose between rebuilding — which resets the generator and
    // destroys reproducibility — and pretending, which silently attaches
    // results to the wrong companies because every column is positional.
    //
    // # What these guarantee, and what they cannot
    //
    // They do NOT keep the rest of the market unchanged. They cannot: the tick
    // draws per company, so changing `n` shifts every subsequent draw and the
    // whole market moves from that point. That is a property of the model, not
    // a limitation of the implementation, and `Engine::new` already says so
    // about roster size.
    //
    // What they DO guarantee is reproducibility, which is the property that
    // actually matters: the generator carries forward across the change, so
    // one seed plus the same sequence of roster edits, applied at the same
    // ticks, reproduces the same market exactly. Replay works; invariance was
    // never on offer.

    /// Append a company. Returns its index.
    ///
    /// Appends rather than inserting, because index order is the draw order:
    /// inserting into the middle would renumber every company after it and
    /// change which draws they receive. An embedder that needs a particular
    /// ordering must establish it before the first tick.
    pub fn add_company(&mut self, company: TickCompany) -> usize {
        self.companies.push(company);
        self.attribution.push([0.0; 7]);
        self.tick_components.push([0.0; 7]);
        self.tick_fundamental.push(f64::NAN);
        self.tick_anchor.push(f64::NAN);
        self.companies.len() - 1
    }

    /// Remove the company at `index`, returning it.
    ///
    /// `Vec::remove`, so the tail shifts down by one and keeps its relative
    /// order. `swap_remove` would be cheaper and is wrong: it moves the last
    /// company into the hole, silently reordering the roster, and roster order
    /// is contractual.
    ///
    /// Returns `None` for an out-of-range index rather than panicking — a
    /// removal racing a bankruptcy is an embedder bug worth reporting, not a
    /// reason to abort the module and take the session with it.
    pub fn remove_company(&mut self, index: usize) -> Option<TickCompany> {
        if index >= self.companies.len() {
            return None;
        }
        if index < self.tick_components.len() {
            self.tick_components.remove(index);
        }
        if index < self.tick_fundamental.len() {
            self.tick_fundamental.remove(index);
        }
        if index < self.tick_anchor.len() {
            self.tick_anchor.remove(index);
        }
        if index < self.attribution.len() {
            self.attribution.remove(index);
        }
        Some(self.companies.remove(index))
    }

    /// Find a company's index by id.
    ///
    /// Linear, because the roster is ~100 names and a map would be a second
    /// structure to keep in step with the ordering that actually matters.
    pub fn index_of(&self, id: &str) -> Option<usize> {
        self.companies.iter().position(|c| c.id == id)
    }

    /// Build the executable order book for one instrument, right now.
    ///
    /// # This is the book the tick itself settles through
    ///
    /// Not a display copy or an approximation of one. The same
    /// `build_live_book` call the price settlement uses, on the same state, so
    /// the depth a caller reads is the depth they would actually trade
    /// against. That is what makes slippage emergent: a large order pays worse
    /// prices because it consumed real levels, not because a coefficient said
    /// large orders cost more.
    ///
    /// Rebuilt per call rather than persisted, which is correct rather than
    /// merely convenient: a market maker re-quotes every tick anyway, so the
    /// book is a pure function of fair value, spread and resting orders. That
    /// keeps the tick pure and replay deterministic for free.
    ///
    /// Returns `None` for an out-of-range index.
    pub fn book_for(&self, index: usize) -> Option<crate::order_book::OrderBook> {
        let company = self.companies.get(index)?;
        Some(crate::microstructure::build_live_book(
            &company.micro_view(company.stock.price),
            &crate::microstructure::LiveBookOptions {
                vix: self.economy.vix,
                ..Default::default()
            },
        ))
    }

    /// Company ids, in roster order. The mapping every column is positional against.
    pub fn ids(&self) -> Vec<String> {
        self.companies.iter().map(|c| c.id.clone()).collect()
    }

    /// Columnar read of one field, for the FFI boundary.
    ///
    /// A single contiguous `f64` buffer crosses a WASM boundary as one view;
    /// 108 marshalled objects do not. The `Vec` is built on demand rather than
    /// maintained, because the embedder reads far less often than the tick
    /// writes.
    /// Write one column back onto the roster.
    ///
    /// The inverse of [`Engine::column`], and the second half of a
    /// constant-time checkpoint: a saved market is its columns plus the
    /// generator position, and until now only the reading direction existed.
    ///
    /// # The match is exhaustive on purpose
    ///
    /// Every branch is spelled out with no wildcard, so adding a variant to
    /// [`PriceField`] fails to COMPILE until it is handled here. That is the
    /// difference between this and the "restore everything" method the
    /// docstring on `set_rng_state` warns against: the compiler keeps the two
    /// directions in step rather than a reviewer remembering to.
    ///
    /// # NaN means absent, matching the read side
    ///
    /// The optional fields round-trip through NaN in both directions. Writing
    /// a NaN clears the field rather than storing a NaN, so a column read out
    /// and written back reproduces the original `Option` exactly -- including
    /// the difference between "no mispricing yet" and "a mispricing of zero",
    /// which are different markets.
    ///
    /// Returns an error when the slice length does not match the roster.
    /// Silently writing a prefix would restore a market that was correct for
    /// the first N companies and stale for the rest.
    /// Write the three fair-value inputs back onto the roster.
    ///
    /// [`Engine::set_column`] covers the STOCK fields -- price, variance,
    /// inventory, the mispricing carry. These three live on the company
    /// rather than the stock, and until now nothing could set them after
    /// construction. That made them frozen for the life of an engine, which
    /// is fine for a batch sweep and wrong for an embedder whose companies
    /// report earnings: fair value is `eps * target_pe`, so a stale `eps` is
    /// a company valued on the fundamentals it had on day one, for ever.
    ///
    /// NaN clears the field, matching `set_column` and the columnar contract
    /// everywhere else -- zero is a real EPS (a company that broke exactly
    /// even) and cannot double as the absent marker. `None` here is not
    /// "unknown", it is what the valuation reads as "no earnings path", and
    /// the two must stay distinguishable.
    ///
    /// Consumes no draws, so it cannot move the generator: a caller may sync
    /// as often as it likes without changing the market's trajectory.
    ///
    /// Lengths are checked against the roster rather than truncated. Writing
    /// a prefix would leave a market correct for its first companies and
    /// stale for the rest -- the failure this method exists to end.
    pub fn set_fundamentals(
        &mut self,
        eps: &[f64],
        book_value_per_share: &[f64],
        revenue_growth: &[f64],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len) in [
            ("eps", eps.len()),
            ("book_value_per_share", book_value_per_share.len()),
            ("revenue_growth", revenue_growth.len()),
        ] {
            if len != n {
                return Err(format!(
                    "{name} has {len} values for {n} companies"
                ));
            }
        }
        fn optional(v: f64) -> Option<f64> {
            if v.is_nan() {
                None
            } else {
                Some(v)
            }
        }
        for (i, company) in self.companies.iter_mut().enumerate() {
            company.eps = optional(eps[i]);
            company.book_value_per_share = optional(book_value_per_share[i]);
            company.revenue_growth = optional(revenue_growth[i]);
        }
        Ok(())
    }

    /// Write the two trading-status flags back onto the roster.
    ///
    /// The last per-company state an embedder could not update. A company that
    /// goes bankrupt or is taken private in the game kept trading here: the
    /// tick skips a company only when it reads `is_bankrupt || !is_public`
    /// (`market/tick.rs`), and the index excludes bankrupt constituents
    /// (`market/index_value.rs`). Without a setter, neither could ever become
    /// true after construction, so a failed company went on printing prices
    /// and went on counting toward the index.
    ///
    /// Narrower than [`Engine::set_fundamentals`] because it does not
    /// compound -- a stale `eps` misprices a company a little more every
    /// quarter, while a stale flag is wrong from the moment it changes and no
    /// worse afterwards. Both are wrong, so both are settable.
    ///
    /// `&[bool]` rather than the f64 columnar convention used elsewhere,
    /// deliberately: there is no "absent" flag for NaN to mean, and a boolean
    /// carried as a float invites a caller to pass 0.5.
    ///
    /// Consumes no draws.
    pub fn set_status(
        &mut self,
        is_bankrupt: &[bool],
        is_public: &[bool],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len) in [
            ("is_bankrupt", is_bankrupt.len()),
            ("is_public", is_public.len()),
        ] {
            if len != n {
                return Err(format!("{name} has {len} values for {n} companies"));
            }
        }
        for (i, company) in self.companies.iter_mut().enumerate() {
            company.is_bankrupt = is_bankrupt[i];
            company.is_public = is_public[i];
        }
        Ok(())
    }

    /// The two trading-status flags, in roster order.
    pub fn status(&self) -> (Vec<bool>, Vec<bool>) {
        (
            self.companies.iter().map(|c| c.is_bankrupt).collect(),
            self.companies.iter().map(|c| c.is_public).collect(),
        )
    }

    /// The three fair-value inputs, NaN where absent.
    ///
    /// The read side of [`Engine::set_fundamentals`], so a caller can check
    /// what the engine is actually valuing on rather than assuming its own
    /// copy is what arrived.
    pub fn fundamentals(&self) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
        let nan = f64::NAN;
        (
            self.companies.iter().map(|c| c.eps.unwrap_or(nan)).collect(),
            self.companies
                .iter()
                .map(|c| c.book_value_per_share.unwrap_or(nan))
                .collect(),
            self.companies
                .iter()
                .map(|c| c.revenue_growth.unwrap_or(nan))
                .collect(),
        )
    }

    pub fn set_column(&mut self, field: PriceField, values: &[f64]) -> Result<(), String> {
        if values.len() != self.companies.len() {
            return Err(format!(
                "expected {} values for {} companies, got {}",
                self.companies.len(),
                self.companies.len(),
                values.len()
            ));
        }
        // NaN in, `None` out. Zero is a real value for every optional field
        // here, so it cannot double as the absent marker.
        fn optional(v: f64) -> Option<f64> {
            if v.is_nan() {
                None
            } else {
                Some(v)
            }
        }
        for (company, &value) in self.companies.iter_mut().zip(values) {
            let stock = &mut company.stock;
            match field {
                PriceField::Price => stock.price = value,
                PriceField::PreviousClose => stock.previous_close = value,
                PriceField::Open => stock.open = value,
                PriceField::High => stock.high = value,
                PriceField::Low => stock.low = value,
                PriceField::Volume => stock.volume = value,
                PriceField::MarketCap => stock.market_cap = value,
                PriceField::MispricingS => stock.mispricing_s = optional(value),
                PriceField::MakerInventory => stock.maker_inventory = optional(value),
                PriceField::GarchVariance => stock.garch_variance = value,
                PriceField::PreviousTickPrice => {
                    stock.previous_tick_price = optional(value)
                }
                PriceField::MispricingSPrevClose => {
                    stock.mispricing_s_prev_close = optional(value)
                }
                PriceField::MispricingMomentum => {
                    stock.mispricing_momentum = optional(value)
                }
                PriceField::LastDailyReturn => stock.last_daily_return = optional(value),
                PriceField::AvgVolume => stock.avg_volume = value,
                PriceField::Beta => stock.beta = optional(value),
                PriceField::ShortInterest => stock.short_interest = value,
                PriceField::FloatShares => stock.float = value,
            }
        }
        Ok(())
    }

    pub fn column(&self, field: PriceField) -> Vec<f64> {
        self.companies
            .iter()
            .map(|c| match field {
                PriceField::Price => c.stock.price,
                PriceField::PreviousClose => c.stock.previous_close,
                PriceField::Open => c.stock.open,
                PriceField::High => c.stock.high,
                PriceField::Low => c.stock.low,
                PriceField::Volume => c.stock.volume,
                PriceField::MarketCap => c.stock.market_cap,
                PriceField::MispricingS => c.stock.mispricing_s.unwrap_or(f64::NAN),
                PriceField::MakerInventory => c.stock.maker_inventory.unwrap_or(f64::NAN),
                PriceField::GarchVariance => c.stock.garch_variance,
                PriceField::PreviousTickPrice => {
                    c.stock.previous_tick_price.unwrap_or(f64::NAN)
                }
                PriceField::MispricingSPrevClose => {
                    c.stock.mispricing_s_prev_close.unwrap_or(f64::NAN)
                }
                PriceField::MispricingMomentum => {
                    c.stock.mispricing_momentum.unwrap_or(f64::NAN)
                }
                PriceField::LastDailyReturn => c.stock.last_daily_return.unwrap_or(f64::NAN),
                PriceField::AvgVolume => c.stock.avg_volume,
                PriceField::Beta => c.stock.beta.unwrap_or(f64::NAN),
                PriceField::ShortInterest => c.stock.short_interest,
                PriceField::FloatShares => c.stock.float,
            })
            .collect()
    }

    /// Convenience for the most-read column.
    pub fn prices(&self) -> Vec<f64> {
        self.column(PriceField::Price)
    }

    /// Overwrite prices from a columnar buffer.
    ///
    /// For the embedder to apply an effect this engine does not model — an
    /// earnings gap, a corporate action. It does NOT recompute `s`, so a
    /// caller changing the price must also decide what that means for the
    /// mispricing, exactly as the TypeScript's earnings path does.
    pub fn write_prices(&mut self, prices: &[f64]) {
        assert_eq!(prices.len(), self.companies.len(), "one price per company");
        for (company, &price) in self.companies.iter_mut().zip(prices) {
            company.stock.price = price;
            company.stock.market_cap = price * company.stock.shares_outstanding;
        }
    }
}

/// Fields exposed columnar-wise across the FFI boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PriceField {
    Price,
    PreviousClose,
    Open,
    High,
    Low,
    Volume,
    MarketCap,
    /// `NaN` for a company that has never ticked, since a column cannot carry
    /// `None`. The embedder should read it as "unset", not as a number.
    MispricingS,
    /// `NaN` when the maker has never quoted this company.
    ///
    /// Not zero. Zero is a REAL inventory -- a maker holding nothing -- and
    /// before the first tick every company read 0.0, which said "flat" about a
    /// book that did not exist yet. Absence is not zero anywhere else in this
    /// library and it should not have been here.
    MakerInventory,
    GarchVariance,
    /// The previous tick's print. `NaN` before the second tick.
    PreviousTickPrice,
    /// `s` as it stood at the last close, and the momentum term carried from
    /// it. Both `NaN` before the first close: they are day-boundary state, so
    /// a session that has not crossed one has no value to report.
    MispricingSPrevClose,
    MispricingMomentum,
    /// The last completed day's return. `NaN` before the first close.
    LastDailyReturn,
    /// Cross-sectional characteristics, constant through a run. Exposed as
    /// columns so a factor study can join them to `bars` without carrying the
    /// universe alongside.
    AvgVolume,
    Beta,
    ShortInterest,
    FloatShares,
}

/// Index of `random_noise` among the components, found rather than written.
///
/// Every component is an f64, so a literal index would keep compiling and
/// start feeding the variance process the crowd lean the day a component is
/// inserted ahead of it.
fn random_noise_index() -> usize {
    crate::market::factors::S_COMPONENT_KEYS
        .iter()
        .position(|name| *name == "random_noise")
        .expect("random_noise is one of the components")
}

/// Counts draws as they are taken.
///
/// The obvious alternative — clone the generator, replay uniforms until the
/// clone catches up — does NOT work on a mixed stream, and the reason is worth
/// recording because it looks like it should. `next_normal` is Box-Muller: it
/// consumes two PCG steps and leaves a spare cached, and a later normal
/// consumes zero. A probe pulling uniforms never reproduces that spare, so the
/// two states are never equal and the search runs to its bound.
///
/// Counting at the call is exact, costs a `usize` increment, and does not care
/// what kind of draw it was.
struct Counting<'a> {
    inner: &'a mut GameRng,
    count: usize,
}

impl Rng for Counting<'_> {
    fn next_f64(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_f64()
    }
    fn next_normal(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_normal()
    }
}

/// What a session needs beyond the engine's own state.
pub struct SessionRequest<'a> {
    pub start: GameTime,
    pub ticks: usize,
    pub volatility_multiplier: f64,
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    pub order_volumes: &'a [(String, OrderVolume)],
    /// Run the close bookkeeping when the session finishes normally.
    pub close_at_end: bool,
    /// Open the market before the first tick.
    ///
    /// True is the reference's behaviour and the right default: it runs one
    /// session per day, so opening inside the session and opening the day are
    /// the same act.
    ///
    /// They stop being the same act when a day is made of SEVERAL sessions,
    /// which is what agent stepping does -- act, run some ticks, act again.
    /// `open_market` resets the attribution accumulator and re-anchors the
    /// daily open, so re-opening per step silently made both per-step:
    ///
    /// - `attribution` documents itself as per-DAY and was per-session. A
    ///   large buy in step 0 of a six-step day moved the market -- the tape
    ///   records it -- and `attribution("order_flow_impact")` read exactly
    ///   zero at the close. An agent's own impact was erased from the ground
    ///   truth that scores it.
    /// - Worse, it is not only reporting. `close_at_end` feeds
    ///   `attribution_column(random_noise)` to GARCH as the day's innovation,
    ///   so a stepped day updated variance from the LAST STEP's noise rather
    ///   than the day's.
    ///
    /// Set false for the second and later sessions of one day. A day of one
    /// session is unaffected either way, which is why no parity vector moves.
    pub reopen: bool,
    pub daily_innovations: &'a [Option<f64>],
    pub sector_base_variances: &'a [f64],
    /// Stop early when a condition is met, for event-driven advancement.
    pub stop: Option<StopCondition>,
}

/// Why a session might end before its last tick.
///
/// Deliberately limited to what the ENGINE can decide from its own state.
/// Stopping on a fill needs the order book and the caller's resting orders,
/// neither of which this engine owns — that belongs with whoever owns order
/// state, and inventing a half-version here would be worse than not having it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum StopCondition {
    /// A named company's price leaves a band. `None` on a side means unbounded.
    PriceOutside {
        company: usize,
        below: Option<f64>,
        above: Option<f64>,
    },
}

impl StopCondition {
    fn triggered(&self, _tick: usize, companies: &[TickCompany]) -> bool {
        match *self {
            StopCondition::PriceOutside {
                company,
                below,
                above,
            } => {
                let Some(c) = companies.get(company) else {
                    return false;
                };
                let price = c.stock.price;
                below.is_some_and(|b| price < b) || above.is_some_and(|a| price > a)
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SessionOutcome {
    pub draws_consumed: usize,
    /// `Some(tick)` when a [`StopCondition`] fired. The close is NOT run in
    /// that case — the day is not over, the caller interrupted it.
    pub halted_at: Option<usize>,
}

/// A reusable per-day columnar buffer.
///
/// Row-major, `tick * companies + company`, so one company's path is a strided
/// read and one tick's cross-section is contiguous. The cross-section is the
/// hot direction: emission is per tick.
///
/// `f64` only. See [`Engine::run_session`] for why there is no `f32` variant.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SessionBuffer {
    pub companies: usize,
    /// How many ticks the last session actually wrote — less than capacity
    /// when a [`StopCondition`] fired.
    pub ticks_written: usize,
    pub prices: Vec<f64>,
    pub volumes: Vec<f64>,
    pub mispricing_s: Vec<f64>,
    pub fundamental: Vec<f64>,
    pub anchor: Vec<f64>,
    /// The seven component columns, each `ticks * companies`, in
    /// `S_COMPONENT_KEYS` order. Seven flat buffers rather than one of
    /// `[f64; 7]`, because each becomes an Arrow column and a column wants a
    /// contiguous run of its own values.
    pub components: [Vec<f64>; 7],
}

impl SessionBuffer {
    pub fn new() -> Self {
        Self::default()
    }

    fn resize(&mut self, ticks: usize, companies: usize) {
        let needed = ticks * companies;
        if self.prices.len() != needed {
            self.prices.resize(needed, 0.0);
            self.volumes.resize(needed, 0.0);
            self.mispricing_s.resize(needed, 0.0);
            self.fundamental.resize(needed, f64::NAN);
            self.anchor.resize(needed, f64::NAN);
            for column in self.components.iter_mut() {
                column.resize(needed, 0.0);
            }
        }
        self.companies = companies;
        self.ticks_written = ticks;
    }

    fn write_tick(
        &mut self,
        tick: usize,
        companies: &[TickCompany],
        components: &[[f64; 7]],
        fundamental: &[f64],
        anchor: &[f64],
    ) {
        let base = tick * self.companies;
        for (i, c) in companies.iter().enumerate() {
            self.prices[base + i] = c.stock.price;
            self.volumes[base + i] = c.stock.volume;
            // NaN for a company that has never ticked — a column cannot carry
            // `None`, and zero would be a real mispricing.
            self.mispricing_s[base + i] = c.stock.mispricing_s.unwrap_or(f64::NAN);
            self.fundamental[base + i] = fundamental.get(i).copied().unwrap_or(f64::NAN);
            self.anchor[base + i] = anchor.get(i).copied().unwrap_or(f64::NAN);
            let row = components.get(i).copied().unwrap_or([0.0; 7]);
            for (k, column) in self.components.iter_mut().enumerate() {
                column[base + i] = row[k];
            }
        }
    }

    /// One tick's cross-section, contiguous.
    pub fn tick_prices(&self, tick: usize) -> &[f64] {
        let base = tick * self.companies;
        &self.prices[base..base + self.companies]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };
    use crate::market::TickStock;

    fn sectors() -> Vec<String> {
        ["technology", "energy", "healthcare"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    fn company(id: &str, price: f64) -> TickCompany {
        TickCompany {
            id: id.to_string(),
            ticker: id.to_string(),
            sector: "technology".to_string(),
            is_bankrupt: false,
            is_public: true,
            sector_volatility: Some(1.0),
            sector_avg_pe: Some(32.0),
            eps: Some(4.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.1),
            stock: TickStock {
                price,
                previous_close: price,
                previous_tick_price: None,
                open: price,
                high: price,
                low: price,
                volume: 0.0,
                avg_volume: 1e6,
                shares_outstanding: 1e8,
                market_cap: price * 1e8,
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                maker_inventory: None,
                garch_variance: 0.015 * 0.015,
                last_daily_return: None,
                beta: Some(1.0),
                short_interest: 0.0,
                float: 1e8,
            },
        }
    }

    fn engine(seed: u32) -> Engine {
        Engine::new(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
        )
    }

    fn request(hour: i64, minute: i64) -> TickRequest<'static> {
        TickRequest {
            time: GameTime {
                hour,
                minute,
                day_of_week: 3,
            },
            volatility_multiplier: 0.7,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
        }
    }

    #[test]
    fn the_same_seed_produces_the_same_market() {
        // The property the whole port exists to preserve.
        let run = || {
            let mut e = engine(4242);
            e.open_market();
            for m in 0..60 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            (e.prices(), e.draws_consumed())
        };
        let (a, da) = run();
        let (b, db) = run();
        assert_eq!(da, db, "draw counts must match");
        for (i, (x, y)) in a.iter().zip(&b).enumerate() {
            assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
        }
    }

    #[test]
    fn different_seeds_produce_different_markets() {
        // The companion assertion: reproducible-because-constant would pass
        // the test above and be worthless.
        let run = |seed| {
            let mut e = engine(seed);
            e.open_market();
            for m in 0..60 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.prices()
        };
        assert_ne!(run(1)[0].to_bits(), run(2)[0].to_bits());
    }

    #[test]
    fn a_closed_market_costs_nothing() {
        let mut e = engine(7);
        let before_prices = e.prices();
        let out = e.tick(&TickRequest {
            time: GameTime {
                hour: 11,
                minute: 0,
                day_of_week: 6,
            },
            ..request(11, 0)
        });
        assert_eq!(out.market_status, MarketStatus::Closed);
        assert_eq!(
            out.draws_consumed, 0,
            "a closed market must not advance the stream"
        );
        assert_eq!(e.draws_consumed(), 0);
        assert_eq!(e.prices(), before_prices);
    }

    #[test]
    fn the_draw_schedule_is_what_the_documentation_claims() {
        // 1 market normal + one per sector + 2 per active company, plus 4 more
        // each at settlement when the book runs.
        let mut e = engine(11);
        e.open_market();

        let open = e.tick(&request(10, 0));
        assert_eq!(open.market_status, MarketStatus::Open);
        assert_eq!(open.draws_consumed, 1 + 3 + 2 * 3 + 4 * 3);

        let extended = e.tick(&request(8, 0));
        assert_eq!(extended.market_status, MarketStatus::PreMarket);
        assert_eq!(
            extended.draws_consumed,
            1 + 3 + 2 * 3,
            "extended hours must not settle through the book"
        );
    }

    #[test]
    fn an_inactive_company_costs_no_draws() {
        let mut e = engine(13);
        e.companies_mut()[1].is_bankrupt = true;
        e.open_market();
        let out = e.tick(&request(10, 0));
        assert_eq!(out.active_indices, vec![0, 2]);
        assert_eq!(out.draws_consumed, 1 + 3 + 2 * 2 + 4 * 2);
    }

    #[test]
    fn embedder_draws_share_the_engines_stream() {
        // The point of `draw_uniform`. An embedder running its own generator
        // would interleave differently and diverge every price, so taking a
        // draw here must move the market that follows it.
        //
        // Asserted across the ROSTER after several ticks, not on one name
        // after one tick. Prints round to cents, so a sub-cent perturbation
        // legitimately need not move a $100 stock within a single minute — an
        // assertion that narrow would be demanding something the model does
        // not promise. Measured: after one tick only the $220 name differs;
        // by tick five all three do.
        let run = |extra_draws: usize| {
            let mut e = engine(99);
            e.open_market();
            for _ in 0..extra_draws {
                e.draw_uniform();
            }
            for m in 0..5i64 {
                e.tick(&request(10, m));
            }
            e.prices()
        };
        let without = run(0);
        let with_draw = run(1);
        assert!(
            without
                .iter()
                .zip(&with_draw)
                .any(|(a, b)| a.to_bits() != b.to_bits()),
            "an embedder draw must shift the shared stream: {without:?} vs {with_draw:?}"
        );
    }

    #[test]
    fn the_cumulative_draw_count_includes_embedder_draws() {
        let mut e = engine(5);
        e.draw_uniform();
        e.draw_normal();
        assert_eq!(e.draws_consumed(), 2);
        e.open_market();
        let out = e.tick(&request(10, 0));
        assert_eq!(e.draws_consumed(), 2 + out.draws_consumed);
    }

    #[test]
    fn the_daily_step_runs_economy_then_cycle_then_the_bank() {
        let mut e = engine(21);
        let vix_before = e.economy().vix;
        let out = e.advance_day(&DayAdvanceRequest {
            volatility: 0.7,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 1,
            timestamp: 24 * 60,
        });
        assert!(out.draws_consumed > 0, "the daily macro step must draw");
        assert_ne!(e.economy().vix, vix_before, "the economy must have stepped");
    }

    #[test]
    fn the_open_reset_anchors_the_breaker_to_todays_open() {
        let mut e = engine(3);
        e.companies_mut()[0].stock.price = 137.0;
        e.open_market();
        assert_eq!(e.column(PriceField::PreviousClose)[0], 137.0);
        assert_eq!(e.column(PriceField::Volume)[0], 0.0);
    }

    #[test]
    fn the_close_rolls_momentum_and_takes_no_draws() {
        let mut e = engine(17);
        e.open_market();
        for m in 0..10 {
            e.tick(&request(10, m));
        }
        let before = e.draws_consumed();
        e.close_market(&DayCloseRequest {
            daily_innovations: &[None, None, None],
            sector_base_variances: &[0.000225; 3],
            avg_volume: AvgVolumePolicy::default(),
        });
        assert_eq!(e.draws_consumed(), before, "the close must not draw");
        // `s` has moved during the session, so the roll must record it.
        assert!(e.companies()[0].stock.mispricing_momentum.is_some());
    }

    // ── Day-chunked stepping ──────────────────────────────────────────────

    fn session<'a>(
        ticks: usize,
        innovations: &'a [Option<f64>],
        variances: &'a [f64],
    ) -> SessionRequest<'a> {
        SessionRequest {
            start: GameTime {
                hour: 9,
                minute: 30,
                day_of_week: 3,
            },
            ticks,
            volatility_multiplier: 0.7,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            close_at_end: true,
            // These tests run one session per day, where opening inside the
            // session and opening the day are the same act. True preserves
            // exactly what they measured before `reopen` existed.
            reopen: true,
            daily_innovations: innovations,
            sector_base_variances: variances,
            stop: None,
        }
    }

    #[test]
    fn a_chunked_session_matches_driving_the_ticks_by_hand() {
        // The whole point of `run_session` is that it is the SAME simulation,
        // just without the boundary crossings. If it diverged from the manual
        // loop it would be a second engine.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];

        let chunked = {
            let mut e = engine(4242);
            let mut buf = SessionBuffer::new();
            e.run_session(&session(60, &innovations, &variances), &mut buf);
            (e.prices(), e.draws_consumed())
        };

        let by_hand = {
            let mut e = engine(4242);
            e.open_market();
            for m in 0..60i64 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                avg_volume: AvgVolumePolicy::default(),
            });
            (e.prices(), e.draws_consumed())
        };

        assert_eq!(chunked.1, by_hand.1, "draw counts must match");
        for (i, (a, b)) in chunked.0.iter().zip(&by_hand.0).enumerate() {
            assert_eq!(a.to_bits(), b.to_bits(), "company {i}");
        }
    }

    #[test]
    fn the_buffer_holds_every_tick_of_the_session() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(11);
        let mut buf = SessionBuffer::new();
        e.run_session(&session(40, &innovations, &variances), &mut buf);

        assert_eq!(buf.ticks_written, 40);
        assert_eq!(buf.companies, 3);
        assert_eq!(buf.prices.len(), 40 * 3);
        // The last written cross-section is the engine's current state.
        assert_eq!(buf.tick_prices(39), e.prices().as_slice());
        assert!(buf.prices.iter().all(|p| p.is_finite() && *p > 0.0));
    }

    #[test]
    fn the_buffer_is_reused_across_days_rather_than_growing() {
        // Nothing accumulates Rust-side: a year of tick-grain output would be
        // millions of rows, and the buffer is one day.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(7);
        let mut buf = SessionBuffer::new();

        for _ in 0..5 {
            e.run_session(&session(30, &innovations, &variances), &mut buf);
            assert_eq!(buf.prices.len(), 30 * 3, "the buffer grew across days");
        }
    }

    #[test]
    fn a_stop_condition_halts_the_session_and_leaves_the_day_open() {
        // Event-driven advancement. The close must NOT run: the day is not
        // over, the caller interrupted it, and running the close would roll
        // momentum on a half-day.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(31337);
        let mut buf = SessionBuffer::new();

        let mut req = session(390, &innovations, &variances);
        // A band tight enough that any movement trips it.
        let start = e.prices()[0];
        req.stop = Some(StopCondition::PriceOutside {
            company: 0,
            below: Some(start * 0.9999),
            above: Some(start * 1.0001),
        });

        let out = e.run_session(&req, &mut buf);
        assert!(out.halted_at.is_some(), "the stop condition never fired");
        let halted = out.halted_at.unwrap();
        assert!(
            halted < 389,
            "halted at the very end, so nothing was skipped"
        );
        assert_eq!(buf.ticks_written, halted + 1);
        // The close did not run. Probed via `last_daily_return`, which ONLY
        // `close_day` writes — `mispricing_momentum` would be the obvious
        // check and is wrong, because the tick lazy-initialises it to
        // Some(0.0) on a company that has never ticked.
        assert!(
            e.companies()[0].stock.last_daily_return.is_none(),
            "the close ran on an interrupted day"
        );
    }

    #[test]
    fn a_stop_condition_that_never_fires_runs_the_whole_session() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(5);
        let mut buf = SessionBuffer::new();
        let mut req = session(50, &innovations, &variances);
        req.stop = Some(StopCondition::PriceOutside {
            company: 0,
            below: Some(0.01),
            above: Some(1e9),
        });
        let out = e.run_session(&req, &mut buf);
        assert_eq!(out.halted_at, None);
        assert_eq!(buf.ticks_written, 50);
    }

    #[test]
    fn the_session_reports_the_draws_it_consumed() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(13);
        let mut buf = SessionBuffer::new();
        let out = e.run_session(&session(10, &innovations, &variances), &mut buf);
        // 10 open ticks at 1 + 3 sectors + 2 and 4 per company.
        assert_eq!(out.draws_consumed, 10 * (1 + 3 + 2 * 3 + 4 * 3));
        assert_eq!(e.draws_consumed(), out.draws_consumed);
    }

    #[test]
    fn columns_are_aligned_with_the_company_order() {
        let e = engine(1);
        let prices = e.column(PriceField::Price);
        assert_eq!(prices.len(), e.len());
        for (i, c) in e.companies().iter().enumerate() {
            assert_eq!(prices[i], c.stock.price);
        }
    }

    #[test]
    fn writing_prices_updates_market_cap_with_them() {
        let mut e = engine(1);
        e.write_prices(&[1.0, 2.0, 3.0]);
        assert_eq!(e.prices(), vec![1.0, 2.0, 3.0]);
        assert_eq!(e.column(PriceField::MarketCap)[0], 1.0 * 1e8);
    }

    #[test]
    fn a_full_session_runs_and_stays_bounded() {
        // 390 ticks with the day's boundaries, as an embedder would drive it.
        let mut e = engine(31337);
        e.open_market();
        for m in 0..390i64 {
            e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
        }
        e.close_market(&DayCloseRequest {
            daily_innovations: &[None; 3],
            sector_base_variances: &[0.000225; 3],
            avg_volume: AvgVolumePolicy::default(),
        });

        for (i, price) in e.prices().iter().enumerate() {
            assert!(
                price.is_finite() && *price > 0.0,
                "company {i} priced at {price}"
            );
            // The session breaker bounds every print against the open.
            let open = e.companies()[i].stock.previous_close;
            assert!(
                *price <= open * 1.25 + 1e-9 && *price >= (open * 0.75).max(0.01) - 1e-9,
                "company {i} escaped the session band: {price} against an open of {open}"
            );
        }
    }

    #[test]
    fn fundamentals_round_trip_including_the_absent_marker() {
        let mut e = engine(7);
        let n = e.len();
        assert!(n >= 3);

        e.set_fundamentals(
            &[8.0, f64::NAN, 0.0],
            &[25.0, 30.0, f64::NAN],
            &[0.2, 0.3, 0.4],
        )
        .expect("one value per company");

        let (eps, book, growth) = e.fundamentals();
        assert_eq!(eps[0], 8.0);
        assert!(eps[1].is_nan(), "NaN must survive as absent");
        // Zero is a real EPS -- a company that broke exactly even -- and must
        // NOT be confused with absent.
        assert_eq!(eps[2], 0.0);
        assert!(e.companies()[2].eps == Some(0.0));
        assert!(e.companies()[1].eps.is_none());
        assert_eq!(book[1], 30.0);
        assert!(book[2].is_nan());
        assert_eq!(growth[0], 0.2);
    }

    #[test]
    fn a_length_mismatch_is_refused_rather_than_truncated() {
        let mut e = engine(7);
        let n = e.len();
        assert!(e.set_fundamentals(&vec![1.0; n - 1], &vec![1.0; n], &vec![1.0; n]).is_err());
        assert!(e.set_fundamentals(&vec![1.0; n], &vec![1.0; n + 1], &vec![1.0; n]).is_err());
        assert!(e.set_fundamentals(&vec![1.0; n], &vec![1.0; n], &vec![1.0; n]).is_ok());
    }

    #[test]
    fn stale_earnings_move_the_price_which_is_why_this_exists() {
        // The claim the whole sync rests on: fair value is `eps * target_pe`,
        // so an engine that never hears about an earnings revision prices the
        // company on the fundamentals it was built with.
        //
        // Two identical engines, one told that every company doubled its
        // earnings. If the prices came out the same, syncing fundamentals
        // would be pointless work.
        let mut stale = engine(11);
        let mut fresh = engine(11);

        let n = fresh.len();
        let (eps, book, growth) = fresh.fundamentals();
        let doubled: Vec<f64> = eps.iter().map(|v| v * 2.0).collect();
        fresh
            .set_fundamentals(&doubled, &book, &growth)
            .expect("one value per company");

        for e in [&mut stale, &mut fresh] {
            e.open_market();
            e.run_session(&session(60, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        }

        let a = stale.prices();
        let b = fresh.prices();
        assert_eq!(a.len(), n);
        assert!(
            a.iter().zip(b.iter()).any(|(x, y)| x != y),
            "doubling every company's earnings changed no price at all"
        );
        // And it is the RNG-free difference: both engines drew the same
        // number of times, so the divergence is the valuation rather than a
        // shifted stream.
        assert_eq!(stale.draws_consumed(), fresh.draws_consumed());
    }

    #[test]
    fn a_bankrupt_company_stops_ticking_once_the_engine_is_told() {
        // The reason `set_status` exists. The tick skips a company only when
        // it reads `is_bankrupt || !is_public`, so before there was a setter
        // a failed company went on printing prices for ever.
        let mut e = engine(5);
        e.open_market();
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        let before = e.prices()[0];

        e.set_status(&[true, false, false], &[true, true, true])
            .expect("one flag per company");
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());

        assert_eq!(
            e.prices()[0],
            before,
            "company 0 was marked bankrupt and kept printing"
        );
        // And its neighbours did keep moving, so the test is not observing a
        // dead market.
        assert!(
            e.prices()[1] != before || e.prices()[2] != before,
            "nothing moved at all; this proves nothing about the flag"
        );
    }

    #[test]
    fn a_company_taken_private_stops_ticking_too() {
        let mut e = engine(5);
        e.open_market();
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        let before = e.prices()[1];

        e.set_status(&[false; 3], &[true, false, true])
            .expect("one flag per company");
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        assert_eq!(e.prices()[1], before, "an unlisted company kept printing");
    }

    #[test]
    fn status_round_trips_and_refuses_a_length_mismatch() {
        let mut e = engine(5);
        e.set_status(&[true, false, true], &[false, true, false])
            .expect("one flag per company");
        let (bankrupt, public) = e.status();
        assert_eq!(bankrupt, vec![true, false, true]);
        assert_eq!(public, vec![false, true, false]);
        assert!(e.set_status(&[true; 2], &[true; 3]).is_err());
        assert!(e.set_status(&[true; 3], &[true; 4]).is_err());
    }
}
