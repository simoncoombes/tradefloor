/* Load every built page in a real browser and check it.
 *
 *     node tools/docs/learn/verify.cjs docs/learn
 *
 * A static site generator can only prove that it wrote files. This proves
 * the files work: it drives headless Chrome over the DevTools protocol,
 * opens all twenty-five pages and fails on anything a reader would meet —
 * a thrown exception, a console error, a request that 404s, a binding that
 * reached the page unresolved.
 *
 * It also checks the invariant the whole two-back-end design rests on: what
 * the build wrote and what the runtime draws over it must be the same
 * markup. If they diverge, the page a crawler indexes is not the page a
 * reader gets, and every other guarantee here is worth nothing.
 *
 * Chrome runs with reduced motion forced, which is what makes the check
 * deterministic — the pages honour it by not starting their players — and
 * incidentally tests that path.
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CHROME = process.env.CHROME ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const outDir = path.resolve(process.argv[2] || 'docs/learn');

// --------------------------------------------------------------- the browser

async function launch() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'pt-verify-'));
  const proc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--force-prefers-reduced-motion',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: ['ignore', 'pipe', 'pipe'] });

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* One tab, one page, one verdict. The tab is thrown away each time so a
 * page cannot leave state behind for the next one to trip over. */
async function visit(port, fileUrl, opts) {
  opts = opts || {};
  const created = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent('about:blank')}`,
    { method: 'PUT' }
  ).then((r) => r.json());

  const ws = new WebSocket(created.webSocketDebuggerUrl);
  const problems = [];
  let id = 0;
  const pending = new Map();

  const send = (method, params) => new Promise((resolve, reject) => {
    const msg = { id: ++id, method, params: params || {} };
    pending.set(msg.id, { resolve, reject });
    ws.send(JSON.stringify(msg));
  });

  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });

  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve } = pending.get(m.id);
      pending.delete(m.id);
      resolve(m.result);
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      problems.push(`exception: ${d.exception ? d.exception.description : d.text}`);
    } else if (m.method === 'Log.entryAdded') {
      const e = m.params.entry;
      if (e.level === 'error') problems.push(`console: ${e.text}${e.url ? ' (' + e.url + ')' : ''}`);
    } else if (m.method === 'Network.loadingFailed') {
      problems.push(`request failed: ${m.params.errorText}`);
    }
  });

  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Network.enable');
  /* With scripts off, what comes back is the page as the build wrote it,
   * parsed by the same parser that will parse it for a reader. That is the
   * only fair thing to compare the mounted page against: comparing to the
   * file's text would flag every fixup an HTML parser is required to make,
   * starting with the <tbody> it inserts into every table. */
  if (opts.noScript) await send('Emulation.setScriptExecutionDisabled', { value: true });

  const loaded = new Promise((resolve) => {
    const onMsg = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.method === 'Page.loadEventFired') { ws.removeEventListener('message', onMsg); resolve(); }
    };
    ws.addEventListener('message', onMsg);
  });
  await send('Page.navigate', { url: fileUrl });
  await Promise.race([loaded, sleep(15000)]);
  await sleep(400);  // let the mount's first frame land

  const probe = await send('Runtime.evaluate', {
    expression: opts.noScript ? ROOT_ONLY : PROBE,
    returnByValue: true,
    awaitPromise: false,
    /* The probe has to run even with page scripts disabled. */
    includeCommandLineAPI: false,
    contextId: undefined,
  });

  ws.close();
  await fetch(`http://127.0.0.1:${port}/json/close/${created.id}`).catch(() => {});

  const value = probe && probe.result && probe.result.value;
  return { problems, probe: value, error: probe && probe.exceptionDetails };
}

/* The no-script pass reads the built markup back as the browser parsed it.
 * It deliberately asserts nothing else: a page with scripts off is missing
 * its search button by design, so the full probe would fail it. */
const ROOT_ONLY = `(() => {
  const root = document.getElementById('pt-root');
  return { findings: [], rootHTML: root ? root.innerHTML : '', title: document.title, words: 0 };
})()`;

/* Runs inside the page. Everything it reports is something a reader could
 * see, so each finding names what is wrong rather than which assertion
 * failed. */
const PROBE = `(() => {
  const root = document.getElementById('pt-root');
  const body = document.body.innerText || '';
  const tpl = document.getElementById('pt-template');
  const findings = [];

  if (!root) findings.push('no #pt-root');
  if (root && root.children.length === 0) findings.push('#pt-root is empty after mount');
  if (!tpl) findings.push('no #pt-template');

  // An unresolved binding is the failure mode this dialect has: it looks
  // like prose until you read it.
  const stray = body.match(/\\{\\{[^}]{0,60}\\}\\}/g);
  if (stray) findings.push('unresolved bindings in the text: ' + stray.slice(0, 3).join(' '));

  const menus = document.querySelectorAll('details.ptmenu').length;
  if (menus !== 5) findings.push('door menus: ' + menus + ', expected 5');

  if (!document.getElementById('pt-search-open')) findings.push('no search button');
  if (!document.getElementById('pt-theme')) findings.push('no theme toggle');
  if (!(window.PT_SEARCH || []).length) findings.push('search index is empty');

  const h1 = document.querySelector('#pt-root h1');
  if (!h1 || !h1.textContent.trim()) findings.push('no h1');

  const navs = document.querySelectorAll('nav a[href$=".html"]').length;
  if (navs < 2) findings.push('footer prev/next missing');

  // Images that resolved to nothing.
  const broken = [...document.images].filter(i => i.complete && i.naturalWidth === 0)
                                     .map(i => i.getAttribute('src'));
  if (broken.length) findings.push('broken images: ' + broken.join(', '));

  return {
    findings,
    rootHTML: root ? root.innerHTML : '',
    title: document.title,
    words: body.trim().split(/\\s+/).length
  };
})()`;

// -------------------------------------------------------------------- driver

/* Compare the two ways of producing the same page.
 *
 * The invariant that matters is that a reader without JavaScript is served
 * the same document as a reader with it: the same elements, in the same
 * order, carrying the same words. That is checked strictly, because it is
 * what the static build exists for.
 *
 * Attribute values are checked too but reported rather than failed. One
 * page has a caret that blinks, drawn at `opacity:0` in the still frame the
 * build emits and `opacity:1` once it is animating — a difference between a
 * document at rest and the same document in motion, not a defect.
 * Attribute *order* is not compared at all: the diff appends an attribute
 * it has to add, and where an element lands in the tree decides whether it
 * does, which changes nothing a reader can see.
 */
function tokens(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, '')
    .match(/<[^>]+>|[^<]+/g) || [];
}

function tagOf(token) {
  const m = /^<\/?([a-zA-Z][-a-zA-Z0-9]*)/.exec(token);
  return m ? (token[1] === '/' ? '/' : '') + m[1].toLowerCase() : null;
}

function attrsOf(token) {
  const out = {};
  const re = /([-a-zA-Z:_]+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(token))) out[m[1]] = m[2];
  return out;
}

function compare(builtHTML, mountedHTML) {
  const a = tokens(builtHTML), b = tokens(mountedHTML);
  const errors = [], notes = [];

  if (a.length !== b.length) {
    errors.push(`node count differs: ${a.length} built, ${b.length} mounted`);
    return { errors, notes };
  }

  for (let i = 0; i < a.length; i++) {
    const ta = tagOf(a[i]), tb = tagOf(b[i]);
    if (ta !== tb) {
      errors.push(`element ${i} is <${ta}> built, <${tb}> mounted`);
      if (errors.length > 3) break;
      continue;
    }
    if (ta === null) {
      const xa = a[i].replace(/\s+/g, ' ').trim();
      const xb = b[i].replace(/\s+/g, ' ').trim();
      if (xa !== xb) {
        /* A reveal that is still running has drawn a prefix of the finished
         * text. That direction is fine and is in fact the point: the built
         * page carries the whole sentence, so a crawler and a reader with
         * no JavaScript get all of it. The other direction — text on the
         * live page that the built page does not have — is a real fault. */
        if (xa.startsWith(xb) && xb.length < xa.length) {
          notes.push(`text still revealing at ${i}: "${xb.slice(-40)}…"`);
        } else {
          errors.push(`text differs at ${i}: "${xa.slice(0, 120)}" vs "${xb.slice(0, 120)}"`);
          if (errors.length > 3) break;
        }
      }
      continue;
    }
    const aa = attrsOf(a[i]), ab = attrsOf(b[i]);
    for (const k of new Set([...Object.keys(aa), ...Object.keys(ab)])) {
      if (aa[k] !== ab[k]) {
        notes.push(`<${ta}> ${k}: ${short(aa[k])} built, ${short(ab[k])} mounted`);
      }
    }
  }
  return { errors, notes };
}

function short(v) {
  if (v === undefined) return '(absent)';
  return v.length > 46 ? '…' + v.slice(-42) : v;
}

(async () => {
  const pages = fs.readdirSync(outDir).filter((f) => f.endsWith('.html')).sort();
  if (!pages.length) { console.error(`no pages in ${outDir}`); process.exit(1); }

  const { proc, profile, port } = await launch();
  let failures = 0;

  try {
    for (const file of pages) {
      const full = path.join(outDir, file);
      const res = await visit(port, 'file://' + full);
      const findings = [...res.problems, ...((res.probe && res.probe.findings) || [])];

      if (res.error) findings.push('probe failed: ' + res.error.text);

      const plain = await visit(port, 'file://' + full, { noScript: true });
      const before = plain.probe ? plain.probe.rootHTML : null;
      let notes = [];
      if (before !== null && res.probe) {
        const cmp = compare(before, res.probe.rootHTML);
        for (const e of cmp.errors) findings.push('built vs mounted: ' + e);
        notes = cmp.notes;
      }

      const words = res.probe ? res.probe.words : 0;
      if (findings.length) {
        failures++;
        console.log(`FAIL ${file}`);
        for (const f of findings) console.log(`       ${f}`);
      } else {
        console.log(`ok   ${file}  ${String(words).padStart(5)} words` +
                    (notes.length ? `  (${notes.length} attribute diff${notes.length > 1 ? 's' : ''})` : ''));
        if (process.env.PT_VERIFY_NOTES) {
          for (const n of notes) console.log(`       ${n}`);
        }
      }
    }
  } finally {
    proc.kill();
    // Chrome unlinks its lock files on the way out; give it a moment before
    // taking the directory out from under it.
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
    catch (err) { /* a leftover profile in tmp is not a failure */ }
  }

  console.log(`\n${pages.length - failures}/${pages.length} pages clean`);
  process.exit(failures ? 1 : 0);
})();
