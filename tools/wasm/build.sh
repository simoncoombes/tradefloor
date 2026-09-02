#!/usr/bin/env bash
# Build the browser binding and check it against the native one.
#
# Needs rustup with wasm32-unknown-unknown and wasm-bindgen-cli at the same
# version as the wasm-bindgen crate:
#   rustup target add wasm32-unknown-unknown
#   cargo install wasm-bindgen-cli --version <crate version>
set -euo pipefail
cd "$(dirname "$0")/../.."

# rustup's toolchain first. A distro or Homebrew cargo ships the host target
# only, and the failure is a wall of "can't find crate for `core`" from every
# dependency rather than one clear message about the missing target.
export PATH="$HOME/.cargo/bin:$PATH"
rustc --print target-list >/dev/null 2>&1 || {
  echo "no rustc on PATH"; exit 1; }
rustup target list --installed 2>/dev/null | grep -qx wasm32-unknown-unknown || {
  echo "wasm32-unknown-unknown is not installed."
  echo "  rustup target add wasm32-unknown-unknown"; exit 1; }
OUT=${1:-dist/wasm}

cargo build --manifest-path rust/Cargo.toml --features wasm \
      --target wasm32-unknown-unknown --release
mkdir -p "$OUT"
wasm-bindgen --target nodejs --out-dir "$OUT" \
      rust/target/wasm32-unknown-unknown/release/tradefloor.wasm

W=rust/target/wasm32-unknown-unknown/release/tradefloor.wasm
echo "raw     $(wc -c < "$W") bytes"
echo "gzip    $(gzip -9 -c "$W" | wc -c) bytes"

node tools/wasm/check.mjs "$OUT"

# The nodejs target above is what check.mjs's `require` needs; a browser
# page needs the `web` target instead (an ES module with an async `init()`
# that fetches and instantiates the .wasm itself, rather than a
# `require()`-based CommonJS module) -- the two targets' generated JS are
# not interchangeable, so this is a second wasm-bindgen invocation into its
# own directory rather than a flag on the first one. tools/wasm/surgery.mjs
# imports from here; "$OUT/web" keeps it out of "$OUT" itself, where
# check.mjs's `require(...tradefloor.js)` would otherwise resolve the
# wrong target if the two were merged into one directory.
wasm-bindgen --target web --out-dir "$OUT/web" \
      rust/target/wasm32-unknown-unknown/release/tradefloor.wasm
