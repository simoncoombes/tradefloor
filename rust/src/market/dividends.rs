//! Cash dividends (`ModelParams::dividend_payout_share`).
//!
//! Off by default: with the dial at 0.0 nothing here is called, no name
//! carries a [`DividendState`], and every preset is bit-identical.
//!
//! # The rule
//!
//! A name pays when its earnings are positive and its revenue growth is under
//! `dividend_growth_cutoff`. Its PAYOUT is `min(1, dial * sector payout)`
//! (`crate::sectors::Sector::dividend_payout`) and its TARGET YIELD is that
//! payout times its earnings over its price when the state is created (the
//! price at construction on a fresh engine). Both are public: a reader of
//! the roster can compute them.
//!
//! Ex-dates fall every [`DIVIDEND_PERIOD`] sessions from a per-name phase, a
//! hash of the ticker ([`phase_for`]), so the roster's ex-dates spread over
//! the quarter with no draw. Each amount is declared [`DECLARATION_LEAD`]
//! sessions before its ex-date by a Lintner partial adjustment toward the
//! target yield times an EMA of the name's own closes, capped at
//! `dividend_yield_ceiling` times the target yield at the declaring close.
//!
//! The rule reads the name's own PAST CLOSES, not its earnings. The only
//! earnings a dividend could read here are hidden state (the fair-value
//! level against the mispricing, and the true earnings cycle, published a
//! year late), so an earnings rule would leak both. In this model the
//! fair-value level carries the market's permanent moves, so the earnings
//! and the price move together and the price rule follows earnings anyway.
//!
//! # Fair value and the ex-date
//!
//! Fair value accrues the next amount between ex-dates, `A = D * k / 63` at
//! `k` sessions since the last ex-date, added in dollars wherever fair value
//! is read (the tick, the overnight opening print, the re-mark, the
//! stationary opening). At the ex-date open the price drops by the amount
//! exactly and the accrual starts again from zero, so the price path is the
//! same at first order and a holder's total return rises by the yield.

use crate::mathx;
use crate::params::ModelParams;

use super::tick::TickCompany;

/// Sessions between a name's ex-dates: a quarter of the market year.
pub const DIVIDEND_PERIOD: i64 = 63;

/// Sessions between a declaration and its ex-date.
pub const DECLARATION_LEAD: i64 = 21;

/// Half-life, in sessions, of the EMA of closes the declaration reads.
pub const PRICE_EMA_HALF_LIFE: f64 = 21.0;

/// A name's dividend state. Present on every public name while
/// `dividend_payout_share` is set (a non-payer carries a zero payout), and
/// absent otherwise.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
#[non_exhaustive]
pub struct DividendState {
    /// Share of earnings distributed: `min(1, dial * sector payout)`, or 0.
    pub payout: f64,
    /// The target annual yield, payout times earnings over the price when
    /// the state was created.
    pub target_yield: f64,
    /// EMA of the name's closes, [`PRICE_EMA_HALF_LIFE`] sessions.
    pub price_ema: f64,
    /// The quarterly amount: the one declared, or the last paid until the
    /// next declaration.
    pub amount: f64,
    /// Whether `amount` is the declared amount for the next ex-date.
    pub declared: bool,
    /// Fair value's accrual this session, in price units.
    pub accrual: f64,
    /// The amount that went ex at this session's open, 0.0 on any other.
    pub paid_today: f64,
}

/// Number of f64s a state takes in the snapshot, in field order.
pub const STATE_WIDTH: usize = 7;

impl DividendState {
    pub fn to_array(&self) -> [f64; STATE_WIDTH] {
        [
            self.payout,
            self.target_yield,
            self.price_ema,
            self.amount,
            if self.declared { 1.0 } else { 0.0 },
            self.accrual,
            self.paid_today,
        ]
    }

    pub fn from_slice(v: &[f64]) -> Option<Self> {
        if v.len() != STATE_WIDTH || v.iter().any(|x| x.is_nan()) {
            return None;
        }
        Some(Self {
            payout: v[0],
            target_yield: v[1],
            price_ema: v[2],
            amount: v[3],
            declared: v[4] != 0.0,
            accrual: v[5],
            paid_today: v[6],
        })
    }
}

/// The name's ex-date phase in [0, 63): FNV-1a of the ticker, so it follows
/// the name rather than its roster slot and takes no draw.
pub fn phase_for(ticker: &str) -> i64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in ticker.as_bytes() {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    (h % DIVIDEND_PERIOD as u64) as i64
}

/// Sessions since the name's last ex-date on `day`, in [0, 63).
pub fn sessions_since_ex(ticker: &str, day: i64) -> i64 {
    (day - phase_for(ticker)).rem_euclid(DIVIDEND_PERIOD)
}

/// A name's payout: `min(1, dial * sector payout)` for a profitable name
/// growing slower than the cutoff, 0.0 for any other.
pub fn payout_for(p: &ModelParams, company: &TickCompany) -> f64 {
    if p.dividend_payout_share == 0.0 {
        return 0.0;
    }
    if !matches!(company.eps, Some(e) if e > 0.0 && e.is_finite()) {
        return 0.0;
    }
    if company.revenue_growth.unwrap_or(0.0) >= p.dividend_growth_cutoff {
        return 0.0;
    }
    let sector = match crate::sectors::by_key(&company.sector) {
        Some(s) => s.dividend_payout,
        None => return 0.0,
    };
    mathx::min(1.0, p.dividend_payout_share * sector)
}

/// The share of earnings the buyback term reads for this name. Under
/// `dividend_buyback_substitution` it is `buyback_payout_share` less the
/// name's dividend payout, floored at zero, so `buyback_payout_share` is the
/// total payout; otherwise it is `buyback_payout_share`, unread.
pub fn buyback_share(p: &ModelParams, company: &TickCompany) -> f64 {
    if p.dividend_payout_share == 0.0 || p.dividend_buyback_substitution == 0.0 {
        return p.buyback_payout_share;
    }
    let payout = match company.stock.dividend {
        Some(d) => d.payout,
        None => payout_for(p, company),
    };
    mathx::max(0.0, p.buyback_payout_share - payout)
}

/// Fair value with the name's accrued dividend: `fv` itself, bit for bit,
/// when no dividend has accrued (and on every preset).
pub fn with_accrual(fv: f64, company: &TickCompany) -> f64 {
    match company.stock.dividend {
        Some(d) if d.accrual != 0.0 => fv + d.accrual,
        _ => fv,
    }
}

/// The name's accrual this session, 0.0 without a state.
pub fn accrual(company: &TickCompany) -> f64 {
    company.stock.dividend.map(|d| d.accrual).unwrap_or(0.0)
}

/// The quarterly Lintner speed for an annual one.
pub fn quarterly_speed(p: &ModelParams) -> f64 {
    1.0 - mathx::pow(1.0 - p.dividend_adjustment_speed, 0.25)
}

/// The EMA's weight on a new close.
pub fn ema_weight() -> f64 {
    1.0 - mathx::pow(2.0, -1.0 / PRICE_EMA_HALF_LIFE)
}

/// The amount the rule declares from `state` at a close of `close`:
/// a partial adjustment toward the target yield on the EMA, floored at zero
/// and capped at `dividend_yield_ceiling` times the target yield on the close.
pub fn declare(p: &ModelParams, state: &DividendState, close: f64) -> f64 {
    let target = state.target_yield * state.price_ema / 4.0;
    let next = mathx::max(0.0, state.amount + quarterly_speed(p) * (target - state.amount));
    let ceiling = p.dividend_yield_ceiling * state.target_yield * mathx::max(close, 0.0) / 4.0;
    mathx::min(next, ceiling)
}

/// A fresh state for a name at price `price`: the payout, the target yield,
/// the EMA at the price, and a first amount at the target.
pub fn new_state(p: &ModelParams, company: &TickCompany, price: f64) -> DividendState {
    let payout = payout_for(p, company);
    let target_yield = if payout > 0.0 && price > 0.0 && price.is_finite() {
        payout * company.eps.unwrap_or(0.0) / price
    } else {
        0.0
    };
    DividendState {
        payout,
        target_yield,
        price_ema: price,
        amount: target_yield * price / 4.0,
        declared: false,
        accrual: 0.0,
        paid_today: 0.0,
    }
}

/// The accrual on a day `k` sessions after the last ex-date: the declared
/// amount once declared, and before that the amount the rule would declare
/// on today's state, times `k / 63`.
pub fn accrual_for(p: &ModelParams, state: &DividendState, close: f64, k: i64) -> f64 {
    if k == 0 {
        return 0.0;
    }
    let d = if state.declared { state.amount } else { declare(p, state, close) };
    d * k as f64 / DIVIDEND_PERIOD as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_phase_is_in_range_and_follows_the_ticker() {
        for t in ["AAPL", "MSFT", "KAT0", "KAT11", ""] {
            let ph = phase_for(t);
            assert!((0..DIVIDEND_PERIOD).contains(&ph));
            assert_eq!(ph, phase_for(t));
        }
        assert_eq!(sessions_since_ex("AAPL", phase_for("AAPL")), 0);
        assert_eq!(sessions_since_ex("AAPL", phase_for("AAPL") + 62), 62);
        assert_eq!(sessions_since_ex("AAPL", phase_for("AAPL") - 1), 62);
    }

    #[test]
    fn the_declaration_adjusts_partially_and_is_capped() {
        let mut p = ModelParams::pt_v1();
        p.dividend_payout_share = 1.0;
        let s = DividendState {
            payout: 0.5,
            target_yield: 0.02,
            price_ema: 100.0,
            amount: 0.0,
            declared: false,
            accrual: 0.0,
            paid_today: 0.0,
        };
        // Target 0.5 a quarter; the quarterly speed of 0.4 a year is 0.1199.
        let d = declare(&p, &s, 100.0);
        assert!((d - 0.5 * quarterly_speed(&p)).abs() < 1e-12);
        // A collapsed close caps the amount at twice the target yield on it.
        let high = DividendState { amount: 0.5, ..s };
        let d = declare(&p, &high, 10.0);
        assert!((d - 2.0 * 0.02 * 10.0 / 4.0).abs() < 1e-12);
    }
}
