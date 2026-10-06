//! The widths of the engine's state, for a host that sizes buffers by them.
//!
//! A host that saves an engine's state into flat arrays, or hands it across
//! a WebAssembly or FFI boundary, needs to know how many numbers each row
//! and each record holds. Take every such width from here rather than
//! deriving it. A width derived from something else, such as
//! `S_COMPONENT_KEYS.len() + 1` for an attribution row, keeps compiling when
//! the engine gains a slot and silently under-sizes the buffer.
//!
//! Every constant here is re-exported at the crate root, so
//! `tradefloor::COMPONENT_COUNT` and `tradefloor::widths::COMPONENT_COUNT`
//! are the same item.
//!
//! # A width that changes is a breaking change
//!
//! A test in this module pins every value below. Changing one fails it on
//! purpose: the change ships in a release that may break the API (a 0.x
//! minor while the crate is below 1.0), with its own CHANGELOG line giving
//! the old and new width. RELEASING.md and CONTRIBUTING.md say the same.
//!
//! | constant | value | what it sizes |
//! |---|---|---|
//! | [`COMPONENT_COUNT`] | 12 | one attribution row, [`crate::engine::Engine::attribution`] |
//! | [`TICK_COMPONENT_COUNT`] | 9 | one tick row, [`crate::engine::Engine::tick_components`] |
//! | [`NOISE_PART_COUNT`] | 3 | one row of [`crate::engine::Engine::noise_parts`] |
//! | [`RNG_STREAM_WIDTH`] | 5 | one [`crate::rng::RngState`] as numbers |
//! | [`ENGINE_RNG_STREAMS`] | 10 | the streams in [`crate::engine::EngineRngState`] |
//! | [`ENGINE_RNG_STATE_WIDTH`] | 50 | a whole [`crate::engine::EngineRngState`] as numbers |
//! | [`MARKET_VARIANCE_STATE_WIDTH`] | 6 | [`crate::engine::Engine::market_variance_state`] |
//! | [`TAKEN_WIDTH`] | 5 | one row of the agent book's `taken` table |
//! | [`SECTOR_COUNT`] | 12 | the sector keys, and every per-sector array |

pub use crate::agent_book::TAKEN_WIDTH;
pub use crate::market::factors::{COMPONENT_COUNT, TICK_COMPONENT_COUNT};

/// The numbers in one row of [`crate::engine::Engine::noise_parts`]: the
/// market, sector and idiosyncratic parts of a name's day noise.
pub const NOISE_PART_COUNT: usize = 3;

/// The numbers in one [`crate::rng::RngState`]: `state`, `increment`,
/// `spare` (a host stores its absence as it likes, NaN for instance),
/// `uniforms` and `normals`. Three before 0.7.0, which added the two draw
/// counts.
///
/// `state` and `increment` are full 64-bit integers. A host that packs them
/// into 64-bit floats, as JavaScript numbers are, loses their low bits for
/// values above 2^53; split each into two 32-bit halves instead.
pub const RNG_STREAM_WIDTH: usize = 5;

/// The streams an [`crate::engine::EngineRngState`] carries. Its words
/// ([`crate::engine::EngineRngState::to_words`]) run in stream-id order,
/// [`crate::engine::EngineRngState::STREAM_NAMES`]: market, economy,
/// external, jumps, volume, news, volume_idio, overnight, market_vol_level,
/// crisis_epicentre. Its fields declare volume_idio before news, so the
/// two orders differ in streams 5 and 6. Equal to
/// [`crate::rng::stream::COUNT`]. The one-shot opening stream
/// ([`crate::rng::stream::OPENING`]) is spent at construction and is not
/// carried. Seven before 0.7.0, eight in 0.7.x and ten from 0.8.0.
pub const ENGINE_RNG_STREAMS: usize = crate::rng::stream::COUNT;

/// The numbers in a whole [`crate::engine::EngineRngState`]:
/// [`ENGINE_RNG_STREAMS`] streams of [`RNG_STREAM_WIDTH`] each.
pub const ENGINE_RNG_STATE_WIDTH: usize = ENGINE_RNG_STREAMS * RNG_STREAM_WIDTH;

/// The numbers in [`crate::engine::Engine::market_variance_state`].
pub const MARKET_VARIANCE_STATE_WIDTH: usize = 6;

/// The sectors, in [`crate::sectors::keys`] order. Every per-sector array
/// the engine keeps (`sector_variance`, `sector_day_factor`) has this many
/// entries on a roster built from the shipped sector table.
pub const SECTOR_COUNT: usize = crate::sectors::SECTORS.len();

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::EngineRngState;
    use crate::rng::RngState;

    /// Every width a host sizes a buffer by, at the value the last release
    /// shipped. Change a row here only together with a CHANGELOG line that
    /// names the old and new width, in a release that may break the API.
    const PINNED: &[(&str, usize)] = &[
        // 11 through 0.9.x; 0.10.0 added the dividend slot, the last.
        ("COMPONENT_COUNT", 12),
        ("TICK_COMPONENT_COUNT", 9),
        ("NOISE_PART_COUNT", 3),
        ("RNG_STREAM_WIDTH", 5),
        ("ENGINE_RNG_STREAMS", 10),
        ("ENGINE_RNG_STATE_WIDTH", 50),
        ("MARKET_VARIANCE_STATE_WIDTH", 6),
        ("TAKEN_WIDTH", 5),
        ("SECTOR_COUNT", 12),
    ];

    fn actual() -> Vec<(&'static str, usize)> {
        vec![
            ("COMPONENT_COUNT", COMPONENT_COUNT),
            ("TICK_COMPONENT_COUNT", TICK_COMPONENT_COUNT),
            ("NOISE_PART_COUNT", NOISE_PART_COUNT),
            ("RNG_STREAM_WIDTH", RNG_STREAM_WIDTH),
            ("ENGINE_RNG_STREAMS", ENGINE_RNG_STREAMS),
            ("ENGINE_RNG_STATE_WIDTH", ENGINE_RNG_STATE_WIDTH),
            ("MARKET_VARIANCE_STATE_WIDTH", MARKET_VARIANCE_STATE_WIDTH),
            ("TAKEN_WIDTH", TAKEN_WIDTH),
            ("SECTOR_COUNT", SECTOR_COUNT),
        ]
    }

    #[test]
    fn a_state_width_changes_only_on_purpose() {
        assert_eq!(
            actual(),
            PINNED.to_vec(),
            "a state width moved. Every host that saves state into buffers of \
             these sizes breaks, and code that derived the width still \
             compiles. Update PINNED only together with a CHANGELOG line \
             giving the old and new width, in a release that may break the \
             API (RELEASING.md, \"State shapes and widths\")"
        );
    }

    /// The constants are tied to the types they describe, so a field added
    /// to a state struct, or a row widened, fails to compile here instead of
    /// leaving a constant that says the old width.
    #[test]
    fn the_constants_describe_the_types() {
        // Exhaustive destructuring: a new field is a compile error here.
        let RngState { state, increment, spare, uniforms, normals } = RngState {
            state: 0,
            increment: 0,
            spare: None,
            uniforms: 0,
            normals: 0,
        };
        let fields = [
            state as f64,
            increment as f64,
            spare.unwrap_or(f64::NAN),
            uniforms as f64,
            normals as f64,
        ];
        assert_eq!(fields.len(), RNG_STREAM_WIDTH);

        let s = RngState { state: 0, increment: 0, spare: None, uniforms: 0, normals: 0 };
        let EngineRngState {
            market,
            economy,
            external,
            jumps,
            volume,
            volume_idio,
            news,
            overnight,
            market_vol_level,
            crisis_epicentre,
        } = EngineRngState {
            market: s,
            economy: s,
            external: s,
            jumps: s,
            volume: s,
            volume_idio: s,
            news: s,
            overnight: s,
            market_vol_level: s,
            crisis_epicentre: s,
        };
        let streams = [
            market, economy, external, jumps, volume, volume_idio, news,
            overnight, market_vol_level, crisis_epicentre,
        ];
        assert_eq!(streams.len(), ENGINE_RNG_STREAMS);
        assert_eq!(streams.len() * RNG_STREAM_WIDTH, ENGINE_RNG_STATE_WIDTH);

        // The engine's own rows, typed by these constants. A row of another
        // width is a type mismatch and does not compile.
        let companies = crate::universe::random_universe(4, 1)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect();
        let engine = crate::engine::Engine::with_params_keeping_opening(
            1,
            companies,
            crate::economy::create_initial_economy_state(&Default::default()),
            crate::economy::create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
            crate::params::ModelParams::preset("pt-v20").unwrap(),
        );
        let _: &[[f64; COMPONENT_COUNT]] = engine.attribution();
        let _: &[[f64; TICK_COMPONENT_COUNT]] = engine.tick_components();
        let _: &[[f64; NOISE_PART_COUNT]] = engine.noise_parts();
        let (a, b, c, d, e, f) = engine.market_variance_state();
        assert_eq!([a, b, c, d, e, f].len(), MARKET_VARIANCE_STATE_WIDTH);
        assert_eq!(crate::sectors::keys().len(), SECTOR_COUNT);
        let book = crate::agent_book::BookState::default();
        let _: &[[f64; TAKEN_WIDTH]] = &book.taken;
    }

    #[test]
    fn rng_words_round_trip_at_the_published_widths() {
        use crate::engine::{SessionBuffer, SessionRequest};
        use crate::rng::stream;

        let companies = crate::universe::random_universe(4, 3)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect();
        let mut engine = crate::engine::Engine::new(
            3,
            companies,
            crate::economy::create_initial_economy_state(&Default::default()),
            crate::economy::create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
        );
        engine.open_market();
        let bell = crate::market::GameTime::new(9, 30, 3);
        engine.run_session(&SessionRequest::new(bell, 7), &mut SessionBuffer::new());

        let state = engine.rng_state();
        let words = state.to_words();
        assert_eq!(words.len(), ENGINE_RNG_STATE_WIDTH);
        assert_eq!(EngineRngState::from_words(&words), Ok(state));
        assert!(
            [state.market, state.economy].iter().any(|s| s.spare.is_some()),
            "the test should cover a stream holding a spare"
        );

        // Stream k sits at words k * W, in stream-id order.
        let w = RNG_STREAM_WIDTH;
        for (id, s) in [
            (stream::MARKET, state.market),
            (stream::NEWS, state.news),
            (stream::VOLUME_IDIO, state.volume_idio),
            (stream::CRISIS_EPICENTRE, state.crisis_epicentre),
        ] {
            let k = id as usize;
            let got: Vec<u64> = words[k * w..(k + 1) * w].iter().map(|x| x.to_bits()).collect();
            let want: Vec<u64> = s.to_words().iter().map(|x| x.to_bits()).collect();
            assert_eq!(got, want, "stream {id}");
        }

        // A restore through the words continues the same sequence.
        let mut restored = engine.clone();
        restored.set_rng_state(EngineRngState::from_words(&words).unwrap());
        for _ in 0..5 {
            assert_eq!(engine.draw_normal().to_bits(), restored.draw_normal().to_bits());
        }

        // Refusals.
        assert!(EngineRngState::from_words(&words[1..]).is_err());
        assert!(RngState::from_words(&words[..w + 1]).is_err());
        let mut even = state.market.to_words();
        even[1] = f64::NAN; // a canonical NaN: its bits are even
        assert!(RngState::from_words(&even).unwrap_err().contains("even"));
        let mut spare = state.market.to_words();
        spare[2] = f64::INFINITY;
        assert!(RngState::from_words(&spare).is_err());
        let mut count = state.market.to_words();
        count[3] = 1.5;
        assert!(RngState::from_words(&count).is_err());
        count[3] = -1.0;
        assert!(RngState::from_words(&count).is_err());
    }

    #[test]
    fn rng_streams_read_back_by_name_in_any_order() {
        use crate::engine::{SessionBuffer, SessionRequest};
        use crate::rng::stream;

        let companies = crate::universe::random_universe(4, 5)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect();
        let mut engine = crate::engine::Engine::new(
            5,
            companies,
            crate::economy::create_initial_economy_state(&Default::default()),
            crate::economy::create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
        );
        engine.open_market();
        let bell = crate::market::GameTime::new(9, 30, 3);
        engine.run_session(&SessionRequest::new(bell, 7), &mut SessionBuffer::new());
        engine.close_day(1);
        let state = engine.rng_state();

        // The names follow the stream ids, and name the fields they hold.
        let ids = [
            ("market", stream::MARKET),
            ("economy", stream::ECONOMY),
            ("external", stream::EXTERNAL),
            ("jumps", stream::JUMPS),
            ("volume", stream::VOLUME),
            ("news", stream::NEWS),
            ("volume_idio", stream::VOLUME_IDIO),
            ("overnight", stream::OVERNIGHT),
            ("market_vol_level", stream::MARKET_VOL_LEVEL),
            ("crisis_epicentre", stream::CRISIS_EPICENTRE),
        ];
        assert_eq!(ids.len(), ENGINE_RNG_STREAMS);
        for (name, id) in ids {
            assert_eq!(EngineRngState::STREAM_NAMES[id as usize], name);
        }
        let named = state.to_named_words();
        let words = state.to_words();
        let w = RNG_STREAM_WIDTH;
        for (k, (name, block)) in named.iter().enumerate() {
            assert_eq!(*name, EngineRngState::STREAM_NAMES[k]);
            let a: Vec<u64> = block.iter().map(|x| x.to_bits()).collect();
            let b: Vec<u64> = words[k * w..(k + 1) * w].iter().map(|x| x.to_bits()).collect();
            assert_eq!(a, b, "{name}");
        }
        let field = |name: &str| match name {
            "news" => state.news,
            "volume_idio" => state.volume_idio,
            "market" => state.market,
            _ => unreachable!(),
        };
        for name in ["news", "volume_idio", "market"] {
            let k = EngineRngState::STREAM_NAMES.iter().position(|n| *n == name).unwrap();
            assert_eq!(named[k].1.map(f64::to_bits), field(name).to_words().map(f64::to_bits));
        }
        assert_ne!(state.news, state.volume_idio, "the test needs the two to differ");

        // Any order reads back the same state.
        let mut pairs: Vec<(&str, &[f64])> =
            named.iter().map(|(n, b)| (*n, b.as_slice())).collect();
        pairs.reverse();
        assert_eq!(EngineRngState::from_named_words(&pairs), Ok(state));

        // Refusals: an unknown name, a repeat, a stream left out.
        let mut unknown = pairs.clone();
        unknown[0].0 = "volume-idio";
        assert!(EngineRngState::from_named_words(&unknown).unwrap_err().contains("unknown"));
        let mut twice = pairs.clone();
        twice[1] = twice[0];
        assert!(EngineRngState::from_named_words(&twice).unwrap_err().contains("twice"));
        assert!(EngineRngState::from_named_words(&pairs[1..]).unwrap_err().contains("missing"));
    }
}
