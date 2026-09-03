// Does the browser build produce the same market as the native one, and
// does draw surgery on a Sim (P11: fork, patchDraws, drawPatches,
// streamPositions, jumpAddress) behave the way the native build does?
//
// The first check below calls ONE core function -- engine::
// fixed_simulation_digest -- from both surfaces, so it compares
// implementations of the ENGINE rather than two harnesses that must
// themselves be kept in step. See tests/test_wasm_parity.py.
//
// The surgery checks have no such shared function to call: the frozen
// wasm surface (tradefloor-design's programme/P11-wasm-surgery.md) is
// five bindings on Sim, and none of them is a digest. So they hash
// `prices` with the same recipe tests/test_wasm_parity.py's
// `_prices_digest` uses -- sha256 over each price's big-endian bytes, in
// roster order -- and both sides pin the same two hex strings, measured
// once and compared by value rather than recomputed against each other.
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const dir = process.argv[2] ?? 'dist/wasm';
const pt = require(`${process.cwd()}/${dir}/tradefloor.js`);

let failed = false;

function report(name, ok, detail) {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${name}`);
  if (detail) console.log(detail);
  if (!ok) failed = true;
}

// The same recipe as tests/test_wasm_parity.py's `_prices_digest`:
// sha256 over each price's big-endian bytes, in order. Not
// fixed_simulation_digest's recipe -- that also hashes five macro
// fields a Sim has no getter for, so a patched Sim's digest can only be
// over the prices.
function pricesDigest(prices) {
  const h = createHash('sha256');
  const buf = Buffer.alloc(8);
  for (const p of prices) {
    buf.writeDoubleBE(p);
    h.update(buf);
  }
  return h.digest('hex');
}

console.log(`  tradefloor ${pt.version()}`);
console.log(`  presets   ${pt.preset_names().join(', ')}`);

// -- 1. The existing fixed-simulation digest ---------------------------
//
// The case pinned in tests/test_wasm_parity.py.
{
  const EXPECTED =
    '2b2f314181bde90a9ccabbc8232b03cba2e04221bee85501ffc78ae042cfd8f5';
  const got = pt.priceDigest(12, 7, 3, 5, 65, 'pt-v3');
  report('fixed_simulation_digest matches the native build', got === EXPECTED,
    `        wasm      ${got}\n        expected  ${EXPECTED}`);
}

// -- 2. Draw surgery: the case tests/test_wasm_parity.py's
//       SURGERY_CASE pins ------------------------------------------------
//
// size=12, universe_seed=7, seed=3, days=15, ticks=65, preset=pt-v13,
// unfiring day 13's market jump (stream id 3, kind 0 = uniform, address
// 169, value 1.0 = noise.NO_FIRE). pt-v13 is the first shipped preset
// with a nonzero jump_intensity_market; day 13 is the day that test
// file's SURGERY_CASE docstring found the market jump actually fires on
// under this seed, so this is not a comparison of two identical runs.
{
  const CASE = { size: 12, universeSeed: 7, seed: 3, days: 15, ticks: 65,
                 preset: 'pt-v13' };
  const DAY = 13;
  const JUMPS_STREAM = 3;
  const UNIFORM_KIND = 0;
  const NO_FIRE = 1.0;
  const CONTROL_DIGEST =
    '7b7cf4c6b14d7ff5bc84c65edbc88b6e3258a3f21cd76bdb9f04277c54c1cbc9';
  const SHOCK_DIGEST =
    '764be68c99799b9ab636d3876ef721d26331db95c9e9b796214046d9557b6455';

  const control = new pt.Sim(CASE.size, CASE.universeSeed, CASE.seed,
                              CASE.preset);
  control.runDays(CASE.days, CASE.ticks);
  const controlDigest = pricesDigest(control.prices);
  report('surgery control digest matches the native build',
    controlDigest === CONTROL_DIGEST,
    `        wasm      ${controlDigest}\n        expected  ${CONTROL_DIGEST}`);

  const shock = new pt.Sim(CASE.size, CASE.universeSeed, CASE.seed,
                            CASE.preset);
  const address = shock.jumpAddress(DAY);
  shock.patchDraws([JUMPS_STREAM, UNIFORM_KIND, address, NO_FIRE]);
  shock.runDays(CASE.days, CASE.ticks);
  const shockDigest = pricesDigest(shock.prices);
  report('surgery patched digest matches the native build',
    shockDigest === SHOCK_DIGEST,
    `        address   ${address} (native side pins 169)\n` +
    `        wasm      ${shockDigest}\n        expected  ${SHOCK_DIGEST}`);

  report('unfiring day 13 actually moved the price',
    controlDigest !== shockDigest);
}

// -- 3. A fork is independent -------------------------------------------
{
  const parent = new pt.Sim(5, 11, 3, 'pt-v3');
  const child = parent.fork();
  child.patchDraws([3, 0, 0, 1.0]);
  const parentPatches = parent.drawPatches();
  const childPatches = child.drawPatches();
  report('patching a fork leaves the parent unpatched',
    parentPatches.length === 0 && childPatches.length === 4,
    // Both are Float64Array, which JSON.stringify renders as {"0":...}
    // rather than [...]; Array.from is display only, not the assertion.
    `        parent.drawPatches()  ${JSON.stringify(Array.from(parentPatches))}\n` +
    `        child.drawPatches()   ${JSON.stringify(Array.from(childPatches))}`);
}

// -- 4. Fork and patch cost, on the SURGERY_CASE roster -----------------
//
// Reported once by hand as a number in a message rather than a test
// (28.7us / 96.6us on one machine); a reviewer's own re-measurement got
// a same-order-of-magnitude but not matching number (roughly 75us /
// 170us) on a different, shared, noisy machine, which is exactly the
// risk of a number with nothing pinning it -- found by review. This
// asserts a bound generous enough to absorb that machine-to-machine
// spread rather than pin either figure: the claim being tested is
// "cheap enough that the run afterward dominates", not a specific
// number of microseconds.
{
  const CASE = { size: 12, universeSeed: 7, seed: 3, preset: 'pt-v13' };
  const DAY = 13;
  const WARMUP = 200;
  const N = 2000;
  // 50-100x either measurement above: this fails only on a genuine
  // regression (an accidental O(roster) or O(overlay) blowup in fork or
  // patchDraws), not on a slower or more loaded machine than either one
  // measured on.
  const FORK_BOUND_US = 10_000;
  const FORK_PATCH_BOUND_US = 20_000;

  for (let i = 0; i < WARMUP; i++) {
    const s = new pt.Sim(CASE.size, CASE.universeSeed, CASE.seed, CASE.preset);
    const f = s.fork();
    f.patchDraws([3, 0, f.jumpAddress(DAY), 1.0]);
  }

  const base = new pt.Sim(CASE.size, CASE.universeSeed, CASE.seed, CASE.preset);

  let t0 = process.hrtime.bigint();
  for (let i = 0; i < N; i++) { base.fork(); }
  let t1 = process.hrtime.bigint();
  const forkUs = Number(t1 - t0) / N / 1000;

  t0 = process.hrtime.bigint();
  for (let i = 0; i < N; i++) {
    const f = base.fork();
    const addr = f.jumpAddress(DAY);
    f.patchDraws([3, 0, addr, 1.0]);
  }
  t1 = process.hrtime.bigint();
  const forkPatchUs = Number(t1 - t0) / N / 1000;

  report(`fork() stays under ${FORK_BOUND_US}us on a ${CASE.size}-instrument roster`,
    forkUs < FORK_BOUND_US,
    `        measured  ${forkUs.toFixed(2)}us (mean of ${N}, after ${WARMUP} warmup calls)`);
  report(`fork+jumpAddress+patchDraws stays under ${FORK_PATCH_BOUND_US}us`,
    forkPatchUs < FORK_PATCH_BOUND_US,
    `        measured  ${forkPatchUs.toFixed(2)}us (mean of ${N}, after ${WARMUP} warmup calls)`);
}

if (failed) {
  console.error('\nDIVERGED: see FAIL lines above.');
  process.exit(1);
}
console.log('\n  bit-identical with the native build.');
