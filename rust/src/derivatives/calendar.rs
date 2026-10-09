//! The expiry calendar, in session numbers.
//!
//! The model has no dates: a year is 252 sessions, a quarter 63 and a month
//! 21, counted from 0 at the first session, as `Engine::earnings_calendar`
//! counts. So every expiry is a session number, and the calendar is a pure
//! function of it.
//!
//! Index futures are quarterly. Each expires at the opening print of session
//! 15 of its quarter's last month (the 15th of the month's 21 sessions,
//! counting from 1), which is where the third Friday falls in a month of 21
//! trading days at the latest: quarter `q` covers sessions `63q` to `63q +
//! 62`, its last month starts at `63q + 42`, and its future expires at
//! session `63q + 56`. The front two are listed, and each has a roll date
//! [`ROLL_SESSIONS`] sessions before its expiry, the session from which the
//! next contract is the front by the roll rule.
//!
//! VIX futures are monthly. Each expires at session 15 of its 21-session
//! month, `21m + 14`, where the month's index options expire, so the
//! quarterly index futures' expiries are every third of them. The next
//! [`VIX_FUTURES_LISTED`] are listed: 126 sessions, the furthest horizon the
//! VIX futures' rows read.

/// Sessions in the model's year.
pub const SESSIONS_PER_YEAR: i64 = 252;

/// Sessions in the model's quarter.
pub const SESSIONS_PER_QUARTER: i64 = 63;

/// Sessions in the model's month.
pub const SESSIONS_PER_MONTH: i64 = 21;

/// The session of its month an index future expires on, counting the
/// month's sessions from 1: the third-Friday analogue.
pub const INDEX_FUTURE_EXPIRY_SESSION: i64 = 15;

/// How many index futures are listed at once: the front two.
pub const INDEX_FUTURES_LISTED: usize = 2;

/// Sessions before its expiry at which a contract stops being the front by
/// the roll rule.
pub const ROLL_SESSIONS: i64 = 6;

/// The session the index future of quarter `quarter` expires at, at its
/// opening print: session 15 of the quarter's last month.
pub fn quarterly_expiry(quarter: i64) -> i64 {
    quarter * SESSIONS_PER_QUARTER + 2 * SESSIONS_PER_MONTH + INDEX_FUTURE_EXPIRY_SESSION - 1
}

/// The first `count` index-future expiries strictly after session `after`,
/// in order. `after` may be negative (before the first session).
pub fn index_future_expiries(after: i64, count: usize) -> Vec<i64> {
    let offset = quarterly_expiry(0);
    // The first quarter whose expiry is past `after`.
    let mut q = (after - offset).div_euclid(SESSIONS_PER_QUARTER);
    while quarterly_expiry(q) <= after {
        q += 1;
    }
    while q > 0 && quarterly_expiry(q - 1) > after {
        q -= 1;
    }
    (0..count as i64).map(|k| quarterly_expiry(q + k)).collect()
}

/// How many VIX futures are listed at once: the next six monthly expiries.
pub const VIX_FUTURES_LISTED: usize = 6;

/// How many of them, from the front, have an agent-facing book with latent
/// depth; the rest quote from the maker's ladder alone.
pub const VIX_FUTURES_WITH_DEPTH: usize = 3;

/// The session the VIX future of month `month` expires at, at its open:
/// session 15 of the month, where the month's index options expire.
pub fn monthly_expiry(month: i64) -> i64 {
    month * SESSIONS_PER_MONTH + INDEX_FUTURE_EXPIRY_SESSION - 1
}

/// The first `count` VIX-future expiries strictly after session `after`,
/// in order. `after` may be negative.
pub fn vix_future_expiries(after: i64, count: usize) -> Vec<i64> {
    let offset = monthly_expiry(0);
    let mut m = (after - offset).div_euclid(SESSIONS_PER_MONTH);
    while monthly_expiry(m) <= after {
        m += 1;
    }
    while monthly_expiry(m - 1) > after {
        m -= 1;
    }
    (0..count as i64).map(|k| monthly_expiry(m + k)).collect()
}

/// Whether `session` is a VIX-future expiry.
pub fn is_vix_future_expiry(session: i64) -> bool {
    session.rem_euclid(SESSIONS_PER_MONTH) == monthly_expiry(0)
}

/// How many policy-rate futures are listed: the month in progress and the
/// next twelve.
pub const POLICY_RATE_FUTURES_LISTED: usize = 13;

/// How many term-rate futures are listed: the quarter in progress and the
/// next seven.
pub const TERM_RATE_FUTURES_LISTED: usize = 8;

/// The last session of the 21-session month `month`, where its policy-rate
/// future settles at the close.
pub fn month_end(month: i64) -> i64 {
    (month + 1) * SESSIONS_PER_MONTH - 1
}

/// The last session of the 63-session quarter `quarter`, where its term-rate
/// future settles at the close.
pub fn quarter_end(quarter: i64) -> i64 {
    (quarter + 1) * SESSIONS_PER_QUARTER - 1
}

/// The first `count` period ends of `length` sessions at or after session
/// `from`, in order.
pub fn period_ends(from: i64, length: i64, count: usize) -> Vec<i64> {
    let first = from.div_euclid(length);
    (0..count as i64).map(|k| (first + k + 1) * length - 1).collect()
}

/// A contract's roll date: [`ROLL_SESSIONS`] sessions before its expiry.
pub fn roll_session(expiry: i64) -> i64 {
    expiry - ROLL_SESSIONS
}

/// Whether `session` is an index-future expiry.
pub fn is_index_future_expiry(session: i64) -> bool {
    session.rem_euclid(SESSIONS_PER_QUARTER) == quarterly_expiry(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expiries_fall_on_session_15_of_each_quarters_last_month() {
        assert_eq!(quarterly_expiry(0), 56);
        assert_eq!(quarterly_expiry(1), 119);
        assert_eq!(quarterly_expiry(3), 245);
        for q in 0..40 {
            let e = quarterly_expiry(q);
            // In the quarter's third month, the 15th session counting from 1.
            let in_quarter = e - q * SESSIONS_PER_QUARTER;
            assert_eq!(in_quarter / SESSIONS_PER_MONTH, 2);
            assert_eq!(in_quarter % SESSIONS_PER_MONTH + 1, INDEX_FUTURE_EXPIRY_SESSION);
            assert!(is_index_future_expiry(e));
            assert!(!is_index_future_expiry(e + 1));
        }
        // Four a year, 63 sessions apart.
        let all: Vec<i64> = (0..8).map(quarterly_expiry).collect();
        for w in all.windows(2) {
            assert_eq!(w[1] - w[0], SESSIONS_PER_QUARTER);
        }
    }

    #[test]
    fn the_front_two_are_the_next_two_expiries_strictly_after_a_session() {
        assert_eq!(index_future_expiries(-1, 2), vec![56, 119]);
        assert_eq!(index_future_expiries(0, 2), vec![56, 119]);
        assert_eq!(index_future_expiries(55, 2), vec![56, 119]);
        // At the expiry's own open it settles, so it is no longer listed.
        assert_eq!(index_future_expiries(56, 2), vec![119, 182]);
        assert_eq!(index_future_expiries(118, 2), vec![119, 182]);
        assert_eq!(index_future_expiries(-200, 1), vec![quarterly_expiry(-4)]);
        assert_eq!(quarterly_expiry(-4), -196);
        for t in -100..1000 {
            let e = index_future_expiries(t, 2);
            assert!(e[0] > t && e[0] - t <= SESSIONS_PER_QUARTER, "{t} {e:?}");
            assert_eq!(e[1] - e[0], SESSIONS_PER_QUARTER);
            assert!(is_index_future_expiry(e[0]));
        }
        assert_eq!(roll_session(56), 50);
    }

    #[test]
    fn rate_futures_periods_end_on_each_months_and_quarters_last_session() {
        assert_eq!(month_end(0), 20);
        assert_eq!(quarter_end(0), 62);
        assert_eq!(period_ends(0, SESSIONS_PER_MONTH, 2), vec![20, 41]);
        assert_eq!(period_ends(20, SESSIONS_PER_MONTH, 1), vec![20]);
        assert_eq!(period_ends(21, SESSIONS_PER_MONTH, 1), vec![41]);
        assert_eq!(period_ends(62, SESSIONS_PER_QUARTER, 2), vec![62, 125]);
        for t in 0..1000 {
            let m = period_ends(t, SESSIONS_PER_MONTH, POLICY_RATE_FUTURES_LISTED);
            assert!(m[0] >= t && m[0] - t < SESSIONS_PER_MONTH);
            assert_eq!(m[12] - m[0], 12 * SESSIONS_PER_MONTH);
            let q = period_ends(t, SESSIONS_PER_QUARTER, TERM_RATE_FUTURES_LISTED);
            assert!(q[0] >= t && q[0] - t < SESSIONS_PER_QUARTER);
        }
    }

    #[test]
    fn vix_futures_expire_monthly_on_session_15_and_the_next_six_are_listed() {
        assert_eq!(monthly_expiry(0), 14);
        assert_eq!(monthly_expiry(2), 56);
        for m in 0..120 {
            let e = monthly_expiry(m);
            assert_eq!(e % SESSIONS_PER_MONTH + 1, INDEX_FUTURE_EXPIRY_SESSION);
            assert!(is_vix_future_expiry(e) && !is_vix_future_expiry(e + 1));
        }
        // Every quarterly index-future expiry is a VIX-future expiry.
        for q in 0..40 {
            assert!(is_vix_future_expiry(quarterly_expiry(q)));
        }
        assert_eq!(vix_future_expiries(-1, 2), vec![14, 35]);
        assert_eq!(vix_future_expiries(13, 1), vec![14]);
        // At the expiry's own open it settles, so it is no longer listed.
        assert_eq!(vix_future_expiries(14, 1), vec![35]);
        for t in -100..2000 {
            let e = vix_future_expiries(t, VIX_FUTURES_LISTED);
            assert!(e[0] > t && e[0] - t <= SESSIONS_PER_MONTH, "{t} {e:?}");
            // The sixth is at most 126 sessions out, the horizon VF6 reads.
            assert!(e[VIX_FUTURES_LISTED - 1] - t <= 126, "{t} {e:?}");
            for w in e.windows(2) {
                assert_eq!(w[1] - w[0], SESSIONS_PER_MONTH);
            }
        }
    }
}
