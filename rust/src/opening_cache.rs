//! Engines whose construction played a market prehistory, kept so one
//! process does not play the same one twice.
//!
//! # Why
//!
//! A preset with `market_prehistory_sessions` on plays that many sessions on
//! a copy of the engine before day zero (`Engine::live_market_prehistory`).
//! On `pt-v21`, which plays 504, building an engine over 20 names took 1.45 s
//! on an Apple M-series machine, against about 1 ms on `pt-v20`, and the cost
//! grows with the roster: 0.44 s for 5 names, 2.9 s for 40. A test suite, a
//! notebook and a host that rebuilds an engine to restore a snapshot into it
//! build the same few engines over and over: the library's own suite built
//! one 20-name engine under one seed hundreds of times.
//!
//! # Why it cannot move a result
//!
//! Construction reads its arguments and nothing else: the seed, the roster,
//! the economy, the central bank, the sector keys, the model and whether the
//! opening is settled. The memos it passes through on the way
//! (`stationary_phase_shares_for`) return the very value the walk would
//! have. So two builds from the same arguments are the same engine, and a
//! hit hands back a clone of the engine the first build produced. A clone is
//! exact by `#[derive(Clone)]`, which is what `fork` relies on already.
//!
//! The key is every argument, written out by its derived `Debug` and hashed
//! with SHA-256. `Debug` prints every field of every type here, a field
//! added later included, so a new input cannot be left out of the key by
//! someone forgetting it. It prints each f64 as the shortest decimal that
//! reads back to the same bits, with the sign of a zero, so two keys agree
//! only when the arguments agree to the bit. The one thing it does not
//! print is a NaN's payload, so a build whose arguments hold a NaN anywhere
//! is not cached at all.
//!
//! Only a build that plays a prehistory is cached. Every other build costs
//! about a millisecond, and keeping it would spend memory to save nothing.
//!
//! # What it holds
//!
//! At most [`DEFAULT_CAPACITY`] engines by default and [`NAME_BUDGET`]
//! names between them, the least recently used leaving first; an engine
//! over the budget on its own is never kept. A kept pt-v21 engine holds
//! about 160 KB over 20 names, 490 KB over 120 and 1.9 MB over 500
//! (resident memory over 16 kept engines of each), so the cache holds about
//! 9 MB at most. [`set_capacity`] changes the count, and 0 turns the
//! cache off and empties it. The cache is one per process and is locked for
//! a lookup or an insert, never across a build, so threads that build at
//! once each play their own prehistory and the second insert of a key
//! replaces the first with an equal engine.

use std::sync::Mutex;

use sha2::{Digest, Sha256};

use crate::economy::{CentralBankState, EconomyState};
use crate::engine::Engine;
use crate::market::TickCompany;
use crate::params::ModelParams;

/// How many engines the cache holds until a host says otherwise.
pub const DEFAULT_CAPACITY: usize = 16;

/// The most names the kept engines hold between them, which is what their
/// memory follows. A roster over it is built every time.
pub const NAME_BUDGET: usize = 2000;

/// The key: SHA-256 over the arguments' `Debug`.
pub(crate) type Key = [u8; 32];

struct Cache {
    capacity: usize,
    /// [`NAME_BUDGET`], a field so a test can hold a cache to a smaller one.
    budget: usize,
    /// Least recently used first.
    entries: Vec<(Key, Engine)>,
    hits: u64,
    misses: u64,
}

impl Cache {
    const fn new(capacity: usize, budget: usize) -> Self {
        Cache { capacity, budget, entries: Vec::new(), hits: 0, misses: 0 }
    }

    fn get(&mut self, key: &Key) -> Option<Engine> {
        if self.capacity == 0 {
            return None;
        }
        match self.entries.iter().position(|(k, _)| k == key) {
            Some(at) => {
                let entry = self.entries.remove(at);
                let engine = entry.1.clone();
                self.entries.push(entry);
                self.hits += 1;
                #[cfg(test)]
                HITS.with(|n| n.set(n.get() + 1));
                Some(engine)
            }
            None => {
                self.misses += 1;
                None
            }
        }
    }

    fn put(&mut self, key: Key, engine: &Engine) {
        if self.capacity == 0 || engine.companies().len() > self.budget {
            return;
        }
        self.entries.retain(|(k, _)| *k != key);
        self.entries.push((key, engine.clone()));
        self.trim();
    }

    fn set_capacity(&mut self, capacity: usize) {
        self.capacity = capacity;
        self.trim();
    }

    fn names(&self) -> usize {
        self.entries.iter().map(|(_, e)| e.companies().len()).sum()
    }

    /// Drop the least recently used engines until the rest fit the
    /// capacity and the name budget.
    fn trim(&mut self) {
        while self.entries.len() > self.capacity || self.names() > self.budget {
            self.entries.remove(0);
        }
    }

    fn info(&self) -> OpeningCacheInfo {
        OpeningCacheInfo {
            capacity: self.capacity,
            entries: self.entries.len(),
            names: self.names(),
            hits: self.hits,
            misses: self.misses,
        }
    }
}

static CACHE: Mutex<Cache> = Mutex::new(Cache::new(DEFAULT_CAPACITY, NAME_BUDGET));

/// The cache, whatever a panic elsewhere left the lock in: every update
/// leaves the cache whole before it can panic, so a poisoned lock guards
/// nothing half-written.
fn cache() -> std::sync::MutexGuard<'static, Cache> {
    CACHE.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// What the cache holds and has done since the process started.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OpeningCacheInfo {
    /// The most engines it holds.
    pub capacity: usize,
    /// The engines it holds now.
    pub entries: usize,
    /// The names across those engines, which is what their memory follows.
    pub names: usize,
    /// Builds served from the cache.
    pub hits: u64,
    /// Builds that played their prehistory with the cache on.
    pub misses: u64,
}

/// Whether the cache is on, so a build off it need not work out a key.
pub(crate) fn on() -> bool {
    cache().capacity > 0
}

/// The key for a build, or `None` when the build must not be cached: the
/// roster is over [`NAME_BUDGET`], or an argument holds a NaN, whose payload
/// `Debug` does not print.
pub(crate) fn key(
    seed: u64,
    companies: &[TickCompany],
    economy: &EconomyState,
    central_bank: &CentralBankState,
    sector_keys: &[String],
    params: &ModelParams,
    settle_opening: bool,
) -> Option<Key> {
    if companies.len() > NAME_BUDGET {
        return None;
    }
    let text = format!(
        "{seed:?}|{companies:?}|{economy:?}|{central_bank:?}|{sector_keys:?}|{params:?}|{settle_opening:?}"
    );
    if text.contains("NaN") {
        return None;
    }
    Some(Sha256::digest(text.as_bytes()).into())
}

/// A clone of the engine built under `key`, if the cache holds one.
pub(crate) fn get(key: &Key) -> Option<Engine> {
    cache().get(key)
}

/// Keep a clone of `engine`, just built under `key`.
pub(crate) fn put(key: Key, engine: &Engine) {
    cache().put(key, engine);
}

/// Hold at most `capacity` engines, dropping the least recently used beyond
/// it. 0 turns the cache off and empties it. The counts are kept.
pub(crate) fn set_capacity(capacity: usize) {
    cache().set_capacity(capacity);
}

/// What the cache holds and has done.
pub(crate) fn info() -> OpeningCacheInfo {
    cache().info()
}

#[cfg(test)]
thread_local! {
    /// Builds this thread was served from the cache. Tests read it, because
    /// the process-wide counts move under every test running beside them.
    static HITS: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
}

/// Cache hits on this thread so far. Test-only.
#[cfg(test)]
pub(crate) fn hits_here() -> u64 {
    HITS.with(|n| n.get())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{create_initial_central_bank_state, create_initial_economy_state};
    use crate::engine::{SessionBuffer, SessionRequest};

    fn roster(names: usize, seed: u64) -> Vec<TickCompany> {
        crate::universe::random_universe(names, seed)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect()
    }

    fn sector_keys() -> Vec<String> {
        crate::sectors::keys().iter().map(|s| s.to_string()).collect()
    }

    /// pt-v21 with a 21-session prehistory, so a cold build is quick.
    fn short_prehistory() -> ModelParams {
        crate::params::PT_V21.with_override("market_prehistory_sessions", 21.0).unwrap()
    }

    fn build(seed: u64, companies: Vec<TickCompany>, params: ModelParams) -> Engine {
        Engine::with_params(
            seed, companies, create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(), params,
        )
    }

    fn key_of(seed: u64, companies: &[TickCompany], params: &ModelParams) -> Option<Key> {
        key(seed, companies, &create_initial_economy_state(&Default::default()),
            &create_initial_central_bank_state(0), &sector_keys(), params, true)
    }

    fn run_day(e: &mut Engine, day: i64, buffer: &mut SessionBuffer) {
        e.open_market();
        let bell = crate::market::GameTime::new(9, 30, 3);
        e.run_session(&SessionRequest::new(bell, 390), buffer);
        e.close_day(day);
    }

    /// A build served from the cache is the build itself: the same state,
    /// the same generators and draw counts, and the same market day after
    /// day. On the full pt-v21 prehistory as well as a short one.
    #[test]
    fn a_cached_build_is_the_cold_build_to_the_bit() {
        for (seed, params) in [(90_101, short_prehistory()), (90_102, crate::params::PT_V21)] {
            let companies = roster(4, seed);
            let mut cold = Engine::build_from_opening(
                seed, companies.clone(), create_initial_economy_state(&Default::default()),
                create_initial_central_bank_state(0), sector_keys(), params.clone(), true,
            );
            // The first build may itself be served if another test built
            // this engine on another thread; the second is served unless
            // the cache was trimmed in between, which a retry gets past.
            let mut served = None;
            for _ in 0..4 {
                let before = hits_here();
                let e = build(seed, companies.clone(), params.clone());
                if hits_here() > before {
                    served = Some(e);
                    break;
                }
            }
            let mut served = served.expect("no build was served from the cache");
            assert_eq!(served.state_hash(0, false), cold.state_hash(0, false));
            assert_eq!(served.rng_state(), cold.rng_state());
            assert_eq!(served.draws_consumed(), cold.draws_consumed());
            assert_eq!(served.opening_settled(), cold.opening_settled());
            assert_eq!(served.opening_carry().len(), cold.opening_carry().len());
            for (a, b) in served.opening_carry().iter().zip(cold.opening_carry()) {
                assert_eq!(a.to_bits(), b.to_bits());
            }
            let mut buffer = SessionBuffer::new();
            for day in 1..=5u32 {
                run_day(&mut served, i64::from(day), &mut buffer);
                run_day(&mut cold, i64::from(day), &mut buffer);
                assert_eq!(served.state_hash(day, false), cold.state_hash(day, false), "day {day}");
                assert_eq!(served.draws_consumed(), cold.draws_consumed(), "day {day}");
            }
        }
    }

    /// Every argument is in the key, to the bit: the seed, any field of any
    /// company, the economy, the central bank, the sector keys and their
    /// order, the sign of a zero in the model, and whether the opening is
    /// settled.
    #[test]
    fn the_key_reads_every_argument_to_the_bit() {
        let companies = roster(3, 5);
        let params = short_prehistory();
        let base = key_of(7, &companies, &params).unwrap();
        assert_eq!(key_of(7, &companies, &params), Some(base), "a key is a function");
        assert_ne!(key_of(8, &companies, &params), Some(base));

        let mut nudged = companies.clone();
        let p = nudged[2].stock.price;
        nudged[2].stock.price = f64::from_bits(p.to_bits() + 1);
        assert_ne!(key_of(7, &nudged, &params), Some(base), "one ulp of one price");
        let mut renamed = companies.clone();
        renamed[0].ticker.push('X');
        assert_ne!(key_of(7, &renamed, &params), Some(base));
        let mut swapped = companies.clone();
        swapped.swap(0, 1);
        assert_ne!(key_of(7, &swapped, &params), Some(base), "the roster's order");

        // A switch pt-v21 leaves at 0.0, which the digest leaves out at
        // either sign.
        let off = crate::params::DIGEST_SILENT_AT_ZERO
            .iter()
            .find(|name| params.get(name).map(f64::to_bits) == Some(0))
            .expect("pt-v21 leaves a silent switch off");
        let signed = params.with_override(off, -0.0).unwrap();
        assert_eq!(signed.get(off).map(f64::to_bits), Some((-0.0f64).to_bits()), "{off}");
        assert_eq!(signed.fingerprint(), params.fingerprint(), "the digest is silent on {off}");
        assert_ne!(key_of(7, &companies, &signed), Some(base), "the sign of a zero");

        let econ = create_initial_economy_state(&Default::default());
        let bank = create_initial_central_bank_state(0);
        let keys = sector_keys();
        let k = |econ: &EconomyState, bank: &CentralBankState, keys: &[String], settle: bool| {
            key(7, &companies, econ, bank, keys, &params, settle)
        };
        assert_eq!(k(&econ, &bank, &keys, true), Some(base));
        let mut e2 = econ.clone();
        e2.vix = f64::from_bits(e2.vix.to_bits() + 1);
        assert_ne!(k(&e2, &bank, &keys, true), Some(base));
        assert_ne!(k(&econ, &create_initial_central_bank_state(1), &keys, true), Some(base));
        let mut reordered = keys.clone();
        reordered.swap(0, 1);
        assert_ne!(k(&econ, &bank, &reordered, true), Some(base));
        assert_ne!(k(&econ, &bank, &keys, false), Some(base));
    }

    /// `Debug` prints any NaN as `NaN`, so a build holding one is never
    /// keyed; nor is a roster over the size the cache keeps.
    #[test]
    fn a_nan_or_a_large_roster_is_never_keyed() {
        let params = short_prehistory();
        let mut companies = roster(3, 5);
        companies[1].stock.short_interest = f64::NAN;
        assert_eq!(key_of(7, &companies, &params), None);
        let big = roster(NAME_BUDGET + 1, 5);
        assert_eq!(key_of(7, &big, &params), None);
        assert!(key_of(7, &big[..NAME_BUDGET], &params).is_some());
    }

    /// The cache's own bookkeeping, on a cache of its own so no other test
    /// moves it: the least recently used engine leaves first, a hit counts
    /// as a use, 0 turns it off and empties it, and a hit is a copy.
    #[test]
    fn the_least_recently_used_engine_leaves_first() {
        let p = crate::params::PT_V20;
        let engines: Vec<Engine> = (0..3u64)
            .map(|s| Engine::with_params_keeping_opening(
                s, roster(2, 1), create_initial_economy_state(&Default::default()),
                create_initial_central_bank_state(0), sector_keys(), p.clone()))
            .collect();
        let keys: Vec<Key> = (0..3u8).map(|i| [i; 32]).collect();
        let mut cache = Cache::new(2, NAME_BUDGET);
        assert!(cache.get(&keys[0]).is_none());
        cache.put(keys[0], &engines[0]);
        cache.put(keys[1], &engines[1]);
        let a = cache.get(&keys[0]).expect("kept");
        assert_eq!(a.state_hash(0, false), engines[0].state_hash(0, false));
        cache.put(keys[2], &engines[2]);
        assert!(cache.get(&keys[1]).is_none(), "1 was the least recently used");
        assert!(cache.get(&keys[0]).is_some());
        assert!(cache.get(&keys[2]).is_some());
        let info = cache.info();
        assert_eq!((info.capacity, info.entries, info.names), (2, 2, 4));
        assert_eq!((info.hits, info.misses), (3, 2));
        cache.put(keys[2], &engines[2]);
        assert_eq!(cache.info().entries, 2, "a key is kept once");
        cache.set_capacity(0);
        assert_eq!(cache.info().entries, 0);
        cache.put(keys[0], &engines[0]);
        assert!(cache.get(&keys[0]).is_none());
        assert_eq!(cache.info().misses, 2, "an off cache counts nothing");
    }

    /// The name budget binds before the count does once the rosters are
    /// large: two-name engines under a budget of five names leave two kept,
    /// and an engine over the budget on its own is not kept at all.
    #[test]
    fn the_name_budget_holds_across_the_kept_engines() {
        let p = crate::params::PT_V20;
        let build = |s: u64, names: usize| Engine::with_params_keeping_opening(
            s, roster(names, 1), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(), p.clone());
        let mut cache = Cache::new(16, 5);
        for i in 0..4u8 {
            cache.put([i; 32], &build(u64::from(i), 2));
        }
        let info = cache.info();
        assert_eq!((info.entries, info.names), (2, 4));
        assert!(cache.get(&[3; 32]).is_some() && cache.get(&[2; 32]).is_some());
        assert!(cache.get(&[1; 32]).is_none());
        cache.put([9; 32], &build(9, 6));
        assert!(cache.get(&[9; 32]).is_none(), "over the budget on its own");
        assert_eq!(cache.info().entries, 2);
    }
}
