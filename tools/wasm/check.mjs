// Does the browser build produce the same market as the native one?
//
// Both surfaces call ONE core function -- engine::fixed_simulation_digest --
// so this compares implementations of the ENGINE rather than two harnesses
// that must themselves be kept in step. See tests/test_wasm_parity.py.
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const dir = process.argv[2] ?? 'dist/wasm';
const pt = require(`${process.cwd()}/${dir}/pretium.js`);

// The case pinned in tests/test_wasm_parity.py.
const EXPECTED = '2b2f314181bde90a9ccabbc8232b03cba2e04221bee85501ffc78ae042cfd8f5';
const got = pt.priceDigest(12, 7, 3, 5, 65, 'pt-v3');

console.log(`  pretium   ${pt.version()}`);
console.log(`  presets   ${pt.preset_names().join(', ')}`);
console.log(`  wasm      ${got}`);
console.log(`  expected  ${EXPECTED}`);

if (got !== EXPECTED) {
  console.error('\nDIVERGED: the browser build and the native build do not ' +
                'agree on the same fixed simulation.');
  process.exit(1);
}
console.log('\n  bit-identical with the native build.');
