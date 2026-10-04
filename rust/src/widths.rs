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
//! | [`COMPONENT_COUNT`] | 11 | one attribution row, [`crate::engine::Engine::attribution`] |
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

/// The streams an [`crate::engine::EngineRngState`] carries, in its field
/// order: market, economy, external, jumps, volume, volume_idio, news,
/// overnight, market_vol_level, crisis_epicentre. Equal to
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
        ("COMPONENT_COUNT", 11),
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
}
