//! The engine's forecast of its own state (`forecast_horizon_sessions`).
//!
//! Each close computes, from public state only, the expected published VIX,
//! the expected one-session variance of the index and of each name, the
//! expected policy rate and the expected oil price at horizons 1 to `H`
//! sessions. Futures are priced as these expectations plus a stated premium,
//! and option term structures scale by the expected variance, so a curve
//! cannot hand an agent an edge the model's own dynamics do not support.
//!
//! How each series is computed is on
//! [`crate::params::ModelParams::forecast_horizon_sessions`]; this module
//! holds the result and its snapshot form.

/// The forecast one close computed: entry `h - 1` of each series is the
/// expectation `h` sessions ahead, for `h` in 1 to [`Forecast::horizon`].
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Forecast {
    /// The engine's elapsed sessions at the close that computed it.
    pub day: i64,
    /// The published VIX expected at the close `h` sessions ahead.
    pub vix: Vec<f64>,
    /// The index's one-session variance expected for session `t + h`, in
    /// fraction squared per session: the identity the VIX is read from
    /// (`market::index_var`), so entry 0 is the variance of the next
    /// session's return as the close reads it.
    pub index_variance: Vec<f64>,
    /// The policy rate expected at the close `h` sessions ahead, per cent.
    pub policy_rate: Vec<f64>,
    /// The oil price expected at the close `h` sessions ahead.
    pub oil: Vec<f64>,
    /// Per company slot, in roster order: the name's one-session variance
    /// expected for session `t + h`, fraction squared per session. Empty for
    /// a name outside the index (bankrupt, taken private or with no
    /// capitalisation).
    pub name_variance: Vec<Vec<f64>>,
}

impl Forecast {
    /// The number of sessions the forecast runs.
    pub fn horizon(&self) -> usize {
        self.vix.len()
    }

    /// The forecast as one flat list of numbers, the form the snapshot and
    /// the state hash carry: the day, the horizon `H` and the slot count `N`,
    /// then the four series of `H`, then each slot's length and its series.
    pub fn to_words(&self) -> Vec<f64> {
        let h = self.horizon();
        let mut out = Vec::with_capacity(3 + 4 * h + self.name_variance.len() * (h + 1));
        out.push(self.day as f64);
        out.push(h as f64);
        out.push(self.name_variance.len() as f64);
        for series in [&self.vix, &self.index_variance, &self.policy_rate, &self.oil] {
            out.extend_from_slice(series);
        }
        for row in &self.name_variance {
            out.push(row.len() as f64);
            out.extend_from_slice(row);
        }
        out
    }

    /// The inverse of [`Forecast::to_words`], refusing a list that is not
    /// one.
    pub fn from_words(words: &[f64]) -> Result<Self, String> {
        let bad = |why: &str| format!("a forecast's numbers do not parse: {why}");
        let count = |v: f64, what: &str| -> Result<usize, String> {
            if v.is_finite() && v >= 0.0 && v.fract() == 0.0 && v <= 1e9 {
                Ok(v as usize)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        if words.len() < 3 {
            return Err(bad("fewer than three numbers"));
        }
        let day = words[0];
        if !(day.is_finite() && day.fract() == 0.0) {
            return Err(bad(&format!("the day is {day}")));
        }
        let h = count(words[1], "the horizon")?;
        let n = count(words[2], "the slot count")?;
        let mut at = 3;
        let mut take = |len: usize| -> Result<Vec<f64>, String> {
            if at + len > words.len() {
                return Err(bad("it is shorter than its counts say"));
            }
            let v = words[at..at + len].to_vec();
            at += len;
            Ok(v)
        };
        let vix = take(h)?;
        let index_variance = take(h)?;
        let policy_rate = take(h)?;
        let oil = take(h)?;
        let mut name_variance = Vec::with_capacity(n);
        for _ in 0..n {
            let len = take(1)?[0];
            let len = count(len, "a slot's length")?;
            if len != 0 && len != h {
                return Err(bad(&format!("a slot carries {len} numbers for a horizon of {h}")));
            }
            name_variance.push(take(len)?);
        }
        if at != words.len() {
            return Err(bad("it is longer than its counts say"));
        }
        Ok(Forecast { day: day as i64, vix, index_variance, policy_rate, oil, name_variance })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_words_round_trip_and_a_bad_list_is_refused() {
        let f = Forecast {
            day: 12,
            vix: vec![20.0, 19.5],
            index_variance: vec![1e-4, 1.1e-4],
            policy_rate: vec![3.0, 3.0],
            oil: vec![80.0, 80.5],
            name_variance: vec![vec![2e-4, 2.1e-4], vec![], vec![3e-4, 3e-4]],
        };
        let words = f.to_words();
        assert_eq!(Forecast::from_words(&words).unwrap(), f);
        assert!(Forecast::from_words(&words[..words.len() - 1]).is_err());
        let mut longer = words.clone();
        longer.push(0.0);
        assert!(Forecast::from_words(&longer).is_err());
        let mut odd = words;
        odd[1] = 2.5;
        assert!(Forecast::from_words(&odd).is_err());
    }
}
