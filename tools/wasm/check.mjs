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

console.log(`  tradefloor ${pt.version()}`);
console.log(`  presets   ${pt.preset_names().join(', ')}`);
console.log(`  wasm      ${got}`);
console.log(`  expected  ${EXPECTED}`);

if (got !== EXPECTED) {
  console.error('\nDIVERGED: the browser build and the native build do not ' +
                'agree on the same fixed simulation.');
  process.exit(1);
}
console.log('\n  bit-identical with the native build.');
