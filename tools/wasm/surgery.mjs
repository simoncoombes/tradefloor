// Draw surgery in the browser.
//
// Forks one Sim into two, patches the jumps stream's market uniform for
// one day on the fork so it cannot fire (noise.NO_FIRE, the value
// python/tradefloor/counterfactual.py's World.unfire documents), and
// runs both day by day so the chart is a path rather than one snapshot.
// Every number this page shows -- the version, the presets, the prices,
// the address, the digests -- comes from a Sim built here; none of it
// is retyped from elsewhere.
//
// Needs the `web`-target build tools/wasm/build.sh produces into
// dist/wasm/web/ (a SEPARATE wasm-bindgen invocation from the one
// tools/wasm/check.mjs uses -- see that script's comment). Served over
// HTTP, not opened as a file:// URL: the generated init() fetches the
// .wasm file relative to this module's own URL, and a browser's fetch
// refuses a local file. `python -m http.server` from the repository
// root, then open /tools/wasm/surgery.html, is enough.
import init, { Sim, version } from '../../dist/wasm/web/tradefloor.js';

// The case tests/test_wasm_parity.py's SURGERY_CASE pins and
// tools/wasm/check.mjs recomputes through these same bindings. Fixed
// here rather than exposed as controls, so the page stays the minimal
// demonstration the design note asks for: the one control is which day
// to unfire.
const PRESET = 'pt-v13';
const SIZE = 12;
const UNIVERSE_SEED = 7;
const SEED = 3;
const TICKS_PER_DAY = 65;
// Matches SURGERY_CASE["days"] exactly: the pinned digests below are
// only reachable if this page runs the same number of days that case
// does, not merely a superset ending later.
const DAYS = 15;
const DEFAULT_DAY = 13;
// A jump that fires at a day's close only shows up in the ROUNDED price
// a day or two later, once mispricing_s has mean-reverted into it far
// enough to move a cent (measured: day 13 needed two days of runway
// within DAYS, not one). Capping the picker here keeps every offered
// day's arm the same runway the default one was checked with.
const MAX_PICKABLE_DAY = DAYS - 2;

// rng::stream's ids and DrawKind's discriminants (rust/src/rng.rs).
// patchDraws and drawPatches carry these two numbers with no name
// attached on the wasm side -- "the page names them" is this file's job,
// per programme/P11-wasm-surgery.md.
const STREAMS = ['market', 'economy', 'external', 'jumps', 'volume',
                 'news', 'volume_idio'];
const JUMPS_STREAM = STREAMS.indexOf('jumps');
const UNIFORM_KIND = 0;
const NO_FIRE = 1.0;

// Pinned on the native side by tests/test_wasm_parity.py's
// SURGERY_CONTROL_DIGEST / SURGERY_SHOCK_DIGEST, and recomputed through
// the wasm bindings by tools/wasm/check.mjs. Shown as a live comparison
// only when the reader is looking at exactly that case (day ===
// DEFAULT_DAY; every other constant above is fixed already) -- a claim
// of agreement with check.mjs on a case check.mjs never ran would be
// retyped prose, not a caveat computed at call time.
const PINNED_CONTROL_DIGEST =
  '7b7cf4c6b14d7ff5bc84c65edbc88b6e3258a3f21cd76bdb9f04277c54c1cbc9';
const PINNED_SHOCK_DIGEST =
  '764be68c99799b9ab636d3876ef721d26331db95c9e9b796214046d9557b6455';

const statusEl = document.querySelector('#status');
const versionEl = document.querySelector('#version');
const daySelect = document.querySelector('#day');
const runButton = document.querySelector('#run');
const chartEl = document.querySelector('#chart');
const digestsEl = document.querySelector('#digests');
const caveatsEl = document.querySelector('#caveats');
const caseTextEl = document.querySelector('#case-text');

// sha256 over each price's big-endian bytes, in roster order -- the
// same recipe tests/test_wasm_parity.py's _prices_digest and
// tools/wasm/check.mjs's pricesDigest use. Not fixed_simulation_digest's
// recipe: that also hashes five macro fields a Sim has no getter for.
async function pricesDigest(prices) {
  const buf = new ArrayBuffer(8 * prices.length);
  const view = new DataView(buf);
  prices.forEach((p, i) => view.setFloat64(i * 8, p, false));
  const hash = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

// The equal-weighted mean of a Sim's current prices, in plain units
// (not normalised to a base of 100 or similar): the only aggregate the
// frozen bindings support computing, since averaging what `prices`
// already returns is a display choice, not a modelling one, and this
// page adds no new binding to compute one on the Rust side.
function meanPrice(prices) {
  let sum = 0;
  for (const p of prices) sum += p;
  return sum / prices.length;
}

/** Run the case, unfiring `day`'s market jump on the shock arm, and
 * return both day-by-day price paths plus the address patched. */
function runCase(day) {
  const control = new Sim(SIZE, UNIVERSE_SEED, SEED, PRESET);
  // Forked before either arm has run a single day, so `jumpAddress`
  // reads the stream at its construction-time position -- the
  // arithmetic World.unfire uses assumes the same, and CONTRIBUTING's
  // draw-schedule rule means that position does not depend on anything
  // this page has done yet.
  const shock = control.fork();

  const address = shock.jumpAddress(day);
  shock.patchDraws([JUMPS_STREAM, UNIFORM_KIND, address, NO_FIRE]);

  const controlPath = [];
  const shockPath = [];
  for (let d = 1; d <= DAYS; d++) {
    control.runDay(TICKS_PER_DAY);
    shock.runDay(TICKS_PER_DAY);
    controlPath.push(meanPrice(control.prices));
    shockPath.push(meanPrice(shock.prices));
  }

  return { address, controlPath, shockPath,
           controlPrices: control.prices, shockPrices: shock.prices };
}

function drawChart(controlPath, shockPath, day) {
  const w = 720, h = 320, pad = 28;
  const all = controlPath.concat(shockPath);
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;

  const x = (i) => pad + (i / (DAYS - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - lo) / span) * (h - 2 * pad);

  const line = (path) =>
    path.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
      .join(' ');

  const dayX = x(day - 1);
  chartEl.innerHTML = `
    <line x1="${dayX}" y1="${pad}" x2="${dayX}" y2="${h - pad}"
          stroke="#999" stroke-dasharray="3,3" />
    <text x="${dayX + 4}" y="${pad + 10}" font-size="11" fill="#666">day ${day}</text>
    <!-- The shock path is drawn first and wider, and the control path
         second and narrower, so where the two arms are bit-identical
         (every day up to and including the patched one -- the jump
         lands at that day's close and is first seen at the next open)
         the control line's black covers the shock line's red, and only
         where they diverge does red show either side of it. Drawing
         order stands in for a legend that would otherwise have to
         explain why one colour disappears before the marked day. -->
    <path d="${line(shockPath)}" fill="none" stroke="#c0392b" stroke-width="4" />
    <path d="${line(controlPath)}" fill="none" stroke="#1a1a1a" stroke-width="1.6" />
  `;
}

async function render(day) {
  statusEl.textContent = 'running...';
  statusEl.classList.remove('error');
  try {
    const { address, controlPath, shockPath, controlPrices, shockPrices } =
      runCase(day);
    drawChart(controlPath, shockPath, day);

    const controlDigest = await pricesDigest(controlPrices);
    const shockDigest = await pricesDigest(shockPrices);
    digestsEl.textContent =
      `control digest  ${controlDigest}\n` +
      `shock   digest  ${shockDigest}\n` +
      `jumps uniform address patched: ${address}`;

    const isDefaultCase = day === DEFAULT_DAY;
    const pinsAgree = controlDigest === PINNED_CONTROL_DIGEST &&
                       shockDigest === PINNED_SHOCK_DIGEST;
    if (isDefaultCase) {
      caveatsEl.textContent = pinsAgree
        ? 'This is the case tests/test_wasm_parity.py pins on the ' +
          'native side. Both digests above match it.'
        : 'This is the case tests/test_wasm_parity.py pins on the ' +
          'native side, but at least one digest above does not match ' +
          'the pinned value. The two builds disagree.';
    } else if (controlDigest === shockDigest) {
      caveatsEl.textContent =
        `Day ${day}'s market jump did not fire under this seed, so ` +
        'unfiring it changed nothing: both digests above agree. Day ' +
        `${DEFAULT_DAY} is the case checked against the native build.`;
    } else {
      caveatsEl.textContent =
        `Day ${day} has not been checked against the native build; ` +
        `day ${DEFAULT_DAY} is the one tests/test_wasm_parity.py pins.`;
    }

    statusEl.textContent = `ran ${DAYS} days on both arms.`;
  } catch (err) {
    statusEl.textContent = `failed: ${err.message ?? err}`;
    statusEl.classList.add('error');
    throw err;
  }
}

async function main() {
  statusEl.textContent = 'loading tradefloor.wasm...';
  await init();

  for (let d = 1; d <= MAX_PICKABLE_DAY; d++) {
    const opt = document.createElement('option');
    opt.value = String(d);
    opt.textContent = String(d);
    daySelect.appendChild(opt);
  }
  daySelect.value = String(DEFAULT_DAY);

  const probe = new Sim(SIZE, UNIVERSE_SEED, SEED, PRESET);
  versionEl.textContent =
    `tradefloor ${version()} -- model ${probe.modelFingerprint.slice(0, 12)}`;
  caseTextEl.textContent =
    `${SIZE} instruments, universe seed ${UNIVERSE_SEED}, sim seed ` +
    `${SEED}, preset ${PRESET}, ${TICKS_PER_DAY} ticks per day, ` +
    `${DAYS} days on both arms.`;

  runButton.addEventListener('click', () => render(Number(daySelect.value)));
  await render(DEFAULT_DAY);
}

main().catch((err) => {
  statusEl.textContent = `failed to start: ${err.message ?? err}`;
  statusEl.classList.add('error');
  console.error(err);
});
