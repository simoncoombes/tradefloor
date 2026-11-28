//! European option prices and Greeks: Black (1976) on the forward.
//!
//! Every transcendental goes through [`crate::mathx`], so a price computed
//! natively and one computed in the browser agree to the bit, as every other
//! number the engine prints does. The normal distribution function is
//! `erfc`'s, the one the margin's quantile already reads.
//!
//! A European option on the index settles on the index at its expiry, and
//! the index's forward to that expiry is `F = (S - PV(D)) e^{r tau}` (the
//! index futures' carry, `engine::futures`). Black's formula prices the
//! option on that forward and discounts at `e^{-r tau}`, so put-call parity,
//! `C - P = DF (F - K)`, holds by construction.
//!
//! The Greeks are taken against the index level `S` with the dividends'
//! present value held, so `dF / dS = e^{r tau}`: delta and gamma per index
//! point, vega per unit of volatility (1.00, not one vol point), theta per
//! session (a 252-session year) with `S`, the volatility and the rate held.

use super::symbol::Right;
use crate::mathx;

/// Sessions in the year the Greeks' theta divides by.
const SESSIONS_PER_YEAR: f64 = 252.0;

/// `1 / sqrt(2 pi)`.
const INV_ROOT_TWO_PI: f64 = 0.398_942_280_401_432_7;

/// The standard normal distribution function.
pub fn norm_cdf(x: f64) -> f64 {
    0.5 * mathx::erfc(-x / std::f64::consts::SQRT_2)
}

/// The standard normal density.
pub fn norm_pdf(x: f64) -> f64 {
    INV_ROOT_TWO_PI * mathx::exp(-0.5 * x * x)
}

/// What is left at expiry: `max(0, F - K)` for a call, `max(0, K - F)` for a
/// put.
pub fn intrinsic(right: Right, forward: f64, strike: f64) -> f64 {
    match right {
        Right::Call => mathx::max(0.0, forward - strike),
        Right::Put => mathx::max(0.0, strike - forward),
    }
}

/// `d1` and `d2` of Black's formula, `None` where there is no time or no
/// volatility left.
fn d1_d2(forward: f64, strike: f64, years: f64, vol: f64) -> Option<(f64, f64)> {
    if !(years > 0.0 && vol > 0.0 && forward > 0.0 && strike > 0.0) {
        return None;
    }
    let v = vol * mathx::sqrt(years);
    let d1 = (mathx::log(forward / strike) + 0.5 * v * v) / v;
    Some((d1, d1 - v))
}

/// Black's price of a European option: on the forward `forward`, struck at
/// `strike`, `years` to expiry, at the volatility `vol` (a fraction a
/// year), discounted by `discount`. With no time or no volatility left it is
/// the discounted intrinsic value.
pub fn black_price(right: Right, forward: f64, strike: f64, years: f64, vol: f64, discount: f64) -> f64 {
    match d1_d2(forward, strike, years, vol) {
        None => discount * intrinsic(right, forward, strike),
        Some((d1, d2)) => match right {
            Right::Call => discount * (forward * norm_cdf(d1) - strike * norm_cdf(d2)),
            Right::Put => discount * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1)),
        },
    }
}

/// An option's sensitivities, against the index level with the dividends'
/// present value held.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct Greeks {
    /// Per index point.
    pub delta: f64,
    /// Per index point, squared.
    pub gamma: f64,
    /// Per unit of volatility: the change for a move from 0.20 to 1.20.
    pub vega: f64,
    /// Per session that passes, the level, the volatility and the rate held.
    pub theta: f64,
}

/// The price and Greeks of a European option on the index, from what its
/// forward is made of: `carry_spot` the index less the present value of the
/// dividends before expiry, `rate` the continuously compounded rate a year
/// (the forward is `carry_spot e^{rate years}` and the discount
/// `e^{-rate years}`). Returns `(price, greeks)`.
pub fn black_greeks(
    right: Right,
    carry_spot: f64,
    strike: f64,
    years: f64,
    vol: f64,
    rate: f64,
) -> (f64, Greeks) {
    let growth = mathx::exp(rate * years);
    let discount = mathx::exp(-rate * years);
    let forward = carry_spot * growth;
    let price = black_price(right, forward, strike, years, vol, discount);
    let Some((d1, _)) = d1_d2(forward, strike, years, vol) else {
        // Expired or without volatility: a step in the level, no curvature.
        let itm = match right {
            Right::Call => forward > strike,
            Right::Put => forward < strike,
        };
        let delta = match (right, itm) {
            (Right::Call, true) => 1.0,
            (Right::Put, true) => -1.0,
            _ => 0.0,
        };
        let theta = match (right, itm) {
            // The discounted strike accrues as time passes.
            (Right::Call, true) => -rate * strike * discount / SESSIONS_PER_YEAR,
            (Right::Put, true) => rate * strike * discount / SESSIONS_PER_YEAR,
            _ => 0.0,
        };
        return (price, Greeks { delta, gamma: 0.0, vega: 0.0, theta });
    };
    let root = mathx::sqrt(years);
    let n1 = norm_pdf(d1);
    let delta = match right {
        Right::Call => norm_cdf(d1),
        Right::Put => norm_cdf(d1) - 1.0,
    };
    // d(price)/dS = discount * growth * N(d1); discount * growth is one up
    // to rounding, kept so the finite differences agree to the bit's order.
    let delta = delta * discount * growth;
    let gamma = discount * growth * growth * n1 / (forward * vol * root);
    let vega = discount * forward * n1 * root;
    // d(price)/d(years) at S held: the forward grows at the rate, the
    // discount shrinks, and the time value grows.
    let df_dt = match right {
        Right::Call => rate * forward * norm_cdf(d1),
        Right::Put => rate * forward * (norm_cdf(d1) - 1.0),
    };
    let dp_dt = -rate * price + discount * (df_dt + forward * n1 * vol / (2.0 * root));
    (price, Greeks { delta, gamma, vega, theta: -dp_dt / SESSIONS_PER_YEAR })
}

/// The volatility at which Black's price is `price`, by bisection on the
/// volatility's logarithm: `None` for a price outside the arbitrage bounds
/// (below the discounted intrinsic value, at or above the discounted
/// forward for a call or strike for a put) or with no time left. Found to a
/// relative 1e-12, in at most 200 halvings, so the same price gives the same
/// volatility everywhere.
pub fn implied_vol(right: Right, price: f64, forward: f64, strike: f64, years: f64, discount: f64) -> Option<f64> {
    if !(years > 0.0 && forward > 0.0 && strike > 0.0 && discount > 0.0 && price.is_finite()) {
        return None;
    }
    let floor = discount * intrinsic(right, forward, strike);
    let cap = match right {
        Right::Call => discount * forward,
        Right::Put => discount * strike,
    };
    if !(price > floor && price < cap) {
        return None;
    }
    let (mut lo, mut hi) = (mathx::log(1e-6), mathx::log(20.0));
    if black_price(right, forward, strike, years, mathx::exp(hi), discount) < price {
        return None;
    }
    for _ in 0..200 {
        let mid = 0.5 * (lo + hi);
        if black_price(right, forward, strike, years, mathx::exp(mid), discount) < price {
            lo = mid;
        } else {
            hi = mid;
        }
        if hi - lo < 1e-12 {
            break;
        }
    }
    Some(mathx::exp(0.5 * (lo + hi)))
}

#[cfg(test)]
mod tests {
    use super::*;

    const S: f64 = 1000.0;

    fn cases() -> Vec<(f64, f64, f64, f64)> {
        // (strike, years, vol, rate)
        let mut out = Vec::new();
        for k in [700.0, 900.0, 990.0, 1000.0, 1015.0, 1100.0, 1400.0] {
            for t in [2.0 / 252.0, 21.0 / 252.0, 0.5, 1.0] {
                for v in [0.08, 0.2, 0.6] {
                    for r in [0.0, 0.03, -0.01] {
                        out.push((k, t, v, r));
                    }
                }
            }
        }
        out
    }

    #[test]
    fn put_call_parity_holds_on_the_forward() {
        for (k, t, v, r) in cases() {
            let df = mathx::exp(-r * t);
            let f = S * mathx::exp(r * t);
            let c = black_price(Right::Call, f, k, t, v, df);
            let p = black_price(Right::Put, f, k, t, v, df);
            assert!((c - p - df * (f - k)).abs() <= 1e-10 * S, "{k} {t} {v} {r}: {c} {p}");
        }
    }

    #[test]
    fn prices_sit_inside_their_bounds_and_rise_with_volatility_and_time() {
        for (k, t, v, r) in cases() {
            let df = mathx::exp(-r * t);
            let f = S * mathx::exp(r * t);
            for right in [Right::Call, Right::Put] {
                let p = black_price(right, f, k, t, v, df);
                assert!(p >= df * intrinsic(right, f, k) - 1e-9, "{right:?} {k} {t} {v}");
                assert!(p <= df * if right == Right::Call { f } else { k });
                // Strictly, wherever any time value is left to rise.
                let up = black_price(right, f, k, t, v * 1.1, df);
                assert!(up >= p);
                if p - df * intrinsic(right, f, k) > 1e-6 {
                    assert!(up > p, "{right:?} {k} {t} {v}");
                }
                assert!(black_price(right, f, k, t * 1.1, v, 1.0) >= black_price(right, f, k, t, v, 1.0));
            }
        }
        // No time or no volatility: the discounted intrinsic value.
        assert_eq!(black_price(Right::Call, 1010.0, 1000.0, 0.0, 0.2, 0.99), 0.99 * 10.0);
        assert_eq!(black_price(Right::Put, 1010.0, 1000.0, 0.5, 0.0, 0.99), 0.0);
    }

    #[test]
    fn the_greeks_are_the_finite_differences_of_the_price() {
        for (k, t, v, r) in cases() {
            for right in [Right::Call, Right::Put] {
                let price = |s: f64, t: f64, v: f64| black_greeks(right, s, k, t, v, r).0;
                let (p, g) = black_greeks(right, S, k, t, v, r);
                assert_eq!(p.to_bits(), price(S, t, v).to_bits());
                // A thousandth of the move's sd, so the differences' own
                // error is far below the tolerance on a two-session option.
                let h = 1e-3 * S * v * t.sqrt();
                let delta = (price(S + h, t, v) - price(S - h, t, v)) / (2.0 * h);
                let gamma = (price(S + h, t, v) - 2.0 * p + price(S - h, t, v)) / (h * h);
                let dv = 1e-5;
                let vega = (price(S, t, v + dv) - price(S, t, v - dv)) / (2.0 * dv);
                let dt = 1e-6;
                let theta = -(price(S, t + dt, v) - price(S, t - dt, v)) / (2.0 * dt) / 252.0;
                let scale = |x: f64| 1e-6 + 1e-4 * x.abs();
                assert!((g.delta - delta).abs() <= scale(delta), "delta {right:?} {k} {t} {v} {r}: {} {delta}", g.delta);
                assert!(
                    (g.gamma - gamma).abs() <= 1e-6 + 2e-3 * gamma.abs(),
                    "gamma {right:?} {k} {t} {v} {r}: {} {gamma}",
                    g.gamma
                );
                assert!((g.vega - vega).abs() <= 1e-4 + 1e-5 * vega.abs(), "vega {right:?} {k} {t} {v} {r}: {} {vega}", g.vega);
                assert!(
                    (g.theta - theta).abs() <= 1e-6 + 1e-4 * theta.abs(),
                    "theta {right:?} {k} {t} {v} {r}: {} {theta}",
                    g.theta
                );
            }
        }
    }

    #[test]
    fn the_implied_volatility_inverts_the_price() {
        for (k, t, v, r) in cases() {
            let df = mathx::exp(-r * t);
            let f = S * mathx::exp(r * t);
            for right in [Right::Call, Right::Put] {
                let p = black_price(right, f, k, t, v, df);
                // A price indistinguishable from its bound has no volatility to find.
                if p - df * intrinsic(right, f, k) < 1e-9 * S {
                    continue;
                }
                let iv = implied_vol(right, p, f, k, t, df).unwrap();
                assert!((iv - v).abs() <= 1e-8, "{right:?} {k} {t} {v}: {iv}");
            }
        }
        assert!(implied_vol(Right::Call, 5.0, 1000.0, 990.0, 0.1, 1.0).is_none(), "below intrinsic");
        assert!(implied_vol(Right::Put, 1000.0, 1000.0, 990.0, 0.1, 1.0).is_none(), "at the strike");
        assert!(implied_vol(Right::Call, 10.0, 1000.0, 990.0, 0.0, 1.0).is_none(), "no time");
    }

    #[test]
    fn the_normal_is_the_normal() {
        assert_eq!(norm_cdf(0.0), 0.5);
        assert!((norm_cdf(1.959_963_984_540_054) - 0.975).abs() < 1e-15);
        assert!((norm_pdf(0.0) - 0.398_942_280_401_432_7).abs() < 1e-16);
        assert!((norm_cdf(-3.0) + norm_cdf(3.0) - 1.0).abs() < 1e-16);
    }
}
