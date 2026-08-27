//! Shared types, ported from `src/types/`.
//!
//! Only what the engine modules actually read. The reference implementation's types
//! directory is large and mostly describes UI and save-file shapes; mirroring
//! it wholesale would create a second source of truth for structures this
//! crate never touches.

/// Ported from `Difficulty` in the reference implementation.
///
/// Only `Hard` and `Expert` change behaviour in the spread model, but the
/// full set is carried so that a difficulty cannot be silently misrouted by
/// being absent from the enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Difficulty {
    Easy,
    Normal,
    Hard,
    Expert,
}
