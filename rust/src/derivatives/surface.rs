//! The implied-volatility surface options are quoted from: SSVI (Gatheral
//! and Jacquier, "Arbitrage-free SVI volatility surfaces", Quantitative
//! Finance 14(1) 59-71, 2014), with its parts tied to the model.
//!
//! # The form
//!
//! Total implied variance at log-moneyness `k = ln(K / F)` and `T` sessions
//! out is
//!
//! `w(k, T) = theta_T / 2 * (1 + rho phi(theta_T) k + sqrt((phi k + rho)^2 + 1 - rho^2))`
//!
//! with `theta_T` the at-the-money total variance and the paper's power law
//! `phi(theta) = eta theta^-gamma (1 + theta)^(gamma - 1)`. The paper's
//! sufficient conditions are checked at every quote ([`Ssvi::check`]): no
//! butterfly arbitrage needs `theta phi (1 + |rho|) < 4` and `theta phi^2 (1 +
//! |rho|) <= 4` (its Theorem 4.2), and no calendar arbitrage needs `theta_T`
//! non-decreasing in `T` and `0 <= d(theta phi)/d theta <= (1 + sqrt(1 -
//! rho^2)) phi / rho^2` (its Theorem 4.1). With `gamma` in `[0, 1/2]` and
//! `eta (1 + |rho|) <= 2` (its Remark 4.4) every condition holds at every
//! `theta`, so the fit keeps `|rho|` inside `2 / eta - 1` and the checks
//! confirm it.
//!
//! # What ties it to the model
//!
//! - The level: `theta` at 21 sessions is the one whose strip, priced by the
//!   Cboe VIX formula ([`strip_moments`]), returns the VIX the surface is
//!   built on ([`fit_smile`]). So the surface and the published VIX agree,
//!   and the VIX's variance premium is the surface's.
//! - The skew: `rho` is the one whose 21-session strip has the risk-neutral
//!   skewness asked of it (the Cboe SKEW formula), the model's physical skew
//!   at the VIX plus a premium; `eta` and `gamma` are the curvature.
//! - The term structure: `theta_T` is `theta_21` times the ratio of the
//!   forecast's expected index variance to `T` over its expected variance
//!   to 21 sessions, times a premium per tenor, plus the variance of the
//!   earnings reports the calendar puts before `T` ([`term_shape`],
//!   [`ThetaCurve`]).
//!
//! Everything here is a pure function of its arguments; the engine supplies
//! the VIX, the forecast and the calendar (`engine::options`).

use super::pricing::black_price;
use super::symbol::Right;
use crate::mathx;

/// Points on a strip's log-moneyness grid.
pub const STRIP_POINTS: usize = 400;

/// A strip reaches this many at-the-money total standard deviations either
/// side of the forward.
pub const STRIP_WIDTH: f64 = 8.0;

/// The sessions the VIX's 30 days are: the tenor the surface's level is
/// fitted at.
pub const VIX_SESSIONS: f64 = 21.0;

/// The tenors the term premia are quoted at, sessions: the premium is 0 at
/// [`VIX_SESSIONS`], `surface_term_premium_short` at the first and
/// `surface_term_premium_long` at the second, log-linear in the tenor's log
/// between them and beyond.
pub const TERM_PREMIUM_TENORS: (f64, f64) = (6.0, 252.0);

/// Sessions in the model's year.
const YEAR: f64 = 252.0;

/// The SSVI parameters: the correlation `rho` and the power law's `eta` and
/// `gamma`.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct Ssvi {
    pub rho: f64,
    pub eta: f64,
    pub gamma: f64,
    /// The vertex's move toward the money (`surface_index_vertex`): the
    /// smile is SSVI's at `k + shift m`, `m = -rho / phi` its vertex. 0.0 is
    /// SSVI itself.
    pub shift: f64,
    /// The total variance `anchor` at which the power law is read, and the
    /// exponent `tenor` it falls by from there (`surface_index_tenor_exponent`):
    /// `phi(theta) = phi_SSVI(anchor) (theta / anchor)^-tenor`. An anchor of
    /// 0.0 is SSVI's own power law.
    pub anchor: f64,
    pub tenor: f64,
}

impl Ssvi {
    pub fn new(rho: f64, eta: f64, gamma: f64) -> Self {
        Ssvi { rho, eta, gamma, shift: 0.0, anchor: 0.0, tenor: 0.0 }
    }

    /// The same smile with its vertex moved a share `shift` of the way to the
    /// money.
    pub fn with_shift(self, shift: f64) -> Self {
        Ssvi { shift, ..self }
    }

    /// The same smile at `anchor`, with the power law falling from there by
    /// `tenor` in place of `gamma`: the smile at `anchor` is unchanged, and
    /// the smiles at other tenors scale from it. `tenor` 0.0 is the smile
    /// itself.
    pub fn with_tenor(self, anchor: f64, tenor: f64) -> Self {
        if tenor == 0.0 {
            return self;
        }
        Ssvi { anchor, tenor, ..self }
    }

    fn power_law(&self, theta: f64) -> f64 {
        self.eta * mathx::pow(theta, -self.gamma) * mathx::pow(1.0 + theta, self.gamma - 1.0)
    }

    /// The power law, `eta theta^-gamma (1 + theta)^(gamma - 1)`: 0 with `eta`
    /// at 0, where the smile is flat.
    pub fn phi(&self, theta: f64) -> f64 {
        if self.eta == 0.0 {
            return 0.0;
        }
        if self.anchor > 0.0 {
            return self.power_law(self.anchor) * mathx::pow(theta / self.anchor, -self.tenor);
        }
        self.power_law(theta)
    }

    /// Total implied variance at log-moneyness `k` on the smile whose
    /// at-the-money total variance is `theta`.
    pub fn total_variance(&self, k: f64, theta: f64) -> f64 {
        let p = self.phi(theta);
        let r = self.rho;
        // The vertex moved toward the money (`shift`); exactly SSVI at 0.0.
        let k = if self.shift == 0.0 || p == 0.0 { k } else { k - self.shift * r / p };
        let x = p * k + r;
        0.5 * theta * (1.0 + r * p * k + mathx::sqrt(x * x + 1.0 - r * r))
    }

    /// Durrleman's condition, `g(k) >= 0`, on 401 points over eight
    /// at-the-money total standard deviations either side: the check that
    /// replaces Gatheral and Jacquier's sufficient conditions once the
    /// vertex is moved, where they no longer apply.
    pub fn check_durrleman(&self, theta: f64) -> Result<(), String> {
        let width = 8.0 * mathx::sqrt(theta);
        let h = 1e-5 * mathx::max(width, 1e-3);
        for i in 0..=400 {
            let k = -width + 2.0 * width * f64::from(i) / 400.0;
            let w = self.total_variance(k, theta);
            let w1 = (self.total_variance(k + h, theta) - self.total_variance(k - h, theta)) / (2.0 * h);
            let w2 = (self.total_variance(k + h, theta) + self.total_variance(k - h, theta) - 2.0 * w) / (h * h);
            let g = (1.0 - k * w1 / (2.0 * w)) * (1.0 - k * w1 / (2.0 * w)) - 0.25 * w1 * w1 * (1.0 / w + 0.25) + 0.5 * w2;
            if !(w > 0.0 && g >= -1e-9) {
                return Err(format!("butterfly: Durrleman's g is {g} at k {k}, theta {theta}"));
            }
        }
        Ok(())
    }

    /// The largest `|rho|` the fit allows at `eta`: inside `2 / eta - 1`, where
    /// the paper's Remark 4.4 keeps every smile free of static arbitrage, and
    /// below 0.999.
    pub fn rho_bound(eta: f64) -> f64 {
        if eta <= 1.0 {
            0.999
        } else {
            mathx::max(0.0, mathx::min(0.999, 2.0 / eta - 1.0))
        }
    }

    /// Gatheral and Jacquier's sufficient conditions at `theta`: the
    /// butterfly pair of their Theorem 4.2 and the bound on `d(theta
    /// phi)/d theta` of their Theorem 4.1. `Err` names the first that fails.
    pub fn check(&self, theta: f64) -> Result<(), String> {
        if self.shift != 0.0 {
            return self.check_durrleman(theta);
        }
        let (r, e, g) = (self.rho, self.eta, self.gamma);
        if !(theta > 0.0 && theta.is_finite()) {
            return Err(format!("theta is {theta}; it is finite and positive"));
        }
        if !(r.abs() < 1.0 && (0.0..=1.0).contains(&g) && e >= 0.0 && e.is_finite() && (0.0..1.0).contains(&self.tenor)) {
            return Err(format!("rho {r}, eta {e}, gamma {g}: |rho| < 1, eta >= 0, gamma in [0, 1]"));
        }
        let p = self.phi(theta);
        let a = 1.0 + r.abs();
        if !(theta * p * a < 4.0) {
            return Err(format!("butterfly: theta phi (1 + |rho|) is {} at theta {theta}, not below 4", theta * p * a));
        }
        if !(theta * p * p * a <= 4.0) {
            return Err(format!("butterfly: theta phi^2 (1 + |rho|) is {} at theta {theta}, above 4", theta * p * p * a));
        }
        // d(theta phi)/d theta = phi (1 - gamma) / (1 + theta) for this phi;
        // phi (1 - tenor) for the anchored one.
        let slope = if self.anchor > 0.0 { p * (1.0 - self.tenor) } else { p * (1.0 - g) / (1.0 + theta) };
        if slope < 0.0 {
            return Err(format!("calendar: d(theta phi)/d theta is {slope} at theta {theta}, below 0"));
        }
        if r != 0.0 {
            let cap = (1.0 + mathx::sqrt(1.0 - r * r)) * p / (r * r);
            if slope > cap {
                return Err(format!("calendar: d(theta phi)/d theta is {slope} at theta {theta}, above {cap}"));
            }
        }
        Ok(())
    }
}

/// What a strip says: the Cboe VIX formula's value and the risk-neutral
/// skewness of the log return the Cboe SKEW formula reads.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct StripMoments {
    /// The VIX formula, in volatility points.
    pub vix: f64,
    /// The skewness `S` of the log return.
    pub skewness: f64,
}

impl StripMoments {
    /// The Cboe SKEW index, `100 - 10 S`.
    pub fn skew_index(&self) -> f64 {
        100.0 - 10.0 * self.skewness
    }
}

/// The Cboe VIX and SKEW formulas on one strip `years` long, on the smile
/// `iv` (the implied volatility at log-moneyness `k`): out-of-the-money
/// prices at [`STRIP_POINTS`] log-moneyness points spread evenly over
/// [`STRIP_WIDTH`] at-the-money total sds either side of the forward, each
/// strike's width half the distance between its neighbours (one-sided at
/// the ends), `K0` the forward, so the formulas' correction terms vanish.
/// Both are free of the forward's level and of the discount (they read
/// `e^{rT} Q(K) / K^2` sums), so the strip is priced on a unit forward and no
/// discount. `None` with no time or no at-the-money volatility.
pub fn strip_moments(iv: impl Fn(f64) -> f64, years: f64) -> Option<StripMoments> {
    let s0 = iv(0.0) * mathx::sqrt(years);
    if !(years > 0.0 && s0 > 0.0 && s0.is_finite()) {
        return None;
    }
    let n = STRIP_POINTS;
    let start = -STRIP_WIDTH * s0;
    let step = 2.0 * STRIP_WIDTH * s0 / (n - 1) as f64;
    let ks: Vec<f64> = (0..n).map(|i| if i == n - 1 { STRIP_WIDTH * s0 } else { start + i as f64 * step }).collect();
    let strikes: Vec<f64> = ks.iter().map(|k| mathx::exp(*k)).collect();
    let (mut s1, mut p2, mut p3) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let k = strikes[i];
        let dk = if i == 0 {
            strikes[1] - strikes[0]
        } else if i == n - 1 {
            strikes[n - 1] - strikes[n - 2]
        } else {
            0.5 * (strikes[i + 1] - strikes[i - 1])
        };
        let right = if k >= 1.0 { Right::Call } else { Right::Put };
        let q = black_price(right, 1.0, k, years, iv(ks[i]), 1.0);
        let lk = ks[i];
        let w = dk / (k * k) * q;
        s1 += w;
        p2 += 2.0 * (1.0 - lk) * w;
        p3 += 3.0 * (2.0 * lk - lk * lk) * w;
    }
    let p1 = -s1;
    let var = p2 - p1 * p1;
    if !(s1 > 0.0 && var > 0.0) {
        return None;
    }
    let vix = 100.0 * mathx::sqrt(2.0 / years * s1);
    let skewness = (p3 - 3.0 * p1 * p2 + 2.0 * p1 * p1 * p1) / (var * mathx::sqrt(var));
    Some(StripMoments { vix, skewness })
}

/// The strip of the smile `ssvi` at at-the-money total variance `theta`,
/// `years` long.
pub fn smile_moments(ssvi: &Ssvi, theta: f64, years: f64) -> Option<StripMoments> {
    strip_moments(|k| mathx::sqrt(ssvi.total_variance(k, theta) / years), years)
}

/// A fitted 21-session smile: its at-the-money total variance and
/// correlation, what its strip reads, and whether the skewness asked was
/// within the correlations the fit allows.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct SmileFit {
    pub theta: f64,
    pub rho: f64,
    pub moments: StripMoments,
    /// False when the skewness asked lay beyond what `|rho|` at its bound
    /// gives, and `rho` sits at the bound.
    pub reached: bool,
}

/// The at-the-money total variance at which the smile of correlation `rho`
/// prices `target_vix` on its strip, from `start`: each step scales `theta`
/// by the squared ratio of the target to the strip's VIX, which is nearly
/// proportional to `theta`. Converges to a relative 1e-13 in a handful of
/// steps; at most 100.
fn theta_for_vix(
    rho: f64,
    eta: f64,
    gamma: f64,
    shift: f64,
    target_vix: f64,
    years: f64,
    start: f64,
) -> Option<(f64, StripMoments)> {
    let ssvi = Ssvi::new(rho, eta, gamma).with_shift(shift);
    let mut theta = start;
    let mut last = None;
    for _ in 0..100 {
        let m = smile_moments(&ssvi, theta, years)?;
        let ratio = target_vix / m.vix;
        last = Some((theta, m));
        if (ratio - 1.0).abs() <= 1e-13 {
            break;
        }
        theta *= ratio * ratio;
        if !(theta > 0.0 && theta.is_finite()) {
            return None;
        }
    }
    last
}

/// The smile `years` long (the VIX's 21 sessions) whose strip prices
/// `target_vix` and, with `target_skewness`, has that risk-neutral
/// skewness: `theta` and `rho` at the curvature `eta`, `gamma`.
///
/// Without a skewness, or with `eta` at 0 (a flat smile), `rho` is 0 and
/// only `theta` is solved. With one, `rho` is solved inside `+/-
/// Ssvi::rho_bound(eta)` by regula falsi (Illinois), the skewness rising with
/// `rho`, and `theta` re-solved at each `rho`; a skewness beyond the bound's
/// is met at the bound, `reached` false. `None` when no strip prices.
pub fn fit_smile(target_vix: f64, target_skewness: Option<f64>, years: f64, eta: f64, gamma: f64) -> Option<SmileFit> {
    fit_smile_shifted(target_vix, target_skewness, years, eta, gamma, 0.0)
}

/// [`fit_smile`] on the smile with its vertex moved a share `shift` of the
/// way to the money ([`Ssvi::with_shift`]); 0.0 is [`fit_smile`] itself.
pub fn fit_smile_shifted(
    target_vix: f64,
    target_skewness: Option<f64>,
    years: f64,
    eta: f64,
    gamma: f64,
    shift: f64,
) -> Option<SmileFit> {
    if !(target_vix > 0.0 && target_vix.is_finite() && years > 0.0) {
        return None;
    }
    let start = (target_vix / 100.0) * (target_vix / 100.0) * years;
    let target = match target_skewness {
        Some(s) if eta > 0.0 && s.is_finite() => s,
        _ => {
            let (theta, moments) = theta_for_vix(0.0, eta, gamma, shift, target_vix, years, start)?;
            return Some(SmileFit { theta, rho: 0.0, moments, reached: target_skewness.is_none() || eta == 0.0 });
        }
    };
    let bound = Ssvi::rho_bound(eta);
    let mut theta_hint = start;
    let mut eval = |rho: f64| -> Option<(f64, StripMoments)> {
        let r = theta_for_vix(rho, eta, gamma, shift, target_vix, years, theta_hint)?;
        theta_hint = r.0;
        Some(r)
    };
    let (mut a, mut b) = (-bound, bound);
    let (ta, ma) = eval(a)?;
    let (tb, mb) = eval(b)?;
    let (mut fa, mut fb) = (ma.skewness - target, mb.skewness - target);
    if fa >= 0.0 {
        return Some(SmileFit { theta: ta, rho: a, moments: ma, reached: fa == 0.0 });
    }
    if fb <= 0.0 {
        return Some(SmileFit { theta: tb, rho: b, moments: mb, reached: fb == 0.0 });
    }
    let mut best = (tb, b, mb);
    let mut side = 0i8;
    for _ in 0..200 {
        let c = (a * fb - b * fa) / (fb - fa);
        let (tc, mc) = eval(c)?;
        let fc = mc.skewness - target;
        best = (tc, c, mc);
        if fc.abs() <= 1e-10 || (b - a) <= 1e-13 {
            break;
        }
        if fc < 0.0 {
            a = c;
            fa = fc;
            if side == -1 {
                fb *= 0.5;
            }
            side = -1;
        } else {
            b = c;
            fb = fc;
            if side == 1 {
                fa *= 0.5;
            }
            side = 1;
        }
    }
    Some(SmileFit { theta: best.0, rho: best.1, moments: best.2, reached: true })
}

/// The term structure's inputs, as the engine reads them.
#[derive(Debug, Clone, Copy)]
#[non_exhaustive]
pub struct TermInputs<'a> {
    /// The index's expected one-session variance `h` sessions ahead, `h`
    /// from 1 (the forecast's `index_variance`); empty for a flat expected
    /// variance. Past its end the last value holds.
    pub variance: &'a [f64],
    /// How much of the session in progress has run, in `[0, 1)`: knot `n` is
    /// `n - elapsed` sessions from now, the open of the `n`th session after
    /// the last open.
    pub elapsed: f64,
    /// The log premia on implied variance at the short and long tenors of
    /// [`TERM_PREMIUM_TENORS`].
    pub premium_short: f64,
    pub premium_long: f64,
    /// The earnings reports before each knot: `(n, variance)`, the report
    /// printing at the open knot `n` reaches (so knot `n` and every later one
    /// carries it), its variance in fraction squared.
    pub events: &'a [(usize, f64)],
    /// The weight on the events' variance (`surface_earnings_weight`).
    pub event_weight: f64,
}

impl<'a> TermInputs<'a> {
    /// The inputs, field by field.
    pub fn new(
        variance: &'a [f64],
        elapsed: f64,
        premium_short: f64,
        premium_long: f64,
        events: &'a [(usize, f64)],
        event_weight: f64,
    ) -> Self {
        TermInputs { variance, elapsed, premium_short, premium_long, events, event_weight }
    }
}

/// The log premium on implied variance at `t` sessions: 0 at the VIX's 21,
/// log-linear in `ln t` to the short tenor's and the long tenor's, held at
/// one session below one.
pub fn term_premium(t: f64, short: f64, long: f64) -> f64 {
    let t = mathx::max(1.0, t);
    let (lo, hi) = TERM_PREMIUM_TENORS;
    if t >= VIX_SESSIONS {
        if long == 0.0 {
            0.0
        } else {
            long * mathx::log(t / VIX_SESSIONS) / mathx::log(hi / VIX_SESSIONS)
        }
    } else if short == 0.0 {
        0.0
    } else {
        short * mathx::log(VIX_SESSIONS / t) / mathx::log(VIX_SESSIONS / lo)
    }
}

/// The term structure's shape at knots 1 to `n_max`: at knot `n`, `t = n -
/// elapsed` sessions out, the expected index variance to `t` (the forecast's
/// one-session variances summed, less the elapsed share of the first)
/// times `exp(term_premium(t))`, plus the weighted variance of the reports
/// it reaches.
pub fn term_shape(inputs: &TermInputs<'_>, n_max: usize) -> Vec<f64> {
    let v = |h: usize| -> f64 {
        if inputs.variance.is_empty() {
            1.0
        } else {
            inputs.variance[std::cmp::min(h, inputs.variance.len()) - 1]
        }
    };
    let mut out = Vec::with_capacity(n_max);
    let mut cumulative = -inputs.elapsed * v(1);
    let mut events = 0.0;
    for n in 1..=n_max {
        cumulative += v(n);
        for &(at, x) in inputs.events {
            if at == n {
                events += x;
            }
        }
        let t = n as f64 - inputs.elapsed;
        let premium = term_premium(t, inputs.premium_short, inputs.premium_long);
        let scaled = if premium == 0.0 { cumulative } else { cumulative * mathx::exp(premium) };
        out.push(if inputs.event_weight == 0.0 { scaled } else { scaled + inputs.event_weight * events });
    }
    out
}

/// At-the-money total variance by tenor, non-decreasing: knot `n` at `n -
/// elapsed` sessions, 0 at 0, linear between knots and past the last at the
/// last two's slope.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct ThetaCurve {
    pub elapsed: f64,
    pub knots: Vec<f64>,
}

impl ThetaCurve {
    /// The curve of shape `shape` (knots 1 on), made non-decreasing by a
    /// running maximum, and scaled so that it reads `theta_vix` at the
    /// VIX's 21 sessions. `None` for an empty or non-positive shape.
    pub fn new(shape: &[f64], elapsed: f64, theta_vix: f64) -> Option<Self> {
        if shape.len() < 2 || !(theta_vix > 0.0) {
            return None;
        }
        let mut knots = Vec::with_capacity(shape.len());
        let mut top = 0.0;
        for &x in shape {
            if !(x.is_finite()) {
                return None;
            }
            top = mathx::max(top, x);
            knots.push(top);
        }
        let unit = ThetaCurve { elapsed, knots };
        let at = unit.at(VIX_SESSIONS);
        if !(at > 0.0) {
            return None;
        }
        let scale = theta_vix / at;
        Some(ThetaCurve { elapsed, knots: unit.knots.iter().map(|x| x * scale).collect() })
    }

    /// The curve of shape `shape` (knots 1 on) itself, made non-decreasing
    /// by a running maximum and scaled by `factor`, with no level fixed: a
    /// single name's, whose shape is already its expected variance. `None`
    /// for an empty or non-positive shape.
    pub fn from_shape(shape: &[f64], elapsed: f64, factor: f64) -> Option<Self> {
        if shape.len() < 2 || !(factor > 0.0 && factor.is_finite()) {
            return None;
        }
        let mut knots = Vec::with_capacity(shape.len());
        let mut top = 0.0;
        for &x in shape {
            if !(x.is_finite()) {
                return None;
            }
            top = mathx::max(top, x);
            knots.push(if factor == 1.0 { top } else { top * factor });
        }
        if !(knots[0] > 0.0) {
            return None;
        }
        Some(ThetaCurve { elapsed, knots })
    }

    /// The at-the-money total variance `t` sessions out.
    pub fn at(&self, t: f64) -> f64 {
        if !(t > 0.0) {
            return 0.0;
        }
        let x = t + self.elapsed;
        let first = 1.0 - self.elapsed;
        if t <= first {
            return self.knots[0] * t / first;
        }
        let n = self.knots.len();
        let i = x.floor() as usize;
        if i >= n {
            let slope = self.knots[n - 1] - self.knots[n - 2];
            return self.knots[n - 1] + slope * (x - n as f64);
        }
        let frac = x - i as f64;
        if frac == 0.0 {
            return self.knots[i - 1];
        }
        self.knots[i - 1] + frac * (self.knots[i] - self.knots[i - 1])
    }
}

/// The grid [`StripHold`] is read on: `ln(theta / anchor)` from
/// `HOLD_LO` in steps of `HOLD_STEP`, the anchor a node.
const HOLD_LO: f64 = -6.0;
const HOLD_STEP: f64 = 0.5;
const HOLD_NODES: usize = 23;

/// The strip's total variance (the VIX formula's variance times the years)
/// of `ssvi` at at-the-money total variance `theta`: a function of the
/// total-variance smile alone, so read on a one-year strip.
fn strip_variance(ssvi: &Ssvi, theta: f64) -> Option<f64> {
    smile_moments(ssvi, theta, 1.0).map(|m| (m.vix / 100.0) * (m.vix / 100.0))
}

/// The term scaling with each tenor's strip held
/// (`surface_index_tenor_strip`): the at-the-money total variance `theta'`
/// at which the scaled smile's strip is the strip SSVI's own power law gives
/// at `theta`, as `ln(theta' / theta)` on a grid of `ln(theta / anchor)`,
/// linear between nodes and held at the ends. The anchor is a node and maps
/// to itself (the two smiles are one there), so the 21-session fit is
/// unchanged. Both strips rise with `theta`, so the map does too.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct StripHold {
    pub anchor: f64,
    pub logs: Vec<f64>,
}

impl StripHold {
    /// The map from SSVI's own smile `scaled` with its tenor dropped to
    /// `scaled` itself. A node whose strip does not price (total variances far
    /// beyond any listed tenor's, tens of years of variance) takes its
    /// neighbour's toward the anchor. `None` without a tenor.
    pub fn new(scaled: &Ssvi) -> Option<Self> {
        if scaled.tenor == 0.0 || !(scaled.anchor > 0.0) {
            return None;
        }
        let base = Ssvi { anchor: 0.0, tenor: 0.0, ..*scaled };
        let node = |theta: f64| -> Option<f64> {
            let target = strip_variance(&base, theta)?;
            // Each step scales theta by the ratio of the strips, nearly
            // proportional to theta, as `theta_for_vix` does.
            let mut held = theta;
            for _ in 0..100 {
                let ratio = target / strip_variance(scaled, held)?;
                if (ratio - 1.0).abs() <= 1e-12 {
                    break;
                }
                held *= ratio;
                if !(held > 0.0 && held.is_finite()) {
                    return None;
                }
            }
            Some(mathx::log(held / theta))
        };
        let zero = (-HOLD_LO / HOLD_STEP).round() as usize;
        let mut logs = vec![0.0; HOLD_NODES];
        for i in (zero + 1)..HOLD_NODES {
            let theta = scaled.anchor * mathx::exp(HOLD_LO + HOLD_STEP * i as f64);
            logs[i] = node(theta).unwrap_or(logs[i - 1]);
        }
        for i in (0..zero).rev() {
            let theta = scaled.anchor * mathx::exp(HOLD_LO + HOLD_STEP * i as f64);
            logs[i] = node(theta).unwrap_or(logs[i + 1]);
        }
        Some(StripHold { anchor: scaled.anchor, logs })
    }

    /// The held at-the-money total variance for SSVI's `theta`.
    pub fn at(&self, theta: f64) -> f64 {
        if !(theta > 0.0) {
            return theta;
        }
        let u = (mathx::log(theta / self.anchor) - HOLD_LO) / HOLD_STEP;
        let n = self.logs.len();
        let l = if u <= 0.0 {
            self.logs[0]
        } else if u >= (n - 1) as f64 {
            self.logs[n - 1]
        } else {
            let i = u.floor() as usize;
            let f = u - i as f64;
            if f == 0.0 { self.logs[i] } else { self.logs[i] + f * (self.logs[i + 1] - self.logs[i]) }
        };
        if l == 0.0 { theta } else { theta * mathx::exp(l) }
    }
}

/// A surface: the SSVI smile on a term structure of at-the-money total
/// variance, `T` in sessions from the moment it was built.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Surface {
    /// The underlying's root: `IDX`.
    pub root: String,
    pub ssvi: Ssvi,
    pub curve: ThetaCurve,
    /// The VIX the 21-session point was fitted to, and the skewness asked.
    pub vix: f64,
    pub skewness_target: Option<f64>,
    pub fit: SmileFit,
    /// The term scaling's strips held (`surface_index_tenor_strip`): the
    /// curve's at-the-money variance read through the map. `None` reads the
    /// curve itself.
    pub hold: Option<StripHold>,
}

impl Surface {
    /// At-the-money total variance `t` sessions out.
    pub fn theta(&self, t: f64) -> f64 {
        let theta = self.curve.at(t);
        match &self.hold {
            Some(hold) => hold.at(theta),
            None => theta,
        }
    }

    /// Total implied variance at log-moneyness `k`, `t` sessions out.
    pub fn total_variance(&self, k: f64, t: f64) -> f64 {
        let theta = self.theta(t);
        if !(theta > 0.0) {
            return 0.0;
        }
        self.ssvi.total_variance(k, theta)
    }

    /// Implied volatility, a fraction a year, at log-moneyness `k`, `t`
    /// sessions out.
    pub fn iv(&self, k: f64, t: f64) -> f64 {
        if !(t > 0.0) {
            return 0.0;
        }
        mathx::sqrt(self.total_variance(k, t) * YEAR / t)
    }

    /// The Cboe VIX and SKEW formulas on the strip `t` sessions out.
    pub fn strip(&self, t: f64) -> Option<StripMoments> {
        strip_moments(|k| self.iv(k, t), t / YEAR)
    }

    /// Gatheral and Jacquier's conditions at every knot, and the knots
    /// non-decreasing. `Err` names the first that fails.
    pub fn check(&self) -> Result<(), String> {
        let mut last = 0.0;
        for (i, &knot) in self.curve.knots.iter().enumerate() {
            let theta = self.hold.as_ref().map_or(knot, |h| h.at(knot));
            if theta < last {
                return Err(format!("calendar: theta falls from {last} to {theta} at knot {}", i + 1));
            }
            last = theta;
            self.ssvi.check(theta)?;
        }
        // Off SSVI the knots' order is not enough: total variance must not
        // fall with the tenor at any log-moneyness.
        if self.ssvi.shift != 0.0 {
            for pair in self.curve.knots.windows(2) {
                let pair = self.hold.as_ref().map_or([pair[0], pair[1]], |h| [h.at(pair[0]), h.at(pair[1])]);
                let width = 8.0 * mathx::sqrt(pair[1]);
                for i in 0..=200 {
                    let k = -width + 2.0 * width * f64::from(i) / 200.0;
                    let (a, b) = (self.ssvi.total_variance(k, pair[0]), self.ssvi.total_variance(k, pair[1]));
                    if b < a - 1e-12 {
                        return Err(format!("calendar: total variance falls from {a} to {b} at k {k}"));
                    }
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T21: f64 = 21.0 / 252.0;

    #[test]
    fn the_strip_hold_is_built_and_rises_at_every_level() {
        // VIX about 2 to 300, every correlation the fit allows: the map is
        // built, the anchor maps to itself, and theta' rises with theta.
        for &rho in &[-0.999, -0.9, -0.4, 0.0, 0.8, 0.999] {
            for &a in &[0.00005, 0.001, 0.01, 0.06, 0.4, 1.0] {
                let s = Ssvi::new(rho, 1.0, 0.5).with_tenor(a, 0.4);
                let h = StripHold::new(&s).unwrap();
                assert_eq!(h.at(a), a);
                let mut last = 0.0;
                for i in 0..400 {
                    let t = a * mathx::exp(-8.0 + 0.035 * f64::from(i));
                    let held = h.at(t);
                    assert!(held > last && held.is_finite(), "rho {rho} anchor {a} theta {t}");
                    last = held;
                }
            }
        }
    }

    #[test]
    fn a_flat_smile_reads_vix_100_sigma_and_skew_100() {
        for sigma in [0.08, 0.15, 0.3, 0.8] {
            let m = strip_moments(|_| sigma, T21).unwrap();
            // The strip's truncation at eight sds and its 400 points leave
            // a few parts in a million; a lognormal's log return has the
            // skewness of a normal, 0.
            assert!((m.vix - 100.0 * sigma).abs() < 2e-4 * 100.0 * sigma, "{sigma}: {}", m.vix);
            assert!(m.skewness.abs() < 1e-3, "{sigma}: {}", m.skewness);
            assert!((m.skew_index() - 100.0).abs() < 1e-2);
        }
    }

    #[test]
    fn a_put_skew_raises_skew() {
        let flat = smile_moments(&Ssvi::new(0.0, 0.0, 0.5), 0.04 * T21, T21).unwrap();
        let skewed = smile_moments(&Ssvi::new(-0.7, 1.0, 0.5), 0.04 * T21, T21).unwrap();
        assert!(skewed.skew_index() > flat.skew_index() + 5.0, "{} {}", skewed.skew_index(), flat.skew_index());
        // And SKEW rises as rho falls.
        let mut last = f64::NEG_INFINITY;
        for rho in [0.6, 0.3, 0.0, -0.3, -0.6, -0.9] {
            let m = smile_moments(&Ssvi::new(rho, 1.0, 0.5), 0.04 * T21, T21).unwrap();
            assert!(m.skew_index() > last, "{rho}");
            last = m.skew_index();
        }
    }

    #[test]
    fn the_fit_prices_the_vix_and_the_skewness_asked() {
        for vix in [11.0, 18.0, 30.0, 65.0] {
            for skew in [-0.5, -1.5, -2.5] {
                let fit = fit_smile(vix, Some(skew), T21, 1.0, 0.5).unwrap();
                assert!(fit.reached);
                assert!((fit.moments.vix - vix).abs() < 1e-9 * vix, "{vix} {skew}: {}", fit.moments.vix);
                assert!((fit.moments.skewness - skew).abs() < 1e-8, "{vix} {skew}: {}", fit.moments.skewness);
                // Re-read from the fitted parameters: SV1 by construction.
                let again = smile_moments(&Ssvi::new(fit.rho, 1.0, 0.5), fit.theta, T21).unwrap();
                assert!((again.vix - vix).abs() < 1e-9 * vix);
                assert!(fit.rho.abs() < Ssvi::rho_bound(1.0));
                Ssvi::new(fit.rho, 1.0, 0.5).check(fit.theta).unwrap();
            }
        }
        // Flat: theta alone.
        let flat = fit_smile(20.0, None, T21, 1.2, 0.4).unwrap();
        assert_eq!(flat.rho, 0.0);
        assert!((flat.moments.vix - 20.0).abs() < 1e-9 * 20.0);
        // A skewness past the bound is met at the bound.
        let far = fit_smile(20.0, Some(-40.0), T21, 1.2, 0.4).unwrap();
        assert!(!far.reached);
        assert_eq!(far.rho, -Ssvi::rho_bound(1.2));
        assert!((far.moments.vix - 20.0).abs() < 1e-9 * 20.0);
    }

    #[test]
    fn the_conditions_hold_inside_the_remark_and_fail_outside() {
        for eta in [0.2, 1.0, 1.5, 1.99] {
            let bound = Ssvi::rho_bound(eta);
            assert!(eta * (1.0 + bound) <= 2.0 + 1e-12 || eta <= 1.0);
            for gamma in [0.0, 0.25, 0.5] {
                for rho in [-bound, -0.5 * bound, 0.0, bound] {
                    let s = Ssvi::new(rho, eta, gamma);
                    for theta in [1e-6, 1e-4, 0.003, 0.05, 1.0, 50.0] {
                        s.check(theta).unwrap_or_else(|e| panic!("{eta} {gamma} {rho} {theta}: {e}"));
                    }
                }
            }
        }
        // Outside the remark: eta 3 and rho -0.9 at a large theta.
        assert!(Ssvi::new(-0.9, 3.0, 0.5).check(10.0).is_err());
        assert!(Ssvi::new(-0.9, 1.0, 0.5).check(0.0).is_err());
        assert!(Ssvi::new(1.0, 1.0, 0.5).check(0.1).is_err());
    }

    /// The static arbitrage the conditions rule out, read off prices: on a
    /// fine grid of strikes, call prices fall and are convex in the strike
    /// on every smile, and at a fixed log-moneyness total variance rises with
    /// the tenor.
    #[test]
    fn surface_prices_have_no_butterfly_or_calendar_arbitrage() {
        let variance: Vec<f64> = (0..300).map(|h| 1.2e-4 * (1.0 + 0.6 * mathx::exp(-(h as f64) / 30.0))).collect();
        let events = [(10usize, 4e-5), (40, 4e-5)];
        let inputs = TermInputs {
            variance: &variance,
            elapsed: 0.3,
            premium_short: -0.05,
            premium_long: 0.15,
            events: &events,
            event_weight: 1.0,
        };
        let shape = term_shape(&inputs, 270);
        let fit = fit_smile(24.0, Some(-2.0), T21, 1.3, 0.45).unwrap();
        let surface = Surface {
            root: "IDX".into(),
            ssvi: Ssvi::new(fit.rho, 1.3, 0.45),
            curve: ThetaCurve::new(&shape, 0.3, fit.theta).unwrap(),
            vix: 24.0,
            skewness_target: Some(-2.0),
            fit,
            hold: None,
        };
        surface.check().unwrap();
        // The VIX point is the fit's, to the last bits.
        assert!((surface.theta(21.0) - fit.theta).abs() <= 1e-14 * fit.theta);
        assert!((surface.strip(21.0).unwrap().vix - 24.0).abs() < 1e-9 * 24.0);
        let ks: Vec<f64> = (-120..=120).map(|i| i as f64 * 0.005).collect();
        let tenors = [0.4, 0.7, 1.0, 5.0, 9.7, 10.0, 10.7, 21.0, 63.0, 126.5, 252.0, 280.0];
        for &t in &tenors {
            let years = t / 252.0;
            let calls: Vec<f64> = ks
                .iter()
                .map(|k| black_price(Right::Call, 1.0, mathx::exp(*k), years, surface.iv(*k, t), 1.0))
                .collect();
            for i in 1..calls.len() {
                assert!(calls[i] <= calls[i - 1] + 1e-15, "{t}: calls rise at {}", ks[i]);
            }
            for i in 1..calls.len() - 1 {
                let (k0, k1, k2) = (mathx::exp(ks[i - 1]), mathx::exp(ks[i]), mathx::exp(ks[i + 1]));
                let left = (calls[i] - calls[i - 1]) / (k1 - k0);
                let right = (calls[i + 1] - calls[i]) / (k2 - k1);
                assert!(right >= left - 1e-12, "{t}: a butterfly at {} is negative", ks[i]);
            }
        }
        for w in tenors.windows(2) {
            for k in &ks {
                assert!(surface.total_variance(*k, w[1]) >= surface.total_variance(*k, w[0]), "{w:?} {k}");
            }
        }
    }

    #[test]
    fn the_term_shape_reads_the_forecast_the_premia_and_the_reports() {
        let flat = TermInputs { variance: &[], elapsed: 0.0, premium_short: 0.0, premium_long: 0.0, events: &[], event_weight: 0.0 };
        let shape = term_shape(&flat, 252);
        for (i, x) in shape.iter().enumerate() {
            assert_eq!(*x, (i + 1) as f64);
        }
        // With no premia or reports, theta scales with the expected variance:
        // a forecast that doubles after 21 sessions doubles the slope there.
        let v: Vec<f64> = (1..=100).map(|h| if h <= 21 { 1e-4 } else { 2e-4 }).collect();
        let fc = TermInputs { variance: &v, ..flat };
        let curve = ThetaCurve::new(&term_shape(&fc, 100), 0.0, 0.003).unwrap();
        assert!((curve.at(21.0) - 0.003).abs() < 1e-18);
        assert!((curve.at(42.0) - 0.009).abs() < 1e-15);
        // The long premium is exp(premium_long) at 252 sessions.
        let prem = TermInputs { premium_long: 0.2, ..flat };
        let s = term_shape(&prem, 252);
        assert!((s[251] / 252.0 - mathx::exp(0.2)).abs() < 1e-12);
        assert_eq!(s[20], 21.0);
        // A report reaches every knot from its own.
        let events = [(5usize, 3.0)];
        let ev = TermInputs { events: &events, event_weight: 0.5, ..flat };
        let s = term_shape(&ev, 10);
        assert_eq!(s[3], 4.0);
        assert_eq!(s[4], 5.0 + 1.5);
        assert_eq!(s[9], 10.0 + 1.5);
        // Elapsed: knot n sits at n - elapsed sessions.
        let part = TermInputs { elapsed: 0.25, ..flat };
        let c = ThetaCurve::new(&term_shape(&part, 30), 0.25, 21.0).unwrap();
        assert!((c.at(0.75) - 0.75).abs() < 1e-12);
        assert!((c.at(21.0) - 21.0).abs() < 1e-12);
        assert!((c.at(40.0) - 40.0).abs() < 1e-12, "{}", c.at(40.0));
        // Past the last knot, the last slope.
        assert!((c.at(35.0) - 35.0).abs() < 1e-12);
    }

    #[test]
    fn a_falling_shape_is_held_at_its_running_maximum() {
        let c = ThetaCurve::new(&[1.0, 3.0, 2.0, 4.0], 0.0, 3.0);
        // Too short to reach 21 sessions on its own knots: read by the last slope.
        let c = c.unwrap();
        assert_eq!(c.knots[1], c.knots[2]);
        for w in c.knots.windows(2) {
            assert!(w[1] >= w[0]);
        }
    }
}
