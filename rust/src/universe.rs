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
        // The lower bound sits below 1.0 so some names start visibly cheap.
        let pe_ratio = sector.avg_pe * between(&mut rng, 0.55, 1.6);

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

        // Book value below price for profitable names and above it for many
        // loss-makers, which is what makes the book path bind.
        let book_value_per_share = initial_price * between(&mut rng, 0.25, 1.4);

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
    fn generation_does_not_touch_the_engine_stream() {
        // Its own sequence, so "same universe, different market draws" is
        // expressible. If they shared a stream, changing the universe seed
        // would change the market and the experimental design would be a lie.
        assert_ne!(UNIVERSE_STREAM, 99, "must not be the MAIN stream");
    }
}
