/* Prerender the design pages to static HTML.
 *
 * Each handoff page is a template plus a component class. The class is
 * ordinary JavaScript written against a browser, so this evaluates it in a
 * vm context with just enough of a browser to get through `render()` - and
 * deliberately no further. `componentDidMount` is never called here: it
 * reads localStorage, installs document listeners and picks a theme, all of
 * which are the reader's business and none of which belong in a file served
 * to everyone.
 *
 * Usage:  node prerender.cjs <manifest.json>
 * where the manifest is { pages: [{src, slug}], data: [...js files...] }.
 * Emits one JSON document on stdout, so the Python builder owns all file
 * writing and this stays a pure function of its inputs.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const PT = require(path.join(__dirname, 'site', 'learn-runtime.js'));

// --------------------------------------------------------------- extraction

/* The `.dc.html` shape is fixed: a <helmet> of head content, a template, and
 * one trailing <script type="text/x-dc">. Anything else in the file is the
 * prototyping harness and is dropped. */
function split(src) {
  const helmet = /<helmet>([\s\S]*?)<\/helmet>/.exec(src);
  const openTag = /<x-dc(?:\s[^>]*)?>/.exec(src);
  const closeAt = src.lastIndexOf('</x-dc>');
  if (!openTag || closeAt < 0) throw new Error('no <x-dc> block');

  let body = src.slice(openTag.index + openTag[0].length, closeAt);
  if (helmet) body = body.replace(helmet[0], '');

  const script = /<script[^>]*data-dc-script[^>]*>([\s\S]*?)<\/script>/.exec(src);
  const props = script ? /data-props="([^"]*)"/.exec(script[0]) : null;

  return {
    helmet: helmet ? helmet[1] : '',
    template: body.trim(),
    script: script ? script[1] : '',
    props: resolveProps(props ? JSON.parse(props[1].replace(/&quot;/g, '"')) : {})
  };
}

/* `data-props` is the prototyping tool's prop *schema*, not the props: each
 * entry describes an editor control and carries the value under `default`.
 * A component asking for `this.props.defaultMarket` wants 'calm', not the
 * enum descriptor that offers it, so unwrap one level. `$preview` is the
 * canvas's own viewport setting and is dropped. */
function resolveProps(schema) {
  const out = {};
  for (const key of Object.keys(schema)) {
    if (key === '$preview') continue;
    const spec = schema[key];
    out[key] = spec && typeof spec === 'object' && !Array.isArray(spec) && 'editor' in spec
      ? spec.default : spec;
  }
  return out;
}

function headBits(helmet) {
  const title = /<title>([\s\S]*?)<\/title>/.exec(helmet);
  const desc = /<meta\s+name="description"\s+content="([^"]*)"/.exec(helmet);
  const styles = [];
  const re = /<style>([\s\S]*?)<\/style>/g;
  let m;
  while ((m = re.exec(helmet))) styles.push(m[1]);
  return {
    title: title ? decode(title[1].trim()) : '',
    description: desc ? decode(desc[1]) : '',
    styles
  };
}

function decode(s) {
  return s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<')
          .replace(/&gt;/g, '>').replace(/&amp;/g, '&')
          .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(+d));
}

// ---------------------------------------------------------------- door list

/* Point a component's door list at the site's, instead of its own copy.
 *
 * Nine of the components carry their own list of every page and which door
 * it is in. Eight build the footer index from it, which the builder
 * generates now, so those copies are dead. The ninth is the front door,
 * whose "Start here" cards are *live* - and it was listing twenty-one pages
 * of twenty-five, and filing the MCP page under a door the handoff's own
 * copy puts it elsewhere.
 *
 * Deleting the dead eight and rewriting the ninth would be two changes with
 * two failure modes. Handing all nine the same list is one, and leaves
 * nothing behind that can disagree with the site.
 *
 * This runs before the component is evaluated, so the static markup and the
 * markup the runtime draws come from the same list.
 */
function wireDoors(script, slug) {
  let changed = false;

  const iife = script.indexOf('doors: (function () {');
  if (iife >= 0) {
    const open = script.indexOf('(', iife + 'doors:'.length);
    let end = balanced(script, open, '(', ')');
    // The function expression is immediately called: skip its `()` too.
    while (end < script.length && /[\s(]/.test(script[end])) {
      if (script[end] === '(') end = balanced(script, end, '(', ')');
      else end++;
    }
    script = script.slice(0, iife) + 'doors: this.props.doors' + script.slice(end);
    changed = true;
  }

  const field = script.indexOf('DOORS = [');
  if (field >= 0) {
    const end = balanced(script, script.indexOf('[', field), '[', ']');
    script = script.slice(0, field) +
      'DOORS = (this.props.doors || []).map(' +
      '(d) => [d.title, d.links.map((l) => [l.label, l.href])])' +
      script.slice(end);
    changed = true;
  }

  if (!changed && /\bDOORS\b|doors:\s*\(/.test(script)) {
    throw new Error(`${slug}: a door list is present in a shape wireDoors does not know`);
  }
  return { script, changed };
}

/* Index just past the bracketed expression beginning at `start`. A count
 * rather than a regular expression, because the blocks being replaced hold
 * both kinds of bracket and a nested function. */
function balanced(text, start, open, close) {
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    if (text[i] === open) depth++;
    else if (text[i] === close) {
      depth--;
      if (depth === 0) return i + 1;
    }
  }
  throw new Error('unbalanced expression');
}

// ---------------------------------------------------------- style extraction

/* Turn repeated inline styles into classes.
 *
 * The design files are inline-styled because that is what the prototyping
 * environment emitted, and the handoff is explicit that it is "a constraint
 * of the prototyping environment and **not** a recommendation". Undoing it
 * by hand across 3,729 style attributes would be a large chance to change
 * one of them by accident, so it is done here, mechanically, and checked by
 * comparing screenshots of every page before and after.
 *
 * Only a style with no bindings in it can become a class. A style is worth
 * a class when it is repeated; an element inside a loop counts for more,
 * because one attribute in the template is many in the rendered page.
 *
 * The classes are assigned across all pages at once, so one rule serves
 * every page that uses it, and named in a stable order so a rebuild that
 * changes nothing produces the same stylesheet.
 */
const LOOP_WEIGHT = 8;
const WORTH_A_CLASS = 6;

function collectStyles(ast, counts, inLoop) {
  for (const node of ast) {
    const loop = inLoop || node.kind === 'for';
    if (node.kind === 'el') {
      const style = (node.attrs || []).find((a) => a.name === 'style');
      if (style && !style.parts.some((p) => typeof p !== 'string')) {
        const key = style.raw.trim();
        if (key) counts.set(key, (counts.get(key) || 0) + (loop ? LOOP_WEIGHT : 1));
      }
    }
    if (node.children) collectStyles(node.children, counts, loop);
  }
}

function assignClasses(counts) {
  const worth = [...counts.entries()].filter(([, n]) => n >= WORTH_A_CLASS);
  // Heaviest first, then alphabetical, so the names do not move between
  // builds that did not change anything.
  worth.sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  const map = new Map();
  worth.forEach(([style], i) => map.set(style, `pt-s${i}`));
  return map;
}

function applyClasses(ast, map) {
  let applied = 0;
  walk(ast, (node) => {
    if (node.kind !== 'el') return;
    const style = (node.attrs || []).find((a) => a.name === 'style');
    if (!style || style.parts.some((p) => typeof p !== 'string')) return;
    const name = map.get(style.raw.trim());
    if (!name) return;
    node.attrs = node.attrs.filter((a) => a !== style);
    const existing = (node.attrs || []).find((a) => a.name === 'class');
    if (existing) existing.raw = (existing.raw + ' ' + name).trim();
    else node.attrs.push({ name: 'class', raw: name, parts: [name] });
    applied++;
  });
  return applied;
}

// ------------------------------------------------------------- shell removal

/* Lift the shell out of a page and return what is left.
 *
 * Every page carries its own copy of the masthead, the search overlay, the
 * "All pages" index and a prev/next pair. Those copies disagree: the four
 * pages added after the design review have no menus and no search at all,
 * and one of them still points BACK and NEXT at two pages that the same
 * review deleted. Rebuilding them from the site's page order is the only
 * way the set can be consistent, so they are stripped here and the builder
 * generates them.
 *
 * What stays is the page's own matter - the breadcrumb and the content
 * sections - which is the part the design actually specifies per page.
 */
function stripShell(ast) {
  const wrap = ast.find((n) => n.kind === 'el');
  if (!wrap) throw new Error('no wrapper element');
  const removed = { overlay: false, header: false, index: false, nav: false };

  const main = wrap.children.find((n) => n.kind === 'el' && n.tag === 'main');
  if (!main) throw new Error('no <main>');

  wrap.children = wrap.children.filter((n) => {
    if (n.kind !== 'el') return true;
    if (n.tag === 'header') { removed.header = true; return false; }
    // The search overlay is the fixed-position div that precedes the header.
    if (n.tag === 'div' && /position:fixed/.test(attrRaw(n, 'style'))) {
      removed.overlay = true;
      return false;
    }
    return true;
  });

  const els = main.children.filter((n) => n.kind === 'el');
  const lastNav = [...els].reverse().find((n) => n.tag === 'nav');
  const indexSection = [...els].reverse().find(
    (n) => n.tag === 'section' && /^\s*All pages\s*$/.test(headingText(n))
  );
  main.children = main.children.filter((n) => {
    if (n === lastNav) { removed.nav = true; return false; }
    if (n === indexSection) { removed.index = true; return false; }
    return true;
  });

  /* What comes back is the <main> alone. The wrapper the design put around
   * it only ever held the overlay, the header and main itself, and the
   * builder now supplies the first two - so returning the wrapper as well
   * would nest one page-height container inside another. */
  return { ast: [main], removed, wrapStyle: attrRaw(wrap, 'style') };
}

/* Turn the theme-swapped image pairs into CSS classes.
 *
 * The design binds each pair's visibility to a `dark` flag the page's own
 * component holds: `style="display:{{ showLight }}"`. That has two faults
 * once the shell owns the theme. The wrong image is on screen until the
 * script runs, and the flag never hears about a toggle the shell performed,
 * so the logos stop agreeing with the page around them. Two stylesheet
 * rules do the same job before the first paint and keep doing it.
 */
function themeImages(ast) {
  let swapped = 0;
  walk(ast, (node) => {
    if (node.kind !== 'el') return;
    const style = (node.attrs || []).find((a) => a.name === 'style');
    if (!style) return;
    let cls = null;
    if (/display:\s*\{\{\s*showLight\s*\}\}/.test(style.raw)) cls = 'pt-light';
    else if (/display:\s*\{\{\s*showDark\s*\}\}/.test(style.raw)) cls = 'pt-dark';
    if (!cls) return;

    style.raw = style.raw
      .replace(/display:\s*\{\{\s*show(Light|Dark)\s*\}\}\s*;?/, '')
      .replace(/^;+|;+$/g, '');
    const existing = (node.attrs || []).find((a) => a.name === 'class');
    if (existing) existing.raw = (existing.raw + ' ' + cls).trim();
    else node.attrs.push({ name: 'class', raw: cls, parts: [cls] });
    swapped++;
  });
  return swapped;
}

/* Give every table its own horizontal scroll box.
 *
 * The front door prints a five-column truth table at its natural width. On
 * a 360px screen that is 366px of table in 325px of column, and because
 * nothing around it scrolls, the whole page scrolls instead - so a reader
 * on a phone drags the masthead and the prose sideways to read one row.
 * The wrapper goes in before either back end runs, so the built markup and
 * the runtime's tree agree about it.
 */
function wrapTables(ast) {
  let wrapped = 0;
  walk(ast, (node) => {
    if (!node.children) return;
    // The walk descends into what this callback just built, so a wrapper
    // must not be a candidate to be wrapped again.
    if (node.kind === 'el' && attrRaw(node, 'class').split(/\s+/).includes('pt-scroll')) return;
    node.children = node.children.map((child) => {
      if (child.kind !== 'el' || child.tag !== 'table') return child;
      if (/overflow-x\s*:\s*auto/.test(attrRaw(node, 'style'))) return child;
      wrapped++;
      return {
        kind: 'el', tag: 'div',
        attrs: [{ name: 'class', raw: 'pt-scroll', parts: ['pt-scroll'] }],
        children: [child],
      };
    });
  });
  return wrapped;
}

function walk(nodes, fn) {
  for (const n of nodes) {
    fn(n);
    if (n.children) walk(n.children, fn);
  }
}

function attrRaw(node, name) {
  const a = (node.attrs || []).find((x) => x.name === name);
  return a ? a.raw : '';
}

function headingText(section) {
  const h = (section.children || []).find(
    (n) => n.kind === 'el' && (n.tag === 'h2' || n.tag === 'h3')
  );
  return h ? PT.textOf(h) : '';
}

// ------------------------------------------------------------ the fake browser

/* Enough of a browser to evaluate a render(), and no more. Every method that
 * would touch a document is a no-op returning an empty result, so a
 * component that reaches for the DOM during render gets a defined answer
 * instead of a crash - and a component that only reaches for it in a
 * lifecycle hook never notices. */
function browser(shared) {
  const noop = () => {};
  const emptyEl = { focus: noop, blur: noop, scrollIntoView: noop, closest: () => null,
                    getAttribute: () => null, setAttribute: noop, removeAttribute: noop,
                    style: {}, classList: { add: noop, remove: noop, toggle: noop } };
  const doc = {
    documentElement: emptyEl, body: emptyEl,
    querySelector: () => null, querySelectorAll: () => [],
    getElementById: () => null, createElement: () => emptyEl,
    addEventListener: noop, removeEventListener: noop
  };
  const win = {
    document: doc,
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    /* A page served without JavaScript cannot animate, so the static
     * markup should be the still frame - which is what the pages already
     * draw when the reader has asked for reduced motion. Answering `true`
     * here is not a lie about the reader's settings; it is the honest
     * description of a document with no script running. */
    matchMedia: (q) => ({
      matches: /prefers-reduced-motion/.test(String(q)),
      addEventListener: noop, removeEventListener: noop
    }),
    navigator: { platform: '', userAgent: 'prerender' },
    addEventListener: noop, removeEventListener: noop,
    requestAnimationFrame: noop, cancelAnimationFrame: noop,
    setTimeout: noop, clearTimeout: noop, setInterval: noop, clearInterval: noop,
    devicePixelRatio: 1, innerWidth: 1180, innerHeight: 900,
    console
  };
  Object.assign(win, shared);
  win.window = win;
  win.self = win;
  win.globalThis = win;
  return win;
}

// ------------------------------------------------------------------- driver

const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const base = manifest.base;

/* The shared data modules assign onto `window`, so they are evaluated once
 * in their own context and the resulting globals handed to every page. */
const shared = {};
{
  const ctx = browser({});
  vm.createContext(ctx);
  for (const file of manifest.data) {
    vm.runInContext(fs.readFileSync(path.join(base, file), 'utf8'), ctx, { filename: file });
  }
  for (const key of Object.keys(ctx)) {
    if (key === 'window' || key === 'self' || key === 'globalThis' || key === 'document') continue;
    if (!(key in browser({}))) shared[key] = ctx[key];
  }
}

const out = { pages: [], styles: [], classes: {} };
const seenStyle = new Set();

/* Phase one: read every page and get it into the shape it will be rendered
 * in - the door list wired, the theme images classed, the tables wrapped,
 * the shell lifted out. Nothing is rendered yet, because the styles have to
 * be counted across all twenty-five pages before any one of them is
 * written. */
const prepared = [];

for (const page of manifest.pages) {
  const src = fs.readFileSync(path.join(base, page.src), 'utf8');
  const piece = split(src);
  const head = headBits(piece.helmet);

  const wiredDoors = wireDoors(piece.script, page.src);
  piece.script = wiredDoors.script;
  // The door list is a prop now, so the component must be built with it.
  piece.props = { ...piece.props, doors: manifest.doors || [] };

  for (const css of head.styles) {
    if (!seenStyle.has(css)) { seenStyle.add(css); out.styles.push({ slug: page.slug, css }); }
  }

  const ast = PT.parse(piece.template);
  const themed = themeImages(ast);
  const wrapped = wrapTables(ast);
  const stripped = stripShell(ast);

  prepared.push({ page, piece, head, wiredDoors, themed, wrapped, stripped });
}

// Phase two: decide which styles earn a class, across the whole site.
const counts = new Map();
for (const p of prepared) collectStyles(p.stripped.ast, counts, false);
const classMap = assignClasses(counts);
for (const [style, name] of classMap) out.classes[name] = style;

// Phase three: apply them, then render.
for (const { page, piece, head, wiredDoors, themed, wrapped, stripped } of prepared) {
  const classed = applyClasses(stripped.ast, classMap);
  const template = PT.serialize(stripped.ast);

  const ctx = browser(shared);
  ctx.DCLogic = PT.DCLogic;
  vm.createContext(ctx);
  vm.runInContext(piece.script + '\n;globalThis.__Component = Component;', ctx,
                  { filename: page.src });

  const Component = ctx.__Component;
  const inst = new Component(piece.props);
  inst.__schedule = () => {};
  /* Some pages settle their opening state in the mount hook rather than in
   * the field initialiser. Running it here is safe because the stub browser
   * absorbs the DOM work and returns nothing for localStorage, so the page
   * prerenders in the light theme with no listeners installed and no timers
   * running - which is exactly the state a reader without JavaScript is in. */
  if (inst.componentDidMount) {
    try { inst.componentDidMount(); } catch (err) {
      process.stderr.write(`  note: ${page.src} componentDidMount: ${err.message}\n`);
    }
  }

  const vals = (inst.renderVals || inst.render).call(inst);
  const html = PT.toHTML(PT.evaluate(PT.parse(template), vals));

  out.pages.push({
    slug: page.slug,
    src: page.src,
    title: head.title,
    description: head.description,
    html,
    template,
    removed: stripped.removed,
    themeImages: themed,
    tablesWrapped: wrapped,
    styleClasses: classed,
    wrapStyle: stripped.wrapStyle,
    script: piece.script,
    props: piece.props,
    doorsWired: wiredDoors.changed,
    /* The glossary holds its forty-odd definitions as data on the
     * component. Handing that over is better than reading them back out of
     * the rendered cards: the structured data then says what the page says
     * because it came from the same place, not because a pattern happened
     * to match. */
    terms: Array.isArray(inst.TERMS) ? inst.TERMS : null
  });
}

process.stdout.write(JSON.stringify(out));
