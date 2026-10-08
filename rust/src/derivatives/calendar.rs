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
}
