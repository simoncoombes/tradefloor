// Does the web-target build produce the same market as the nodejs one?
//
// `--target web` wraps the same .wasm as an ES module whose init function
// takes the module bytes, so a page fetches it and node can read it from
// disk. This loads it the second way and compares the two pins
// `check.mjs` compares, then checks that a forked Sim continues exactly as
// its parent does.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const dir = resolve(process.argv[2] ?? 'dist/wasm/web');
const pt = await import(pathToFileURL(`${dir}/tradefloor.js`).href);
pt.initSync({ module: readFileSync(`${dir}/tradefloor_bg.wasm`) });

// tests/test_wasm_parity.py, EXPECTED and HIGH_EXPECTED.
const EXPECTED = '1c5acabf07692228c840518b51240abe0e379fdd5272b9a4575206e8f93159ea';
const HIGH_EXPECTED = 'cd73cf3a2a637c4fbf353eb36c2fe74d5513d4985be4c2362eeab65544000aba';
const got = pt.priceDigest(12, 7, 3, 5, 65, 'pt-v3');
const high = pt.priceDigest(12, 2n ** 64n - 1n, 2n ** 63n + 12345n, 5, 65, 'pt-v19');

// A fork at day 3, both branches run to day 7, against one run straight to
// day 7. No intervention, so any difference is state the fork lost.
const straight = new pt.Sim(12, 7, 3, 'pt-v22');
straight.runDays(7, 65);
const parent = new pt.Sim(12, 7, 3, 'pt-v22');
parent.runDays(3, 65);
const branch = parent.fork();
parent.runDays(4, 65);
branch.runDays(4, 65);
const same = (a, b) => a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
const forkOk = same(parent.prices, straight.prices) && same(branch.prices, straight.prices)
  && branch.day === 7;

console.log(`  web build  tradefloor ${pt.version()}`);
console.log(`  wasm       ${got}`);
console.log(`  wasm u64   ${high}`);
console.log(`  fork       ${forkOk ? 'continues as its parent' : 'DIVERGED'}`);
if (got !== EXPECTED || high !== HIGH_EXPECTED || !forkOk) {
  console.error('\nThe web build disagrees with the pinned market, or a fork diverged.');
  process.exit(1);
}
console.log('\nweb build: identical');
