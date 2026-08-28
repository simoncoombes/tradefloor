/* pretium learn - the production runtime for the documentation site.
 *
 * The design handoff ships each page as a `.dc.html`: a template in a small
 * declarative dialect, plus a component class holding the page's data and
 * interaction. The dialect is tiny - dotted-path interpolation, one loop
 * form, one conditional, three event attributes - so rather than transcribe
 * twenty-five pages of final markup into another framework's syntax, this
 * runtime implements the dialect and the templates stay the source.
 *
 * It runs in two places against one parser:
 *
 *   build time  parse -> evaluate -> toHTML, emitting a static page that
 *               reads correctly with no JavaScript and indexes properly.
 *   browser     parse -> evaluate -> patch, upgrading that static page in
 *               place so the sliders, tabs, filters and players work.
 *
 * Both back ends consume the same virtual node tree, so what a crawler is
 * served and what a reader ends up with cannot drift.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PTLearn = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* Elements that never take children. The design files close some of them
   * explicitly (`<img ...></img>`), which is not legal HTML but is what the
   * prototyping environment emitted; the parser accepts and discards those
   * closing tags rather than nesting everything that follows inside them. */
  var VOID = {
    area: 1, base: 1, br: 1, col: 1, embed: 1, hr: 1, img: 1, input: 1,
    link: 1, meta: 1, param: 1, source: 1, track: 1, wbr: 1
  };

  var EVENTS = { onclick: 'click', oninput: 'input', onkeydown: 'keydown', onchange: 'change' };

  /* Prototyping-only attributes. They tell the design tool how many
   * placeholder rows to draw before real data arrives; they mean nothing
   * once the data is real. */
  function isHint(name) {
    return name.indexOf('hint-placeholder') === 0 || name === 'data-screen-label' ||
           name === 'data-dc-script' || name === 'data-props';
  }

  // ---------------------------------------------------------------- parsing

  var ENTITY = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0' };

  /* Literal text in the templates is HTML-escaped at source. Decoding it
   * here, once, is what keeps the two back ends honest: the serialiser
   * re-escapes on the way out and the DOM back end sets `data` directly, so
   * `&#9660;` is the same triangle in a crawler's copy and a reader's. */
  function decodeEntities(src) {
    if (src.indexOf('&') < 0) return src;
    return src.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, function (whole, body) {
      if (body.charAt(0) === '#') {
        var code = body.charAt(1) === 'x' || body.charAt(1) === 'X'
          ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
        return code >= 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
      }
      var named = ENTITY[body.toLowerCase()];
      return named === undefined ? whole : named;
    });
  }

  /* Split an attribute value or a run of text into literal and {{ expr }}
   * parts. A value with no braces and no entities stays a single string,
   * which lets the serialiser pass it through with one escape. */
  function parts(src) {
    var out = [], at = 0, re = /\{\{\s*([^}]*?)\s*\}\}/g, m;
    while ((m = re.exec(src))) {
      if (m.index > at) out.push(decodeEntities(src.slice(at, m.index)));
      out.push({ expr: m[1] });
      at = m.index + m[0].length;
    }
    if (at < src.length) out.push(decodeEntities(src.slice(at)));
    return out;
  }

  function hasExpr(p) {
    for (var i = 0; i < p.length; i++) if (typeof p[i] !== 'string') return true;
    return false;
  }

  var ATTR_RE = /([a-zA-Z_:@][-a-zA-Z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;

  function parseAttrs(src) {
    var out = [], m;
    ATTR_RE.lastIndex = 0;
    while ((m = ATTR_RE.exec(src))) {
      var name = m[1];
      var raw = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : '';
      out.push({ name: name, raw: raw, parts: parts(raw) });
    }
    return out;
  }

  function attr(list, name) {
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
    return null;
  }

  /* A tolerant tag-level parser. The input is machine-generated and
   * well-nested apart from the void-element quirk above, so this does not
   * need the error recovery a general HTML parser needs - but it does need
   * to leave whitespace exactly as it found it, because `<pre>` blocks on
   * the install and API pages carry significant indentation. */
  function parse(src) {
    var stack = [{ children: [] }], i = 0;
    var TAG = /<(\/?)([a-zA-Z][-a-zA-Z0-9]*)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>/g;
    var m;
    TAG.lastIndex = 0;
    while ((m = TAG.exec(src))) {
      if (m.index > i) pushText(src.slice(i, m.index));
      i = m.index + m[0].length;
      var closing = m[1] === '/', tag = m[2].toLowerCase(), rest = m[3] || '', selfClose = m[4] === '/';

      if (closing) {
        if (VOID[tag]) continue;           // `</img>`: the quirk, discarded
        for (var s = stack.length - 1; s > 0; s--) {
          if (stack[s].tag === tag) { stack.length = s; break; }
        }
        continue;
      }

      var attrs = parseAttrs(rest);
      var node;
      if (tag === 'sc-for') {
        /* `tag` is what the closing-tag search below matches on. Without it
         * `</sc-for>` never pops the stack, so everything after a loop is
         * parsed as being inside it and renders once per item. */
        node = { kind: 'for', tag: tag, list: (attr(attrs, 'list') || { raw: '' }).raw,
                 as: (attr(attrs, 'as') || { raw: 'item' }).raw, children: [] };
      } else if (tag === 'sc-if') {
        node = { kind: 'if', tag: tag,
                 value: (attr(attrs, 'value') || { raw: '' }).raw, children: [] };
      } else {
        node = { kind: 'el', tag: tag, attrs: attrs.filter(function (a) { return !isHint(a.name); }), children: [] };
      }
      stack[stack.length - 1].children.push(node);
      if (!VOID[tag] && !selfClose) stack.push(node);
    }
    if (i < src.length) pushText(src.slice(i));
    normaliseTables(stack[0].children);
    return stack[0].children;

    function pushText(text) {
      if (!text) return;
      stack[stack.length - 1].children.push({ kind: 'text', parts: parts(text) });
    }
  }

  /* AST back to template source, bindings intact. The build uses it to lift
   * the shell out of a page and hand back what remains, so the masthead and
   * the footer can be generated from the site's own page order instead of
   * being inherited twenty-five times from whatever order the design was in
   * when each page was drawn. */
  function serialize(nodes) {
    var out = '';
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.kind === 'text') {
        for (var t = 0; t < n.parts.length; t++) {
          out += typeof n.parts[t] === 'string' ? reEscape(n.parts[t]) : '{{ ' + n.parts[t].expr + ' }}';
        }
      } else if (n.kind === 'for') {
        out += '<sc-for list="' + n.list + '" as="' + n.as + '">' + serialize(n.children) + '</sc-for>';
      } else if (n.kind === 'if') {
        out += '<sc-if value="' + n.value + '">' + serialize(n.children) + '</sc-if>';
      } else {
        out += '<' + n.tag;
        for (var a = 0; a < n.attrs.length; a++) out += ' ' + n.attrs[a].name + '="' + n.attrs[a].raw + '"';
        out += '>';
        if (!VOID[n.tag]) out += serialize(n.children) + '</' + n.tag + '>';
      }
    }
    return out;
  }

  function reEscape(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* The text of an element, bindings skipped - enough to recognise a
   * heading, and enough to build a search index from a rendered page. */
  function textOf(node) {
    if (node.kind === 'text') {
      var out = '';
      for (var i = 0; i < node.parts.length; i++) {
        if (typeof node.parts[i] === 'string') out += node.parts[i];
      }
      return out;
    }
    var s = '';
    for (var c = 0; c < (node.children || []).length; c++) s += textOf(node.children[c]);
    return s;
  }

  var TABLE_SECTIONS = { thead: 1, tbody: 1, tfoot: 1, caption: 1, colgroup: 1 };

  /* Give every table the <tbody> the HTML parser would have given it.
   *
   * A browser parsing `<table><tr>` inserts a tbody; the templates do not
   * write one. Left alone that is the difference between the tree the build
   * emits and the tree the browser holds, and the diff then tries to
   * replace a tbody full of rows with a single row - which is how a table
   * loses most of its contents on the first frame after mount. Inserting it
   * here, once, means both back ends and the parser agree.
   */
  function normaliseTables(nodes) {
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (!n.children) continue;
      normaliseTables(n.children);
      if (n.kind !== 'el' || n.tag !== 'table') continue;

      var out = [], run = null;
      for (var c = 0; c < n.children.length; c++) {
        var child = n.children[c];
        var isSection = child.kind === 'el' && TABLE_SECTIONS[child.tag];
        // Whitespace between rows is moved out of the table by the parser.
        if (child.kind === 'text' && !/\S/.test(interpolate(child.parts, {}))) continue;
        if (isSection) { run = null; out.push(child); continue; }
        if (!run) { run = { kind: 'el', tag: 'tbody', attrs: [], children: [] }; out.push(run); }
        run.children.push(child);
      }
      n.children = out;
    }
  }

  // ------------------------------------------------------------- evaluation

  /* Every binding in the twenty-five pages is a plain dotted path - there is
   * not one operator among the 386 distinct expressions - so resolution is a
   * walk, with no expression evaluator to sandbox. */
  function resolve(expr, scope) {
    expr = String(expr).trim();
    if (expr === 'true') return true;
    if (expr === 'false') return false;
    var path = expr.split('.'), value = scope;
    for (var i = 0; i < path.length; i++) {
      if (value == null) return undefined;
      value = value[path[i]];
    }
    return value;
  }

  function interpolate(p, scope) {
    if (p.length === 1 && typeof p[0] === 'string') return p[0];
    var out = '';
    for (var i = 0; i < p.length; i++) {
      if (typeof p[i] === 'string') { out += p[i]; continue; }
      var v = resolve(p[i].expr, scope);
      out += v == null || v === false ? '' : String(v);
    }
    return out;
  }

  /* template AST + scope -> virtual nodes. Loops push a child scope rather
   * than mutating, so the depth-2 nesting the tables use resolves the inner
   * and outer variables independently. */
  function evaluate(nodes, scope, out) {
    out = out || [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.kind === 'text') {
        var t = interpolate(n.parts, scope);
        if (t) out.push({ text: t });
      } else if (n.kind === 'for') {
        var list = resolve(stripBraces(n.list), scope);
        if (!list || typeof list.length !== 'number') continue;
        for (var j = 0; j < list.length; j++) {
          var child = Object.create(scope);
          child[n.as] = list[j];
          child.$index = j;
          evaluate(n.children, child, out);
        }
      } else if (n.kind === 'if') {
        if (resolve(stripBraces(n.value), scope)) evaluate(n.children, scope, out);
      } else {
        var vnode = { tag: n.tag, attrs: {}, on: null, children: [] };
        for (var a = 0; a < n.attrs.length; a++) {
          var at = n.attrs[a], key = at.name.toLowerCase();
          if (EVENTS[key]) {
            var fn = hasExpr(at.parts) ? resolve(at.parts[0].expr, scope) : null;
            if (typeof fn === 'function') (vnode.on || (vnode.on = {}))[EVENTS[key]] = fn;
            continue;
          }
          var val = interpolate(at.parts, scope);
          if (val === '' && at.raw !== '' && hasExpr(at.parts)) continue;  // binding resolved empty
          vnode.attrs[at.name] = val;
        }
        evaluate(n.children, scope, vnode.children);
        out.push(vnode);
      }
    }
    return out;
  }

  function stripBraces(s) {
    var m = /\{\{\s*([^}]*?)\s*\}\}/.exec(s);
    return m ? m[1] : s;
  }

  // ------------------------------------------------------ HTML serialisation

  function escapeText(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(s) {
    return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  /* Every text node is escaped here without exception, because by this point
   * template text and bound values have both been decoded to plain
   * characters and are indistinguishable - which is the point. */
  function toHTML(vnodes) {
    var out = '';
    for (var i = 0; i < vnodes.length; i++) {
      var n = vnodes[i];
      if (n.text !== undefined) { out += escapeText(n.text); continue; }
      out += '<' + n.tag;
      for (var k in n.attrs) if (Object.prototype.hasOwnProperty.call(n.attrs, k)) {
        out += ' ' + k + '="' + escapeAttr(n.attrs[k]) + '"';
      }
      out += '>';
      if (VOID[n.tag]) continue;
      out += toHTML(n.children);
      out += '</' + n.tag + '>';
    }
    return out;
  }

  // -------------------------------------------------------- DOM reconciling

  /* A shape diff, not a keyed one. The templates render the same tree shape
   * for a given page - a loop's length changes, but the elements around it
   * do not move - so matching by position is enough and costs nothing. It
   * earns its place over replacing the subtree for three reasons: the search
   * input keeps focus and caret while results update beneath it, an open
   * <details> menu stays open, and the market player's 200ms frames do not
   * churn the whole page. */
  var PRESERVE = { open: 1 };

  function patch(parent, vnodes, ns) {
    if (ns === undefined) {
      ns = parent.namespaceURI === SVG_NS && parent.tagName.toLowerCase() !== 'foreignobject'
        ? SVG_NS : null;
    }
    var i = 0;
    for (; i < vnodes.length; i++) {
      var want = vnodes[i], have = parent.childNodes[i];
      if (want.text !== undefined) {
        if (have && have.nodeType === 3) { if (have.data !== want.text) have.data = want.text; }
        else insert(parent, document.createTextNode(want.text), have);
        continue;
      }
      if (have && have.nodeType === 1 && have.tagName.toLowerCase() === want.tag) {
        patchEl(have, want, ns);
      } else {
        insert(parent, build(want, ns), have);
      }
    }
    while (parent.childNodes.length > vnodes.length) parent.removeChild(parent.lastChild);
  }

  function insert(parent, node, before) {
    if (before) parent.replaceChild(node, before); else parent.appendChild(node);
  }

  function patchEl(el, vnode, ns) {
    if (vnode.tag === 'svg') ns = SVG_NS;
    else if (vnode.tag === 'foreignObject') ns = null;
    var k;
    for (k in vnode.attrs) if (Object.prototype.hasOwnProperty.call(vnode.attrs, k)) {
      /* An input's `value` attribute does not follow the property once the
       * reader has typed, so set the property and let the attribute be. */
      if (k === 'value' && 'value' in el) {
        if (el.value !== vnode.attrs[k]) el.value = vnode.attrs[k];
      } else if (el.getAttribute(k) !== vnode.attrs[k]) {
        el.setAttribute(k, vnode.attrs[k]);
      }
    }
    var existing = el.attributes;
    for (var a = existing.length - 1; a >= 0; a--) {
      var name = existing[a].name;
      if (!(name in vnode.attrs) && !PRESERVE[name]) el.removeAttribute(name);
    }
    bind(el, vnode);
    patch(el, vnode.children, ns);
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';

  /* Elements inside an <svg> have to be created in the SVG namespace.
   * `document.createElement('polyline')` yields an HTMLUnknownElement, which
   * accepts every attribute, reports no error and draws nothing - so a
   * chart rebuilt rather than patched would simply vanish, silently, which
   * on this site means the picture the page exists to show. */
  function build(vnode, ns) {
    if (vnode.text !== undefined) return document.createTextNode(vnode.text);
    if (vnode.tag === 'svg') ns = SVG_NS;
    else if (vnode.tag === 'foreignObject') ns = null;
    var el = ns ? document.createElementNS(ns, vnode.tag) : document.createElement(vnode.tag);
    for (var k in vnode.attrs) if (Object.prototype.hasOwnProperty.call(vnode.attrs, k)) {
      if (k === 'value' && 'value' in el) el.value = vnode.attrs[k];
      else el.setAttribute(k, vnode.attrs[k]);
    }
    bind(el, vnode);
    for (var i = 0; i < vnode.children.length; i++) el.appendChild(build(vnode.children[i], ns));
    return el;
  }

  /* Handlers are stored on the element and dispatched through one listener
   * per event type, so a re-render swaps the closure without adding a
   * listener each time. */
  function bind(el, vnode) {
    var on = vnode.on;
    if (!on && !el.__pt) return;
    var store = el.__pt || (el.__pt = {});
    for (var type in EVENTS_BY_TYPE) {
      var fn = on && on[type];
      if (fn && !store[type]) {
        el.addEventListener(type, dispatch(el, type));
      }
      store[type] = fn || null;
    }
  }
  var EVENTS_BY_TYPE = { click: 1, input: 1, keydown: 1, change: 1 };
  function dispatch(el, type) {
    return function (e) { var fn = el.__pt && el.__pt[type]; if (fn) fn(e); };
  }

  // -------------------------------------------------------------- component

  function DCLogic(props) { this.props = props || {}; }
  DCLogic.prototype.setState = function (patchObj) {
    var next = typeof patchObj === 'function' ? patchObj(this.state) : patchObj;
    for (var k in next) if (Object.prototype.hasOwnProperty.call(next, k)) this.state[k] = next[k];
    if (this.__schedule) this.__schedule();
  };

  /* Mount a component over its server-rendered markup. The first draw
   * reconciles against what the build already emitted, so nothing flashes:
   * the static page and the runtime's first frame are the same tree. */
  function mount(host, Component, ast, props) {
    var inst = new Component(props || {});
    var queued = false;
    inst.__schedule = function () {
      if (queued) return;
      queued = true;
      (typeof requestAnimationFrame === 'function' ? requestAnimationFrame : setTimeout)(function () {
        queued = false;
        draw();
      });
    };
    var first = true;
    function draw() {
      /* `renderVals` is the handoff's name for the method that returns the
       * page's props. `render` is accepted too so a future page written by
       * hand can use the more obvious name. */
      var vals = (inst.renderVals || inst.render).call(inst);
      patch(host, evaluate(ast, vals));
      if (first) { first = false; return; }
      if (inst.componentDidUpdate) inst.componentDidUpdate();
    }
    draw();
    if (inst.componentDidMount) inst.componentDidMount();
    /* The mount hook almost always calls setState - it is where the stored
     * theme is read - so give the page one synchronous draw before handing
     * back, and let componentDidUpdate see it. */
    if (inst.componentDidUpdate) inst.componentDidUpdate();
    return inst;
  }

  return {
    parse: parse, evaluate: evaluate, toHTML: toHTML, patch: patch,
    serialize: serialize, textOf: textOf,
    resolve: resolve, DCLogic: DCLogic, mount: mount, VOID: VOID
  };
});
