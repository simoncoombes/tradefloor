//! Universe generation.
//!
//! # Why this is required rather than a convenience
//!
//! A realistic study needs on the order of a hundred instruments. No research
//! user is going to hand-author a hundred rosters, so without a generator the
//! practical universe size is "however many someone was willing to type",
//! which is not a modelling choice anyone made on purpose.
//!
//! # Its seed is separate from the simulation's, deliberately
//!
//! "Same universe, different market draws" is the standard experimental design
//! for variance estimation, and it is only expressible if the two seeds are
//! independent. Generation therefore runs on its own stream and consumes
//! nothing from the engine's.
//!
//! # These ranges are editorial, and say so
//!
//! Unlike the sector anchors, nothing here is pinned by a golden vector. They
//! are chosen to produce a *plausible* cross-section — a spread of market
//! caps, P/E ratios near each sector's anchor, loss-makers among the
//! growth names — rather than uniform noise, which would make every study run
//! on a market that looks nothing like one.
//!
//! What IS guaranteed is determinism: the same `(n, seed)` gives the same
//! universe on every platform, which is what makes `random(108, seed=7)`
//! citable in a paper.

use crate::rng::GameRng;
use crate::sectors::{Sector, SECTORS};

/// The stream universe generation draws from.
///
/// Distinct from the engine's MAIN stream so that generating a universe
/// cannot perturb the market, and vice versa.
const UNIVERSE_STREAM: u32 = 21;

/// Widest the P/E scatter reaches from its sector anchor, as a ratio.
///
/// The scatter is drawn LOG-uniformly between `anchor / PE_SCATTER` and
/// `anchor * PE_SCATTER`, so its endpoints are reciprocals and its mean in
/// log is exactly zero. That is the whole of why this constant is a single
/// ratio rather than a pair of bounds: a pair can be written down that does
/// not centre, and one was. See [`random_universe`].
///
/// 1.7 rather than a round 2.0 because it preserves the previous scatter's
/// WIDTH while moving its centre. The old bounds spanned `ln(1.6/0.55)` =
/// 1.068 in log; these span `2*ln(1.7)` = 1.061, which is inside one per
/// cent. The cross-section is as dispersed as it was and is no longer
/// tilted.
const PE_SCATTER: f64 = 1.7;

/// Widest the book-value scatter reaches from a fairly valued book, as a
/// ratio. Same construction and the same reason as [`PE_SCATTER`].
///
/// 2.4 preserves the previous scatter's width: the old bounds spanned
/// `ln(1.4/0.25)` = 1.723 in log against this one's `2*ln(2.4)` = 1.751.
const BOOK_SCATTER: f64 = 2.4;

/// One generated instrument, in the shape the public API accepts.
#[derive(Debug, Clone, PartialEq)]
pub struct GeneratedInstrument {
    pub ticker: String,
    pub sector: &'static str,
    pub initial_price: f64,
    pub shares_outstanding: f64,
    pub eps: f64,
    pub book_value_per_share: f64,
    pub revenue_growth: f64,
    pub avg_volume: f64,
    pub beta: f64,
    /// Shares sold short. A COUNT, not a fraction -- the squeeze rule divides
    /// it by the float, so a value of `0.03` means three hundredths of one
    /// share, not three per cent.
    pub short_interest: f64,
}

/// Uniform in `[lo, hi)`.
fn between(rng: &mut GameRng, lo: f64, hi: f64) -> f64 {
    lo + rng.next_f64() * (hi - lo)
}

/// Log-uniform in `[lo, hi)`.
///
/// Market capitalisation spans four orders of magnitude and is roughly
/// log-distributed. Drawing it uniformly would put almost every name in the
/// mega-cap tier and produce a market with no small caps in it — and since the
/// spread tier is selected from market cap, that would also mean a market with
/// almost no illiquid names, which is the interesting half.
fn log_between(rng: &mut GameRng, lo: f64, hi: f64) -> f64 {
    let t = rng.next_f64();
    crate::mathx::exp(crate::mathx::log(lo) + t * (crate::mathx::log(hi) - crate::mathx::log(lo)))
}

/// A deterministic ticker from an index: AAA, AAB, ... ZZZ.
///
/// Positional rather than random so two universes of different sizes share a
/// prefix, which makes eyeballing a diff between them possible.
fn ticker_for(index: usize) -> String {
    let a = index / (26 * 26) % 26;
    let b = index / 26 % 26;
    let c = index % 26;
    [
        (b'A' + a as u8) as char,
        (b'A' + b as u8) as char,
        (b'A' + c as u8) as char,
    ]
    .iter()
    .collect()
}

/// Generate `n` plausible instruments.
///
/// Sectors are assigned round-robin rather than sampled, so every sector is
/// represented at any `n >= 12` and no run is missing a whole slice of the
/// market by chance. Within a sector everything else is drawn.
///
/// # A generated roster starts at its own fair value
///
/// The price and the fundamentals that value it are drawn here, and they
/// have to be RECONCILED or the roster opens away from fair value and the
/// first quarter of every run is spent unwinding that. This generator did
/// not reconcile them, and the cost was measurable: at pt-v16 the day-zero
/// cross-sectional mean of `log(price / fair_value)` was **+0.050** over
/// two hundred rosters of forty names, with 58 per cent of names above
/// fair value, and the equal-weight index then drifted down for a year on
/// a 60-day half-life as that unwound.
///
/// What that was worth, isolated. On `Universe.random(40, seed=111)` at
/// 252 days over seeds 1 to 30, with the down tilt, the jump mean and the
/// whole macro path all switched off so that only the initial condition is
/// left to unwind, the equal-weight index drifted **-8.417** per cent a
/// year on the roster the previous generator produced and **-4.585** on
/// this one. Both arms ran on one build, the old roster loaded back
/// through `Universe::from_json`, so the roster is the only difference
/// between them.
///
/// The half that survives is that roster's own draw and not a residual
/// bias: seed 111 still opens at +0.048 against +0.087, which is 54.9 per
/// cent of its old day-zero mispricing, and it still drifts -4.585 against
/// -8.417, which is 54.5 per cent of its old day-zero drift. An initial
/// condition decaying on a fixed half-life predicts exactly that
/// proportionality, and the two agree to half a percentage point.
///
/// Both halves of the reconciliation are the same statement. Fair value is
/// `eps * target_pe` for a profitable name and `book * LOSS_MAKING_PRICE_TO_BOOK`
/// for a loss-maker, so in both cases the price sits at fair value exactly
/// when the drawn ratio is one. The ratio is therefore drawn log-uniformly
/// between reciprocal endpoints, which puts its mean in log at zero and
/// half the names on each side of fair value, rather than uniformly over a
/// range whose midpoint is one. A range that is symmetric in the ratio is
/// not symmetric in the log of it, and the difference is a bias:
/// `between(0.55, 1.6)` centres on 1.075, and `E[ln u]` over it is
/// +0.0293, so the old multiple was systematically above its anchor, the
/// earnings that price implied systematically below, and the valuation
/// below the price on which it was built.
///
/// # What this does not reconcile
///
/// The neutral point of the valuation model, not the opening state of any
/// particular market. `target_pe` is the sector anchor exactly when the
/// discount rate is `NEUTRAL_DISCOUNT_RATE`; the world opens 56 basis
/// points above that, which compresses every profitable name's multiple by
/// about one per cent and leaves a residual day-zero mispricing of
/// **+0.0107** on the earnings path and zero on the book path, since the
/// book path applies no rate adjustment.
///
/// That residual is deliberately left here. It is a property of where the
/// economy opens relative to where the valuation model is neutral, not of
/// this generator, and correcting it here would bake an economy constant
/// into universe generation, where nothing would catch it going stale if
/// the opening state moved. It would also make a roster depend on the
/// market it is about to be run in, and the independence of the two seeds
/// is the property the module header exists to defend.
///
/// # What is centred is the draw, which is the thing that was biased
///
/// The defect was a biased draw and the draw is now unbiased. A roster of
/// forty names taken from it still scatters, and that scatter is sampling
/// error rather than bias: the cross-sectional sd of the day-zero
/// mispricing is about 0.32, so a forty-name mean carries a standard error
/// of about 0.05 and any roster lands that far either side by chance.
/// `Universe.random(40, seed=111)` is one that draws high, reading +0.048
/// where the draw it comes from now averages +0.013. A different seed is as
/// likely to read low.
///
/// Removing that too would mean correcting each roster to its own realised
/// mean, and that is deliberately not done rather than merely undone. The
/// correction depends on `n`, so it is a second pass whose result depends
/// on the roster size, and it would break the invariant that a larger
/// universe extends a smaller one. That invariant is why the short interest
/// below is drawn inside this loop rather than in a pass of its own, and it
/// is worth more than a centred forty-name sample. A generator that
/// silently re-centred every roster would also be reporting a cross-section
/// tighter than the one it drew.
pub fn random_universe(n: usize, seed: u32) -> Vec<GeneratedInstrument> {
    let mut rng = GameRng::new(seed, UNIVERSE_STREAM);
    let mut out = Vec::with_capacity(n);

    for i in 0..n {
        let sector: &Sector = &SECTORS[i % SECTORS.len()];

        // $200M to $2T. The spread tier thresholds sit at $1B, $10B and $50B,
        // so this range straddles all four tiers rather than clustering in one.
        let market_cap = log_between(&mut rng, 2.0e8, 2.0e12);
        let initial_price = log_between(&mut rng, 3.0, 600.0);
        let shares_outstanding = market_cap / initial_price;

        // P/E scattered around the sector anchor, so a generated name is
        // valued near its sector without every name being identical to it.
        // Log-uniform between reciprocal endpoints: some names start
        // visibly cheap, as many start visibly dear, and the roster as a
        // whole starts on its anchor rather than above it.
        //
        // ONE draw, as `between` was, so the stream position after this
        // line is what it always was and every other field of every name is
        // untouched.
        let pe_ratio = sector.avg_pe
            * log_between(&mut rng, 1.0 / PE_SCATTER, PE_SCATTER);

        // Roughly one name in nine loses money. Loss-makers are a real and
        // load-bearing part of a cross-section: they take the book-value
        // valuation path, which is otherwise untested by any generated
        // universe.
        let loss_maker = rng.next_f64() < 0.11;
        let eps = if loss_maker {
            -initial_price * between(&mut rng, 0.01, 0.09)
        } else {
            initial_price / pe_ratio
        };

        // Book value scattered around the book a loss-maker would need to be
        // fairly valued at this price, which is `price / LOSS_MAKING_PRICE_TO_BOOK`
        // because the book path values a name at book times that multiple.
        // Reciprocal endpoints again, so a loss-maker is as likely to open
        // cheap as dear. Drawn for every name, profitable or not, exactly as
        // before: `fair_value_book_floor` reads it on the earnings path too,
        // and the draw order does not depend on which path a name takes.
        //
        // Book value stays below price for most profitable names and above
        // it for many loss-makers, which is what makes the book path bind.
        let book_centre = initial_price / crate::fair_value::LOSS_MAKING_PRICE_TO_BOOK;
        let book_value_per_share = log_between(
            &mut rng,
            book_centre / BOOK_SCATTER,
            book_centre * BOOK_SCATTER,
        );

        // Growth correlates loosely with the sector's multiple: a 32x anchor
        // implies the market expects growth, a 10x anchor does not.
        let growth_centre = (sector.avg_pe - 10.0) / 100.0;
        let revenue_growth = growth_centre + between(&mut rng, -0.06, 0.14);

        // Turnover scales with size but not proportionally -- small caps trade
        // a larger fraction of their float.
        let turnover = between(&mut rng, 0.001, 0.02);
        let avg_volume = crate::mathx::max(1000.0, shares_outstanding * turnover);

        // Beta around the sector's relative volatility, which is what that
        // field is actually for.
        let beta = crate::mathx::max(0.15, sector.volatility * between(&mut rng, 0.6, 1.35));

        // Shares sold short, as a COUNT. Log-uniform between 0.4% and 30% of
        // shares outstanding: the squeeze rule fires above a fifth of the
        // float, so roughly one name in eleven can squeeze.
        //
        // Drawn LAST in the per-instrument block, and inside the loop rather
        // than in a second pass over the roster. A second pass would have left
        // every other field byte-identical to the previous generator, which
        // was tempting -- and it breaks the invariant that a larger universe
        // extends a smaller one, because the second pass starts at a stream
        // position that depends on `n`. Generation is a single pass over one
        // stream, and that is what makes "the same market plus twenty more
        // instruments" expressible.
        let short_interest = shares_outstanding * log_between(&mut rng, 0.004, 0.30);

        out.push(GeneratedInstrument {
            ticker: ticker_for(i),
            sector: sector.key,
            initial_price,
            shares_outstanding,
            eps,
            book_value_per_share,
            revenue_growth,
            avg_volume,
            beta,
            short_interest,
        });
    }


    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_same_seed_gives_the_same_universe() {
        // The property that makes `random(108, seed=7)` citable.
        assert_eq!(random_universe(50, 7), random_universe(50, 7));
    }

    #[test]
    fn a_different_seed_gives_a_different_universe() {
        assert_ne!(random_universe(50, 7), random_universe(50, 8));
    }

    #[test]
    fn a_larger_universe_extends_a_smaller_one() {
        // Generation is a single pass over one stream, so the first n names of
        // a bigger universe are the same names. That is worth having: it makes
        // "the same market plus twenty more instruments" expressible, and it
        // makes a diff between two sizes readable.
        let small = random_universe(20, 3);
        let large = random_universe(60, 3);
        assert_eq!(small[..], large[..20]);
    }

    #[test]
    fn every_sector_is_represented_once_the_universe_is_large_enough() {
        // Round-robin, not sampled. Sampling would leave some run missing a
        // whole slice of the market by chance, and the user would have no way
        // to know their study had no utilities in it.
        let u = random_universe(12, 1);
        let mut seen: Vec<&str> = u.iter().map(|i| i.sector).collect();
        seen.sort_unstable();
        seen.dedup();
        assert_eq!(seen.len(), 12);
    }

    #[test]
    fn it_contains_loss_makers_and_profitable_names() {
        // A universe of only profitable companies never exercises the
        // book-value valuation path, so a study on it would silently cover
        // half the model.
        let u = random_universe(200, 11);
        let losers = u.iter().filter(|i| i.eps < 0.0).count();
        assert!(losers > 5, "only {losers} loss-makers in 200");
        assert!(losers < 60, "{losers} loss-makers in 200 is not a market");
    }

    #[test]
    fn market_caps_span_every_spread_tier() {
        // The tiers are $1B, $10B and $50B. A universe sitting entirely in one
        // of them would have uniform liquidity, which is the opposite of what
        // a microstructure study needs.
        let u = random_universe(300, 5);
        let caps: Vec<f64> = u
            .iter()
            .map(|i| i.initial_price * i.shares_outstanding)
            .collect();
        assert!(caps.iter().any(|c| *c < 1.0e9), "no small caps");
        assert!(caps.iter().any(|c| *c > 5.0e10), "no mega caps");
    }

    #[test]
    fn everything_generated_is_finite_and_sane() {
        for inst in random_universe(500, 13) {
            assert!(inst.initial_price > 0.0 && inst.initial_price.is_finite());
            assert!(inst.shares_outstanding > 0.0 && inst.shares_outstanding.is_finite());
            assert!(inst.avg_volume >= 1000.0 && inst.avg_volume.is_finite());
            assert!(inst.beta > 0.0 && inst.beta.is_finite());
            assert!(inst.eps.is_finite());
            assert!(inst.book_value_per_share > 0.0);
            assert!(inst.revenue_growth.is_finite());
        }
    }

    #[test]
    fn tickers_are_unique_and_positional() {
        let u = random_universe(300, 2);
        assert_eq!(u[0].ticker, "AAA");
        assert_eq!(u[26].ticker, "ABA");
        let mut t: Vec<&str> = u.iter().map(|i| i.ticker.as_str()).collect();
        t.sort_unstable();
        t.dedup();
        assert_eq!(t.len(), 300, "tickers collided");
    }

    #[test]
    fn a_generated_roster_opens_at_its_own_fair_value() {
        // The defect this reconciliation closed: the price was drawn first
        // and the earnings that value it were drawn from a scatter whose
        // mean in LOG sat above one, so fair value came out below the price
        // it was built from and every roster opened overvalued.
        //
        // Measured at the valuation model's neutral point, where
        // `target_pe` is the sector anchor exactly, so this test asserts the
        // generator's own contribution and not the economy's opening state.
        let u = random_universe(4000, 7);
        let economy = crate::fair_value::EconomyValuationInputs {
            corporate_bond_yield: Some(crate::fair_value::NEUTRAL_DISCOUNT_RATE * 100.0),
            federal_funds_rate: crate::fair_value::NEUTRAL_DISCOUNT_RATE * 100.0,
            qe_pe_boost: Some(0.0),
            qe_assets_ratio: None,
        };
        let mut total = 0.0;
        let mut above = 0usize;
        let mut on_book = 0usize;
        for inst in &u {
            let sector = crate::sectors::by_key(inst.sector).expect("generated");
            let breakdown = crate::fair_value::compute_fair_value(
                &crate::fair_value::CompanyValuationInputs {
                    sector_avg_pe: Some(sector.avg_pe),
                    eps: Some(inst.eps),
                    book_value_per_share: Some(inst.book_value_per_share),
                    revenue_growth: Some(inst.revenue_growth),
                },
                &economy,
            );
            let s = crate::mathx::log(inst.initial_price / breakdown.fair_value);
            total += s;
            if s > 0.0 {
                above += 1;
            }
            if breakdown.book_value_path {
                on_book += 1;
            }
        }
        let mean = total / u.len() as f64;
        // The sampling error of a 4000-name mean is about 0.005, so 0.02 is
        // four of them and this bound is loose enough not to be flaky and
        // tight enough to fail the +0.044 the old generator produced.
        assert!(
            mean.abs() < 0.02,
            "day-zero mean log(price/fair_value) is {mean}, not centred"
        );
        let fraction = above as f64 / u.len() as f64;
        assert!(
            (fraction - 0.5).abs() < 0.03,
            "{fraction} of names open above fair value, not about half"
        );
        // Both valuation paths are exercised, or the assertion above could
        // pass while one of the two reconciliations was wrong.
        assert!(on_book > 200, "only {on_book} names on the book path");
        assert!(on_book < 800, "{on_book} names on the book path is not a market");
    }

    #[test]
    fn the_valuation_ratios_are_symmetric_in_log_rather_than_in_the_ratio() {
        // The mechanism behind the test above, stated on its own so a
        // failure says which half broke. A range symmetric in the ratio is
        // biased in the log of it, which is the form the valuation reads.
        // What has to hold is that the range's midpoint IN LOG is zero,
        // because `log_between` interpolates in log and the valuation
        // multiplies by the ratio. Asserting the endpoints are reciprocal
        // would be a proxy for it, and a weaker one.
        for scatter in [PE_SCATTER, BOOK_SCATTER] {
            let midpoint_in_log = crate::mathx::log(1.0 / scatter)
                + crate::mathx::log(scatter);
            assert!(
                midpoint_in_log.abs() < 1e-15,
                "range around {scatter} is off-centre in log by {midpoint_in_log}"
            );
        }
        // The old bounds, kept here as the counter-example: their midpoint
        // is 1.075 and their geometric mean is 1.0298, and it is the second
        // number the valuation multiplies by.
        let midpoint = (0.55 + 1.6) / 2.0;
        let geometric = crate::mathx::exp((crate::mathx::log(0.55)
            + crate::mathx::log(1.6))
            / 2.0);
        assert!(midpoint > 1.0 && geometric < 1.0);
        // And the width is preserved, so this is a re-centring and not a
        // narrowing of the cross-section.
        let was = crate::mathx::log(1.6 / 0.55);
        let now = 2.0 * crate::mathx::log(PE_SCATTER);
        assert!((now / was - 1.0).abs() < 0.02, "pe scatter width moved");
        let was_book = crate::mathx::log(1.4 / 0.25);
        let now_book = 2.0 * crate::mathx::log(BOOK_SCATTER);
        assert!((now_book / was_book - 1.0).abs() < 0.02, "book scatter width moved");
    }

    #[test]
    fn reconciling_the_roster_did_not_move_the_stream() {
        // Both reconciled fields take exactly one draw each, as they did
        // before, so every OTHER field is bit-identical to what this
        // generator has always produced. That is what keeps the change
        // contained to the two quantities it is about.
        //
        // Pinned against values recorded from the previous generator at
        // 70bcbb8, `random_universe(40, 111)`, name 0.
        let u = random_universe(40, 111);
        assert_eq!(u[0].ticker, "AAA");
        assert_eq!(u[0].sector, "technology");
        assert_eq!(u[0].initial_price, 200.45251589262966);
        assert_eq!(u[0].shares_outstanding, 19483093.73920724);
        assert_eq!(u[0].revenue_growth, 0.2776378159318119);
        assert_eq!(u[0].avg_volume, 75955.40594971534);
        assert_eq!(u[0].beta, 0.92810710019432);
        assert_eq!(u[0].short_interest, 3531201.404338788);
    }

    #[test]
    fn generation_does_not_touch_the_engine_stream() {
        // Its own sequence, so "same universe, different market draws" is
        // expressible. If they shared a stream, changing the universe seed
        // would change the market and the experimental design would be a lie.
        assert_ne!(UNIVERSE_STREAM, 99, "must not be the MAIN stream");
    }
}

/// One instrument's initial state, independent of any binding.
///
/// The mapping below decides a market's STARTING CONDITIONS -- that the
/// previous close is the opening price, that GARCH variance begins at the
/// sector's base, that float equals shares outstanding. Those are modelling
/// decisions, not glue, and a binding that re-made them would fork the
/// initial state while looking like it was only copying fields. Both the
/// Python and WebAssembly surfaces build this and call `to_tick_company`.
pub struct InstrumentInit {
    pub ticker: String,
    pub sector: String,
    pub initial_price: f64,
    pub shares_outstanding: f64,
    pub eps: Option<f64>,
    pub book_value_per_share: Option<f64>,
    pub revenue_growth: Option<f64>,
    pub avg_volume: f64,
    pub beta: f64,
    pub short_interest: f64,
}

impl InstrumentInit {
    pub fn market_cap(&self) -> f64 {
        self.initial_price * self.shares_outstanding
    }

    /// The engine's view of this instrument on day zero.
    ///
    /// `index` disambiguates the id when a roster repeats a ticker; roster
    /// order is contractual, so it is the position and not a hash.
    pub fn to_tick_company(&self, index: usize) -> crate::market::TickCompany {
        let sector = crate::sectors::by_key(&self.sector)
            .expect("validated at construction");
        crate::market::TickCompany {
            id: format!("{}-{index}", self.ticker),
            ticker: self.ticker.clone(),
            sector: self.sector.clone(),
            is_bankrupt: false,
            is_public: true,
            stock: crate::market::TickStock {
                price: self.initial_price,
                previous_close: self.initial_price,
                previous_tick_price: None,
                open: self.initial_price,
                high: self.initial_price,
                low: self.initial_price,
                volume: 0.0,
                avg_volume: self.avg_volume,
                shares_outstanding: self.shares_outstanding,
                market_cap: self.market_cap(),
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                maker_inventory: None,
                garch_variance: sector.base_daily_variance(),
                // Seeded at the sector base, like `garch_variance` beside
                // it, so a cascade switched on mid-life starts where the
                // single-component process already is.
                garch_cascade: [sector.base_daily_variance();
                                crate::market::garch::CASCADE_MAX],
                last_daily_return: None,
                beta: Some(self.beta),
                short_interest: self.short_interest,
                float: self.shares_outstanding,
            },
            sector_volatility: Some(sector.volatility),
            sector_avg_pe: Some(sector.avg_pe),
            eps: self.eps,
            book_value_per_share: self.book_value_per_share,
            revenue_growth: self.revenue_growth,
        }
    }
}

impl GeneratedInstrument {
    /// The generated roster's instruments carry every field, so the
    /// optional ones are always present.
    pub fn to_init(&self) -> InstrumentInit {
        InstrumentInit {
            ticker: self.ticker.clone(),
            sector: self.sector.to_string(),
            initial_price: self.initial_price,
            shares_outstanding: self.shares_outstanding,
            eps: Some(self.eps),
            book_value_per_share: Some(self.book_value_per_share),
            revenue_growth: Some(self.revenue_growth),
            avg_volume: self.avg_volume,
            beta: self.beta,
            short_interest: self.short_interest,
        }
    }
}
