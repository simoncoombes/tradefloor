// Does the browser build produce the same market as the native one?
//
// Both surfaces call ONE core function -- engine::fixed_simulation_digest --
// so this compares implementations of the ENGINE rather than two harnesses
// that must themselves be kept in step. See tests/test_wasm_parity.py.
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const dir = process.argv[2] ?? 'dist/wasm';
const pt = require(`${process.cwd()}/${dir}/tradefloor.js`);

// The case pinned in tests/test_wasm_parity.py. Re-recorded when the
// universe generator was reconciled, which re-drew the roster the case
// runs on, and checked through this script on wasm32-unknown-unknown
// against the native value: identical.
const EXPECTED = '1c5acabf07692228c840518b51240abe0e379fdd5272b9a4575206e8f93159ea';
const got = pt.priceDigest(12, 7, 3, 5, 65, 'pt-v3');

// The same probe on 64-bit seeds (0.8.5), pinned as HIGH_EXPECTED in
// tests/test_wasm_parity.py. A seed past Number.MAX_SAFE_INTEGER is a BigInt,
// since a Number cannot hold it exactly; a small seed may be either.
const HIGH_EXPECTED = '415ebce7634bff21c3c903f57f6a7b4c728b48d2379b2cb5eeea5f7bc9a16675';
const high = pt.priceDigest(12, 2n ** 64n - 1n, 2n ** 63n + 12345n, 5, 65, 'pt-v19');
const sameAsNumber = pt.priceDigest(12, 7n, 3n, 5, 65, 'pt-v3') === got;
let refusedUnsafe = false;
try {
  pt.priceDigest(12, 7, 2 ** 63, 5, 65, 'pt-v19');
} catch (e) {
  refusedUnsafe = String(e.message ?? e).includes('2**64 - 1');
}

console.log(`  tradefloor ${pt.version()}`);
console.log(`  presets   ${pt.preset_names().join(', ')}`);
console.log(`  wasm      ${got}`);
console.log(`  expected  ${EXPECTED}`);
console.log(`  wasm u64  ${high}`);
console.log(`  expected  ${HIGH_EXPECTED}`);

if (got !== EXPECTED || high !== HIGH_EXPECTED) {
  console.error('\nDIVERGED: the browser build and the native build do not ' +
                'agree on the same fixed simulation.');
  process.exit(1);
}
if (!sameAsNumber || !refusedUnsafe) {
  console.error('\nSEEDS: a BigInt seed must give the Number seed\'s market, ' +
                'and a Number past MAX_SAFE_INTEGER must be refused.');
  process.exit(1);
}
console.log('\n  bit-identical with the native build.');
