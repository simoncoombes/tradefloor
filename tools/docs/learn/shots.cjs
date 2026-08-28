/* Screenshot every page, and compare two sets.
 *
 *     node tools/docs/learn/shots.cjs capture <dir> [--site docs]
 *     node tools/docs/learn/shots.cjs compare <before> <after>
 *
 * `verify.cjs` proves the markup is the same. It cannot prove the page still
 * looks the same, because a change to the stylesheet moves both the built
 * page and the mounted page together and the comparison stays happy. This
 * is the check for work that changes how things are drawn rather than what
 * is drawn — extracting inline styles into classes, most of all, where the
 * whole risk is that one of several thousand declarations goes missing and
 * nothing else notices.
 *
 * Pages are captured full height at two widths, in both themes, with
 * animation stilled so two runs of the same page agree.
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const zlib = require('zlib');

const CHROME = process.env.CHROME ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const VIEWS = [
  { name: 'wide-light', width: 1180, dark: false, mobile: false },
  { name: 'wide-dark', width: 1180, dark: true, mobile: false },
  { name: 'phone-light', width: 390, dark: false, mobile: true },
];

async function launch() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'pt-shots-'));
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--hide-scrollbars', '--force-prefers-reduced-motion',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });

  const portFile = path.join(profile, 'DevToolsActivePort');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    if (fs.existsSync(portFile)) {
      const [port] = fs.readFileSync(portFile, 'utf8').split('\n');
      if (port) return { proc, profile, port: Number(port) };
    }
    await sleep(60);
  }
  proc.kill();
  throw new Error('Chrome did not report a debugging port');
}

async function shoot(port, fileUrl, view) {
  const created = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`,
    { method: 'PUT' }).then((r) => r.json());
  const ws = new WebSocket(created.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params) => new Promise((resolve) => {
    const i = ++id;
    pending.set(i, resolve);
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  });

  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: view.width, height: 900, deviceScaleFactor: 1, mobile: view.mobile,
  });
  /* The theme is stored per origin, and every file:// page shares one, so it
   * is set on the document instead — the same attribute the toggle sets. */
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `try{localStorage.setItem('pt-learn-theme','${view.dark ? 'dark' : 'light'}')}catch(e){}`,
  });
  await send('Page.navigate', { url: fileUrl });
  await sleep(1800);

  const shot = await send('Page.captureScreenshot', {
    format: 'png', captureBeyondViewport: true, optimizeForSpeed: false,
  });
  ws.close();
  await fetch(`http://127.0.0.1:${port}/json/close/${created.id}`).catch(() => {});
  return Buffer.from(shot.data, 'base64');
}

// ------------------------------------------------------------------ compare

/* Decode enough PNG to compare pixels: this only ever reads its own output,
 * which Chrome writes as 8-bit RGBA, non-interlaced. Anything else is
 * refused rather than guessed at. */
function decodePNG(buf) {
  let at = 8, width = 0, height = 0, bitDepth = 0, colorType = 0;
  const idat = [];
  while (at < buf.length) {
    const len = buf.readUInt32BE(at);
    const type = buf.toString('ascii', at + 4, at + 8);
    const data = buf.subarray(at + 8, at + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      if (data[12] !== 0) throw new Error('interlaced PNG');
    } else if (type === 'IDAT') {
      idat.push(data);
    } else if (type === 'IEND') break;
    at += 12 + len;
  }
  if (bitDepth !== 8) throw new Error(`bit depth ${bitDepth}`);
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : 0;
  if (!channels) throw new Error(`colour type ${colorType}`);

  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const out = Buffer.alloc(height * stride);
  let pos = 0;
  for (let y = 0; y < height; y++) {
    const filter = raw[pos++];
    const line = raw.subarray(pos, pos + stride);
    pos += stride;
    const cur = out.subarray(y * stride, (y + 1) * stride);
    const prev = y ? out.subarray((y - 1) * stride, y * stride) : null;
    for (let i = 0; i < stride; i++) {
      const a = i >= channels ? cur[i - channels] : 0;
      const b = prev ? prev[i] : 0;
      const c = prev && i >= channels ? prev[i - channels] : 0;
      let v = line[i];
      if (filter === 1) v += a;
      else if (filter === 2) v += b;
      else if (filter === 3) v += (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      } else if (filter !== 0) throw new Error(`filter ${filter}`);
      cur[i] = v & 0xff;
    }
  }
  return { width, height, channels, data: out };
}

/* Anti-aliasing and subpixel text rendering move a channel by a step or two
 * between otherwise identical runs, so a pixel counts as changed only when
 * it moves further than that. */
const TOLERANCE = 8;

function difference(a, b) {
  if (a.width !== b.width || a.height !== b.height) {
    return { sizeChanged: true, before: `${a.width}x${a.height}`, after: `${b.width}x${b.height}` };
  }
  let changed = 0;
  let worst = 0;
  const rows = new Map();
  const px = a.width * a.height;
  for (let i = 0; i < px; i++) {
    const o = i * a.channels;
    let d = 0;
    for (let c = 0; c < 3; c++) d = Math.max(d, Math.abs(a.data[o + c] - b.data[o + c]));
    if (d <= TOLERANCE) continue;
    changed++;
    worst = Math.max(worst, d);
    const y = Math.floor(i / a.width);
    rows.set(y, (rows.get(y) || 0) + 1);
  }
  const bands = [...rows.entries()].sort((x, y) => y[1] - x[1]).slice(0, 3)
    .map(([y, n]) => `y=${y} (${n}px)`);
  return { changed, total: px, share: changed / px, worst, bands };
}

// ------------------------------------------------------------------- driver

(async () => {
  const mode = process.argv[2];

  if (mode === 'compare') {
    const [before, after] = [process.argv[3], process.argv[4]].map((p) => path.resolve(p));
    const names = fs.readdirSync(before).filter((f) => f.endsWith('.png')).sort();
    let moved = 0;
    for (const name of names) {
      const bp = path.join(after, name);
      if (!fs.existsSync(bp)) { console.log(`GONE  ${name}`); moved++; continue; }
      const d = difference(decodePNG(fs.readFileSync(path.join(before, name))),
                           decodePNG(fs.readFileSync(bp)));
      if (d.sizeChanged) {
        console.log(`SIZE  ${name}  ${d.before} -> ${d.after}`);
        moved++;
      } else if (d.changed) {
        console.log(`DIFF  ${name}  ${d.changed} px (${(d.share * 100).toFixed(3)}%), ` +
                    `worst ${d.worst}, rows ${d.bands.join(', ')}`);
        moved++;
      }
    }
    console.log(`\n${names.length - moved}/${names.length} views identical`);
    process.exit(moved ? 1 : 0);
  }

  if (mode !== 'capture') {
    console.error('usage: shots.cjs capture <dir> | shots.cjs compare <before> <after>');
    process.exit(2);
  }

  const outDir = path.resolve(process.argv[3]);
  const siteArg = process.argv.indexOf('--site');
  const site = path.resolve(siteArg > 0 ? process.argv[siteArg + 1] : 'docs');
  fs.mkdirSync(outDir, { recursive: true });

  const pages = fs.readdirSync(site).filter((f) => f.endsWith('.html')).sort();
  const { proc, profile, port } = await launch();
  try {
    for (const page of pages) {
      for (const view of VIEWS) {
        const png = await shoot(port, 'file://' + path.join(site, page), view);
        fs.writeFileSync(path.join(outDir, `${page.replace('.html', '')}.${view.name}.png`), png);
      }
      process.stdout.write('.');
    }
  } finally {
    proc.kill();
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
    catch (e) { /* a leftover profile is not a failure */ }
  }
  console.log(`\ncaptured ${pages.length * VIEWS.length} views into ${outDir}`);
})();
