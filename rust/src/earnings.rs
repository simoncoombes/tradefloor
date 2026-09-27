//! The earnings calendar (`earnings_surprise_sigma`): when each public
//! company reports, and the draws that price its reports.
//!
//! Every function here is PURE: a function of the engine's earnings key
//! ([`crate::rng::GameRng::earnings_key`]), the company's id, the quarter and
//! a slot. Nothing is streamed and nothing is stored, so the dates ahead can
//! be listed without drawing, a report's draws do not depend on how many
//! names reported before it, a roster change reshuffles no other name's
//! calendar, and the only state a snapshot needs is the key.
//!
//! # The calendar
//!
//! A quarter is [`QUARTER_SESSIONS`] sessions, the quarter `q` running from
//! session `63 q`. Each name has an offset `o` into the quarter, drawn once
//! from [`REAL_NAME_OFFSETS`], and each quarter adds a jitter `j` uniform on
//! [-3, 3]; the reaction session of quarter `q` is `63 q + o + j`, held
//! inside `[63 q + 1, 63 q + 62]`, so a name reports exactly once a quarter
//! and never twice in one.
//!
//! The reaction session is the first session that trades the report: a real
//! report filed before the open (72 per cent of the forty's) or after the
//! previous close (26 per cent) is realised at that session's opening print,
//! and the engine realises every report there.

use crate::rng::GameRng;

/// Sessions per quarter: a 252-session year in four.
pub const QUARTER_SESSIONS: i64 = 63;

/// The forty real names' median reaction offsets, in sessions after the
/// calendar quarter's first session, sorted (39 names: DIS is dropped for
/// having only 25 filings found). Measured from SEC EDGAR 8-K Item 2.02
/// acceptance times, 2015-01-02 to 2025-07-31 (data.sec.gov submissions,
/// fetched 2026-09-26), against the names' Yahoo daily sessions; a half is
/// a name whose median sits between two sessions. The event-level quantiles
/// are p10 9, p25 13, p50 18, p75 21 and p90 32. Derived statistics of
/// public filings, not the filings.
pub const REAL_NAME_OFFSETS: [f64; 39] = [
    8.0, 9.0, 9.0, 10.0, 10.0, 10.0, 11.0, 11.0, 12.0, 14.0, 14.0, 14.0, 15.0,
    16.0, 16.0, 16.5, 17.0, 17.0, 17.5, 18.0, 18.0, 18.0, 18.0, 18.5, 19.0,
    19.0, 19.0, 19.0, 19.5, 20.0, 20.0, 20.0, 20.0, 21.0, 32.0, 32.5, 33.0,
    43.5, 58.0,
];

/// The jitter's half-width, in sessions.
pub const JITTER: i64 = 3;

// The slots a (quarter, slot) word names. The offset is drawn once per name,
// so its word carries no quarter.
const SLOT_OFFSET: u64 = 0;
const SLOT_JITTER: u64 = 1;
const SLOT_SURPRISE: u64 = 2;
const SLOT_SESSION: u64 = 3;
const SLOT_FOLLOWTHROUGH: u64 = 4;
const SLOT_CYCLE: u64 = 5;

/// A company id's 64-bit FNV-1a hash: the calendar keys on the id rather
/// than the roster position, so a listing or a removal moves no other name.
pub fn id_hash(id: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in id.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

fn word(quarter: i64, slot: u64) -> u64 {
    ((quarter as u64) << 8) | slot
}

fn keyed(key: u64, name: u64, quarter: i64, slot: u64) -> GameRng {
    GameRng::keyed(key, name, word(quarter, slot))
}

/// The name's offset into every quarter, before the jitter.
pub fn name_offset(key: u64, name: u64) -> i64 {
    let mut g = GameRng::keyed(key, name, word(-1, SLOT_OFFSET));
    let u = g.next_f64();
    let n = REAL_NAME_OFFSETS.len();
    let i = std::cmp::min((u * n as f64) as usize, n - 1);
    // A half rounds up: the median of an even count of sessions.
    (REAL_NAME_OFFSETS[i] + 0.5) as i64
}

/// The session name `name` reacts to its report of quarter `quarter` on.
pub fn reaction_session(key: u64, name: u64, quarter: i64) -> i64 {
    let mut g = keyed(key, name, quarter, SLOT_JITTER);
    let u = g.next_f64();
    let j = std::cmp::min((u * (2 * JITTER + 1) as f64) as i64, 2 * JITTER) - JITTER;
    let o = (name_offset(key, name) + j).clamp(1, QUARTER_SESSIONS - 1);
    QUARTER_SESSIONS * quarter + o
}

/// The quarter whose report `name` reacts to on `day`, if `day` is a
/// reaction session.
pub fn reacts_on(key: u64, name: u64, day: i64) -> Option<i64> {
    if day < 0 {
        return None;
    }
    let q = day.div_euclid(QUARTER_SESSIONS);
    (reaction_session(key, name, q) == day).then_some(q)
}

/// A unit-variance draw: a normal at `df` 0.0, else a student t with `df`
/// degrees of freedom scaled to unit variance, the chi-square the sum of
/// `df` squared normals from the same generator.
fn unit_t(g: &mut GameRng, df: f64) -> f64 {
    let z = g.next_normal();
    if df == 0.0 {
        return z;
    }
    let k = df as usize;
    let mut chi2 = 0.0;
    for _ in 0..k {
        let n = g.next_normal();
        chi2 += n * n;
    }
    if !(chi2 > 0.0) {
        return z;
    }
    z * crate::mathx::sqrt((df - 2.0) / chi2)
}

/// The report's surprise in units of its own sd: unit variance.
pub fn surprise_unit(key: u64, name: u64, quarter: i64, df: f64) -> f64 {
    unit_t(&mut keyed(key, name, quarter, SLOT_SURPRISE), df)
}

/// A (quarter, slot) word with an open minute in bits 40 and up: the
/// quarter sits below bit 40 for any run shorter than 2^31 quarters, so no
/// minute's word is another slot's.
fn minute_word(quarter: i64, slot: u64, minute: i64) -> u64 {
    word(quarter, slot) ^ (((minute as u64) + 1) << 40)
}

/// The reaction session's own discovery at open minute `minute` (0 at
/// 9:30), a standard normal: the session walks it into fair value one
/// minute at a time (`Engine::walk_earnings_sessions`).
pub fn session_unit(key: u64, name: u64, quarter: i64, minute: i64) -> f64 {
    GameRng::keyed(key, name, minute_word(quarter, SLOT_SESSION, minute)).next_normal()
}

/// The session after's follow-through at open minute `minute`, a standard
/// normal, walked in the same way.
pub fn followthrough_unit(key: u64, name: u64, quarter: i64, minute: i64) -> f64 {
    GameRng::keyed(key, name, minute_word(quarter, SLOT_FOLLOWTHROUGH, minute)).next_normal()
}

#[allow(dead_code)]
pub(crate) fn cycle_unit(key: u64, name: u64, quarter: i64) -> f64 {
    keyed(key, name, quarter, SLOT_CYCLE).next_normal()
}

/// A log move of sd `sigma` times `unit`, mean one in level:
/// `sigma unit - sigma^2 / 2`.
pub fn mean_one(sigma: f64, unit: f64) -> f64 {
    sigma * unit - 0.5 * sigma * sigma
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_report_a_quarter_inside_the_quarter() {
        for key in [1u64, 42, 0xdead_beef] {
            for id in ["AAA", "BBB", "C0", "technology-7"] {
                let h = id_hash(id);
                for q in 0..40 {
                    let d = reaction_session(key, h, q);
                    assert!(d > 63 * q && d < 63 * (q + 1), "{d} outside quarter {q}");
                    assert_eq!(reacts_on(key, h, d), Some(q));
                    let hits = (63 * q..63 * (q + 1)).filter(|&x| reacts_on(key, h, x).is_some()).count();
                    assert_eq!(hits, 1);
                }
            }
        }
    }

    #[test]
    fn the_surprise_has_unit_variance() {
        for df in [0.0, 4.0] {
            let n = 40_000;
            let (mut s, mut s2) = (0.0, 0.0);
            for q in 0..n {
                let x = surprise_unit(7, id_hash("X"), q, df);
                s += x;
                s2 += x * x;
            }
            let m = s / n as f64;
            let v = s2 / n as f64 - m * m;
            assert!(m.abs() < 0.03, "mean {m}");
            assert!((v - 1.0).abs() < 0.08, "df {df} variance {v}");
        }
    }

    #[test]
    fn the_minute_walks_are_independent_unit_normals() {
        // Distinct minutes, parts and quarters give distinct draws, and a
        // session's 390 minutes sum to about one sd of 390^0.5.
        let h = id_hash("X");
        assert_ne!(session_unit(7, h, 3, 0), session_unit(7, h, 3, 1));
        assert_ne!(session_unit(7, h, 3, 5), followthrough_unit(7, h, 3, 5));
        assert_ne!(session_unit(7, h, 3, 5), session_unit(7, h, 4, 5));
        let n = 400;
        let mut s2 = 0.0;
        for q in 0..n {
            let day: f64 = (0..390).map(|m| session_unit(11, h, q, m)).sum();
            s2 += day * day / 390.0;
        }
        let v = s2 / n as f64;
        assert!((v - 1.0).abs() < 0.2, "variance of a session's walk {v}");
    }

    #[test]
    fn offsets_follow_the_real_table() {
        let mut offs: Vec<i64> = (0..4000).map(|i| name_offset(9, id_hash(&format!("N{i}")))).collect();
        offs.sort();
        let median = offs[offs.len() / 2];
        assert!((17..=19).contains(&median), "median offset {median}");
        assert!(*offs.first().unwrap() >= 8 && *offs.last().unwrap() <= 58);
    }
}
