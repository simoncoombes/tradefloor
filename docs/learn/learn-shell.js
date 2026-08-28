/* Behaviour for the generated shell: theme, door menus, search.
 *
 * None of this is part of a page's component. The shell is the same static
 * markup on all twenty-five pages, so it is driven by ordinary DOM code that
 * runs once, rather than by a template that re-renders — which is also why
 * the search overlay no longer redraws every time a page's own state moves.
 *
 * Everything here degrades. With the script blocked the menus still open,
 * because they are <details>; the theme still follows the system, because
 * the stylesheet has a prefers-color-scheme block; and the only thing lost
 * is search, which is why the search button is the one control that is
 * hidden until the script arrives.
 */
(function () {
  'use strict';

  var THEME_KEY = 'pt-learn-theme';

  // ------------------------------------------------------------------ theme

  function stored() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }
  function store(v) {
    try { localStorage.setItem(THEME_KEY, v); } catch (e) { /* private mode */ }
  }
  function isDark() {
    var set = document.documentElement.getAttribute('data-theme');
    if (set) return set === 'dark';
    var s = stored();
    if (s) return s === 'dark';
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }
  function applyTheme(dark) {
    if (dark) document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.setAttribute('data-theme', 'light');
    store(dark ? 'dark' : 'light');
  }

  var toggle = document.getElementById('pt-theme');
  if (toggle) {
    toggle.addEventListener('click', function () { applyTheme(!isDark()); });
  }

  /* Until the reader chooses, the page follows the system and the root
   * carries no data-theme at all, so `prefers-color-scheme` decides. Once
   * they choose, the attribute is set and stays set. */
  if (stored()) applyTheme(stored() === 'dark');

  // ------------------------------------------------------------------ menus

  /* <details> has no notion of a group. One open at a time, click-outside to
   * close and Escape to close are all added here. */
  document.addEventListener('click', function (e) {
    var inside = e.target && e.target.closest ? e.target.closest('details.ptmenu') : null;
    var all = document.querySelectorAll('details.ptmenu');
    for (var i = 0; i < all.length; i++) {
      if (all[i] !== inside) all[i].removeAttribute('open');
    }
  });

  // ----------------------------------------------------------------- search

  var panel = document.getElementById('pt-search');
  var input = document.getElementById('pt-search-input');
  var list = document.getElementById('pt-search-results');
  var count = document.getElementById('pt-search-count');
  var openBtn = document.getElementById('pt-search-open');
  var hint = document.getElementById('pt-search-hint');
  var sel = 0;
  var hits = [];

  var isMac = /Mac|iPhone|iPad/.test(
    (navigator.platform || '') + ' ' + (navigator.userAgent || '')
  );
  if (hint) hint.textContent = isMac ? 'CMD K' : 'CTRL K';

  function index() { return window.PT_SEARCH || []; }

  function open() {
    if (!panel) return;
    panel.hidden = false;
    panel.style.display = 'flex';
    if (input) { input.value = ''; input.focus(); }
    sel = 0;
    draw('');
  }

  function close() {
    if (!panel) return;
    panel.hidden = true;
    panel.style.display = 'none';
  }

  /* Rank by where the match lands: a page whose title starts with the query
   * first, then a title containing it, then the body. The old index held
   * hand-written keywords and could only ever find whole pages; this one is
   * built from the rendered text, so the snippet under each result is a
   * sentence the reader will actually meet on that page. */
  function score(page, q) {
    var title = page.title.toLowerCase();
    if (title.indexOf(q) === 0) return 3;
    if (title.indexOf(q) >= 0) return 2;
    if (page.text.toLowerCase().indexOf(q) >= 0) return 1;
    return -1;
  }

  function snippet(page, q) {
    if (!q) return page.text.slice(0, 90);
    var at = page.text.toLowerCase().indexOf(q);
    if (at < 0) return page.text.slice(0, 90);
    var from = Math.max(0, at - 28);
    return (from > 0 ? '…' : '') + page.text.slice(from, from + 86);
  }

  function draw(q) {
    if (!list) return;
    q = (q || '').trim().toLowerCase();
    var all = index();
    hits = [];
    for (var i = 0; i < all.length; i++) {
      var s = q ? score(all[i], q) : 0;
      if (s < 0) continue;
      hits.push({ page: all[i], score: s, hint: snippet(all[i], q) });
    }
    hits.sort(function (a, b) {
      return b.score - a.score || a.page.title.localeCompare(b.page.title);
    });
    if (sel >= hits.length) sel = Math.max(0, hits.length - 1);

    list.textContent = '';
    for (var h = 0; h < hits.length; h++) {
      list.appendChild(row(hits[h], h === sel));
    }
    if (count) count.textContent = hits.length + ' of ' + all.length + ' pages';
  }

  function row(hit, active) {
    var a = document.createElement('a');
    a.href = hit.page.slug + '.html';
    a.setAttribute('role', 'option');
    a.style.cssText = 'display:flex;justify-content:space-between;gap:0.9rem;' +
      'align-items:baseline;padding:0.55rem 0.7rem;border-radius:6px;color:var(--ink);' +
      'background:' + (active ? 'var(--accent-soft)' : 'transparent');

    var left = document.createElement('span');
    left.style.cssText = 'display:flex;flex-direction:column;gap:1px;min-width:0';
    var title = document.createElement('span');
    title.style.cssText = "font-family:'Source Serif 4',ui-serif,Georgia,serif;" +
      'font-size:0.9375rem;font-weight:600;color:' + (active ? 'var(--accent-deep)' : 'var(--ink)');
    title.textContent = hit.page.title;
    var sub = document.createElement('span');
    sub.style.cssText = 'font-size:0.75rem;color:var(--ink-3);overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap';
    sub.textContent = hit.hint;
    left.appendChild(title);
    left.appendChild(sub);

    var door = document.createElement('span');
    door.style.cssText = "font-family:'Spline Sans Mono',ui-monospace,monospace;" +
      'font-size:0.625rem;letter-spacing:0.06em;color:var(--ink-3);flex:none';
    door.textContent = hit.page.door || '';

    a.appendChild(left);
    a.appendChild(door);
    return a;
  }

  if (openBtn) openBtn.addEventListener('click', open);
  if (panel) {
    panel.addEventListener('click', function (e) { if (e.target === panel) close(); });
  }
  if (input) {
    input.addEventListener('input', function () { sel = 0; draw(input.value); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, hits.length - 1); draw(input.value); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); draw(input.value); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (hits[sel]) window.location.href = hits[sel].page.slug + '.html';
      } else if (e.key === 'Escape') { close(); }
    });
  }

  document.addEventListener('keydown', function (e) {
    if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      open();
      return;
    }
    if (e.key !== 'Escape') return;
    close();
    var all = document.querySelectorAll('details.ptmenu');
    for (var i = 0; i < all.length; i++) all[i].removeAttribute('open');
  });

  /* The one control that does not work without script, so it does not
   * appear without script either. */
  if (openBtn) openBtn.hidden = false;
})();
