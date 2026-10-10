//! The listed contracts' initial margin (`margin_scan_coverage`): the
//! engine's half.
//!
//! Each listed contract's one-session sd is an exponentially weighted mean
//! of its squared close-to-close mark changes, at a half-life of
//! [`MARGIN_HALF_LIFE`] sessions, the historical-volatility scaling clearing
//! houses' value-at-risk margins use. A contract listed with no history is
//! seeded from its family's front, or from its spec's daily sigma when the
//! family has none. Its initial margin, set at each close, is its multiplier
//! times `z * t * sigma`, `z` the normal quantile leaving `(1 - coverage) /
//! 2` in each tail and `t` the tail allowance (`margin_scan_tail`; 1 at
//! 0.0). Maintenance is initial over [`MAINTENANCE_RATIO`]. The margin reads
//! the marks and writes only its own state, so no price moves.
//!
//! Options (`options_index_listed`) are margined by a scan, as SPAN and
//! OCC's TIMS margin them: an option portfolio's initial margin is its worst
//! loss over [`SCAN_MOVES`] of the index, sized to `z * t` times the index's
//! one-session sd at the VIX, each at [`SCAN_VOL_SHIFTS`] of its implied
//! volatility, sized to `z * t` times [`SCAN_VOL_SD`] ([`option_scan_loss`]).
//! A long option's loss is at most its premium and a spread's at most its
//! width, so offsetting positions margin net, as a clearing house's scan
//! does. The scan reads quotes and writes nothing.

use super::*;
use crate::derivatives::pricing::black_greeks;
use crate::derivatives::{ContractKind, Right, OIL_FUTURE, POLICY_RATE_FUTURE, TERM_RATE_FUTURE, VIX_FUTURE};

/// The half-life, in sessions, of the margin's volatility.
pub const MARGIN_HALF_LIFE: f64 = 7.0;

/// Initial margin over maintenance margin.
pub const MAINTENANCE_RATIO: f64 = 1.1;

fn kind_code(kind: ContractKind) -> f64 {
    match kind {
        ContractKind::IndexFuture => 0.0,
        ContractKind::VixFuture => 1.0,
        ContractKind::PolicyRateFuture => 2.0,
        ContractKind::TermRateFuture => 3.0,
        // Options are margined by the scan and keep no record here.
        _ => 4.0,
    }
}

fn kind_of(code: f64) -> Option<ContractKind> {
    match code as i64 {
        _ if code.fract() != 0.0 => None,
        0 => Some(ContractKind::IndexFuture),
        1 => Some(ContractKind::VixFuture),
        2 => Some(ContractKind::PolicyRateFuture),
        3 => Some(ContractKind::TermRateFuture),
        4 => Some(ContractKind::OilFuture),
        _ => None,
    }
}

/// The index moves an option scan tries, as shares of its move size: the
/// seven price scan points of CME SPAN.
pub const SCAN_MOVES: [f64; 7] = [-1.0, -2.0 / 3.0, -1.0 / 3.0, 0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0];

/// The implied-volatility shifts an option scan tries at each move, as
/// shares of its volatility shift.
pub const SCAN_VOL_SHIFTS: [f64; 3] = [-1.0, 0.0, 1.0];

/// The one-session sd of an option's log implied volatility the scan's
/// volatility shift is sized to: a stated constant, about the daily sd of
/// the published VIX's log changes, not fitted to any row. The tail
/// allowance `margin_scan_tail` scales it with the index's move.
pub const SCAN_VOL_SD: f64 = 0.07;

/// One option position an option scan revalues.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct ScanOption {
    pub(crate) right: Right,
    /// The index less the present value of the dividends before expiry.
    pub(crate) carry_spot: f64,
    pub(crate) strike: f64,
    pub(crate) years: f64,
    pub(crate) vol: f64,
    pub(crate) rate: f64,
    /// Signed contracts times the multiplier: dollars per index point of the
    /// option's price, negative for a short.
    pub(crate) units: f64,
}

/// An option portfolio's worst loss over the scan, dollars, 0 when no
/// scenario loses: each of [`SCAN_MOVES`] times `index_move` (index points)
/// at each of [`SCAN_VOL_SHIFTS`] times `vol_shift` (a share of each
/// option's volatility), the dividends' present value, the rate and the
/// time to expiry held.
pub(crate) fn option_scan_loss(positions: &[ScanOption], index_move: f64, vol_shift: f64) -> f64 {
    if positions.is_empty() {
        return 0.0;
    }
    let now: Vec<f64> = positions
        .iter()
        .map(|p| black_greeks(p.right, p.carry_spot, p.strike, p.years, p.vol, p.rate).0)
        .collect();
    let mut worst: f64 = 0.0;
    for &m in &SCAN_MOVES {
        for &v in &SCAN_VOL_SHIFTS {
            let mut loss = 0.0;
            for (p, before) in positions.iter().zip(now.iter()) {
                let spot = crate::mathx::max(1e-9, p.carry_spot + m * index_move);
                let vol = crate::mathx::max(1e-6, p.vol * (1.0 + v * vol_shift));
                let after = black_greeks(p.right, spot, p.strike, p.years, vol, p.rate).0;
                loss += p.units * (before - after);
            }
            worst = crate::mathx::max(worst, loss);
        }
    }
    worst
}

/// One contract's margin record.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct MarginEntry {
    pub(crate) kind: ContractKind,
    pub(crate) expiry: i64,
    /// The last close's mark.
    pub(crate) last: f64,
    /// The one-session variance of its mark, price points squared.
    pub(crate) variance: f64,
    /// The close-to-close changes it has read.
    pub(crate) changes: u32,
}

const ENTRY_WIDTH: usize = 5;

/// Every listed contract's margin record.
#[derive(Debug, Clone, Default)]
pub(crate) struct MarginState {
    pub(crate) entries: Vec<MarginEntry>,
}

impl MarginState {
    /// The numbers the snapshot's `margin` key and the state hash carry: the
    /// entry count and each entry's kind (0 index, 1 VIX, 2 policy rate, 3
    /// term rate, 4 oil), expiry, last mark, variance and change count.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(1 + ENTRY_WIDTH * self.entries.len());
        out.push(self.entries.len() as f64);
        for e in &self.entries {
            out.push(kind_code(e.kind));
            out.push(e.expiry as f64);
            out.push(e.last);
            out.push(e.variance);
            out.push(f64::from(e.changes));
        }
        out
    }

    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's margin does not parse: {why}");
        let Some(&count) = words.first() else {
            return Err(bad("it is empty"));
        };
        if !(count.is_finite() && count >= 0.0 && count.fract() == 0.0 && count <= 1e6) {
            return Err(bad(&format!("the entry count is {count}")));
        }
        let n = count as usize;
        if words.len() != 1 + ENTRY_WIDTH * n {
            return Err(bad(&format!("{} numbers for {n} entries", words.len())));
        }
        let mut entries = Vec::with_capacity(n);
        for k in 0..n {
            let w = &words[1 + ENTRY_WIDTH * k..1 + ENTRY_WIDTH * (k + 1)];
            let kind = kind_of(w[0]).ok_or_else(|| bad(&format!("a kind is {}", w[0])))?;
            if !(w[1].is_finite() && w[1].fract() == 0.0) {
                return Err(bad(&format!("an expiry is {}", w[1])));
            }
            if !(w[2].is_finite() && w[3].is_finite() && w[3] >= 0.0) {
                return Err(bad("a mark or a variance is not finite and not negative"));
            }
            if !(w[4].is_finite() && w[4] >= 0.0 && w[4].fract() == 0.0 && w[4] <= f64::from(u32::MAX)) {
                return Err(bad(&format!("a change count is {}", w[4])));
            }
            entries.push(MarginEntry { kind, expiry: w[1] as i64, last: w[2], variance: w[3], changes: w[4] as u32 });
        }
        self.entries = entries;
        Ok(())
    }
}

/// The standard normal quantile at `p`, by bisection on its distribution
/// function.
fn normal_quantile(p: f64) -> f64 {
    let cdf = |x: f64| 0.5 * crate::mathx::erfc(-x / std::f64::consts::SQRT_2);
    let (mut lo, mut hi) = (-10.0, 10.0);
    for _ in 0..80 {
        let mid = 0.5 * (lo + hi);
        if cdf(mid) < p {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    0.5 * (lo + hi)
}

impl Engine {
    pub(super) fn margin_on(&self) -> bool {
        self.params.margin_scan_coverage != 0.0
    }

    /// Every listed contract's kind, expiry and last close's mark, in
    /// listing order.
    fn contract_marks(&self) -> Vec<(ContractKind, i64, f64)> {
        let mut out = Vec::new();
        if self.futures_on() {
            out.extend(self.futures.contracts.iter().map(|c| (ContractKind::IndexFuture, c.expiry, c.mark)));
        }
        if self.vix_futures_on() {
            out.extend(self.vix_futures.contracts.iter().map(|c| (ContractKind::VixFuture, c.expiry, c.mark)));
        }
        if self.rate_futures_on() {
            out.extend(self.rate_futures.contracts.iter().map(|c| (c.kind, c.expiry, c.mark)));
        }
        if self.oil_futures_on() {
            out.extend(self.oil_futures.contracts.iter().map(|c| (ContractKind::OilFuture, c.expiry, c.mark)));
        }
        out
    }

    /// The one-session variance a contract of `kind` marked at `mark` is
    /// seeded with when its family has no history: its spec's daily sigma.
    fn margin_seed(&self, kind: ContractKind, mark: f64) -> f64 {
        let sd = match kind {
            ContractKind::IndexFuture => mark * self.market_vol.sigma_daily(),
            ContractKind::VixFuture => mark * VIX_FUTURE.daily_sigma,
            ContractKind::PolicyRateFuture => POLICY_RATE_FUTURE.daily_sigma,
            ContractKind::TermRateFuture => TERM_RATE_FUTURE.daily_sigma,
            _ => mark * OIL_FUTURE.daily_sigma,
        };
        sd * sd
    }

    /// At a close, after every family's marks: each listed contract's
    /// variance takes its mark's change, a newly listed one is seeded, and
    /// a delisted one's record goes. Nothing with the dial off.
    pub(super) fn margin_close_update(&mut self) {
        if !self.margin_on() {
            return;
        }
        let decay = crate::mathx::pow(0.5, 1.0 / MARGIN_HALF_LIFE);
        let marks = self.contract_marks();
        let mut kept: Vec<MarginEntry> = Vec::with_capacity(marks.len());
        for (kind, expiry, mark) in marks {
            if !mark.is_finite() {
                continue;
            }
            let old = self.margin.entries.iter().find(|e| e.kind == kind && e.expiry == expiry).cloned();
            let entry = match old {
                Some(mut e) => {
                    let x = mark - e.last;
                    e.variance = decay * e.variance + (1.0 - decay) * x * x;
                    e.changes = e.changes.saturating_add(1);
                    e.last = mark;
                    e
                }
                None => {
                    // The family's front's variance, the first of its kind
                    // already kept or recorded; the spec's sigma without one.
                    let front = kept
                        .iter()
                        .chain(self.margin.entries.iter())
                        .find(|e| e.kind == kind)
                        .map(|e| e.variance);
                    let variance = front.unwrap_or_else(|| self.margin_seed(kind, mark));
                    MarginEntry { kind, expiry, last: mark, variance, changes: 0 }
                }
            };
            kept.push(entry);
        }
        self.margin.entries = kept;
    }

    /// The option scan's size at the VIX `vix` and the index `index`: the
    /// index move, `z * t` times the index's one-session sd the VIX implies
    /// (`VIX = (1 + vix_variance_premium) 100 sqrt(252 V)`) in index points,
    /// and the volatility shift, `z * t` times [`SCAN_VOL_SD`].
    pub(super) fn option_scan_size(&self, vix: f64, index: f64) -> (f64, f64) {
        let p = &self.params;
        let tail = if p.margin_scan_tail == 0.0 { 1.0 } else { p.margin_scan_tail };
        let z = normal_quantile(0.5 + 0.5 * p.margin_scan_coverage) * tail;
        let sigma = vix / (100.0 * (1.0 + p.vix_variance_premium) * crate::mathx::sqrt(252.0));
        (z * sigma * index, z * SCAN_VOL_SD)
    }

    /// A listed contract's initial margin, dollars a contract: `None` with
    /// the dial off and before the contract's first close.
    pub(crate) fn initial_margin(&self, kind: ContractKind, expiry: i64, multiplier: f64) -> Option<f64> {
        if !self.margin_on() {
            return None;
        }
        let e = self.margin.entries.iter().find(|e| e.kind == kind && e.expiry == expiry)?;
        let p = &self.params;
        let tail = if p.margin_scan_tail == 0.0 { 1.0 } else { p.margin_scan_tail };
        let z = normal_quantile(0.5 + 0.5 * p.margin_scan_coverage);
        Some(multiplier * z * tail * crate::mathx::sqrt(e.variance))
    }

    /// The margin's numbers for the snapshot and the hash: `None` with the
    /// dial off.
    pub fn margin_words(&self) -> Option<Vec<f64>> {
        if self.margin_on() {
            Some(self.margin.to_words())
        } else {
            None
        }
    }

    /// Put the margin back (a restore), refused where the dial is off.
    pub fn set_margin_state(&mut self, words: Option<&[f64]>) -> Result<(), String> {
        match words {
            Some(_) if !self.margin_on() => Err(
                "this snapshot carries the contracts' margin (margin), which only an engine with \
                 margin_scan_coverage set keeps, and this engine's is 0"
                    .to_string(),
            ),
            Some(w) => {
                let mut state = MarginState::default();
                state.set_words(w)?;
                self.margin = state;
                Ok(())
            }
            None if self.margin_on() => Err(
                "an engine with margin_scan_coverage set carries its contracts' margin (margin); this \
                 snapshot lacks it"
                    .to_string(),
            ),
            None => {
                self.margin = MarginState::default();
                Ok(())
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_quantile_inverts_the_normal() {
        assert!(normal_quantile(0.5).abs() < 1e-12);
        assert!((normal_quantile(0.995) - 2.575_829_303_548_9).abs() < 1e-9);
        assert!((normal_quantile(0.975) - 1.959_963_984_540_1).abs() < 1e-9);
    }

    fn option(right: Right, strike: f64, units: f64) -> ScanOption {
        ScanOption { right, carry_spot: 1000.0, strike, years: 21.0 / 252.0, vol: 0.2, rate: 0.03, units }
    }

    #[test]
    fn the_option_scan_margins_shorts_and_nets_offsetting_positions() {
        let (m, v) = (30.0, 0.18);
        let price = |o: &ScanOption| black_greeks(o.right, o.carry_spot, o.strike, o.years, o.vol, o.rate).0;
        // A short call loses most on the largest rise at the highest volatility.
        let short = option(Right::Call, 1000.0, -100.0);
        let up = ScanOption { carry_spot: 1030.0, vol: 0.2 * 1.18, ..short };
        let want = 100.0 * (price(&up) - price(&short));
        assert!((option_scan_loss(&[short], m, v) - want).abs() < 1e-9 * want);
        // A long option loses at most its premium.
        let long = option(Right::Put, 980.0, 100.0);
        let premium = 100.0 * price(&long);
        let l = option_scan_loss(&[long], m, v);
        assert!(l > 0.0 && l < premium);
        // A position and its offset margin nothing.
        assert_eq!(option_scan_loss(&[short, option(Right::Call, 1000.0, 100.0)], m, v), 0.0);
        // A call spread loses at most its width.
        let spread = [option(Right::Call, 1000.0, -100.0), option(Right::Call, 1010.0, 100.0)];
        let s = option_scan_loss(&spread, m, v);
        assert!(s > 0.0 && s <= 100.0 * 10.0);
        assert!(s < option_scan_loss(&spread[..1], m, v));
        // A wider scan asks more of a short.
        assert!(option_scan_loss(&[short], 2.0 * m, v) > option_scan_loss(&[short], m, v));
        assert_eq!(option_scan_loss(&[], m, v), 0.0);
    }

    #[test]
    fn the_words_round_trip() {
        let s = MarginState {
            entries: vec![
                MarginEntry { kind: ContractKind::VixFuture, expiry: 35, last: 18.5, variance: 0.81, changes: 4 },
                MarginEntry { kind: ContractKind::OilFuture, expiry: 56, last: 74.2, variance: 2.25, changes: 0 },
            ],
        };
        let words = s.to_words();
        let mut back = MarginState::default();
        back.set_words(&words).unwrap();
        assert_eq!(back.entries, s.entries);
        assert!(MarginState::default().set_words(&words[..words.len() - 1]).is_err());
    }
}
