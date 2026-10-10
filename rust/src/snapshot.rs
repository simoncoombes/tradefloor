//! An engine's whole state, saved and put back.
//!
//! [`Engine::snapshot`] captures everything that moves the market from here
//! on: the columns, every generator's position, the day's accumulators, the
//! variance and volume states, the fair-value levels, the crisis episode, the
//! economy and the central bank, the rate instruments and the agents' book.
//! [`Engine::restore`] puts an engine back to that state, and a restored
//! engine runs on bit for bit as the one it was taken from.
//!
//! This is the implementation behind the Python package's
//! `Engine.state_snapshot` and `Engine.restore_state`. The Python dict is
//! this snapshot field by field, under the same layout version
//! ([`STATE_SCHEMA`]), and the same checks refuse the same inputs.
//!
//! # The layout
//!
//! A snapshot is a tree of named fields ([`SnapshotValue`]), in the shape the
//! Python dict has: a top-level map whose keys are listed in
//! `docs/REPRODUCIBILITY.md`, with the economy, the central bank and the
//! columns as nested maps and the per-name arrays as little-endian f64
//! bytes. It is a tree rather than a struct so a caller can inspect, archive
//! and, where it knows better, edit it, and so that a field missing from an
//! old save or added by a newer build is refused by name rather than read as
//! a default.
//!
//! # The contract
//!
//! [`Engine::restore`] reads the snapshot against its `state_schema` and
//! requires every field that version carries. A missing field, an unknown
//! field, a value of the wrong type or length, or a non-finite scalar is
//! refused by name. Some fields are carried only under a model dial (for
//! example `fair_value_offset` when the model can move a fair-value level);
//! each is required exactly when this engine's model sets the dial, and the
//! refusal names the dial. A few are carried only while they hold something
//! (`book`, `macro_pins_today`, `current_day`, ...), and their absence is a
//! value.
//!
//! A snapshot with no `state_schema` was written by tradefloor 0.8.5 to
//! 0.8.8, which wrote every field of version 1 and no version, and it is
//! read as version 1. One that lacks a field was written before 0.8.5 or
//! edited, and is refused with the reason. A newer version is refused
//! rather than read in part.
//!
//! A refused restore changes nothing: the new state is built on a copy of
//! the engine and swapped in only once every field has been read.
//!
//! # Bytes
//!
//! [`EngineSnapshot::to_bytes`] and [`EngineSnapshot::from_bytes`] give the
//! snapshot one canonical binary form for a host that stores it, a browser
//! included. It is exact: every f64 travels as its bits, so NaN payloads and
//! the generator states (u64s carried as f64 bit patterns) survive. The same
//! snapshot always encodes to the same bytes. The form is:
//!
//! ```text
//! file  := "tfsnap" 0x00 0x01 value          (magic, encoding version 1)
//! value := 0x00                              none
//!        | 0x01 | 0x02                       false | true
//!        | 0x03 i64                          integer
//!        | 0x04 f64                          float, as its bits
//!        | 0x05 len utf8                     string
//!        | 0x06 len bytes                    bytes
//!        | 0x07 len value*                   list
//!        | 0x08 len value*                   tuple
//!        | 0x09 len (len utf8 value)*        map, keys in order
//!        | 0x0a u64                          integer above i64::MAX
//! ```
//!
//! Every number is little-endian and every `len` is a u64. The top value is a
//! map. The encoding version describes the container and moves only if the
//! byte form itself changes; the layout inside it is `state_schema`.
//!
//! # What a snapshot does not carry
//!
//! History and recording, which move no price: the Python binding's order
//! log and recorded tape, the draw log, the day marks, the diagnostics the
//! last VIX update left behind (`Engine::last_index_variance`), and the
//! count of draws this engine object has taken (`Engine::draws_consumed`,
//! `Engine::draws_by_stream`), which a restored engine counts from its own
//! construction. Every generator's position is carried, with its draw
//! counts (`Engine::stream_positions`). The three tape buffers in
//! [`DayLoop`] are the one exception, carried so a resumed tape books a
//! pending jump on the right row.

use std::fmt;

use crate::economy::{CyclePhase, ForwardGuidance};
use crate::engine::{default_day, Engine, EngineRngState, GdpPublication, PriceField};
use crate::market::NewsEvent;

/// The layout version [`Engine::snapshot`] writes and [`Engine::restore`]
/// reads: the Python package's `Engine.STATE_SCHEMA`.
///
/// Version 1 is the layout tradefloor 0.8.5 wrote, plus
/// `economy.qe_assets_ratio` on a model with `qe_pe_stock_gain` set. A new
/// field, a field gone, or a field whose meaning changes is a new version,
/// and the restore names what an older one lacks rather than filling it in.
pub const STATE_SCHEMA: i64 = 1;

/// The byte form's magic and encoding version.
const MAGIC: &[u8; 8] = b"tfsnap\x00\x01";

/// How deep a decoded tree may nest. The deepest a snapshot goes is four
/// (the book's fills), so this only stops a hostile input exhausting the
/// stack.
const MAX_DEPTH: usize = 32;

// ---------------------------------------------------------------------------
// The tree
// ---------------------------------------------------------------------------

/// One value in a snapshot.
///
/// The types are the ones the Python dict holds, so the two convert one to
/// one: `None`, `bool`, `int`, `float`, `str`, `bytes`, `list`, `tuple` and
/// `dict`. Per-name arrays are [`SnapshotValue::Bytes`] of little-endian
/// f64s.
///
/// Two values are equal when they are the same bits: a NaN float equals a
/// NaN with the same payload, which is what a saved generator state needs.
#[derive(Clone, Debug)]
#[non_exhaustive]
pub enum SnapshotValue {
    None,
    Bool(bool),
    Int(i64),
    /// An integer above `i64::MAX`, such as a 64-bit key. Python reads both
    /// integer variants as `int`; [`SnapshotValue::from_u64`] picks the one
    /// a value needs.
    UInt(u64),
    Float(f64),
    Str(String),
    Bytes(Vec<u8>),
    List(Vec<SnapshotValue>),
    Tuple(Vec<SnapshotValue>),
    Map(SnapshotMap),
}

impl PartialEq for SnapshotValue {
    fn eq(&self, other: &Self) -> bool {
        use SnapshotValue as V;
        match (self, other) {
            (V::None, V::None) => true,
            (V::Bool(a), V::Bool(b)) => a == b,
            (V::Int(a), V::Int(b)) => a == b,
            (V::UInt(a), V::UInt(b)) => a == b,
            (V::Float(a), V::Float(b)) => a.to_bits() == b.to_bits(),
            (V::Str(a), V::Str(b)) => a == b,
            (V::Bytes(a), V::Bytes(b)) => a == b,
            (V::List(a), V::List(b)) | (V::Tuple(a), V::Tuple(b)) => a == b,
            (V::Map(a), V::Map(b)) => a == b,
            _ => false,
        }
    }
}

impl SnapshotValue {
    /// The Python type this value converts to, which is also how a refusal
    /// names a value of the wrong type.
    pub fn type_name(&self) -> &'static str {
        match self {
            SnapshotValue::None => "NoneType",
            SnapshotValue::Bool(_) => "bool",
            SnapshotValue::Int(_) | SnapshotValue::UInt(_) => "int",
            SnapshotValue::Float(_) => "float",
            SnapshotValue::Str(_) => "str",
            SnapshotValue::Bytes(_) => "bytes",
            SnapshotValue::List(_) => "list",
            SnapshotValue::Tuple(_) => "tuple",
            SnapshotValue::Map(_) => "dict",
        }
    }

    /// A u64 as [`SnapshotValue::Int`] where it fits and
    /// [`SnapshotValue::UInt`] above `i64::MAX`, so a value reads back as
    /// the variant it was written as through Python.
    pub fn from_u64(value: u64) -> Self {
        match i64::try_from(value) {
            Ok(i) => SnapshotValue::Int(i),
            Err(_) => SnapshotValue::UInt(value),
        }
    }

    /// Little-endian f64 bytes, the form every per-name array takes.
    pub fn from_f64s(values: &[f64]) -> Self {
        SnapshotValue::Bytes(values.iter().flat_map(|v| v.to_le_bytes()).collect())
    }

    /// The numbers in a [`SnapshotValue::Bytes`] of little-endian f64s, or
    /// `None` when this is not bytes or not a whole number of f64s.
    pub fn as_f64s(&self) -> Option<Vec<f64>> {
        match self {
            SnapshotValue::Bytes(b) if b.len() % 8 == 0 => Some(f64s_of(b)),
            _ => None,
        }
    }

    /// A number: a float, or an integer or bool read as one, as Python
    /// reads them.
    fn number(&self) -> Option<f64> {
        match self {
            SnapshotValue::Float(f) => Some(*f),
            SnapshotValue::Int(i) => Some(*i as f64),
            SnapshotValue::UInt(u) => Some(*u as f64),
            SnapshotValue::Bool(b) => Some(f64::from(u8::from(*b))),
            _ => None,
        }
    }

    /// An integer, or a bool read as one, as Python reads them.
    fn integer(&self) -> Option<i64> {
        match self {
            SnapshotValue::Int(i) => Some(*i),
            SnapshotValue::Bool(b) => Some(i64::from(*b)),
            _ => None,
        }
    }

    /// A whole number from 0 to `u64::MAX`, either integer variant.
    fn unsigned(&self) -> Option<u64> {
        match self {
            SnapshotValue::UInt(u) => Some(*u),
            other => other.integer().and_then(|i| u64::try_from(i).ok()),
        }
    }

    /// The items of a list or a tuple.
    fn items(&self) -> Option<&[SnapshotValue]> {
        match self {
            SnapshotValue::List(v) | SnapshotValue::Tuple(v) => Some(v),
            _ => None,
        }
    }
}

fn f64s_of(bytes: &[u8]) -> Vec<f64> {
    bytes
        .chunks_exact(8)
        .map(|c| f64::from_le_bytes([c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]]))
        .collect()
}

/// A map from field names to values that keeps the order fields were
/// written in, as a Python dict does.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct SnapshotMap {
    entries: Vec<(String, SnapshotValue)>,
}

impl SnapshotMap {
    /// An empty map.
    pub fn new() -> Self {
        SnapshotMap::default()
    }

    /// The value under `key`.
    pub fn get(&self, key: &str) -> Option<&SnapshotValue> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// The value under `key`, to change in place.
    pub fn get_mut(&mut self, key: &str) -> Option<&mut SnapshotValue> {
        self.entries.iter_mut().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// Whether `key` is present.
    pub fn contains_key(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// Set `key`, in place when it is present and at the end when it is
    /// not, returning what it held.
    pub fn insert(&mut self, key: impl Into<String>, value: SnapshotValue) -> Option<SnapshotValue> {
        let key = key.into();
        match self.get_mut(&key) {
            Some(slot) => Some(std::mem::replace(slot, value)),
            None => {
                self.entries.push((key, value));
                None
            }
        }
    }

    /// Take `key` out, keeping the order of the rest.
    pub fn remove(&mut self, key: &str) -> Option<SnapshotValue> {
        let at = self.entries.iter().position(|(k, _)| k == key)?;
        Some(self.entries.remove(at).1)
    }

    /// The keys, in order.
    pub fn keys(&self) -> impl Iterator<Item = &str> {
        self.entries.iter().map(|(k, _)| k.as_str())
    }

    /// The fields, in order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &SnapshotValue)> {
        self.entries.iter().map(|(k, v)| (k.as_str(), v))
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn put(&mut self, key: &str, value: SnapshotValue) {
        self.entries.push((key.to_string(), value));
    }
}

// ---------------------------------------------------------------------------
// The snapshot, the day loop and the error
// ---------------------------------------------------------------------------

/// An engine's whole state, from [`Engine::snapshot`] or
/// [`EngineSnapshot::from_bytes`], for [`Engine::restore`].
///
/// A snapshot holds data, not a promise: one read from bytes, or edited, is
/// checked only when it is restored. The accessors read single fields and
/// return `None` where the field is absent or not of its type.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub struct EngineSnapshot {
    fields: SnapshotMap,
}

impl EngineSnapshot {
    /// A snapshot over a field map, as the Python binding builds one from a
    /// dict. Nothing is checked until [`Engine::restore`].
    pub fn from_map(fields: SnapshotMap) -> Self {
        EngineSnapshot { fields }
    }

    /// The fields, in the order the writer put them.
    pub fn fields(&self) -> &SnapshotMap {
        &self.fields
    }

    /// The fields, to change. A caller who knows what a field missing from an
    /// old save held can write it in here and restore the result.
    pub fn fields_mut(&mut self) -> &mut SnapshotMap {
        &mut self.fields
    }

    pub fn into_fields(self) -> SnapshotMap {
        self.fields
    }

    /// The top-level field `key`.
    pub fn get(&self, key: &str) -> Option<&SnapshotValue> {
        self.fields.get(key)
    }

    /// The layout version, or `None` for a snapshot written before it was
    /// recorded.
    pub fn schema(&self) -> Option<i64> {
        match self.get("state_schema") {
            Some(SnapshotValue::Int(v)) => Some(*v),
            _ => None,
        }
    }

    /// The model the snapshot was taken under (`ModelParams::fingerprint`).
    pub fn model_fingerprint(&self) -> Option<&str> {
        match self.get("model_fingerprint") {
            Some(SnapshotValue::Str(s)) => Some(s),
            _ => None,
        }
    }

    /// The roster, in column order.
    pub fn tickers(&self) -> Option<Vec<&str>> {
        self.get("tickers")?
            .items()?
            .iter()
            .map(|v| match v {
                SnapshotValue::Str(s) => Some(s.as_str()),
                _ => None,
            })
            .collect()
    }

    /// One column, in roster order.
    pub fn column(&self, field: PriceField) -> Option<Vec<f64>> {
        let SnapshotValue::Map(columns) = self.get("columns")? else {
            return None;
        };
        columns.get(column_name(field))?.as_f64s()
    }

    /// The days closed when it was taken, from [`DayLoop::day_count`].
    pub fn day_count(&self) -> Option<u32> {
        u32::try_from(self.get("day_count")?.integer()?).ok()
    }

    /// Whether a day was open, from [`DayLoop::market_open`].
    pub fn market_open(&self) -> Option<bool> {
        match self.get("market_open") {
            Some(SnapshotValue::Bool(b)) => Some(*b),
            _ => None,
        }
    }

    /// The ticks the open day had run, which is the tick the book stamps a
    /// fill with.
    pub fn session_tick(&self) -> Option<u32> {
        u32::try_from(self.get("session_tick")?.integer()?).ok()
    }

    /// The canonical byte form. See the [module docs](self).
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = MAGIC.to_vec();
        encode_map(&self.fields, &mut out);
        out
    }

    /// Read the byte form back. Refuses bytes that are not the form, are cut
    /// short or carry anything after the snapshot; the fields themselves are
    /// checked by [`Engine::restore`].
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        let Some(rest) = bytes.strip_prefix(&MAGIC[..6]) else {
            return Err(SnapshotError::encoding(
                "these bytes are not a tradefloor snapshot: they do not start with \"tfsnap\"",
            ));
        };
        match rest.get(..2) {
            Some([0, 1]) => {}
            Some([0, v]) => {
                return Err(SnapshotError::encoding(format!(
                    "this snapshot's byte form is version {v}, and this build reads version 1. \
                     Upgrade tradefloor rather than reading it in part."
                )))
            }
            _ => {
                return Err(SnapshotError::encoding(
                    "these bytes are not a tradefloor snapshot: the header is cut short or damaged",
                ))
            }
        }
        let mut reader = Decoder { bytes: &rest[2..], at: 0 };
        let value = reader.value(0)?;
        if reader.at != reader.bytes.len() {
            return Err(SnapshotError::encoding(format!(
                "this snapshot carries {} bytes after its end",
                reader.bytes.len() - reader.at
            )));
        }
        match value {
            SnapshotValue::Map(fields) => Ok(EngineSnapshot { fields }),
            other => Err(SnapshotError::encoding(format!(
                "a snapshot is a map of fields, and these bytes hold a {}",
                other.type_name()
            ))),
        }
    }
}

/// What the code driving the day loop keeps beside the engine, which a
/// snapshot carries with it.
///
/// The core engine does not count days or track whether one is open: its
/// caller does, and passes the day to [`Engine::close_day`]. The Python
/// binding keeps these six, and so does a host that numbers its own days.
///
/// - `day_count`: the days closed, the macro chain's clock.
/// - `market_open`: whether a day is open, which decides whether the next
///   session re-opens the day.
/// - `pending_jump`, `pending_overnight`, `pending_fair_value`,
///   `pending_dividend`: the moves a close or an open applied to each name,
///   waiting for the tape row where their effect is observed. Recording
///   state; they move no price.
///
/// [`Engine::snapshot`] writes the default (no day closed, none open, nothing
/// pending); [`Engine::snapshot_with`] writes the caller's.
/// [`Engine::restore`] hands back what the snapshot carried. Either way the
/// engine's own day label and valuation clock are carried exactly, so a
/// caller that keeps no counter loses nothing.
#[derive(Clone, Debug, Default, PartialEq)]
#[non_exhaustive]
pub struct DayLoop {
    pub day_count: u32,
    pub market_open: bool,
    pub pending_jump: Vec<f64>,
    pub pending_overnight: Vec<f64>,
    pub pending_fair_value: Vec<f64>,
    /// The ex-date's move in `s` at the last open, waiting for the day's
    /// first tape row; empty unless a name went ex (`dividend_payout_share`).
    pub pending_dividend: Vec<f64>,
}

impl DayLoop {
    /// `day_count` days closed, a day open or not, nothing pending.
    pub fn new(day_count: u32, market_open: bool) -> Self {
        DayLoop { day_count, market_open, ..Default::default() }
    }
}

/// What kind of refusal a [`SnapshotError`] is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum SnapshotErrorKind {
    /// The bytes are not the snapshot byte form.
    Encoding,
    /// A `state_schema` this build does not read.
    Version,
    /// A field missing or unknown.
    Fields,
    /// A field of the wrong type, length or range.
    Value,
    /// A snapshot of another roster.
    Roster,
    /// A snapshot taken under another model.
    Model,
}

/// Why a snapshot was refused. The message names the field.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub struct SnapshotError {
    kind: SnapshotErrorKind,
    message: String,
}

impl SnapshotError {
    fn new(kind: SnapshotErrorKind, message: impl Into<String>) -> Self {
        SnapshotError { kind, message: message.into() }
    }

    fn encoding(message: impl Into<String>) -> Self {
        Self::new(SnapshotErrorKind::Encoding, message)
    }

    fn value(message: impl Into<String>) -> Self {
        Self::new(SnapshotErrorKind::Value, message)
    }

    pub fn kind(&self) -> SnapshotErrorKind {
        self.kind
    }

    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for SnapshotError {}

type Result<T> = std::result::Result<T, SnapshotError>;

// ---------------------------------------------------------------------------
// Field lists
// ---------------------------------------------------------------------------

/// The column names, in the order the snapshot and the state hash walk them
/// ([`crate::engine::STATE_HASH_COLUMNS`]). The Python binding's
/// `COLUMN_FIELDS` is this list.
pub(crate) const COLUMN_FIELDS: [&str; 18] = [
    "price",
    "previous_close",
    "previous_tick_price",
    "open",
    "high",
    "low",
    "volume",
    "avg_volume",
    "market_cap",
    "mispricing_s",
    "mispricing_s_prev_close",
    "mispricing_momentum",
    "last_daily_return",
    "maker_inventory",
    "garch_variance",
    "beta",
    "short_interest",
    "float_shares",
];

/// The column a name in [`COLUMN_FIELDS`] reads.
pub(crate) fn parse_column(name: &str) -> Option<PriceField> {
    Some(match name {
        "price" => PriceField::Price,
        "previous_close" => PriceField::PreviousClose,
        "open" => PriceField::Open,
        "high" => PriceField::High,
        "low" => PriceField::Low,
        "volume" => PriceField::Volume,
        "market_cap" => PriceField::MarketCap,
        "mispricing_s" => PriceField::MispricingS,
        "maker_inventory" => PriceField::MakerInventory,
        "garch_variance" => PriceField::GarchVariance,
        "previous_tick_price" => PriceField::PreviousTickPrice,
        "mispricing_s_prev_close" => PriceField::MispricingSPrevClose,
        "mispricing_momentum" => PriceField::MispricingMomentum,
        "last_daily_return" => PriceField::LastDailyReturn,
        "avg_volume" => PriceField::AvgVolume,
        "beta" => PriceField::Beta,
        "short_interest" => PriceField::ShortInterest,
        "float_shares" => PriceField::FloatShares,
        _ => return None,
    })
}

fn column_name(field: PriceField) -> &'static str {
    COLUMN_FIELDS
        .iter()
        .find(|name| parse_column(name) == Some(field))
        .expect("every PriceField has a column name")
}

/// A rate instrument's state in a snapshot, in the order the state hash
/// walks it (`Engine::state_hash_with_pending`, `manifest.state_hash`).
pub(crate) const RATE_STATE_FIELDS: [&str; 14] = [
    "level", "marked_yield", "price", "previous_close", "open", "high", "low",
    "volume", "avg_volume", "units_outstanding", "maker_inventory", "day_carry",
    "day_duration", "day_convexity",
];

fn rate_state(inst: &crate::rates::RateInstrument) -> [f64; 14] {
    [
        inst.level, inst.marked_yield, inst.price, inst.previous_close, inst.open,
        inst.high, inst.low, inst.volume, inst.avg_volume, inst.units_outstanding,
        inst.maker_inventory, inst.day_carry, inst.day_duration, inst.day_convexity,
    ]
}

fn set_rate_state(inst: &mut crate::rates::RateInstrument, row: &[f64; 14]) {
    let [level, marked_yield, price, previous_close, open, high, low, volume, avg_volume,
         units_outstanding, maker_inventory, day_carry, day_duration, day_convexity] = *row;
    inst.level = level;
    inst.marked_yield = marked_yield;
    inst.price = price;
    inst.previous_close = previous_close;
    inst.open = open;
    inst.high = high;
    inst.low = low;
    inst.volume = volume;
    inst.avg_volume = avg_volume;
    inst.units_outstanding = units_outstanding;
    inst.maker_inventory = maker_inventory;
    inst.day_carry = day_carry;
    inst.day_duration = day_duration;
    inst.day_convexity = day_convexity;
}

/// Calls `$m!` with every scalar field of the economy a snapshot carries, in
/// snapshot order, so the writer, the reader and the key check share one
/// list.
macro_rules! economy_scalars {
    ($m:ident) => {
        $m!(
            federal_funds_rate, prime_rate, corporate_bond_yield,
            treasury_yield_10y, treasury_yield_2y, mortgage_rate_30y,
            cpi, inflation_rate, core_inflation,
            gdp_growth, gdp,
            unemployment_rate, jobs_created, labor_force_participation,
            usd_index, oil_price, gold_price, copper_price,
            housing_index, home_starts_monthly, housing_transaction_volume,
            long_term_unemployment_rate, structural_unemployment,
            consumer_confidence, business_confidence, fear_greed_index, vix,
            tariff_rate, trade_balance,
            oil_inventory_level, oil_last_opec_day,
            wage_growth,
            previous_day_market_return, rolling_market_return_30d,
            market_pe, qe_pe_boost,
            fiscal_stimulus, government_debt_to_gdp,
            months_in_current_phase, phase_gdp_target, recession_probability,
        )
    };
}

macro_rules! field_names {
    ($($field:ident),* $(,)?) => { &[$(stringify!($field)),*] };
}

/// The economy's scalar keys, from [`economy_scalars`].
const ECONOMY_SCALARS: &[&str] = economy_scalars!(field_names);

/// The top-level keys every version-1 snapshot carries. `manifest.py`'s
/// `_SNAPSHOT_KEYS` is the same list less `session_tick`, which the state
/// hash leaves out, and a test holds the two together.
const SNAPSHOT_KEYS: &[&str] = &[
    "columns", "rng", "tickers", "model_fingerprint",
    "attribution", "tick_components", "tick_fundamental", "tick_anchor",
    "noise_parts", "noise_own_scale2", "jump_move",
    "market_open", "market_variance", "forced_flow_spent",
    "market_vol_log_level", "vix_log_level",
    "crisis_in_episode", "crisis_sessions_under",
    "crisis_epicentre", "crisis_epicentre_pin",
    "nominal_output_base", "volume_state",
    "universe_stress", "volume_idio", "sector_variance", "jump_excitation",
    "sector_day_factor", "sector_target_day",
    "session_news", "economy", "central_bank", "day_count",
    "draw_counts", "draw_overlay", "pending_jump", "pending_overnight",
    "session_tick",
];

/// Top-level keys carried only while they hold something. Their absence is
/// a value: a pristine book, no close forced tonight, no pins today, no
/// fair-value shift waiting, the day the counter gives, the fundamentals
/// and the share counts the engine was built with.
const SNAPSHOT_OPTIONAL_KEYS: &[&str] = &[
    "state_schema", "book", "vix_sets_variance_pending", "macro_pins_today",
    "pending_fair_value", "current_day", "elapsed_days", "fundamentals",
    "shares_outstanding",
    // The spread a `pin_macro(corporate_spread=...)` holds through tonight's
    // close, carried exactly while `macro_pins_today` marks it.
    "pinned_corporate_spread",
];

const CENTRAL_BANK_KEYS: &[&str] = &[
    "last_meeting_date", "next_meeting_date", "target_inflation",
    "target_unemployment", "qe_active", "qe_monthly_purchases",
    "hawkish_dovish_score", "forward_guidance",
];
const NEWS_KEYS: &[&str] = &["ticker", "sector", "price_impact"];
const GDP_PUBLICATION_KEYS: &[&str] =
    &["published", "quarter", "count", "sum", "pending_days", "pending_values"];
const CYCLE_PUBLICATION_KEYS: &[&str] =
    &["key", "published", "last_true", "closes", "turns", "pending_closes", "pending_phases"];
const FUNDAMENTALS_KEYS: &[&str] = &["eps", "book_value_per_share", "revenue_growth"];
const RATES_KEYS: &[&str] = &["instruments", "ig_spread", "last_corporate", "closed_since_open"];

/// Added to a key refusal when the snapshot carries no `state_schema`.
const LEGACY_NOTE: &str = " This snapshot carries no state_schema, so a release \
    before the version was recorded wrote it. tradefloor 0.8.5 to 0.8.8 wrote \
    every field version 1 needs, apart from economy.qe_assets_ratio under a \
    model with qe_pe_stock_gain set. A snapshot missing anything else was \
    written before 0.8.5, when the session tick was not carried and the day \
    label and the valuation clock were one field that was not carried either, \
    or it was edited. A restore cannot know what the missing fields held. \
    Resume it under the release that wrote it, or replay a Checkpoint, which \
    carries the order log. A caller who knows what a missing field held can \
    write it into the dict and restore that.";

// ---------------------------------------------------------------------------
// Key checks
// ---------------------------------------------------------------------------

/// A key a snapshot carries only under a model dial: whether this engine's
/// model calls for it, and the sentence that says why.
///
/// A key is `held` when the model's dial allows it but the engine writes it
/// only while it holds something (a day scale drawn at an open and cleared
/// at the close, say): then it may be carried where the dial is set, and
/// never where it is not.
struct Gated {
    key: &'static str,
    wanted: bool,
    held: bool,
    why: String,
}

impl Gated {
    /// A key carried exactly when `dial` is not 0.
    fn dial(key: &'static str, dial: &str, value: f64) -> Self {
        Gated {
            key,
            wanted: value != 0.0,
            held: false,
            why: format!("{dial} is not 0, and this engine's {dial} is {value}"),
        }
    }

    /// A key carried exactly when `wanted`, for the reason `why`.
    fn when(key: &'static str, wanted: bool, why: String) -> Self {
        Gated { key, wanted, held: false, why }
    }

    /// A key that may be carried only when `dial` is not 0, and is carried
    /// there only while it holds something.
    fn held(key: &'static str, dial: &str, value: f64) -> Self {
        Gated {
            key,
            wanted: value != 0.0,
            held: true,
            why: format!("{dial} is not 0 and it holds something, and this engine's {dial} is {value}"),
        }
    }
}

fn py_names(names: &[String]) -> String {
    let quoted: Vec<String> = names.iter().map(|n| format!("'{n}'")).collect();
    format!("[{}]", quoted.join(", "))
}

/// Compare a map's keys with the ones it must carry: `None` when they
/// agree, and otherwise the refusal, in the words `manifest.state_hash`
/// uses for the same mismatch.
fn key_mismatch(
    what: &str,
    d: &SnapshotMap,
    required: &[&str],
    gated: &[Gated],
    optional: &[&str],
) -> Option<String> {
    let carried: std::collections::BTreeSet<&str> = d.keys().collect();
    let mut missing: Vec<String> = required
        .iter()
        .chain(gated.iter().filter(|g| g.wanted && !g.held).map(|g| &g.key))
        .filter(|k| !carried.contains(**k))
        .map(|k| k.to_string())
        .collect();
    missing.sort();
    let unexpected: Vec<String> = carried
        .iter()
        .filter(|k| {
            !required.contains(k)
                && !optional.contains(k)
                && !gated.iter().any(|g| g.wanted && g.key == **k)
        })
        .map(|k| k.to_string())
        .collect();
    if missing.is_empty() && unexpected.is_empty() {
        return None;
    }
    let mut out = format!(
        "{what} does not match the fields this engine's state carries: \
         missing {}, unexpected {}.",
        py_names(&missing),
        py_names(&unexpected)
    );
    for g in gated {
        let has = carried.contains(g.key);
        if g.held && has && !g.wanted {
            out.push_str(&format!(" {} is carried only when {}.", g.key, g.why));
        } else if !g.held && g.wanted != has {
            out.push_str(&format!(" {} is carried exactly when {}.", g.key, g.why));
        }
    }
    Some(out)
}

fn check_keys(what: &str, d: &SnapshotMap, required: &[&str]) -> Result<()> {
    match key_mismatch(what, d, required, &[], &[]) {
        None => Ok(()),
        Some(message) => Err(SnapshotError::new(SnapshotErrorKind::Fields, message)),
    }
}

/// Whether the snapshot names its layout version, refusing one this build
/// does not read.
fn snapshot_version(snapshot: &SnapshotMap) -> Result<bool> {
    let Some(v) = snapshot.get("state_schema") else {
        return Ok(false);
    };
    let SnapshotValue::Int(version) = v else {
        return Err(SnapshotError::new(
            SnapshotErrorKind::Version,
            format!("snapshot field state_schema must be an integer, got {}", v.type_name()),
        ));
    };
    if *version > STATE_SCHEMA {
        return Err(SnapshotError::new(
            SnapshotErrorKind::Version,
            format!(
                "this snapshot's state_schema is {version}, newer than this build reads \
                 ({STATE_SCHEMA}). Upgrade tradefloor rather than restoring it in part."
            ),
        ));
    }
    if *version < 1 {
        return Err(SnapshotError::new(
            SnapshotErrorKind::Version,
            format!("this snapshot's state_schema is {version}, and versions run from 1."),
        ));
    }
    Ok(true)
}

// ---------------------------------------------------------------------------
// Field readers
// ---------------------------------------------------------------------------

/// A field the key check has already found present.
fn field<'a>(d: &'a SnapshotMap, at: &str, key: &str) -> Result<&'a SnapshotValue> {
    d.get(key)
        .ok_or_else(|| SnapshotError::new(SnapshotErrorKind::Fields, format!("snapshot has no {at}{key}")))
}

fn wrong(at: &str, key: &str, kind: &str, got: &SnapshotValue) -> SnapshotError {
    SnapshotError::value(format!(
        "snapshot field {at}{key} must be {kind}, got {}",
        got.type_name()
    ))
}

fn read_number(d: &SnapshotMap, at: &str, key: &str) -> Result<f64> {
    let v = field(d, at, key)?;
    v.number().ok_or_else(|| wrong(at, key, "a number", v))
}

/// A scalar that must be a finite number.
fn read_finite(d: &SnapshotMap, at: &str, key: &str) -> Result<f64> {
    let value = read_number(d, at, key)?;
    if !value.is_finite() {
        return Err(SnapshotError::value(format!(
            "snapshot field {at}{key} is {value}, and it must be finite"
        )));
    }
    Ok(value)
}

/// An integer field, refusing one out of `T`'s range.
fn read_int<T: TryFrom<i64>>(d: &SnapshotMap, at: &str, key: &str, kind: &str) -> Result<T> {
    let v = field(d, at, key)?;
    if let SnapshotValue::UInt(u) = v {
        return Err(SnapshotError::value(format!(
            "snapshot field {at}{key} must be {kind}, got {u}, which is out of range")));
    }
    let i = v.integer().ok_or_else(|| wrong(at, key, kind, v))?;
    T::try_from(i).map_err(|_| {
        SnapshotError::value(format!("snapshot field {at}{key} must be {kind}, got {i}"))
    })
}

/// A whole number from 0 to `u64::MAX`, such as a 64-bit key.
fn read_u64(d: &SnapshotMap, at: &str, key: &str) -> Result<u64> {
    let v = field(d, at, key)?;
    v.unsigned().ok_or_else(|| wrong(at, key, "a whole number from 0 to 2**64 - 1", v))
}

fn read_bool(d: &SnapshotMap, at: &str, key: &str) -> Result<bool> {
    match field(d, at, key)? {
        SnapshotValue::Bool(b) => Ok(*b),
        v => Err(wrong(at, key, "a bool", v)),
    }
}

fn read_str<'a>(d: &'a SnapshotMap, at: &str, key: &str) -> Result<&'a str> {
    match field(d, at, key)? {
        SnapshotValue::Str(s) => Ok(s),
        v => Err(wrong(at, key, "a string", v)),
    }
}

fn read_opt_str(d: &SnapshotMap, at: &str, key: &str) -> Result<Option<String>> {
    match field(d, at, key)? {
        SnapshotValue::None => Ok(None),
        SnapshotValue::Str(s) => Ok(Some(s.clone())),
        v => Err(wrong(at, key, "a string or None", v)),
    }
}

fn read_opt_number(d: &SnapshotMap, at: &str, key: &str) -> Result<Option<f64>> {
    match field(d, at, key)? {
        SnapshotValue::None => Ok(None),
        v => v.number().map(Some).ok_or_else(|| wrong(at, key, "a number or None", v)),
    }
}

fn read_items<'a>(d: &'a SnapshotMap, at: &str, key: &str, kind: &str) -> Result<&'a [SnapshotValue]> {
    let v = field(d, at, key)?;
    v.items().ok_or_else(|| wrong(at, key, kind, v))
}

/// A refusal for a list holding an item of the wrong type.
fn wrong_item(at: &str, key: &str, kind: &str, item: &SnapshotValue) -> SnapshotError {
    SnapshotError::value(format!(
        "snapshot field {at}{key} must be {kind}, and it holds a {}",
        item.type_name()
    ))
}

fn read_numbers(d: &SnapshotMap, at: &str, key: &str) -> Result<Vec<f64>> {
    let kind = "a list of numbers";
    let v = field(d, at, key)?;
    let items = v.items().ok_or_else(|| wrong(at, key, kind, v))?;
    items.iter().map(|x| x.number().ok_or_else(|| wrong_item(at, key, kind, x))).collect()
}

fn read_map<'a>(d: &'a SnapshotMap, at: &str, key: &str) -> Result<&'a SnapshotMap> {
    match field(d, at, key)? {
        SnapshotValue::Map(m) => Ok(m),
        _ => Err(SnapshotError::value(format!("snapshot field {at}{key} must be a dict"))),
    }
}

/// A buffer of little-endian f64s, refusing one that is not bytes or not a
/// whole number of values.
fn read_buffer(d: &SnapshotMap, at: &str, key: &str) -> Result<Vec<f64>> {
    let SnapshotValue::Bytes(bytes) = field(d, at, key)? else {
        return Err(SnapshotError::value(format!(
            "snapshot field {at}{key} must be bytes of little-endian f64s"
        )));
    };
    if bytes.len() % 8 != 0 {
        return Err(SnapshotError::value(format!(
            "snapshot field {at}{key} carries {} bytes, which is not a whole \
             number of f64s.",
            bytes.len()
        )));
    }
    Ok(f64s_of(bytes))
}

fn check_len(key: &str, got: usize, want: usize) -> Result<()> {
    if got != want {
        return Err(SnapshotError::value(format!(
            "snapshot field {key} carries {got} values and this engine holds {want}."
        )));
    }
    Ok(())
}

/// One economy scalar, written and read at its field's type.
trait EconomyScalar: Sized {
    fn put(&self) -> SnapshotValue;
    fn read(d: &SnapshotMap, key: &str) -> Result<Self>;
}

impl EconomyScalar for f64 {
    fn put(&self) -> SnapshotValue {
        SnapshotValue::Float(*self)
    }
    fn read(d: &SnapshotMap, key: &str) -> Result<Self> {
        read_finite(d, "economy.", key)
    }
}

impl EconomyScalar for i64 {
    fn put(&self) -> SnapshotValue {
        SnapshotValue::Int(*self)
    }
    fn read(d: &SnapshotMap, key: &str) -> Result<Self> {
        read_int(d, "economy.", key, "an integer")
    }
}

impl EconomyScalar for Option<f64> {
    fn put(&self) -> SnapshotValue {
        self.map_or(SnapshotValue::None, SnapshotValue::Float)
    }
    fn read(d: &SnapshotMap, key: &str) -> Result<Self> {
        let value = read_opt_number(d, "economy.", key)?;
        if value.is_some_and(|v| !v.is_finite()) {
            return Err(SnapshotError::value(format!(
                "snapshot field economy.{key} is {value:?}, and it must be finite or None"
            )));
        }
        Ok(value)
    }
}

fn economy_value<T: EconomyScalar>(d: &SnapshotMap, key: &str) -> Result<T> {
    T::read(d, key)
}

// ---------------------------------------------------------------------------
// The book
// ---------------------------------------------------------------------------

fn side_name(side: crate::order_book::Side) -> &'static str {
    match side {
        crate::order_book::Side::Buy => "buy",
        crate::order_book::Side::Sell => "sell",
    }
}

fn parse_side(s: &str) -> Result<crate::order_book::Side> {
    match s {
        "buy" => Ok(crate::order_book::Side::Buy),
        "sell" => Ok(crate::order_book::Side::Sell),
        other => Err(SnapshotError::value(format!("unknown side {other:?}"))),
    }
}

/// The snapshot's `book` entry. `Engine::state_hash` hashes the same fields
/// in the same order.
fn book_value(book: &crate::agent_book::BookState) -> SnapshotValue {
    use SnapshotValue as V;
    let mut d = SnapshotMap::new();
    d.put("sequence", V::Int(book.sequence as i64));
    d.put("fill_sequence", V::Int(book.fill_sequence as i64));
    let flat: Vec<f64> = book.taken.iter().flat_map(|row| row.iter().copied()).collect();
    d.put("taken", V::from_f64s(&flat));
    let orders = book
        .orders
        .iter()
        .map(|o| {
            let mut x = SnapshotMap::new();
            x.put("order_id", V::Str(o.id.clone()));
            x.put("agent", V::Str(o.agent.clone()));
            x.put("ticker", V::Str(o.ticker.clone()));
            x.put("side", V::Str(side_name(o.side).into()));
            x.put("limit_price", V::Float(o.limit));
            x.put("quantity", V::Float(o.quantity));
            x.put("remaining", V::Float(o.remaining));
            x.put("sequence", V::Int(o.sequence as i64));
            x.put("mode", V::Str(o.mode.as_str().into()));
            V::Map(x)
        })
        .collect();
    d.put("orders", V::List(orders));
    let flow = book
        .flow
        .iter()
        .map(|(agent, ticker, bought, sold)| {
            V::Tuple(vec![
                V::Str(agent.clone()),
                V::Str(ticker.clone()),
                V::Float(*bought),
                V::Float(*sold),
            ])
        })
        .collect();
    d.put("flow", V::List(flow));
    let fills = book
        .fills
        .iter()
        .map(|f| {
            let mut x = SnapshotMap::new();
            x.put("agent", V::Str(f.agent.clone()));
            x.put("order_id", V::Str(f.order_id.clone()));
            x.put("ticker", V::Str(f.ticker.clone()));
            x.put("side", V::Str(side_name(f.side).into()));
            x.put("quantity", V::Float(f.quantity));
            x.put("price", V::Float(f.price));
            x.put("liquidity", V::Str(f.liquidity.as_str().into()));
            x.put("counterparty", V::Str(f.counterparty.clone()));
            x.put("reference", V::Float(f.reference));
            x.put("day", V::Int(f.day));
            x.put("tick", V::Int(i64::from(f.tick)));
            x.put("sequence", V::Int(f.sequence as i64));
            V::Map(x)
        })
        .collect();
    d.put("fills", V::List(fills));
    let impacts = book
        .impacts
        .iter()
        .map(|r| {
            let mut x = SnapshotMap::new();
            x.put("agent", V::Str(r.agent.clone()));
            x.put("ticker", V::Str(r.ticker.clone()));
            x.put("bought", V::Float(r.bought));
            x.put("sold", V::Float(r.sold));
            x.put("permanent", V::Float(r.permanent));
            if let Some(v) = r.transient {
                x.put("transient", V::Float(v));
            }
            x.put("day", V::Int(r.day));
            x.put("tick", V::Int(i64::from(r.tick)));
            V::Map(x)
        })
        .collect();
    d.put("impacts", V::List(impacts));
    // The metaorder memory, only while it holds something: an entry absent
    // is a book without one, as every snapshot before it was.
    if !book.memory.is_empty() {
        let flat: Vec<f64> = book.memory.iter().flat_map(|row| row.iter().copied()).collect();
        d.put("memory", V::from_f64s(&flat));
    }
    V::Map(d)
}

/// One entry of a list in the book, which must be a dict.
fn book_entry<'a>(v: &'a SnapshotValue, what: &str) -> Result<&'a SnapshotMap> {
    match v {
        SnapshotValue::Map(m) => Ok(m),
        other => Err(SnapshotError::value(format!(
            "{what} in the snapshot's book must be a dict, got {}",
            other.type_name()
        ))),
    }
}

/// A field of the book or of one of its entries: refused by name when
/// absent.
fn book_field<'a>(d: &'a SnapshotMap, what: &str, key: &str) -> Result<&'a SnapshotValue> {
    d.get(key).ok_or_else(|| {
        SnapshotError::new(SnapshotErrorKind::Fields, format!("{what} has no {key:?}"))
    })
}

/// Refuse a field of the book or of one of its entries that this build
/// does not write, by name, as every other level of a snapshot does: a
/// misspelt optional field (`memory`, `transient`) would otherwise restore
/// as absent.
fn book_unknown(d: &SnapshotMap, what: &str, known: &[&str]) -> Result<()> {
    let unknown: Vec<String> =
        d.keys().filter(|k| !known.contains(k)).map(str::to_string).collect();
    if unknown.is_empty() {
        return Ok(());
    }
    Err(SnapshotError::new(
        SnapshotErrorKind::Fields,
        format!("{what} carries fields this build does not write: {}", py_names(&unknown)),
    ))
}

fn book_from(d: &SnapshotMap) -> Result<crate::agent_book::BookState> {
    use crate::agent_book::*;
    // Every field present before any is read, as a key-by-key read refused
    // the first one missing.
    let top = "the snapshot's book";
    let at = "book.";
    book_unknown(d, top, &["sequence", "fill_sequence", "taken", "orders", "flow", "fills",
                           "impacts", "memory"])?;
    for key in ["sequence", "fill_sequence", "taken"] {
        book_field(d, top, key)?;
    }
    let mut state = BookState {
        sequence: read_int(d, at, "sequence", "a whole number")?,
        fill_sequence: read_int(d, at, "fill_sequence", "a whole number")?,
        ..Default::default()
    };
    let SnapshotValue::Bytes(raw) = book_field(d, top, "taken")? else {
        return Err(SnapshotError::value("snapshot field book.taken must be bytes of little-endian f64s"));
    };
    if raw.len() % (8 * TAKEN_WIDTH) != 0 {
        return Err(SnapshotError::value("the snapshot's book `taken` is not whole rows"));
    }
    for row in f64s_of(raw).chunks_exact(TAKEN_WIDTH) {
        let mut r = [0.0; TAKEN_WIDTH];
        r.copy_from_slice(row);
        state.taken.push(r);
    }
    book_field(d, top, "orders")?;
    for item in read_items(d, at, "orders", "a list of dicts")? {
        let what = "a snapshot order";
        let o = book_entry(item, "an order")?;
        const ORDER: [&str; 9] = ["side", "mode", "order_id", "agent", "ticker", "limit_price",
                                  "quantity", "remaining", "sequence"];
        for key in ORDER {
            book_field(o, what, key)?;
        }
        book_unknown(o, what, &ORDER)?;
        let at = "book.orders[].";
        let mode = read_str(o, at, "mode")?;
        state.orders.push(AgentOrder {
            id: read_str(o, at, "order_id")?.to_string(),
            agent: read_str(o, at, "agent")?.to_string(),
            ticker: read_str(o, at, "ticker")?.to_string(),
            side: parse_side(read_str(o, at, "side")?)?,
            limit: read_number(o, at, "limit_price")?,
            quantity: read_number(o, at, "quantity")?,
            remaining: read_number(o, at, "remaining")?,
            sequence: read_int(o, at, "sequence", "a whole number")?,
            mode: RestMode::parse(mode)
                .ok_or_else(|| SnapshotError::value(format!("unknown order mode {mode:?}")))?,
        });
    }
    book_field(d, top, "flow")?;
    for item in read_items(d, at, "flow", "a list of (agent, ticker, bought, sold)")? {
        let bad = || {
            SnapshotError::value(format!(
                "snapshot field book.flow must be a list of (agent, ticker, bought, sold), got {}",
                item.type_name()
            ))
        };
        let parts = item.items().ok_or_else(bad)?;
        let [SnapshotValue::Str(agent), SnapshotValue::Str(ticker), bought, sold] = parts else {
            return Err(bad());
        };
        state.flow.push((
            agent.clone(),
            ticker.clone(),
            bought.number().ok_or_else(bad)?,
            sold.number().ok_or_else(bad)?,
        ));
    }
    book_field(d, top, "fills")?;
    for item in read_items(d, at, "fills", "a list of dicts")? {
        let what = "a snapshot fill";
        let f = book_entry(item, "a fill")?;
        const FILL: [&str; 12] = ["side", "liquidity", "agent", "order_id", "ticker", "quantity",
                                  "price", "counterparty", "reference", "day", "tick", "sequence"];
        for key in FILL {
            book_field(f, what, key)?;
        }
        book_unknown(f, what, &FILL)?;
        let at = "book.fills[].";
        let liquidity = read_str(f, at, "liquidity")?;
        state.fills.push(AgentFill {
            agent: read_str(f, at, "agent")?.to_string(),
            order_id: read_str(f, at, "order_id")?.to_string(),
            ticker: read_str(f, at, "ticker")?.to_string(),
            side: parse_side(read_str(f, at, "side")?)?,
            quantity: read_number(f, at, "quantity")?,
            price: read_number(f, at, "price")?,
            liquidity: Liquidity::parse(liquidity).ok_or_else(|| {
                SnapshotError::value(format!("unknown liquidity {liquidity:?}"))
            })?,
            counterparty: read_str(f, at, "counterparty")?.to_string(),
            reference: read_number(f, at, "reference")?,
            day: read_int(f, at, "day", "an integer")?,
            tick: read_int(f, at, "tick", "a whole number")?,
            sequence: read_int(f, at, "sequence", "a whole number")?,
        });
    }
    book_field(d, top, "impacts")?;
    for item in read_items(d, at, "impacts", "a list of dicts")? {
        let what = "a snapshot impact";
        let r = book_entry(item, "an impact")?;
        const IMPACT: [&str; 7] = ["agent", "ticker", "bought", "sold", "permanent", "day", "tick"];
        for key in IMPACT {
            book_field(r, what, key)?;
        }
        book_unknown(r, what, &[&IMPACT[..], &["transient"]].concat())?;
        let at = "book.impacts[].";
        state.impacts.push(AgentImpact {
            agent: read_str(r, at, "agent")?.to_string(),
            ticker: read_str(r, at, "ticker")?.to_string(),
            bought: read_number(r, at, "bought")?,
            sold: read_number(r, at, "sold")?,
            permanent: read_number(r, at, "permanent")?,
            transient: match r.get("transient") {
                Some(_) => Some(read_number(r, at, "transient")?),
                None => None,
            },
            day: read_int(r, at, "day", "an integer")?,
            tick: read_int(r, at, "tick", "a whole number")?,
        });
    }
    if d.get("memory").is_some() {
        let SnapshotValue::Bytes(raw) = book_field(d, top, "memory")? else {
            return Err(SnapshotError::value("snapshot field book.memory must be bytes of little-endian f64s"));
        };
        if raw.len() % (8 * MEMORY_WIDTH) != 0 {
            return Err(SnapshotError::value("the snapshot's book `memory` is not whole rows"));
        }
        for row in f64s_of(raw).chunks_exact(MEMORY_WIDTH) {
            let mut r = [0.0; MEMORY_WIDTH];
            r.copy_from_slice(row);
            state.memory.push(r);
        }
    }
    Ok(state)
}

// ---------------------------------------------------------------------------
// Writing and reading an engine
// ---------------------------------------------------------------------------

impl Engine {
    /// Every field that moves this market from here on, as one snapshot.
    ///
    /// Constant time in the run's length. The alternative, replaying an
    /// order log, costs what the original run cost.
    ///
    /// Writes the default [`DayLoop`]: no day closed, none open, nothing
    /// pending. A caller that keeps a day counter, an open flag or pending
    /// tape rows writes them with [`Engine::snapshot_with`]. The engine's own
    /// day label and valuation clock are carried either way.
    ///
    /// ```
    /// use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
    /// use tradefloor::engine::{Engine, SessionBuffer, SessionRequest};
    /// use tradefloor::market::GameTime;
    /// use tradefloor::snapshot::EngineSnapshot;
    ///
    /// fn engine() -> Engine {
    ///     let companies = tradefloor::universe::random_universe(5, 7)
    ///         .iter().enumerate().map(|(i, g)| g.to_init().to_tick_company(i)).collect();
    ///     Engine::new(7, companies, create_initial_economy_state(&Default::default()),
    ///                 create_initial_central_bank_state(0),
    ///                 tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect())
    /// }
    /// fn day(e: &mut Engine, d: i64) {
    ///     e.open_market();
    ///     let bell = GameTime::new(9, 30, d % 5);
    ///     e.run_session(&SessionRequest::new(bell, 390), &mut SessionBuffer::new());
    ///     e.close_day(d + 1);
    /// }
    ///
    /// let mut original = engine();
    /// day(&mut original, 0);
    /// let saved = original.snapshot().to_bytes();
    ///
    /// let mut resumed = engine();
    /// resumed.restore(&EngineSnapshot::from_bytes(&saved)?)?;
    /// day(&mut original, 1);
    /// day(&mut resumed, 1);
    /// assert_eq!(resumed.prices(), original.prices());
    /// # Ok::<(), tradefloor::snapshot::SnapshotError>(())
    /// ```
    pub fn snapshot(&self) -> EngineSnapshot {
        self.snapshot_with(&DayLoop::default())
    }

    /// [`Engine::snapshot`] with the caller's [`DayLoop`]: the Python
    /// binding's `Engine.state_snapshot`.
    pub fn snapshot_with(&self, day_loop: &DayLoop) -> EngineSnapshot {
        use SnapshotValue as V;
        let p = self.params();
        let mut out = SnapshotMap::new();
        // The layout version, which the restore reads first. Outside the
        // state hash: it describes the snapshot, not the market.
        out.put("state_schema", V::Int(STATE_SCHEMA));
        let mut columns = SnapshotMap::new();
        for name in COLUMN_FIELDS {
            let field = parse_column(name).expect("COLUMN_FIELDS names columns");
            columns.put(name, V::from_f64s(&self.column(field)));
        }
        out.put("columns", V::Map(columns));
        // Ten streams, three numbers each: (state, increment, spare). The
        // u64s ride as f64 bit patterns, which survive exactly where a u64
        // would not survive a Python float.
        let rng = self.rng_state();
        let mut rng_out = Vec::with_capacity(3 * crate::rng::stream::COUNT);
        for s in [rng.market, rng.economy, rng.external, rng.jumps, rng.volume,
                  rng.news, rng.volume_idio, rng.overnight, rng.market_vol_level,
                  rng.crisis_epicentre] {
            rng_out.push(V::Float(f64::from_bits(s.state)));
            rng_out.push(V::Float(f64::from_bits(s.increment)));
            rng_out.push(V::Float(s.spare.unwrap_or(f64::NAN)));
        }
        out.put("rng", V::List(rng_out));
        // The counts that give every draw its address, and the overlay.
        let counts = self
            .stream_positions()
            .iter()
            .flat_map(|(u, n)| [V::Float(*u as f64), V::Float(*n as f64)])
            .collect();
        out.put("draw_counts", V::List(counts));
        let mut overlay = Vec::new();
        for id in 0..crate::rng::stream::COUNT as u32 {
            if let Some(o) = self.draw_overlay(id) {
                for ((kind, index), value) in &o.table {
                    overlay.push(V::Tuple(vec![
                        V::Int(i64::from(id)),
                        V::Int(i64::from(*kind as u8)),
                        V::Int(*index as i64),
                        V::Float(*value),
                    ]));
                }
            }
        }
        out.put("draw_overlay", V::List(overlay));
        out.put("tickers", V::List(self.ids().into_iter().map(V::Str).collect()));
        // The model the frozen market was priced under; the restore refuses
        // a mismatch.
        out.put("model_fingerprint", V::Str(self.model_fingerprint().to_string()));
        // The per-day accumulators, at two widths: the day's attribution
        // carries the jump, overnight and fair-value slots, the tick's own
        // decomposition does not. The dividend's slot, the last, only on a
        // model that pays dividends (`dividend_payout_share`), where it can
        // be non-zero, so every other snapshot is the eleven-wide one it was.
        let width = if self.carries_dividends() {
            crate::market::factors::COMPONENT_COUNT
        } else {
            crate::market::factors::DIVIDEND_SLOT
        };
        let flat = |rows: &[[f64; crate::market::factors::COMPONENT_COUNT]]| -> Vec<f64> {
            rows.iter().flat_map(|r| r[..width].iter().copied()).collect()
        };
        let flat_tick = |rows: &[[f64; crate::market::factors::TICK_COMPONENT_COUNT]]| -> Vec<f64> {
            rows.iter().flat_map(|r| r.iter().copied()).collect()
        };
        out.put("attribution", V::from_f64s(&flat(self.attribution())));
        out.put("tick_components", V::from_f64s(&flat_tick(self.tick_components())));
        out.put("tick_fundamental", V::from_f64s(self.tick_fundamental()));
        out.put("tick_anchor", V::from_f64s(self.tick_anchor()));
        // The day's `random_noise` split and the scale its idiosyncratic part
        // was drawn at: the close builds the per-name innovation from them.
        let flat3: Vec<f64> = self.noise_parts().iter().flat_map(|r| r.iter().copied()).collect();
        out.put("noise_parts", V::from_f64s(&flat3));
        out.put("noise_own_scale2", V::from_f64s(self.noise_own_scale2()));
        // The day's GJR sum under a night split, which the attribution no
        // longer carries in `random_noise` (see `Engine::innovation_day`).
        if self.night_split_on() {
            out.put("innovation_day", V::from_f64s(self.innovation_day()));
        }
        // The jump each name booked at the last close, for the session that
        // trades the gap in.
        out.put("jump_move", V::from_f64s(self.jump_move()));
        out.put("market_open", V::Bool(day_loop.market_open));
        let (variance, day_factor, fast, slow, prev_day_factor, smoothed_vix) =
            self.market_variance_state();
        out.put(
            "market_variance",
            V::List([variance, day_factor, fast, slow, prev_day_factor, smoothed_vix]
                .into_iter()
                .map(V::Float)
                .collect()),
        );
        out.put("forced_flow_spent", V::Float(self.forced_flow_spent()));
        out.put("market_vol_log_level", V::Float(self.market_vol_log_level()));
        out.put("vix_log_level", V::Float(self.vix_log_level()));
        // The derived anchor (`vix_level_identity`): a constant of the run,
        // but derived from the roster the engine was BUILT on, so an engine
        // rebuilt on a later day's roster derives another one. Carried so a
        // restore reads the run's own (#268).
        if p.vix_level_identity != 0.0 {
            out.put("vix_anchor", V::Float(self.vix_anchor()));
        }
        // Only where the hash covers it: the memory moves only with
        // `vix_anchor_memory` nonzero.
        if p.vix_anchor_memory != 0.0 {
            out.put("vix_anchor_slow", V::Float(self.vix_anchor_slow()));
        }
        // The market factor's return memory, on the same rule: carried, and
        // hashed, only with `market_vol_leverage` set.
        if self.carries_market_vol_leverage() {
            out.put("market_vol_leverage_memory", V::Float(self.market_vol_leverage_memory()));
        }
        // The cycle's volatility multiplier, on the same rule: carried, and
        // hashed, only with `market_vol_cycle_ratio` set and once a close
        // has set it.
        if self.carries_market_vol_cycle() {
            if let Some(l) = self.market_vol_cycle_log() {
                out.put("market_vol_cycle_log", V::Float(l));
            }
        }
        // The published VIX's stress memory, only where the hash covers it:
        // with `vix_stress_premium` non-zero.
        if self.carries_vix_stress_memory() {
            out.put("vix_stress_memory", V::Float(self.vix_stress_memory()));
        }
        // The crisis episode: -1 is the epicentre `none`, -2 is no pin.
        let (in_episode, sessions_under, epicentre, pin) = self.crisis_episode_raw();
        out.put("crisis_in_episode", V::Bool(in_episode));
        out.put("crisis_sessions_under", V::Int(sessions_under));
        out.put("crisis_epicentre", V::Int(i64::from(epicentre)));
        out.put("crisis_epicentre_pin", V::Int(i64::from(pin.unwrap_or(-2))));
        // A forced close pending tonight and today's pins, each a key only
        // while set, so an engine that never pinned snapshots as it did.
        if self.vix_sets_variance_pending() {
            out.put("vix_sets_variance_pending", V::Bool(true));
        }
        if self.macro_pins_today() != 0 {
            out.put("macro_pins_today", V::Int(i64::from(self.macro_pins_today())));
        }
        // The market's cycle nowcast's generator (`cycle_nowcast_accuracy`),
        // a key only while the dial is set: (state, increment, spare) as the
        // `rng` list carries a stream, then its uniform and normal counts.
        // The belief itself is `economy["cycle_nowcast"]`.
        if p.cycle_nowcast_accuracy != 0.0 {
            let s = self.cycle_nowcast_rng_state();
            out.put(
                "cycle_nowcast_rng",
                V::List(vec![
                    V::Float(f64::from_bits(s.state)),
                    V::Float(f64::from_bits(s.increment)),
                    V::Float(s.spare.unwrap_or(f64::NAN)),
                    V::Float(s.uniforms as f64),
                    V::Float(s.normals as f64),
                ]),
            );
        }
        // The central bank's stress level, a key only while `fed_stress_cut`
        // is set, and the rate indices' live mark, only while
        // `rate_intraday_live` is set and a session holds one.
        if let Some(level) = self.stress_vix_max() {
            out.put("fed_stress_vix_max", V::Float(level));
        }
        // The stress hold's clock and the priced path's forecast, a key each
        // only while its dial is set.
        if let Some(age) = self.stress_hold_age() {
            out.put("fed_stress_hold_age", V::Float(age));
        }
        if let Some(path) = self.policy_path() {
            out.put("treasury_policy_path", V::Float(path));
        }
        // The drawdown hold's window and its base, two keys only while
        // `fed_drawdown_hold` is set.
        if let Some((returns, prev)) = self.drawdown_state() {
            out.put("fed_drawdown_returns", V::from_f64s(&returns));
            out.put("fed_drawdown_mcap_prev", V::Float(prev));
        }
        // What the curve prices of the next meeting, a key only while
        // `policy_anticipation` is set.
        if let Some(priced) = self.policy_anticipation_priced() {
            out.put("policy_anticipation_priced", V::Float(priced));
        }
        if let Some(marks) = self.rate_live_marks() {
            out.put("rate_live_marks", V::List(marks.iter().map(|v| V::Float(*v)).collect()));
        }
        // Today's priced VIX move (`pinned_vix_variance_share`), a key only
        // while a pin has made one: the session's market draws read it.
        if self.pinned_vix_jump() != 0.0 {
            out.put("pinned_vix_jump", V::Float(self.pinned_vix_jump()));
        }
        // The day's market t scale (`market_day_tail_df`), a key only between
        // an open that drew one and the close: the session's market draws
        // read it.
        if self.market_day_scale() != 1.0 {
            out.put("market_day_scale", V::Float(self.market_day_scale()));
        }
        // The price index's divisor and close level, a key only while
        // `index_level_listed` is set.
        if let Some([divisor, close]) = self.index_state() {
            out.put("index_divisor", V::List(vec![V::Float(divisor), V::Float(close)]));
        }
        // The live VIX's projection, only while `vix_intraday_live` is set and
        // a session holds one.
        if let Some(mark) = self.vix_live_mark() {
            out.put("vix_live", V::Float(mark));
        }
        // The forecast the last close computed, only while
        // `forecast_horizon_sessions` is set and a close has computed one.
        if let Some(forecast) = self.forecast() {
            out.put("forecast", V::from_f64s(&forecast.to_words()));
        }
        // The index futures (`futures_index_listed`): their generator on
        // `stream::DERIVATIVES` as (state, increment, spare, uniforms,
        // normals), their numbers, their book once an agent has traded a
        // contract, and the night's path while one is walked
        // (`night_session_steps`).
        if let (Some(s), Some(words)) = (self.derivatives_rng_state(), self.futures_words()) {
            out.put(
                "derivatives_rng",
                V::List(vec![
                    V::Float(f64::from_bits(s.state)),
                    V::Float(f64::from_bits(s.increment)),
                    V::Float(s.spare.unwrap_or(f64::NAN)),
                    V::Float(s.uniforms as f64),
                    V::Float(s.normals as f64),
                ]),
            );
            out.put("futures", V::from_f64s(&words));
        }
        if let Some(book) = self.futures_book_state() {
            out.put("futures_book", book_value(book));
        }
        if let Some(words) = self.night_bridge_words() {
            out.put("night_bridge", V::from_f64s(&words));
        }
        // The VIX's fear memory, a key only while `vix_fear_uptake` is set.
        if self.carries_vix_fear() {
            out.put("vix_fear", V::Float(self.vix_fear()));
        }
        // The VIX futures (`futures_vix_listed`): their numbers, and their
        // book once an agent has traded one.
        if let Some(words) = self.vix_futures_words() {
            out.put("vix_futures", V::from_f64s(&words));
        }
        if let Some(book) = self.vix_futures_book_state() {
            out.put("vix_futures_book", book_value(book));
        }
        // The rate futures (`futures_rates_listed`): their numbers, and their
        // book once an agent has traded one.
        if let Some(words) = self.rate_futures_words() {
            out.put("rate_futures", V::from_f64s(&words));
        }
        if let Some(book) = self.rate_futures_book_state() {
            out.put("rate_futures_book", book_value(book));
        }
        // The oil futures (`futures_oil_listed`).
        if let Some(words) = self.oil_futures_words() {
            out.put("oil_futures", V::from_f64s(&words));
        }
        if let Some(book) = self.oil_futures_book_state() {
            out.put("oil_futures_book", book_value(book));
        }
        // The contracts' margin (`margin_scan_coverage`).
        if let Some(words) = self.margin_words() {
            out.put("margin", V::from_f64s(&words));
        }
        // Oil's long factor (`oil_target_drift_sd`).
        if let Some(words) = self.oil_drift_words() {
            out.put("oil_target_drift", V::from_f64s(&words));
        }
        // The spread a `corporate_spread` pin holds tonight, only while its
        // mark stands.
        if let Some(spread) = self.pinned_corporate_spread() {
            out.put("pinned_corporate_spread", V::Float(spread));
        }
        // Nominal output when the run opened, the base of the growth term:
        // an engine rebuilt without it re-bases its valuation on the day it
        // was rebuilt.
        out.put("nominal_output_base", V::Float(self.nominal_output_base()));
        out.put("volume_state", V::Float(self.volume_state()));
        out.put("universe_stress", V::Float(self.universe_stress()));
        out.put("volume_idio", V::from_f64s(self.volume_idio()));
        out.put("sector_variance", V::from_f64s(self.sector_variance()));
        out.put("jump_excitation", V::from_f64s(self.jump_excitation()));
        // The accrued buyback share-count reductions: their own key, and
        // only while `buyback_accrual` and the payout share are both set, so
        // every preset's snapshot is the one it was.
        if self.carries_buyback_log_shares() {
            out.put("buyback_log_shares", V::from_f64s(&self.buyback_log_shares()));
        }
        // The fair-value levels and the opening draws, only when the model
        // can move a level.
        if self.carries_fair_value_offsets() {
            out.put("fair_value_offset", V::from_f64s(&self.fair_value_offsets()));
            out.put("opening_z", V::from_f64s(self.opening_z()));
            // The prehistory's carried opening (`market_prehistory_valuation`),
            // under its own key and only with that dial on.
            if p.market_prehistory_valuation != 0.0 {
                out.put("opening_carry", V::from_f64s(self.opening_carry()));
            }
        }
        // The dividend states, only on a model that pays dividends, so every
        // other snapshot is the one it was. Seven f64s a name
        // (`market::dividends::STATE_WIDTH`), NaN for a name without one.
        if self.carries_dividends() {
            out.put("dividend", V::from_f64s(&self.dividend_states()));
        }
        // The earnings calendar's key, only with the calendar on: every
        // report's date and draws derive from it, so a restore into an
        // engine built from another seed must carry it or report on other
        // dates.
        if self.carries_earnings() {
            out.put("earnings_key", V::from_u64(self.earnings_key()));
        }
        // What names hold back of the earnings cycle for their reports, only
        // while `earnings_cycle_report_share` runs.
        if self.carries_earnings_withheld() {
            out.put("earnings_withheld", V::from_f64s(self.earnings_withheld()));
        }
        // Tonight's market draw under a night split, only while the
        // session's live lagged wire reads it: a fork taken mid-session
        // needs it to key the wire on the session's own draws.
        if self.carries_night_market_factor() {
            out.put("night_market_factor", V::Float(self.night_market_factor()));
        }
        // The per-name idiosyncratic variance state, only while it runs, so
        // every preset's snapshot is the one it was.
        if self.carries_idio_vol_state() {
            let (ratio, jump, jump_var) = self.idio_vol_state();
            out.put("idio_variance", V::from_f64s(ratio));
            out.put("idio_jump_pending", V::from_f64s(jump));
            out.put("idio_jump_var_pending", V::from_f64s(jump_var));
        }
        out.put("sector_day_factor", V::from_f64s(self.sector_day_factor()));
        out.put("sector_target_day", V::Float(self.sector_target_day()));
        // The day loop's tape rows in waiting.
        out.put("pending_jump", V::from_f64s(&day_loop.pending_jump));
        out.put("pending_overnight", V::from_f64s(&day_loop.pending_overnight));
        if !day_loop.pending_fair_value.is_empty() {
            out.put("pending_fair_value", V::from_f64s(&day_loop.pending_fair_value));
        }
        // Only on a model that pays dividends, the one the restore's key
        // check accepts it on: a host's day loop could carry a buffer here
        // that this engine's model never fills.
        if self.carries_dividends() && !day_loop.pending_dividend.is_empty() {
            out.put("pending_dividend", V::from_f64s(&day_loop.pending_dividend));
        }
        // The day's endogenous news, generated once at the open and read by
        // every tick of the day.
        let news = self
            .session_news()
            .iter()
            .map(|event| {
                let mut item = SnapshotMap::new();
                item.put("ticker", event.company_id.clone().map_or(V::None, V::Str));
                item.put("sector", event.sector.clone().map_or(V::None, V::Str));
                item.put("price_impact", event.price_impact.map_or(V::None, V::Float));
                V::Map(item)
            })
            .collect();
        out.put("session_news", V::List(news));

        // The macro chain's state, field by field.
        let economy = self.economy();
        let mut econ = SnapshotMap::new();
        macro_rules! econ_put {
            ($($field:ident),* $(,)?) => {
                $(econ.put(stringify!($field), EconomyScalar::put(&economy.$field));)*
            };
        }
        economy_scalars!(econ_put);
        econ.put("gdp_trend", V::List(economy.gdp_trend.iter().map(|v| V::Float(*v)).collect()));
        econ.put("cycle_phase", V::Str(economy.cycle_phase.as_str().to_string()));
        if p.cycle_nowcast_accuracy != 0.0 {
            econ.put(
                "cycle_nowcast",
                V::List(self.cycle_nowcast().iter().map(|v| V::Float(*v)).collect()),
            );
        }
        // The published-phase history, oldest first, only while
        // `cycle_publication_lag` keeps one.
        if self.carries_cycle_history() {
            let history = self
                .cycle_history()
                .iter()
                .map(|phase| V::Str(phase.as_str().to_string()))
                .collect();
            econ.put("cycle_history", V::List(history));
        }
        if p.unemployment_adjustment_half_life != 0.0 {
            econ.put("unemployment_impulse", V::Float(economy.unemployment_impulse));
        }
        if p.oil_pushes_in_target != 0.0 {
            econ.put("oil_push_level", V::Float(economy.oil_push_level));
        }
        if p.usd_mean_reversion != 0.0 {
            econ.put("usd_haven_level", V::Float(economy.usd_haven_level));
        }
        if p.gdp_publication_lag != 0.0 {
            let g = self.gdp_publication();
            let mut block = SnapshotMap::new();
            block.put("published", V::Float(g.published));
            block.put("quarter", V::Int(g.quarter));
            block.put("count", V::Int(i64::from(g.count)));
            block.put("sum", V::Float(g.sum));
            block.put("pending_days", V::List(g.pending.iter().map(|&(d, _)| V::Int(d)).collect()));
            block.put("pending_values", V::List(g.pending.iter().map(|&(_, v)| V::Float(v)).collect()));
            econ.put("gdp_publication", V::Map(block));
        }
        // The drawn publication schedule, only while
        // `cycle_publication_lag_draw` is set (the fixed lag's history is
        // then not kept).
        if p.cycle_publication_lag_draw != 0.0 {
            let c = self.cycle_publication();
            let mut block = SnapshotMap::new();
            block.put("key", V::from_u64(c.key));
            block.put("published", V::Str(c.published.as_str().to_string()));
            block.put("last_true", V::Str(c.last_true.as_str().to_string()));
            block.put("closes", V::Int(c.closes));
            block.put("turns", V::from_u64(c.turns));
            block.put("pending_closes", V::List(c.pending.iter().map(|&(d, _)| V::Int(d)).collect()));
            block.put(
                "pending_phases",
                V::List(c.pending.iter().map(|&(_, ph)| V::Str(ph.as_str().to_string())).collect()),
            );
            econ.put("cycle_publication", V::Map(block));
        }
        // The anticipation's left-out drift `D` and the last `A - e`, only
        // while `earnings_anticipation_drift_share` is set.
        if self.carries_anticipation_drift() {
            let (drift, raw) = self.anticipation_drift();
            econ.put("anticipation_drift", V::Float(drift));
            econ.put("anticipation_raw", V::Float(raw));
        }
        if p.earnings_cycle_depth != 0.0 {
            econ.put("earnings_cycle", V::Float(economy.earnings_cycle));
        }
        if self.carries_vix_feedback() {
            econ.put("vix_feedback", V::Float(economy.vix_feedback));
        }
        if p.qe_pe_stock_gain != 0.0 {
            econ.put("qe_assets_ratio", V::Float(economy.qe_assets_ratio));
        }
        // The Fed put's state, on the same rule: only with `fed_put_gain` set.
        if self.carries_fed_put() {
            econ.put("intermeeting_return", V::Float(economy.intermeeting_return));
            econ.put("fed_put", V::Float(economy.fed_put));
            econ.put("fed_put_owed", V::Float(economy.fed_put_owed));
            econ.put("fed_put_mcap_prev", V::Float(economy.fed_put_mcap_prev));
        }
        // Credit's leverage gap, on the same rule: only with
        // `corporate_spread_equity_gain` set.
        if self.carries_spread_equity_gap() {
            econ.put("spread_equity_gap", V::Float(economy.spread_equity_gap));
        }
        out.put("economy", V::Map(econ));

        let bank = self.central_bank();
        let mut cb = SnapshotMap::new();
        cb.put("last_meeting_date", V::Int(bank.last_meeting_date));
        cb.put("next_meeting_date", V::Int(bank.next_meeting_date));
        cb.put("target_inflation", V::Float(bank.target_inflation));
        cb.put("target_unemployment", V::Float(bank.target_unemployment));
        cb.put("qe_active", V::Bool(bank.qe_active));
        cb.put("qe_monthly_purchases", V::Float(bank.qe_monthly_purchases));
        cb.put("hawkish_dovish_score", V::Float(bank.hawkish_dovish_score));
        cb.put("forward_guidance", V::Str(bank.forward_guidance.as_str().to_string()));
        out.put("central_bank", V::Map(cb));

        out.put("day_count", V::Int(i64::from(day_loop.day_count)));
        // The rate instruments, only when the engine holds any, each by name
        // in `RATE_STATE_FIELDS` order.
        let rates = self.rates();
        if !rates.is_empty() {
            let items = rates
                .instruments
                .iter()
                .map(|inst| {
                    let mut item = SnapshotMap::new();
                    item.put("ticker", V::Str(inst.spec.ticker.to_string()));
                    for (name, value) in RATE_STATE_FIELDS.iter().zip(rate_state(inst)) {
                        item.put(name, V::Float(value));
                    }
                    V::Map(item)
                })
                .collect();
            let mut block = SnapshotMap::new();
            block.put("instruments", V::List(items));
            block.put("ig_spread", V::Float(rates.ig_spread));
            block.put("last_corporate", V::Float(rates.last_corporate));
            block.put("closed_since_open", V::Bool(rates.closed_since_open));
            out.put("rates", V::Map(block));
        }
        // The agents' book, only once it has been used.
        if !self.book_state().is_pristine() {
            out.put("book", book_value(self.book_state()));
        }
        // The day's label and the valuation's clock, each only where it is
        // not the day the counter and the open flag give.
        let usual = default_day(day_loop.day_count, day_loop.market_open);
        if self.current_day() != usual {
            out.put("current_day", V::Int(self.current_day()));
        }
        if self.elapsed_days() != usual {
            out.put("elapsed_days", V::Int(self.elapsed_days()));
        }
        // The ticks the day has run, the tick the book stamps a fill with.
        // The one field the state hash does not cover.
        out.put("session_tick", V::Int(i64::from(self.session_ticks())));
        // The fair-value inputs, once `set_fundamentals` has moved them.
        if self.fundamentals_changed() {
            let (eps, book, growth) = self.fundamentals();
            let mut block = SnapshotMap::new();
            block.put("eps", V::from_f64s(&eps));
            block.put("book_value_per_share", V::from_f64s(&book));
            block.put("revenue_growth", V::from_f64s(&growth));
            out.put("fundamentals", V::Map(block));
        }
        // The share counts, once `set_shares_outstanding` has moved them.
        if self.shares_outstanding_changed() {
            out.put("shares_outstanding", V::from_f64s(&self.shares_outstanding()));
        }
        // The variance cascade, only on a model that runs it.
        if self.carries_garch_cascade() {
            out.put("garch_cascade", V::from_f64s(&self.garch_cascade()));
        }
        // The population's ledger and memories, only on an engine built with
        // one (`crate::population`), so every other snapshot is the one it
        // was.
        if let Some(pop) = self.population() {
            let mut block = SnapshotMap::new();
            block.put("fingerprint", V::Str(pop.fingerprint.clone()));
            block.put("tickers", V::List(pop.tickers.iter().cloned().map(V::Str).collect()));
            block.put("state", V::from_f64s(&pop.to_flat()));
            out.put("population", V::Map(block));
        }
        EngineSnapshot { fields: out }
    }

    /// Hold a snapshot's keys against the ones this engine's state carries:
    /// the top level, the economy, the central bank and the columns. A key
    /// carried only under a model dial is required exactly when this
    /// engine's model sets the dial, which is the snapshot's model once the
    /// fingerprint check has passed.
    fn check_snapshot_keys(&self, snapshot: &SnapshotMap, versioned: bool) -> Result<()> {
        let refuse = |mut message: String| {
            if !versioned {
                message.push_str(LEGACY_NOTE);
            }
            SnapshotError::new(SnapshotErrorKind::Fields, message)
        };
        let p = self.params();
        let held: Vec<&str> = self.rates().instruments.iter().map(|i| i.spec.ticker).collect();
        let fair_value = self.carries_fair_value_offsets();
        let fair_value_why = format!(
            "the model can move a fair-value level (fair_value_news_share, \
             fair_value_market_share, opening_mispricing_sigma, \
             opening_market_sigma or earnings_surprise_sigma is not 0), and \
             this engine's model {}",
            if fair_value { "can" } else { "cannot" }
        );
        let drawdown = p.fed_drawdown_hold != 0.0 || p.market_vol_cycle_recovery_release != 0.0;
        let drawdown_why = format!(
            "fed_drawdown_hold or market_vol_cycle_recovery_release is not 0, and \
             this engine's are {} and {}",
            p.fed_drawdown_hold, p.market_vol_cycle_recovery_release
        );
        let idio = self.carries_idio_vol_state();
        let idio_why = format!(
            "idio_vol_alpha, idio_vol_beta or idio_vol_jump_bump is not 0, and this \
             engine's are {}, {} and {}",
            p.idio_vol_alpha, p.idio_vol_beta, p.idio_vol_jump_bump
        );
        let gated = [
            Gated::dial("vix_anchor_slow", "vix_anchor_memory", p.vix_anchor_memory),
            // Optional where wanted: a snapshot from before #268 has none.
            Gated {
                key: "vix_anchor",
                wanted: p.vix_level_identity != 0.0,
                held: true,
                why: format!(
                    "vix_level_identity is not 0, and this engine's \
                     vix_level_identity is {}",
                    p.vix_level_identity
                ),
            },
            Gated::when("fair_value_offset", fair_value, fair_value_why.clone()),
            Gated::when("opening_z", fair_value, fair_value_why),
            Gated::when(
                "garch_cascade",
                self.carries_garch_cascade(),
                format!(
                    "garch_cascade_components is 1 or more, and this engine's \
                     garch_cascade_components is {}",
                    p.garch_cascade_components
                ),
            ),
            // The dial-gated state the later mechanisms carry (every dial 0.0
            // on every shipped preset, so no shipped snapshot carries one).
            Gated::dial("market_vol_leverage_memory", "market_vol_leverage", p.market_vol_leverage),
            Gated::held("market_vol_cycle_log", "market_vol_cycle_ratio", p.market_vol_cycle_ratio),
            Gated::dial("vix_stress_memory", "vix_stress_premium", p.vix_stress_premium),
            Gated::dial("cycle_nowcast_rng", "cycle_nowcast_accuracy", p.cycle_nowcast_accuracy),
            Gated::dial("fed_stress_vix_max", "fed_stress_cut", p.fed_stress_cut),
            Gated::dial("fed_stress_hold_age", "fed_stress_hold", p.fed_stress_hold),
            Gated::dial("treasury_policy_path", "treasury_path_pricing", p.treasury_path_pricing),
            Gated::when("fed_drawdown_returns", drawdown, drawdown_why.clone()),
            Gated::when("fed_drawdown_mcap_prev", drawdown, drawdown_why),
            Gated::dial("policy_anticipation_priced", "policy_anticipation", p.policy_anticipation),
            Gated::held("rate_live_marks", "rate_intraday_live", p.rate_intraday_live),
            Gated::held("pinned_vix_jump", "pinned_vix_variance_share", p.pinned_vix_variance_share),
            Gated::held("market_day_scale", "market_day_tail_df", p.market_day_tail_df),
            Gated::dial("index_divisor", "index_level_listed", p.index_level_listed),
            Gated::held("vix_live", "vix_intraday_live", p.vix_intraday_live),
            Gated::held("forecast", "forecast_horizon_sessions", p.forecast_horizon_sessions),
            Gated::dial("derivatives_rng", "futures_index_listed", p.futures_index_listed),
            Gated::dial("futures", "futures_index_listed", p.futures_index_listed),
            Gated::held("futures_book", "futures_index_listed", p.futures_index_listed),
            Gated::held("night_bridge", "night_session_steps", p.night_session_steps),
            Gated::dial("vix_fear", "vix_fear_uptake", p.vix_fear_uptake),
            Gated::dial("vix_futures", "futures_vix_listed", p.futures_vix_listed),
            Gated::held("vix_futures_book", "futures_vix_listed", p.futures_vix_listed),
            Gated::dial("rate_futures", "futures_rates_listed", p.futures_rates_listed),
            Gated::held("rate_futures_book", "futures_rates_listed", p.futures_rates_listed),
            Gated::dial("oil_futures", "futures_oil_listed", p.futures_oil_listed),
            Gated::held("oil_futures_book", "futures_oil_listed", p.futures_oil_listed),
            Gated::dial("margin", "margin_scan_coverage", p.margin_scan_coverage),
            Gated::dial("oil_target_drift", "oil_target_drift_sd", p.oil_target_drift_sd),
            Gated::when(
                "buyback_log_shares",
                self.carries_buyback_log_shares(),
                format!(
                    "buyback_accrual and buyback_payout_share are both not 0, and \
                     this engine's are {} and {}",
                    p.buyback_accrual, p.buyback_payout_share
                ),
            ),
            Gated::when(
                "opening_carry",
                fair_value && p.market_prehistory_valuation != 0.0,
                format!(
                    "market_prehistory_valuation is not 0 on a model that can move a \
                     fair-value level, and this engine's market_prehistory_valuation is {}",
                    p.market_prehistory_valuation
                ),
            ),
            Gated::when(
                "innovation_day",
                p.overnight_market_share != 0.0 || p.overnight_idio_share != 0.0,
                format!(
                    "overnight_market_share or overnight_idio_share is not 0, and this \
                     engine's are {} and {}",
                    p.overnight_market_share, p.overnight_idio_share
                ),
            ),
            Gated::dial("dividend", "dividend_payout_share", p.dividend_payout_share),
            Gated::held("pending_dividend", "dividend_payout_share", p.dividend_payout_share),
            Gated::dial("earnings_key", "earnings_surprise_sigma", p.earnings_surprise_sigma),
            Gated::when(
                "earnings_withheld",
                self.carries_earnings_withheld(),
                format!(
                    "earnings_surprise_sigma, earnings_cycle_report_share and \
                     earnings_cycle_depth are all not 0, and this engine's are {}, {} and {}",
                    p.earnings_surprise_sigma, p.earnings_cycle_report_share, p.earnings_cycle_depth
                ),
            ),
            Gated::when(
                "night_market_factor",
                self.carries_night_market_factor(),
                format!(
                    "overnight_market_share, market_beta_down_asym_lag and \
                     market_beta_down_asym_lag_live are all not 0, and this engine's are \
                     {}, {} and {}",
                    p.overnight_market_share, p.market_beta_down_asym_lag,
                    p.market_beta_down_asym_lag_live
                ),
            ),
            Gated::when("idio_variance", idio, idio_why.clone()),
            Gated::when("idio_jump_pending", idio, idio_why.clone()),
            Gated::when("idio_jump_var_pending", idio, idio_why),
            Gated::when(
                "population",
                self.population().is_some(),
                "the engine was built with a population".to_string(),
            ),
            Gated {
                key: "rates",
                wanted: !held.is_empty(),
                held: false,
                why: if held.is_empty() {
                    "the engine holds rate instruments, and this one holds none".to_string()
                } else {
                    format!(
                        "the engine holds rate instruments. This engine holds {} \
                         and the snapshot carries none of them; it was taken on \
                         a roster without them",
                        held.join(", ")
                    )
                },
            },
        ];
        if let Some(message) =
            key_mismatch("this snapshot", snapshot, SNAPSHOT_KEYS, &gated, SNAPSHOT_OPTIONAL_KEYS)
        {
            return Err(refuse(message));
        }

        let economy = read_map(snapshot, "", "economy")?;
        let mut required: Vec<&str> = ECONOMY_SCALARS.to_vec();
        required.extend(["gdp_trend", "cycle_phase"]);
        let feedback = self.carries_vix_feedback();
        let gated = [
            Gated::dial("earnings_cycle", "earnings_cycle_depth", p.earnings_cycle_depth),
            Gated {
                key: "vix_feedback",
                wanted: feedback,
                held: false,
                why: format!(
                    "fair_value_vix_discount and fair_value_vix_half_life are \
                     both not 0, and this engine's are {} and {}",
                    p.fair_value_vix_discount, p.fair_value_vix_half_life
                ),
            },
            Gated::dial("qe_assets_ratio", "qe_pe_stock_gain", p.qe_pe_stock_gain),
            Gated::when(
                "cycle_history",
                self.carries_cycle_history(),
                format!(
                    "cycle_publication_lag is not 0 and cycle_publication_lag_draw is 0, \
                     and this engine's are {} and {}",
                    p.cycle_publication_lag, p.cycle_publication_lag_draw
                ),
            ),
            Gated::dial("cycle_nowcast", "cycle_nowcast_accuracy", p.cycle_nowcast_accuracy),
            Gated::dial(
                "cycle_publication", "cycle_publication_lag_draw", p.cycle_publication_lag_draw),
            Gated::dial(
                "anticipation_drift",
                "earnings_anticipation_drift_share",
                p.earnings_anticipation_drift_share,
            ),
            Gated::dial(
                "anticipation_raw",
                "earnings_anticipation_drift_share",
                p.earnings_anticipation_drift_share,
            ),
            Gated::dial("intermeeting_return", "fed_put_gain", p.fed_put_gain),
            Gated::dial("fed_put", "fed_put_gain", p.fed_put_gain),
            Gated::dial("fed_put_owed", "fed_put_gain", p.fed_put_gain),
            Gated::dial("fed_put_mcap_prev", "fed_put_gain", p.fed_put_gain),
            Gated::when(
                "spread_equity_gap",
                self.carries_spread_equity_gap(),
                format!(
                    "corporate_spread_equity_gain or cycle_equity_hazard is not 0, and \
                     this engine's are {} and {}",
                    p.corporate_spread_equity_gain, p.cycle_equity_hazard
                ),
            ),
            Gated::dial(
                "unemployment_impulse",
                "unemployment_adjustment_half_life",
                p.unemployment_adjustment_half_life,
            ),
            Gated::dial("gdp_publication", "gdp_publication_lag", p.gdp_publication_lag),
            Gated::dial("oil_push_level", "oil_pushes_in_target", p.oil_pushes_in_target),
            Gated::dial("usd_haven_level", "usd_mean_reversion", p.usd_mean_reversion),
        ];
        if let Some(message) = key_mismatch("this snapshot's economy", economy, &required, &gated, &[]) {
            return Err(refuse(message));
        }
        let bank = read_map(snapshot, "", "central_bank")?;
        if let Some(message) =
            key_mismatch("this snapshot's central_bank", bank, CENTRAL_BANK_KEYS, &[], &[])
        {
            return Err(refuse(message));
        }
        let columns = read_map(snapshot, "", "columns")?;
        if let Some(message) =
            key_mismatch("this snapshot's columns", columns, &COLUMN_FIELDS, &[], &[])
        {
            return Err(refuse(message));
        }
        Ok(())
    }

    /// Put this engine back to a snapshot's state, and hand back the
    /// [`DayLoop`] it carried.
    ///
    /// Every field the snapshot's `state_schema` names is required, and a
    /// missing field, an unknown one, a value of the wrong type or length,
    /// or a non-finite scalar is refused by name. A field carried only under
    /// a model dial is required exactly when this engine's model sets the
    /// dial. A snapshot with no `state_schema` is read as version 1, which is
    /// what tradefloor 0.8.5 to 0.8.8 wrote. See the [module docs](self).
    ///
    /// A refused restore changes nothing: the new state is built on a copy
    /// of the engine and swapped in only once every field has been read.
    ///
    /// # The roster and the model
    ///
    /// The snapshot's roster must be this engine's, ticker for ticker and in
    /// order, because the columns are positional; and its model fingerprint
    /// must be this engine's, because a state restored under other
    /// coefficients continues a market the snapshot does not describe.
    ///
    /// Matching tickers do not mean a matching universe. Generated tickers
    /// are positional, so two universes drawn from different seeds share
    /// their names and differ in everything else. The engine holds the
    /// fundamentals it was built with, and restoring onto one built from
    /// another universe gives the right prices and the wrong fair values.
    /// Build the engine from the universe the snapshot came from.
    ///
    /// Build it from the same seed too where a cohort's arrival order
    /// matters. Every generator's position comes from the snapshot, but
    /// [`Engine::arrival_order`] under `book_arrival_shuffle` is keyed on the
    /// seed this engine was built with, which a snapshot does not carry.
    ///
    /// # What a restore resets
    ///
    /// The day marks, which name the days this engine opened, and the record
    /// of what was written to each price since its last print, which reads
    /// NaN until the next print on a model that can write a price between
    /// prints. Neither moves a price.
    pub fn restore(&mut self, snapshot: &EngineSnapshot) -> Result<DayLoop> {
        let snapshot = &snapshot.fields;
        let versioned = snapshot_version(snapshot)?;
        self.check_snapshot_keys(snapshot, versioned)?;

        let tickers = read_items(snapshot, "", "tickers", "a list of tickers")?;
        let ids = self.ids();
        let same_roster = tickers.len() == ids.len()
            && tickers.iter().zip(&ids).all(|(t, id)| matches!(t, SnapshotValue::Str(s) if s == id));
        if !same_roster {
            if let Some(bad) = tickers.iter().find(|t| !matches!(t, SnapshotValue::Str(_))) {
                return Err(wrong_item("", "tickers", "a list of tickers", bad));
            }
            return Err(SnapshotError::new(
                SnapshotErrorKind::Roster,
                "snapshot roster does not match this engine. Columns are \
                 positional, so restoring across rosters would attach every \
                 value to the wrong instrument.",
            ));
        }
        let recorded = read_str(snapshot, "", "model_fingerprint")?;
        let ours = self.model_fingerprint();
        if recorded != ours {
            return Err(SnapshotError::new(
                SnapshotErrorKind::Model,
                format!(
                    "this snapshot was taken under model {recorded:?} and \
                     this engine runs {ours:?}. Restoring across models \
                     would continue the frozen market under coefficients \
                     it was never priced with; build the engine with the \
                     snapshot's model instead."
                ),
            ));
        }

        // Everything below writes to `inner`, a copy, and to locals, and the
        // engine takes them only at the end.
        let mut inner = self.clone();
        let n = inner.len();
        let core = SnapshotError::value;

        let columns = read_map(snapshot, "", "columns")?;
        for name in COLUMN_FIELDS {
            let values = read_buffer(columns, "columns.", name)?;
            let field = parse_column(name).expect("COLUMN_FIELDS names columns");
            inner
                .set_column(field, &values)
                .map_err(|e| core(format!("snapshot column {name:?}: {e}")))?;
        }

        let rng = read_numbers(snapshot, "", "rng")?;
        let streams = crate::rng::stream::COUNT;
        if rng.len() != 3 * streams {
            return Err(core(format!(
                "snapshot field rng carries {} numbers and version {STATE_SCHEMA} \
                 carries {}: {streams} generator streams, each as (state, \
                 increment, spare).",
                rng.len(),
                3 * streams
            )));
        }
        let counts = read_numbers(snapshot, "", "draw_counts")?;
        if counts.len() != 2 * streams {
            return Err(core(format!(
                "snapshot field draw_counts carries {} numbers and version \
                 {STATE_SCHEMA} carries {}, two per stream.",
                counts.len(),
                2 * streams
            )));
        }
        if let Some(bad) = counts
            .iter()
            .find(|c| !c.is_finite() || **c < 0.0 || c.fract() != 0.0 || **c > 9.007_199_254_740_992e15)
        {
            return Err(core(format!(
                "snapshot field draw_counts holds {bad}. Each is a count of \
                 draws taken: a whole number from 0."
            )));
        }
        let stream = |k: usize| crate::rng::RngState {
            state: rng[3 * k].to_bits(),
            increment: rng[3 * k + 1].to_bits(),
            spare: if rng[3 * k + 2].is_nan() { None } else { Some(rng[3 * k + 2]) },
            uniforms: counts[2 * k] as u64,
            normals: counts[2 * k + 1] as u64,
        };
        inner.set_rng_state(EngineRngState {
            market: stream(0),
            economy: stream(1),
            external: stream(2),
            jumps: stream(3),
            volume: stream(4),
            news: stream(5),
            volume_idio: stream(6),
            overnight: stream(7),
            market_vol_level: stream(8),
            crisis_epicentre: stream(9),
        });
        let overlay_kind = "a list of (stream, kind, index, value)";
        let overlay = read_items(snapshot, "", "draw_overlay", overlay_kind)?;
        for id in 0..streams as u32 {
            inner.set_draw_overlay(id, None);
        }
        for entry in overlay {
            let bad = || wrong_item("", "draw_overlay", overlay_kind, entry);
            let parts = entry.items().ok_or_else(bad)?;
            let [id, kind, index, value] = parts else {
                return Err(bad());
            };
            let id = id.integer().and_then(|i| u32::try_from(i).ok()).ok_or_else(bad)?;
            let kind = kind.integer().and_then(|i| u8::try_from(i).ok()).ok_or_else(bad)?;
            let index = index.integer().and_then(|i| u64::try_from(i).ok()).ok_or_else(bad)?;
            let value = value.number().ok_or_else(bad)?;
            if id as usize >= streams {
                return Err(core(format!(
                    "snapshot field draw_overlay names stream {id}, and there \
                     are {streams}."
                )));
            }
            let kind = match kind {
                0 => crate::rng::DrawKind::Uniform,
                1 => crate::rng::DrawKind::Normal,
                other => {
                    return Err(core(format!(
                        "snapshot field draw_overlay has kind {other}; a kind is 0 \
                         (uniform) or 1 (normal)."
                    )))
                }
            };
            inner.patch_draw(id, kind, index, value);
        }

        // The per-day accumulators, at the widths this build writes. Without
        // dividends the snapshot leaves the dividend's slot, the last, out.
        let width = if inner.carries_dividends() {
            crate::market::factors::COMPONENT_COUNT
        } else {
            crate::market::factors::DIVIDEND_SLOT
        };
        let tick_width = crate::market::factors::TICK_COMPONENT_COUNT;
        let attribution = read_buffer(snapshot, "", "attribution")?;
        let components = read_buffer(snapshot, "", "tick_components")?;
        let fundamental = read_buffer(snapshot, "", "tick_fundamental")?;
        let anchor = read_buffer(snapshot, "", "tick_anchor")?;
        check_len("attribution", attribution.len(), n * width)?;
        check_len("tick_components", components.len(), n * tick_width)?;
        check_len("tick_fundamental", fundamental.len(), n)?;
        check_len("tick_anchor", anchor.len(), n)?;
        inner
            .restore_day_state(&attribution, &components, &fundamental, &anchor)
            .map_err(core)?;
        inner
            .restore_noise_split(
                &read_buffer(snapshot, "", "noise_parts")?,
                &read_buffer(snapshot, "", "noise_own_scale2")?,
            )
            .map_err(core)?;
        // Required on a model with a night split, refused on any other by
        // the key check above.
        if inner.night_split_on() {
            inner
                .restore_innovation_day(&read_buffer(snapshot, "", "innovation_day")?)
                .map_err(core)?;
        }
        inner.set_jump_move(&read_buffer(snapshot, "", "jump_move")?).map_err(core)?;
        let market_open = read_bool(snapshot, "", "market_open")?;
        inner.set_volume_state(read_finite(snapshot, "", "volume_state")?);
        inner.set_universe_stress(read_finite(snapshot, "", "universe_stress")?);
        let pending_jump = read_buffer(snapshot, "", "pending_jump")?;
        let pending_overnight = read_buffer(snapshot, "", "pending_overnight")?;
        // Absent means no jump's fair-value shift was waiting.
        let pending_fair_value = match snapshot.get("pending_fair_value") {
            Some(_) => read_buffer(snapshot, "", "pending_fair_value")?,
            None => Vec::new(),
        };
        // Absent means no ex-date move was waiting (`dividend_payout_share`;
        // the key check refuses it on a model without dividends).
        let pending_dividend = match snapshot.get("pending_dividend") {
            Some(_) => read_buffer(snapshot, "", "pending_dividend")?,
            None => Vec::new(),
        };
        inner.set_volume_idio(&read_buffer(snapshot, "", "volume_idio")?).map_err(core)?;
        inner
            .set_sector_day(
                &read_buffer(snapshot, "", "sector_day_factor")?,
                read_finite(snapshot, "", "sector_target_day")?,
            )
            .map_err(core)?;
        if inner.carries_fair_value_offsets() {
            inner
                .set_fair_value_offsets(&read_buffer(snapshot, "", "fair_value_offset")?)
                .map_err(core)?;
            inner.set_opening_z(&read_buffer(snapshot, "", "opening_z")?).map_err(core)?;
            if inner.params().market_prehistory_valuation != 0.0 {
                inner
                    .set_opening_carry(&read_buffer(snapshot, "", "opening_carry")?)
                    .map_err(core)?;
            }
        }
        // The per-name state of the dial-gated mechanisms. The key check
        // above has required each exactly where this engine's model sets
        // its dial, so a key read here is present.
        if inner.carries_dividends() {
            inner.set_dividend_states(&read_buffer(snapshot, "", "dividend")?).map_err(core)?;
        }
        if inner.carries_buyback_log_shares() {
            inner
                .set_buyback_log_shares(&read_buffer(snapshot, "", "buyback_log_shares")?)
                .map_err(core)?;
        }
        if inner.carries_earnings() {
            inner.set_earnings_key(read_u64(snapshot, "", "earnings_key")?);
        }
        if inner.carries_night_market_factor() {
            inner.set_night_market_factor(read_finite(snapshot, "", "night_market_factor")?);
        }
        if inner.carries_earnings_withheld() {
            inner
                .set_earnings_withheld(&read_buffer(snapshot, "", "earnings_withheld")?)
                .map_err(core)?;
        }
        if inner.carries_idio_vol_state() {
            inner
                .set_idio_vol_state(
                    &read_buffer(snapshot, "", "idio_variance")?,
                    &read_buffer(snapshot, "", "idio_jump_pending")?,
                    &read_buffer(snapshot, "", "idio_jump_var_pending")?,
                )
                .map_err(core)?;
        }
        inner.set_sector_variance(&read_buffer(snapshot, "", "sector_variance")?).map_err(core)?;
        inner.set_jump_excitation(&read_buffer(snapshot, "", "jump_excitation")?).map_err(core)?;
        let not_news = || core("snapshot field session_news must be a list of dicts".into());
        let items = field(snapshot, "", "session_news")?.items().ok_or_else(not_news)?;
        let mut events = Vec::with_capacity(items.len());
        for item in items {
            let SnapshotValue::Map(d) = item else {
                return Err(not_news());
            };
            check_keys("snapshot field session_news[]", d, NEWS_KEYS)?;
            events.push(NewsEvent {
                company_id: read_opt_str(d, "session_news[].", "ticker")?,
                sector: read_opt_str(d, "session_news[].", "sector")?,
                price_impact: read_opt_number(d, "session_news[].", "price_impact")?,
            });
        }
        inner.set_session_news(events);
        inner.set_forced_flow_spent(read_finite(snapshot, "", "forced_flow_spent")?);
        inner.set_market_vol_log_level(read_finite(snapshot, "", "market_vol_log_level")?);
        inner.set_vix_log_level(read_finite(snapshot, "", "vix_log_level")?);
        if inner.params().vix_anchor_memory != 0.0 {
            inner.set_vix_anchor_slow(read_finite(snapshot, "", "vix_anchor_slow")?);
        }
        // A snapshot from before the anchor was carried keeps the one this
        // engine derived, which is what every restore read until then.
        if snapshot.get("vix_anchor").is_some() {
            let anchor = read_finite(snapshot, "", "vix_anchor")?;
            if !(anchor > 0.0) {
                return Err(SnapshotError::new(
                    SnapshotErrorKind::Fields,
                    format!("snapshot field vix_anchor must be above 0, got {anchor}"),
                ));
            }
            inner.set_vix_anchor(anchor);
        }
        // The cycle's volatility multiplier (`market_vol_cycle_ratio`), unset
        // where the snapshot carries none: taken before the first close set
        // it.
        let cycle_log = match snapshot.get("market_vol_cycle_log") {
            Some(_) => Some(read_finite(snapshot, "", "market_vol_cycle_log")?),
            None => None,
        };
        inner.set_market_vol_cycle_log(cycle_log);
        if inner.carries_vix_stress_memory() {
            inner.set_vix_stress_memory(read_finite(snapshot, "", "vix_stress_memory")?);
        }
        // The crisis episode: -1 is the epicentre `none`, and -2 is no pin.
        let in_episode = read_bool(snapshot, "", "crisis_in_episode")?;
        let sessions_under: i64 = read_int(snapshot, "", "crisis_sessions_under", "an integer")?;
        let epicentre: i32 = read_int(snapshot, "", "crisis_epicentre", "an integer")?;
        let pin: i32 = read_int(snapshot, "", "crisis_epicentre_pin", "an integer")?;
        if sessions_under < 0 || epicentre < -1 || pin < -2 {
            return Err(core(format!(
                "this snapshot's crisis episode is out of range: \
                 crisis_sessions_under {sessions_under} (from 0), \
                 crisis_epicentre {epicentre} (from -1, which is none) and \
                 crisis_epicentre_pin {pin} (from -2, which is no pin)."
            )));
        }
        inner.set_crisis_episode_raw(
            in_episode,
            sessions_under,
            epicentre,
            if pin == -2 { None } else { Some(pin) },
        );
        // Absent means no forced close was pending, and no pin was standing
        // today, when it was taken.
        let pending = match snapshot.get("vix_sets_variance_pending") {
            Some(_) => read_bool(snapshot, "", "vix_sets_variance_pending")?,
            None => false,
        };
        inner.set_vix_sets_variance_pending(pending);
        let pins: u16 = match snapshot.get("macro_pins_today") {
            Some(_) => read_int(snapshot, "", "macro_pins_today", "an integer from 0 to 65535")?,
            None => 0,
        };
        // The spread a spread pin holds, present exactly while its mark is.
        let spread: Option<f64> = match snapshot.get("pinned_corporate_spread") {
            Some(_) => Some(read_finite(snapshot, "", "pinned_corporate_spread")?),
            None => None,
        };
        if spread.is_some() != (pins & crate::engine::PIN_SPREAD != 0) {
            return Err(core(
                "this snapshot carries pinned_corporate_spread without its \
                 mark in macro_pins_today, or the mark without the spread. \
                 The engine writes both or neither."
                    .into(),
            ));
        }
        inner.set_macro_pins_today(pins, spread.unwrap_or(0.0));
        // The central bank's and the curve's dial-gated state. The key check
        // has required each exactly where this engine's model sets its dial.
        let gated_finite = |key: &str, carried: bool| -> Result<Option<f64>> {
            if carried { Ok(Some(read_finite(snapshot, "", key)?)) } else { Ok(None) }
        };
        let p = inner.params().clone();
        inner
            .set_stress_vix_max(gated_finite("fed_stress_vix_max", p.fed_stress_cut != 0.0)?)
            .map_err(core)?;
        inner
            .set_stress_hold_age(gated_finite("fed_stress_hold_age", p.fed_stress_hold != 0.0)?)
            .map_err(core)?;
        inner
            .set_policy_path(gated_finite("treasury_policy_path", p.treasury_path_pricing != 0.0)?)
            .map_err(core)?;
        let drawdown = if p.fed_drawdown_hold != 0.0 || p.market_vol_cycle_recovery_release != 0.0 {
            Some((
                read_buffer(snapshot, "", "fed_drawdown_returns")?,
                read_finite(snapshot, "", "fed_drawdown_mcap_prev")?,
            ))
        } else {
            None
        };
        inner.set_drawdown_state(drawdown).map_err(core)?;
        inner
            .set_policy_anticipation_priced(
                gated_finite("policy_anticipation_priced", p.policy_anticipation != 0.0)?)
            .map_err(core)?;
        // Absent means no session held a live mark when it was taken.
        let live: Option<[f64; 6]> = match snapshot.get("rate_live_marks") {
            Some(_) => {
                let values = read_numbers(snapshot, "", "rate_live_marks")?;
                if let Some(bad) = values.iter().find(|v| !v.is_finite()) {
                    return Err(core(format!(
                        "this snapshot's rate_live_marks holds {bad}; the live mark is \
                         six finite yields.")));
                }
                let arr: [f64; 6] = values.as_slice().try_into().map_err(|_| {
                    core(format!(
                        "this snapshot's rate_live_marks carries {} values; the live mark \
                         is six: the open's projection and the session's, three yields each.",
                        values.len()))
                })?;
                Some(arr)
            }
            None => None,
        };
        inner.set_rate_live_marks(live).map_err(core)?;
        // Absent means no pin had priced a VIX move that session.
        let jump = match snapshot.get("pinned_vix_jump") {
            Some(_) => read_finite(snapshot, "", "pinned_vix_jump")?,
            None => 0.0,
        };
        inner.set_pinned_vix_jump(jump);
        // Absent means no open had drawn a day scale (a snapshot at a close).
        let day_scale = match snapshot.get("market_day_scale") {
            Some(_) => read_finite(snapshot, "", "market_day_scale")?,
            None => 1.0,
        };
        if day_scale <= 0.0 {
            return Err(core(format!(
                "this snapshot's market_day_scale is {day_scale}; the engine writes a \
                 positive finite multiplier.")));
        }
        inner.set_market_day_scale(day_scale);
        // The price index (`index_level_listed`): required while the switch is
        // set, which the key check above has held it to.
        let index = match snapshot.get("index_divisor") {
            Some(_) => {
                let values = read_numbers(snapshot, "", "index_divisor")?;
                let pair: [f64; 2] = values.as_slice().try_into().map_err(|_| {
                    core(format!(
                        "this snapshot's index_divisor carries {} values; it is two, the \
                         divisor and the last close's level.",
                        values.len()))
                })?;
                Some(pair)
            }
            None => None,
        };
        inner.set_index_state(index).map_err(core)?;
        // Absent means no session held a live VIX when it was taken.
        let live_vix = match snapshot.get("vix_live") {
            Some(_) => Some(read_finite(snapshot, "", "vix_live")?),
            None => None,
        };
        inner.set_vix_live_mark(live_vix).map_err(core)?;
        // Absent means no close had computed a forecast.
        let forecast = match snapshot.get("forecast") {
            Some(_) => Some(
                crate::derivatives::Forecast::from_words(&read_buffer(snapshot, "", "forecast")?)
                    .map_err(core)?,
            ),
            None => None,
        };
        inner.set_forecast(forecast).map_err(core)?;
        // The index futures (`futures_index_listed`, `night_session_steps`):
        // required while the switch is set, which the key check above has
        // held them to.
        {
            let words = match snapshot.get("futures") {
                Some(_) => Some(read_buffer(snapshot, "", "futures")?),
                None => None,
            };
            let rng = match snapshot.get("derivatives_rng") {
                Some(_) => {
                    let r = read_numbers(snapshot, "", "derivatives_rng")?;
                    if r.len() != 5 {
                        return Err(core(format!(
                            "snapshot field derivatives_rng must be 5 numbers, got {}", r.len())));
                    }
                    if let Some(bad) = r[3..].iter().find(|c| {
                        !c.is_finite() || **c < 0.0 || c.fract() != 0.0 || **c > 9.007_199_254_740_992e15
                    }) {
                        return Err(core(format!(
                            "snapshot field derivatives_rng holds a draw count of {bad}. Each is a \
                             count of draws taken: a whole number from 0."
                        )));
                    }
                    if r[1].to_bits() & 1 == 0 {
                        return Err(core(
                            "snapshot field derivatives_rng holds an even increment; every PCG \
                             increment is odd, so the word was changed on the way"
                                .to_string(),
                        ));
                    }
                    Some(crate::rng::RngState {
                        state: r[0].to_bits(),
                        increment: r[1].to_bits(),
                        spare: if r[2].is_nan() { None } else { Some(r[2]) },
                        uniforms: r[3] as u64,
                        normals: r[4] as u64,
                    })
                }
                None => None,
            };
            let book = match snapshot.get("futures_book") {
                Some(_) => Some(book_from(read_map(snapshot, "", "futures_book")?)?),
                None => None,
            };
            let night = match snapshot.get("night_bridge") {
                Some(_) => Some(read_buffer(snapshot, "", "night_bridge")?),
                None => None,
            };
            inner
                .set_futures_state(words.as_deref(), rng, book, night.as_deref())
                .map_err(core)?;
        }
        // The VIX's fear memory (`vix_fear_uptake`): required while the switch
        // is set, which the key check above has held it to.
        if inner.carries_vix_fear() {
            inner.set_vix_fear(read_finite(snapshot, "", "vix_fear")?);
        }
        // The VIX futures (`futures_vix_listed`): required while the switch
        // is set, which the key check above has held them to.
        {
            let words = match snapshot.get("vix_futures") {
                Some(_) => Some(read_buffer(snapshot, "", "vix_futures")?),
                None => None,
            };
            let book = match snapshot.get("vix_futures_book") {
                Some(_) => Some(book_from(read_map(snapshot, "", "vix_futures_book")?)?),
                None => None,
            };
            inner.set_vix_futures_state(words.as_deref(), book).map_err(core)?;
        }
        // The rate futures (`futures_rates_listed`), the same.
        {
            let words = match snapshot.get("rate_futures") {
                Some(_) => Some(read_buffer(snapshot, "", "rate_futures")?),
                None => None,
            };
            let book = match snapshot.get("rate_futures_book") {
                Some(_) => Some(book_from(read_map(snapshot, "", "rate_futures_book")?)?),
                None => None,
            };
            inner.set_rate_futures_state(words.as_deref(), book).map_err(core)?;
        }
        // The oil futures (`futures_oil_listed`), the same.
        {
            let words = match snapshot.get("oil_futures") {
                Some(_) => Some(read_buffer(snapshot, "", "oil_futures")?),
                None => None,
            };
            let book = match snapshot.get("oil_futures_book") {
                Some(_) => Some(book_from(read_map(snapshot, "", "oil_futures_book")?)?),
                None => None,
            };
            inner.set_oil_futures_state(words.as_deref(), book).map_err(core)?;
        }
        // The contracts' margin (`margin_scan_coverage`).
        {
            let words = match snapshot.get("margin") {
                Some(_) => Some(read_buffer(snapshot, "", "margin")?),
                None => None,
            };
            inner.set_margin_state(words.as_deref()).map_err(core)?;
        }
        // Oil's long factor (`oil_target_drift_sd`).
        {
            let words = match snapshot.get("oil_target_drift") {
                Some(_) => Some(read_buffer(snapshot, "", "oil_target_drift")?),
                None => None,
            };
            inner.set_oil_drift_state(words.as_deref()).map_err(core)?;
        }
        inner.set_nominal_output_base(read_finite(snapshot, "", "nominal_output_base")?);
        let variance = read_numbers(snapshot, "", "market_variance")?;
        if variance.len() != 6 || variance.iter().any(|v| !v.is_finite()) {
            return Err(core(format!(
                "snapshot field market_variance must be six finite numbers \
                 (variance, day factor, the fast and slow components, the \
                 previous day's factor and the smoothed VIX), got {variance:?}"
            )));
        }
        inner.set_market_variance_state_with_components(
            variance[0], variance[1], variance[2], variance[3], variance[4], variance[5]);
        // The market factor's return memory, after the variance state, whose
        // restore resets it.
        if inner.carries_market_vol_leverage() {
            inner.set_market_vol_leverage_memory(
                read_finite(snapshot, "", "market_vol_leverage_memory")?);
        }

        // The macro chain's state, every field by name.
        let d = read_map(snapshot, "", "economy")?;
        {
            let economy = inner.economy_mut();
            macro_rules! econ_get {
                ($($field:ident),* $(,)?) => {
                    $(economy.$field = economy_value(d, stringify!($field))?;)*
                };
            }
            economy_scalars!(econ_get);
            let trend = read_numbers(d, "economy.", "gdp_trend")?;
            if trend.len() != 4 || trend.iter().any(|v| !v.is_finite()) {
                return Err(core(format!(
                    "snapshot field economy.gdp_trend must be 4 finite numbers, got {trend:?}"
                )));
            }
            economy.gdp_trend = [trend[0], trend[1], trend[2], trend[3]];
            let name = read_str(d, "economy.", "cycle_phase")?;
            economy.cycle_phase = CyclePhase::from_name(name).ok_or_else(|| {
                core(format!(
                    "snapshot field economy.cycle_phase is {name:?}, which is not a cycle phase"
                ))
            })?;
        }
        let params = inner.params().clone();
        if params.earnings_cycle_depth != 0.0 {
            inner.economy_mut().earnings_cycle = read_finite(d, "economy.", "earnings_cycle")?;
        }
        if inner.carries_vix_feedback() {
            inner.economy_mut().vix_feedback = read_finite(d, "economy.", "vix_feedback")?;
        }
        if params.qe_pe_stock_gain != 0.0 {
            inner.economy_mut().qe_assets_ratio = read_finite(d, "economy.", "qe_assets_ratio")?;
        }
        if inner.carries_fed_put() {
            let e = inner.economy_mut();
            e.intermeeting_return = read_finite(d, "economy.", "intermeeting_return")?;
            e.fed_put = read_finite(d, "economy.", "fed_put")?;
            e.fed_put_owed = read_finite(d, "economy.", "fed_put_owed")?;
            e.fed_put_mcap_prev = read_finite(d, "economy.", "fed_put_mcap_prev")?;
        }
        if inner.carries_spread_equity_gap() {
            inner.economy_mut().spread_equity_gap = read_finite(d, "economy.", "spread_equity_gap")?;
        }
        // The market's cycle nowcast (`cycle_nowcast_accuracy`), before the
        // anticipation that reads it: the belief, and its generator as
        // (state, increment, spare, uniforms, normals).
        if params.cycle_nowcast_accuracy != 0.0 {
            let belief = read_numbers(d, "economy.", "cycle_nowcast")?;
            if belief.len() != 5 || belief.iter().any(|v| !v.is_finite()) {
                return Err(core(format!(
                    "snapshot field economy.cycle_nowcast must be 5 finite numbers, got {belief:?}"
                )));
            }
            let r = read_numbers(snapshot, "", "cycle_nowcast_rng")?;
            if r.len() != 5 {
                return Err(core(format!(
                    "snapshot field cycle_nowcast_rng must be 5 numbers, got {}", r.len())));
            }
            // The uniform and normal counts, held as draw_counts are.
            if let Some(bad) = r[3..]
                .iter()
                .find(|c| !c.is_finite() || **c < 0.0 || c.fract() != 0.0 || **c > 9.007_199_254_740_992e15)
            {
                return Err(core(format!(
                    "snapshot field cycle_nowcast_rng holds a draw count of {bad}. Each is a \
                     count of draws taken: a whole number from 0."
                )));
            }
            let rng = crate::rng::RngState {
                state: r[0].to_bits(),
                increment: r[1].to_bits(),
                spare: if r[2].is_nan() { None } else { Some(r[2]) },
                uniforms: r[3] as u64,
                normals: r[4] as u64,
            };
            inner
                .set_cycle_nowcast([belief[0], belief[1], belief[2], belief[3], belief[4]], Some(rng))
                .map_err(core)?;
        }
        // Derived from the phase and the level just restored.
        inner.refresh_earnings_anticipation();
        if inner.carries_cycle_history() {
            let kind = "a list of cycle phases";
            let names = read_items(d, "economy.", "cycle_history", kind)?;
            let mut history = Vec::with_capacity(names.len());
            for name in names {
                let SnapshotValue::Str(name) = name else {
                    return Err(wrong_item("economy.", "cycle_history", kind, name));
                };
                history.push(CyclePhase::from_name(name).ok_or_else(|| {
                    core(format!(
                        "snapshot field economy.cycle_history holds {name:?}, \
                         which is not a cycle phase"
                    ))
                })?);
            }
            inner.set_cycle_history(history).map_err(core)?;
        }
        // The drawn publication schedule (`cycle_publication_lag_draw`).
        if params.cycle_publication_lag_draw != 0.0 {
            let block = read_map(d, "economy.", "cycle_publication")?;
            check_keys("snapshot field economy.cycle_publication", block, CYCLE_PUBLICATION_KEYS)?;
            inner.set_cycle_publication(cycle_publication_from(block)?).map_err(core)?;
        }
        // `D` and the last `A - e` (`earnings_anticipation_drift_share`).
        if inner.carries_anticipation_drift() {
            let drift = read_finite(d, "economy.", "anticipation_drift")?;
            let raw = read_finite(d, "economy.", "anticipation_raw")?;
            inner.set_anticipation_drift(drift, raw).map_err(core)?;
        }
        if params.unemployment_adjustment_half_life != 0.0 {
            inner.economy_mut().unemployment_impulse =
                read_finite(d, "economy.", "unemployment_impulse")?;
        }
        if params.oil_pushes_in_target != 0.0 {
            inner.economy_mut().oil_push_level = read_finite(d, "economy.", "oil_push_level")?;
        }
        if params.usd_mean_reversion != 0.0 {
            inner.economy_mut().usd_haven_level = read_finite(d, "economy.", "usd_haven_level")?;
        }
        let gdp_publication = if params.gdp_publication_lag != 0.0 {
            let block = read_map(d, "economy.", "gdp_publication")?;
            check_keys("snapshot field economy.gdp_publication", block, GDP_PUBLICATION_KEYS)?;
            Some(gdp_publication_from(block)?)
        } else {
            None
        };

        let bank_d = read_map(snapshot, "", "central_bank")?;
        {
            let at = "central_bank.";
            let bank = inner.central_bank_mut();
            bank.last_meeting_date = read_int(bank_d, at, "last_meeting_date", "an integer")?;
            bank.next_meeting_date = read_int(bank_d, at, "next_meeting_date", "an integer")?;
            bank.target_inflation = read_finite(bank_d, at, "target_inflation")?;
            bank.target_unemployment = read_finite(bank_d, at, "target_unemployment")?;
            bank.qe_active = read_bool(bank_d, at, "qe_active")?;
            bank.qe_monthly_purchases = read_finite(bank_d, at, "qe_monthly_purchases")?;
            bank.hawkish_dovish_score = read_finite(bank_d, at, "hawkish_dovish_score")?;
            let name = read_str(bank_d, at, "forward_guidance")?;
            bank.forward_guidance = ForwardGuidance::from_name(name).ok_or_else(|| {
                core(format!(
                    "snapshot field central_bank.forward_guidance is {name:?}, \
                     which is not a forward guidance"
                ))
            })?;
        }
        // The marks name the days THIS engine opened, and a restore replaces
        // the run.
        inner.clear_day_marks();
        let count: i64 = read_int(snapshot, "", "day_count", "an integer")?;
        let day_count = u32::try_from(count).map_err(|_| {
            core(format!(
                "this snapshot's day_count is {count}. It counts the days \
                 the engine has closed, from 0."
            ))
        })?;
        // The day label the draws and the fills carry, and the valuation's
        // clock: each from the snapshot when it carries one, and otherwise
        // the day the counter and the open flag give.
        let usual = default_day(day_count, market_open);
        let label: i64 = match snapshot.get("current_day") {
            Some(_) => read_int(snapshot, "", "current_day", "an integer")?,
            None => usual,
        };
        let elapsed: i64 = match snapshot.get("elapsed_days") {
            Some(_) => read_int(snapshot, "", "elapsed_days", "an integer")?,
            None => usual,
        };
        if label < 0 || elapsed < 0 {
            return Err(core(format!(
                "this snapshot's day numbers are {label} (current_day) and \
                 {elapsed} (elapsed_days). Days count from 0."
            )));
        }
        inner.set_day_label(label);
        inner.set_elapsed_days(elapsed);
        inner.set_session_ticks(read_int(snapshot, "", "session_tick", "a whole number")?);
        // The fair-value inputs. Absent means they had not moved, so the
        // figures each company was built with go back.
        match snapshot.get("fundamentals") {
            Some(_) => {
                let block = read_map(snapshot, "", "fundamentals")?;
                check_keys("snapshot field fundamentals", block, FUNDAMENTALS_KEYS)?;
                let (eps, book, growth) = (
                    read_buffer(block, "fundamentals.", "eps")?,
                    read_buffer(block, "fundamentals.", "book_value_per_share")?,
                    read_buffer(block, "fundamentals.", "revenue_growth")?,
                );
                if eps.iter().chain(&book).chain(&growth).any(|v| v.is_infinite()) {
                    return Err(core(
                        "this snapshot's fundamentals hold an infinite value. Each \
                         is finite, or NaN where the company has none."
                            .into(),
                    ));
                }
                inner.set_fundamentals(&eps, &book, &growth).map_err(core)?;
            }
            None => inner.reset_fundamentals(),
        }
        // The share counts. Absent means they had not moved, so the counts
        // each company was built with go back. Written raw: the market caps
        // and the index divisor come from the snapshot.
        match snapshot.get("shares_outstanding") {
            Some(_) => {
                let shares = read_buffer(snapshot, "", "shares_outstanding")?;
                inner.restore_shares_outstanding(&shares).map_err(core)?;
            }
            None => inner.reset_shares_outstanding(),
        }
        if inner.carries_garch_cascade() {
            inner.set_garch_cascade(&read_buffer(snapshot, "", "garch_cascade")?).map_err(core)?;
        }
        if let Some(state) = gdp_publication {
            inner.set_gdp_publication(state).map_err(core)?;
        }

        // The rate instruments, for the same tickers in the same order. The
        // key check has already required the block exactly when this engine
        // holds them.
        let held: Vec<String> =
            inner.rates().instruments.iter().map(|i| i.spec.ticker.to_string()).collect();
        if !held.is_empty() {
            let block = read_map(snapshot, "", "rates")?;
            check_keys("snapshot field rates", block, RATES_KEYS)?;
            let kind = "a list of dicts";
            let items = read_items(block, "rates.", "instruments", kind)?;
            let mut tickers = Vec::with_capacity(items.len());
            let mut values: Vec<[f64; RATE_STATE_FIELDS.len()]> = Vec::new();
            let mut wanted = vec!["ticker"];
            wanted.extend(RATE_STATE_FIELDS);
            for item in items {
                let SnapshotValue::Map(item) = item else {
                    return Err(wrong_item("rates.", "instruments", kind, item));
                };
                check_keys("a rate instrument in this snapshot", item, &wanted)?;
                tickers.push(read_str(item, "rates.instruments[].", "ticker")?.to_string());
                let mut row = [0.0; RATE_STATE_FIELDS.len()];
                for (k, name) in RATE_STATE_FIELDS.iter().enumerate() {
                    row[k] = read_number(item, "rates.instruments[].", name)?;
                }
                values.push(row);
            }
            if tickers != held {
                return Err(SnapshotError::new(
                    SnapshotErrorKind::Roster,
                    format!(
                        "the snapshot's rate instruments are [{}] and this engine \
                         holds [{}]. Columns are positional, so they must match.",
                        tickers.join(", "),
                        held.join(", ")
                    ),
                ));
            }
            let ig_spread = read_number(block, "rates.", "ig_spread")?;
            let last_corporate = read_number(block, "rates.", "last_corporate")?;
            let closed = read_bool(block, "rates.", "closed_since_open")?;
            let book = inner.rates_mut();
            for (inst, row) in book.instruments.iter_mut().zip(values) {
                set_rate_state(inst, &row);
            }
            book.ig_spread = ig_spread;
            book.last_corporate = last_corporate;
            book.closed_since_open = closed;
        }
        // The book is part of the state: restored when the snapshot carries
        // it, and pristine when it does not, so a restore never keeps the
        // orders or the consumed depth of the market it replaced.
        let book = match snapshot.get("book") {
            Some(_) => book_from(read_map(snapshot, "", "book")?)?,
            None => crate::agent_book::BookState::default(),
        };
        inner.set_book_state(book).map_err(core)?;
        // The population, on an engine built with one: the snapshot must
        // come from the same population (the key check above has already
        // required the block exactly when this engine holds one).
        if let Some(fingerprint) = inner.population().map(|p| p.fingerprint.clone()) {
            let block = read_map(snapshot, "", "population")?;
            check_keys("this snapshot's population", block, &["fingerprint", "tickers", "state"])?;
            let carried = read_str(block, "population.", "fingerprint")?;
            if carried != fingerprint {
                return Err(SnapshotError::new(
                    SnapshotErrorKind::Model,
                    format!(
                        "this snapshot was taken with population {carried}, and this engine \
                         holds population {fingerprint}. Build the engine with the population \
                         the snapshot came from."
                    ),
                ));
            }
            let kind = "a list of tickers";
            let mut tickers = Vec::new();
            for t in read_items(block, "population.", "tickers", kind)? {
                let SnapshotValue::Str(t) = t else {
                    return Err(wrong_item("population.", "tickers", kind, t));
                };
                tickers.push(t.clone());
            }
            let state = read_buffer(block, "population.", "state")?;
            inner.set_population_state(tickers, &state).map_err(core)?;
        }
        // What was written to each price since its last print is tape, not
        // state: the snapshot does not carry it.
        inner.forget_repriced();

        // Every field has been read. Nothing above touched the engine.
        *self = inner;
        Ok(DayLoop {
            day_count,
            market_open,
            pending_jump,
            pending_overnight,
            pending_fair_value,
            pending_dividend,
        })
    }
}

/// A snapshot's `economy.cycle_publication` block
/// (`cycle_publication_lag_draw`), every key present.
fn cycle_publication_from(d: &SnapshotMap) -> Result<crate::engine::CyclePublication> {
    let at = "economy.cycle_publication.";
    let phase = |key: &str, name: &str| -> Result<CyclePhase> {
        CyclePhase::from_name(name).ok_or_else(|| {
            SnapshotError::value(format!(
                "snapshot field {at}{key} holds {name:?}, which is not a cycle phase"
            ))
        })
    };
    let closes = read_items(d, at, "pending_closes", "a list of integers")?;
    let closes: Vec<i64> = closes
        .iter()
        .map(|v| v.integer().ok_or_else(|| wrong_item(at, "pending_closes", "a list of integers", v)))
        .collect::<Result<_>>()?;
    let kind = "a list of cycle phases";
    let names = read_items(d, at, "pending_phases", kind)?;
    if closes.len() != names.len() {
        return Err(SnapshotError::value(format!(
            "snapshot economy.cycle_publication has {} pending closes and {} pending \
             phases (cycle_publication_lag_draw)",
            closes.len(),
            names.len()
        )));
    }
    let mut pending = std::collections::VecDeque::with_capacity(closes.len());
    for (close, name) in closes.into_iter().zip(names) {
        let SnapshotValue::Str(name) = name else {
            return Err(wrong_item(at, "pending_phases", kind, name));
        };
        pending.push_back((close, phase("pending_phases", name)?));
    }
    Ok(crate::engine::CyclePublication {
        key: read_u64(d, at, "key")?,
        published: phase("published", read_str(d, at, "published")?)?,
        last_true: phase("last_true", read_str(d, at, "last_true")?)?,
        closes: read_int(d, at, "closes", "an integer")?,
        turns: read_u64(d, at, "turns")?,
        pending,
    })
}

/// A snapshot's `economy.gdp_publication` block (`gdp_publication_lag`),
/// every key present.
fn gdp_publication_from(d: &SnapshotMap) -> Result<GdpPublication> {
    let at = "economy.gdp_publication.";
    let days = read_items(d, at, "pending_days", "a list of integers")?;
    let days: Vec<i64> = days
        .iter()
        .map(|v| v.integer().ok_or_else(|| wrong_item(at, "pending_days", "a list of integers", v)))
        .collect::<Result<_>>()?;
    let values = read_numbers(d, at, "pending_values")?;
    if days.len() != values.len() {
        return Err(SnapshotError::value(format!(
            "snapshot economy.gdp_publication has {} pending release days and {} \
             pending figures (gdp_publication_lag)",
            days.len(),
            values.len()
        )));
    }
    Ok(GdpPublication {
        published: read_number(d, at, "published")?,
        quarter: read_int(d, at, "quarter", "an integer")?,
        count: read_int(d, at, "count", "a whole number")?,
        sum: read_number(d, at, "sum")?,
        pending: days.into_iter().zip(values).collect(),
    })
}

// ---------------------------------------------------------------------------
// The byte form
// ---------------------------------------------------------------------------

fn encode_len(len: usize, out: &mut Vec<u8>) {
    out.extend_from_slice(&(len as u64).to_le_bytes());
}

fn encode_map(map: &SnapshotMap, out: &mut Vec<u8>) {
    out.push(9);
    encode_len(map.len(), out);
    for (key, value) in map.iter() {
        encode_len(key.len(), out);
        out.extend_from_slice(key.as_bytes());
        encode(value, out);
    }
}

fn encode(value: &SnapshotValue, out: &mut Vec<u8>) {
    match value {
        SnapshotValue::None => out.push(0),
        SnapshotValue::Bool(false) => out.push(1),
        SnapshotValue::Bool(true) => out.push(2),
        SnapshotValue::Int(i) => {
            out.push(3);
            out.extend_from_slice(&i.to_le_bytes());
        }
        SnapshotValue::UInt(u) => {
            out.push(10);
            out.extend_from_slice(&u.to_le_bytes());
        }
        SnapshotValue::Float(f) => {
            out.push(4);
            out.extend_from_slice(&f.to_bits().to_le_bytes());
        }
        SnapshotValue::Str(s) => {
            out.push(5);
            encode_len(s.len(), out);
            out.extend_from_slice(s.as_bytes());
        }
        SnapshotValue::Bytes(b) => {
            out.push(6);
            encode_len(b.len(), out);
            out.extend_from_slice(b);
        }
        SnapshotValue::List(items) | SnapshotValue::Tuple(items) => {
            out.push(if matches!(value, SnapshotValue::List(_)) { 7 } else { 8 });
            encode_len(items.len(), out);
            for item in items {
                encode(item, out);
            }
        }
        SnapshotValue::Map(map) => encode_map(map, out),
    }
}

struct Decoder<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl Decoder<'_> {
    fn short(&self) -> SnapshotError {
        SnapshotError::encoding(format!(
            "this snapshot is cut short at byte {} of {}",
            self.at + MAGIC.len(),
            self.bytes.len() + MAGIC.len()
        ))
    }

    fn take(&mut self, n: usize) -> Result<&[u8]> {
        let end = self.at.checked_add(n).filter(|&end| end <= self.bytes.len());
        let Some(end) = end else {
            return Err(self.short());
        };
        let out = &self.bytes[self.at..end];
        self.at = end;
        Ok(out)
    }

    fn word(&mut self) -> Result<[u8; 8]> {
        let b = self.take(8)?;
        Ok([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])
    }

    /// A length, refused when it is longer than what is left, so a damaged
    /// length cannot ask for a huge allocation.
    fn len(&mut self) -> Result<usize> {
        let len = u64::from_le_bytes(self.word()?);
        let left = (self.bytes.len() - self.at) as u64;
        if len > left {
            return Err(self.short());
        }
        Ok(len as usize)
    }

    fn string(&mut self) -> Result<String> {
        let len = self.len()?;
        let at = self.at;
        let raw = self.take(len)?;
        String::from_utf8(raw.to_vec()).map_err(|_| {
            SnapshotError::encoding(format!(
                "this snapshot has a string at byte {} that is not UTF-8",
                at + MAGIC.len()
            ))
        })
    }

    fn value(&mut self, depth: usize) -> Result<SnapshotValue> {
        if depth > MAX_DEPTH {
            return Err(SnapshotError::encoding(format!(
                "this snapshot nests deeper than {MAX_DEPTH} levels"
            )));
        }
        let at = self.at;
        let tag = self.take(1)?[0];
        Ok(match tag {
            0 => SnapshotValue::None,
            1 => SnapshotValue::Bool(false),
            2 => SnapshotValue::Bool(true),
            3 => SnapshotValue::Int(i64::from_le_bytes(self.word()?)),
            4 => SnapshotValue::Float(f64::from_bits(u64::from_le_bytes(self.word()?))),
            10 => {
                // Canonical only: a value that fits an i64 is an `Int` (tag 3),
                // as `SnapshotValue::from_u64` and every snapshot write it, so
                // one snapshot has one byte form and decodes to equal trees.
                let u = u64::from_le_bytes(self.word()?);
                if i64::try_from(u).is_ok() {
                    return Err(SnapshotError::encoding(format!(
                        "this snapshot has an unsigned integer {u} at byte {}, which fits a \
                         signed one and is written as one (tag 3)",
                        at + MAGIC.len()
                    )));
                }
                SnapshotValue::UInt(u)
            }
            5 => SnapshotValue::Str(self.string()?),
            6 => {
                let len = self.len()?;
                SnapshotValue::Bytes(self.take(len)?.to_vec())
            }
            7 | 8 => {
                let len = self.len()?;
                let mut items = Vec::with_capacity(len);
                for _ in 0..len {
                    items.push(self.value(depth + 1)?);
                }
                if tag == 7 {
                    SnapshotValue::List(items)
                } else {
                    SnapshotValue::Tuple(items)
                }
            }
            9 => {
                let len = self.len()?;
                let mut map = SnapshotMap { entries: Vec::with_capacity(len) };
                for _ in 0..len {
                    let key = self.string()?;
                    if map.contains_key(&key) {
                        return Err(SnapshotError::encoding(format!(
                            "this snapshot has the key {key:?} twice in one map"
                        )));
                    }
                    let value = self.value(depth + 1)?;
                    map.entries.push((key, value));
                }
                SnapshotValue::Map(map)
            }
            other => {
                return Err(SnapshotError::encoding(format!(
                    "this snapshot has an unknown value tag {other} at byte {}",
                    at + MAGIC.len()
                )))
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> EngineSnapshot {
        use SnapshotValue as V;
        let mut inner = SnapshotMap::new();
        inner.insert("x", V::from_f64s(&[1.5, f64::from_bits(0x7ff8_dead_beef_0001)]));
        let mut m = SnapshotMap::new();
        m.insert("none", V::None);
        m.insert("t", V::Bool(true));
        m.insert("f", V::Bool(false));
        m.insert("i", V::Int(-7));
        m.insert("nan", V::Float(f64::from_bits(0x7ff0_0000_0000_0042)));
        m.insert("s", V::Str("épée".into()));
        m.insert("l", V::List(vec![V::Int(1), V::Float(2.0)]));
        m.insert("tu", V::Tuple(vec![V::Str("a".into()), V::None]));
        m.insert("m", V::Map(inner));
        EngineSnapshot::from_map(m)
    }

    #[test]
    fn the_byte_form_round_trips_bit_for_bit() {
        let s = sample();
        let bytes = s.to_bytes();
        let back = EngineSnapshot::from_bytes(&bytes).unwrap();
        assert_eq!(back, s);
        assert_eq!(back.to_bytes(), bytes);
        assert_eq!(back.fields().keys().collect::<Vec<_>>(),
                   ["none", "t", "f", "i", "nan", "s", "l", "tu", "m"]);
    }

    #[test]
    fn damaged_bytes_are_refused() {
        let bytes = sample().to_bytes();
        for cut in [0, 5, 8, 9, 20, bytes.len() - 1] {
            let err = EngineSnapshot::from_bytes(&bytes[..cut]).unwrap_err();
            assert_eq!(err.kind(), SnapshotErrorKind::Encoding, "{cut}: {err}");
        }
        let mut longer = bytes.clone();
        longer.push(0);
        assert!(EngineSnapshot::from_bytes(&longer).unwrap_err().to_string().contains("after its end"));
        let mut newer = bytes.clone();
        newer[7] = 2;
        assert!(EngineSnapshot::from_bytes(&newer).unwrap_err().to_string().contains("version 2"));
        let mut tag = bytes.clone();
        tag[8] = 42;
        assert!(EngineSnapshot::from_bytes(&tag).unwrap_err().to_string().contains("unknown value tag"));
        // A length far past the end asks for nothing.
        let mut huge = MAGIC.to_vec();
        huge.push(6);
        huge.extend_from_slice(&u64::MAX.to_le_bytes());
        assert_eq!(EngineSnapshot::from_bytes(&huge).unwrap_err().kind(), SnapshotErrorKind::Encoding);
        // A list nested past the limit.
        let mut deep = MAGIC.to_vec();
        for _ in 0..40 {
            deep.push(7);
            deep.extend_from_slice(&1u64.to_le_bytes());
        }
        deep.push(0);
        assert!(EngineSnapshot::from_bytes(&deep).unwrap_err().to_string().contains("deeper"));
        // A key twice in one map.
        let mut twice = MAGIC.to_vec();
        twice.push(9);
        twice.extend_from_slice(&2u64.to_le_bytes());
        for _ in 0..2 {
            twice.extend_from_slice(&1u64.to_le_bytes());
            twice.push(b'k');
            twice.push(0);
        }
        assert!(EngineSnapshot::from_bytes(&twice).unwrap_err().to_string().contains("twice"));
        // A top value that is not a map.
        let mut list = MAGIC.to_vec();
        list.push(0);
        assert!(EngineSnapshot::from_bytes(&list).unwrap_err().to_string().contains("NoneType"));
    }

    #[test]
    fn a_map_keeps_its_order_through_edits() {
        let mut m = SnapshotMap::new();
        m.insert("a", SnapshotValue::Int(1));
        m.insert("b", SnapshotValue::Int(2));
        m.insert("c", SnapshotValue::Int(3));
        assert_eq!(m.insert("a", SnapshotValue::Int(9)), Some(SnapshotValue::Int(1)));
        assert_eq!(m.remove("b"), Some(SnapshotValue::Int(2)));
        m.insert("b", SnapshotValue::Int(4));
        assert_eq!(m.keys().collect::<Vec<_>>(), ["a", "c", "b"]);
    }

    #[test]
    fn every_column_has_one_name() {
        for name in COLUMN_FIELDS {
            assert_eq!(column_name(parse_column(name).unwrap()), name);
        }
        for (field, name) in crate::engine::STATE_HASH_COLUMNS.iter().zip(COLUMN_FIELDS) {
            assert_eq!(column_name(*field), name, "the snapshot and the hash walk one order");
        }
    }
}
