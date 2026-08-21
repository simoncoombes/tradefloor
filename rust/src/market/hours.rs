//! Market sessions and the intraday curves, ported from
//! `src/lib/engine/market.ts:60` and the tick preamble at `:1315`.
//!
//! **Tier 1 for the session logic** (comparisons only). The intraday curves
//! use `pow` with integer and non-integer exponents; the non-integer one is a
//! Tier-2 surface, flagged at [`intraday_volume`].

use crate::mathx;

/// Session boundaries as fractional hours.
pub const PRE_MARKET_START: f64 = 7.0;
/// 09:30.
pub const MARKET_OPEN: f64 = 9.5;
/// 16:00.
pub const MARKET_CLOSE: f64 = 16.0;
/// 20:00.
pub const AFTER_HOURS_END: f64 = 20.0;

/// Minutes in the regular session — 390. The denominator of every per-tick
/// scaling in the engine, and the reason drift divides by 390 while noise
/// divides by its square root.
pub const MARKET_MINUTES: f64 = (MARKET_CLOSE - MARKET_OPEN) * 60.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketStatus {
    Closed,
    PreMarket,
    Open,
    AfterHours,
}

impl MarketStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            MarketStatus::Closed => "closed",
            MarketStatus::PreMarket => "pre_market",
            MarketStatus::Open => "open",
            MarketStatus::AfterHours => "after_hours",
        }
    }

    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "closed" => MarketStatus::Closed,
            "pre_market" => MarketStatus::PreMarket,
            "open" => MarketStatus::Open,
            "after_hours" => MarketStatus::AfterHours,
            _ => return None,
        })
    }
}

/// The game clock fields this module reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GameTime {
    pub hour: i64,
    pub minute: i64,
    /// 0 = Sunday, 6 = Saturday, matching JavaScript's `Date#getDay`.
    pub day_of_week: i64,
}

/// Which session a moment falls in.
///
/// The boundaries are half-open (`>= start`, `< end`), so 16:00 exactly is
/// after-hours rather than the last minute of the session — which is why the
/// close-of-day bookkeeping in [`super::daily`] is driven by a transition
/// rather than by testing for 16:00.
pub fn get_market_status(time: GameTime) -> MarketStatus {
    let hour = time.hour as f64 + time.minute as f64 / 60.0;

    if time.day_of_week == 0 || time.day_of_week == 6 {
        return MarketStatus::Closed;
    }
    if (PRE_MARKET_START..MARKET_OPEN).contains(&hour) {
        return MarketStatus::PreMarket;
    }
    if (MARKET_OPEN..MARKET_CLOSE).contains(&hour) {
        return MarketStatus::Open;
    }
    if (MARKET_CLOSE..AFTER_HOURS_END).contains(&hour) {
        return MarketStatus::AfterHours;
    }
    MarketStatus::Closed
}

pub fn is_market_open(time: GameTime) -> bool {
    get_market_status(time) == MarketStatus::Open
}

/// Fraction of the session elapsed: 0 at 09:30, 1 at 16:00.
///
/// Clamped, so pre-market gives 0 and after-hours gives 1 rather than running
/// off either end.
pub fn intraday_fraction(time: GameTime) -> f64 {
    let minutes_since_open = (time.hour as f64 - 9.0) * 60.0 + (time.minute as f64 - 30.0);
    mathx::max(0.0, mathx::min(1.0, minutes_since_open / 390.0))
}

/// U-shaped intraday volatility, 1.0 in the middle of the day rising to 1.2
/// at the bell.
///
/// `pow(2t - 1, 4)` — an integer exponent, so `libm` and V8 agree exactly and
/// this stays Tier 1. Reduced from a 1.0–1.4 range in "Cycle 71".
pub fn intraday_vol(t: f64) -> f64 {
    1.0 + 0.2 * mathx::pow(2.0 * t - 1.0, 4.0)
}

/// U-shaped intraday volume: heavy at the open, light at lunch, heavy into
/// the close.
///
/// **Tier 2.** The `t^2.5` term is a non-integer exponent, which is the one
/// `pow` surface where `libm` and V8 can differ by a ULP — the same surface
/// documented in `DETERMINISM.md` §2 for `weibull_hazard`. The `(1-t)^3` term
/// is integer and exact.
///
/// Extended hours are a flat 0.3x rather than a curve, because there is no
/// bell to shape the day around.
pub fn intraday_volume(t: f64, status: MarketStatus) -> f64 {
    if status != MarketStatus::Open {
        return 0.3;
    }
    0.4 + 2.5 * mathx::pow(mathx::max(0.0, 1.0 - t), 3.0) + 2.0 * mathx::pow(t, 2.5)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(hour: i64, minute: i64) -> GameTime {
        GameTime {
            hour,
            minute,
            day_of_week: 3,
        }
    }

    #[test]
    fn the_session_boundaries_are_half_open() {
        assert_eq!(get_market_status(at(6, 59)), MarketStatus::Closed);
        assert_eq!(get_market_status(at(7, 0)), MarketStatus::PreMarket);
        assert_eq!(get_market_status(at(9, 29)), MarketStatus::PreMarket);
        assert_eq!(get_market_status(at(9, 30)), MarketStatus::Open);
        assert_eq!(get_market_status(at(15, 59)), MarketStatus::Open);
        // 16:00 exactly is already after-hours, not the last open minute.
        assert_eq!(get_market_status(at(16, 0)), MarketStatus::AfterHours);
        assert_eq!(get_market_status(at(19, 59)), MarketStatus::AfterHours);
        assert_eq!(get_market_status(at(20, 0)), MarketStatus::Closed);
    }

    #[test]
    fn weekends_are_closed_at_every_hour() {
        for day_of_week in [0, 6] {
            for hour in 0..24 {
                assert_eq!(
                    get_market_status(GameTime {
                        hour,
                        minute: 0,
                        day_of_week
                    }),
                    MarketStatus::Closed,
                    "day {day_of_week} hour {hour}"
                );
            }
        }
    }

    #[test]
    fn the_session_is_exactly_three_hundred_and_ninety_minutes() {
        // Load-bearing: drift divides by this and noise by its square root.
        assert_eq!(MARKET_MINUTES, 390.0);
        assert_eq!(intraday_fraction(at(9, 30)), 0.0);
        assert_eq!(intraday_fraction(at(16, 0)), 1.0);
    }

    #[test]
    fn the_intraday_fraction_clamps_outside_the_session() {
        assert_eq!(intraday_fraction(at(7, 0)), 0.0);
        assert_eq!(intraday_fraction(at(19, 0)), 1.0);
    }

    #[test]
    fn volatility_is_u_shaped_with_its_minimum_at_midday() {
        let open = intraday_vol(0.0);
        let noon = intraday_vol(0.5);
        let close = intraday_vol(1.0);
        assert!(
            noon < open && noon < close,
            "midday must be the quiet point"
        );
        assert_eq!(noon, 1.0);
        assert_eq!(open, 1.2);
        assert_eq!(close, 1.2);
    }

    #[test]
    fn volume_is_u_shaped_and_heaviest_at_the_open() {
        let open = intraday_volume(0.0, MarketStatus::Open);
        let noon = intraday_volume(0.5, MarketStatus::Open);
        let close = intraday_volume(1.0, MarketStatus::Open);
        assert!(noon < open && noon < close);
        // The open is the heaviest moment of the day, not the close.
        assert!(open > close, "open {open} should exceed close {close}");
    }

    #[test]
    fn extended_hours_volume_is_a_flat_three_tenths() {
        for status in [
            MarketStatus::PreMarket,
            MarketStatus::AfterHours,
            MarketStatus::Closed,
        ] {
            for t in [0.0, 0.3, 1.0] {
                assert_eq!(intraday_volume(t, status), 0.3);
            }
        }
    }
}
