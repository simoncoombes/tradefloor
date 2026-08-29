//! # pretium is now tradefloor
//!
//! Depend on [`tradefloor`](https://docs.rs/tradefloor) instead; docs at
//! <https://tradefloor.dev>. Versions of `pretium` through 0.4.3 are the
//! real library, stay on crates.io forever, and reproduce exactly when
//! pinned — which is what a recorded result should keep doing. This
//! 0.5.0 shim re-exports tradefloor under the old crate name.

pub use tradefloor::*;
