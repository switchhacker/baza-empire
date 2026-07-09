/* Baza Visual Editor — overrides engine + Edit Mode.
   Spec: docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md (B1).
   Loaded on EVERY dashboard page via _nav.html, referenced with a ?v= cache-bust
   that defeats the browser HTTP cache (there is no service worker caching this file).
   Two halves:
   1) apply engine — always on: fetch /api/ui/overrides for this page, apply,
      re-apply on DOM mutation (dashboard pages render lots of content via JS).
   2) Edit Mode — ✏️ toggle: hover-highlight, click-select, inspector panel
      (panel body built in buildInspector(), Task 6). */
(function () {
'use strict';

var PAGE = location.pathname.split('?')[0].replace(/\/+$/, '') || '/';
var API = '/api/ui/overrides';
var OVERRIDES = [];
var editMode = false;
var selected = null;
var styleEl = null;
var applyTimer = null;
var hoverTarget = null;

/* ---------- selectors + fingerprints ---------- */
function esc(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(s)
       : String(s).replace(/([^a-zA-Z0-9_-])/g, '\\$1');
}
function selectorFor(el) {
  if (el.id) return '#' + esc(el.id);
  var dt = el.getAttribute && el.getAttribute('data-tab');
  if (dt) {
    var s = el.tagName.toLowerCase() + '[data-tab="' + esc(dt) + '"]';
    if (document.querySelectorAll(s).length === 1) return s;
  }
  var parts = [], cur = el;
  while (cur && cur !== document.body && parts.length < 7) {
    if (cur.id) { parts.unshift('#' + esc(cur.id)); break; }
    var part = cur.tagName.toLowerCase();
    var par = cur.parentElement;
    if (par) {
      var same = Array.prototype.filter.call(par.children, function (c) { return c.tagName === cur.tagName; });
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
    }
    parts.unshift(part);
    var cand = parts.join(' > ');
    try { if (document.querySelectorAll(cand).length === 1) return cand; } catch (e) {}
    cur = par;
  }
  return parts.join(' > ');
}
function fingerprintFor(el) {
  return {
    tag: el.tagName.toLowerCase(),
    text: (el.textContent || '').trim().slice(0, 60),
    cls: (el.getAttribute && el.getAttribute('class') || '').slice(0, 120)
  };
}

/* ---------- apply engine ---------- */
function ensureSheet() {
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'baza-ov-css';
    document.head.appendChild(styleEl);
  }
  return styleEl;
}
function cssProps(props) {
  return Object.keys(props || {}).map(function (k) {
    var css = k.replace(/[A-Z]/g, function (m) { return '-' + m.toLowerCase(); });
    return css + ':' + props[k] + ' !important';
  }).join(';');
}
function rebuildSheet() {
  var rules = [];
  OVERRIDES.forEach(function (o) {
    try {
      if (o.kind === 'style' && o.value) rules.push(o.selector + '{' + cssProps(o.value) + '}');
      else if (o.kind === 'hide' && o.value !== false) rules.push(o.selector + '{display:none !important}');
    } catch (e) {}
  });
  ensureSheet().textContent = rules.join('\n');
}
function applyDom() {
  OVERRIDES.forEach(function (o) {
    if (o.kind === 'style' || o.kind === 'hide') return; // sheet handles these
    var els;
    try { els = document.querySelectorAll(o.selector); } catch (e) { return; }
    if (!els.length) { o._stale = true; return; }
    o._stale = false;
    Array.prototype.forEach.call(els, function (el) {
      // every mutation is guarded by a value check → no MutationObserver loops
      if (o.kind === 'text') {
        if (el.textContent !== o.value && document.activeElement !== el) el.textContent = o.value;
      } else if (o.kind === 'image') {
        if (el.tagName === 'IMG' && el.getAttribute('src') !== o.value) el.setAttribute('src', o.value);
      } else if (o.kind === 'link') {
        if (el.tagName === 'A' && el.getAttribute('href') !== o.value) el.setAttribute('href', o.value);
      } else if (o.kind === 'attr' && o.value && o.value.name) {
        if (el.getAttribute(o.value.name) !== o.value.value) el.setAttribute(o.value.name, o.value.value);
      } else if (o.kind === 'order' && Array.isArray(o.value)) {
        var kids = o.value.map(function (s) {
          try { return el.querySelector(':scope > ' + s); } catch (e) { return null; }
        }).filter(Boolean).filter(function (k, i, a) { return a.indexOf(k) === i; });
        var current = Array.prototype.filter.call(el.children, function (c) { return kids.indexOf(c) !== -1; });
        var moved = kids.some(function (k, i) { return k !== current[i]; });
        if (moved) kids.forEach(function (k) { el.appendChild(k); });
      }
    });
  });
}
function scheduleApply() {
  clearTimeout(applyTimer);
  applyTimer = setTimeout(applyDom, 150);
}
function refresh() {
  return fetch(API + '?page=' + encodeURIComponent(PAGE))
    .then(function (r) { return r.json(); })
    .then(function (j) { OVERRIDES = j.overrides || []; rebuildSheet(); applyDom(); })
    .catch(function () {});
}

/* ---------- edit mode ---------- */
function inChrome(t) {
  return !!(t.closest && t.closest('#baza-edit-panel,#baza-edit-toggle,#baza-edit-hint'));
}
function setEditMode(on) {
  // Exiting edit mode must first flush any live "Edit on page" session: Esc
  // doesn't blur a contenteditable node, so force the blur while `selected`
  // is still set — the blur handler then saves the edit normally.
  if (!on && document.activeElement && document.activeElement.hasAttribute &&
      document.activeElement.hasAttribute('contenteditable')) {
    document.activeElement.blur();
  }
  editMode = !!on;
  try { sessionStorage.setItem('bazaEdit', on ? '1' : '0'); } catch (e) {}
  document.body.classList.toggle('baza-editing', editMode);
  var btn = document.getElementById('baza-edit-toggle');
  if (btn) btn.classList.toggle('on', editMode);
  var hint = document.getElementById('baza-edit-hint');
  if (editMode && !hint) {
    hint = document.createElement('div');
    hint.id = 'baza-edit-hint';
    hint.textContent = '✏️ Edit Mode — click any element · Esc to exit';
    document.body.appendChild(hint);
  } else if (!editMode && hint) hint.remove();
  if (!editMode) { clearSel(); hidePanel(); }
}
function clearSel() {
  if (hoverTarget) { hoverTarget.classList.remove('baza-hover'); hoverTarget = null; }
  if (selected) { selected.classList.remove('baza-selected'); selected = null; }
}
function select(el) {
  if (selected) selected.classList.remove('baza-selected');
  selected = el;
  selected.classList.add('baza-selected');
  showPanel(); // Task 6 fills the panel; core provides the hook
}
/* Panel shell — buildInspector(panel) is defined in the inspector half (Task 6). */
function showPanel() {
  var p = document.getElementById('baza-edit-panel');
  if (!p) {
    p = document.createElement('div');
    p.id = 'baza-edit-panel';
    document.body.appendChild(p); // body-level: never inside a tab pane
  }
  p.style.display = 'block';
  if (typeof buildInspector === 'function') buildInspector(p);
}
function hidePanel() {
  var p = document.getElementById('baza-edit-panel');
  if (p) p.style.display = 'none';
}

function saveOverride(kind, value) {
  if (!selected) return Promise.resolve(false);
  return fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      page: PAGE, selector: selectorFor(selected), kind: kind, value: value,
      fingerprint: fingerprintFor(selected)
    })
  }).then(function (r) {
    if (!r.ok) throw new Error('save failed: ' + r.status);
    return refresh();
  }).then(function () { return true; })
    .catch(function () { return false; });
}

/* ---------- inspector panel (Task 6) ---------- */
var STYLE_FIELDS = [
  // [override style prop, label, input type]
  ['color',           'Text color',   'color'],
  ['backgroundColor', 'Background',   'color'],
  ['fontSize',        'Font size',    'px'],
  ['fontWeight',      'Weight',       'select:normal,600,700,800'],
  ['fontFamily',      'Font',         'text'],
  ['textAlign',       'Align',        'select:left,center,right'],
  ['padding',         'Padding',      'text'],
  ['margin',          'Margin',       'text'],
  ['border',          'Border',       'text'],
  ['borderRadius',    'Radius',       'px'],
  ['width',           'Width',        'text'],
  ['opacity',         'Opacity',      'text']
];
function existingStyle() {
  if (!selected) return {};
  var sel = selectorFor(selected);
  var found = {};
  OVERRIDES.forEach(function (o) {
    if (o.kind === 'style' && o.selector === sel && o.value) found = o.value;
  });
  return Object.assign({}, found);
}
function mkEl(tag, attrs, text) {
  var e = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
  if (text !== undefined) e.textContent = text;
  return e;
}
function section(panel, label) {
  var s = mkEl('div', { 'class': 'bep-sec' });
  s.appendChild(mkEl('div', { 'class': 'bep-lbl' }, label));
  panel.appendChild(s);
  return s;
}
function mkBtn(label, cls, fn) {
  var b = mkEl('button', { 'class': 'bep-btn' + (cls ? ' ' + cls : '') }, label);
  b.addEventListener('click', fn);
  return b;
}
function toast(msg) {
  var h = document.getElementById('baza-edit-hint');
  if (h) { h.textContent = msg; setTimeout(function () { if (editMode && h) h.textContent = '✏️ Edit Mode — click any element · Esc to exit'; }, 1600); }
}
function buildInspector(panel) {
  if (!selected) return;
  panel.innerHTML = '';
  var sel = selectorFor(selected);

  // head
  var head = mkEl('div', { 'class': 'bep-head' });
  head.appendChild(mkEl('span', { 'class': 'bep-title' }, '✏️ <' + selected.tagName.toLowerCase() + '>'));
  var x = mkEl('span', { 'class': 'bep-x', title: 'Close (element stays selected until Esc)' }, '✕');
  x.addEventListener('click', hidePanel);
  head.appendChild(x);
  panel.appendChild(head);

  // selector info
  var info = section(panel, 'Element');
  info.appendChild(mkEl('div', { 'class': 'bep-sel' }, sel));

  // text — inline contenteditable on the page itself
  if (selected.childElementCount === 0) {
    var st = section(panel, 'Text');
    var ta = mkEl('textarea', { rows: '3' });
    ta.value = (selected.textContent || '').trim();
    st.appendChild(ta);
    st.appendChild(mkBtn('Save text', 'primary', function () {
      saveOverride('text', ta.value).then(function (ok) { toast(ok ? '✓ text saved' : '✗ save failed'); });
    }));
    st.appendChild(mkBtn('Edit on page', '', function () {
      var node = selected;
      if (!node || node.getAttribute('contenteditable') === 'true') { if (node) node.focus(); return; }
      node.setAttribute('contenteditable', 'true');
      node.focus();
      var done = function () {
        node.removeAttribute('contenteditable');
        node.removeEventListener('blur', done);
        ta.value = (node.textContent || '').trim();
        saveOverride('text', ta.value).then(function (ok) { toast(ok ? '✓ text saved' : '✗ save failed'); });
      };
      node.addEventListener('blur', done);
    }));
    st.appendChild(mkEl('div', { 'class': 'bep-note' }, 'Edit on page: type directly into the element, click away to save.'));
  }

  // image
  if (selected.tagName === 'IMG') {
    var si = section(panel, 'Image');
    var url = mkEl('input', { type: 'text', placeholder: 'Image URL or /static/... path' });
    url.value = selected.getAttribute('src') || '';
    si.appendChild(url);
    var file = mkEl('input', { type: 'file', accept: 'image/*' });
    si.appendChild(file);
    file.addEventListener('change', function () {
      if (!file.files.length) return;
      var fd = new FormData();
      fd.append('file', file.files[0]);
      fetch('/api/ui/upload', { method: 'POST', body: fd })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.url) { url.value = j.url; toast('uploaded — hit Save image'); }
          else toast('✗ ' + (j.error || 'upload failed'));
        })
        .catch(function () { toast('✗ upload failed'); });
    });
    si.appendChild(mkBtn('Save image', 'primary', function () {
      saveOverride('image', url.value).then(function (ok) { toast(ok ? '✓ image saved' : '✗ save failed'); });
    }));
  }

  // link
  if (selected.tagName === 'A') {
    var sl = section(panel, 'Link');
    var href = mkEl('input', { type: 'text' });
    href.value = selected.getAttribute('href') || '';
    sl.appendChild(href);
    sl.appendChild(mkBtn('Save link', 'primary', function () {
      saveOverride('link', href.value).then(function (ok) { toast(ok ? '✓ link saved' : '✗ save failed'); });
    }));
  }

  // style
  var ss = section(panel, 'Style');
  var cur = existingStyle();
  var inputs = {};
  STYLE_FIELDS.forEach(function (f) {
    var prop = f[0], label = f[1], type = f[2];
    var row = mkEl('div', { 'class': 'bep-row' });
    row.appendChild(mkEl('label', {}, label));
    var inp;
    var after = null;
    if (type === 'color') {
      inp = mkEl('input', { type: 'color' });
      if (cur[prop]) inp.value = cur[prop];
      after = mkBtn('✕', '', function () {
        inp.dataset.cleared = '1';
        inp.dataset.dirty = '1';
      });
      after.setAttribute('title', 'Clear this color (removes the property on Apply)');
    } else if (type === 'px') {
      inp = mkEl('input', { type: 'number', placeholder: 'px' });
      var n = parseInt(cur[prop], 10);
      if (!isNaN(n)) inp.value = n;
    } else if (type.indexOf('select:') === 0) {
      inp = mkEl('select');
      inp.appendChild(mkEl('option', { value: '' }, '—'));
      type.slice(7).split(',').forEach(function (o) {
        var op = mkEl('option', { value: o }, o);
        if (cur[prop] === o) op.setAttribute('selected', '');
        inp.appendChild(op);
      });
    } else {
      inp = mkEl('input', { type: 'text', placeholder: 'e.g. 8px 12px' });
      if (cur[prop]) inp.value = cur[prop];
    }
    inp.dataset.dirty = '';
    inp.addEventListener('input', function () { inp.dataset.dirty = '1'; delete inp.dataset.cleared; });
    inp.addEventListener('change', function () { inp.dataset.dirty = '1'; delete inp.dataset.cleared; });
    inputs[prop] = { inp: inp, type: type };
    row.appendChild(inp);
    if (after) row.appendChild(after);
    ss.appendChild(row);
  });
  ss.appendChild(mkBtn('Apply style', 'primary', function () {
    var props = existingStyle();
    Object.keys(inputs).forEach(function (prop) {
      var rec = inputs[prop];
      if (!rec.inp.dataset.dirty) return;         // only send touched fields
      if (rec.inp.dataset.cleared) { delete props[prop]; return; }
      var v = rec.inp.value;
      if (v === '' || v === null) { delete props[prop]; return; }
      props[prop] = (rec.type === 'px') ? v + 'px' : v;
    });
    saveOverride('style', props).then(function (ok) { toast(ok ? '✓ style saved' : '✗ save failed'); });
  }));
  ss.appendChild(mkEl('div', { 'class': 'bep-note' }, 'Only fields you touched are saved. Clear a field to remove that property.'));

  // visibility + reset
  var sv = section(panel, 'Element actions');
  sv.appendChild(mkBtn('🙈 Hide element', '', function () {
    saveOverride('hide', true).then(function (ok) { toast(ok ? 'hidden — restore from /web history' : '✗ save failed'); });
  }));
  sv.appendChild(mkBtn('↺ Reset element', 'danger', function () {
    fetch(API + '/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: PAGE, selector: sel })
    }).then(function (r) {
      if (r.ok) location.reload(); else toast('✗ reset failed');
    }).catch(function () { toast('✗ reset failed'); });
  }));
  sv.appendChild(mkEl('div', { 'class': 'bep-note' }, 'Reset element reverts every override on this selector and reloads. Full page history & revert: 🌐 Web tab.'));
}

/* ---------- wiring ---------- */
function initToggle() {
  if (document.getElementById('baza-edit-toggle')) return;
  var btn = document.createElement('button');
  btn.id = 'baza-edit-toggle';
  btn.title = 'Edit Mode — click any element on the page to edit it';
  btn.textContent = '✏️';
  btn.addEventListener('click', function () { setEditMode(!editMode); });
  var host = document.querySelector('.nav-right');
  if (host) host.appendChild(btn);
  else { btn.classList.add('floating'); document.body.appendChild(btn); }
  var qs = new URLSearchParams(location.search);
  if (qs.get('edit') === '1' || sessionStorage.getItem('bazaEdit') === '1') setEditMode(true);
}
document.addEventListener('mouseover', function (e) {
  if (!editMode || inChrome(e.target)) return;
  if (hoverTarget) hoverTarget.classList.remove('baza-hover');
  hoverTarget = e.target;
  hoverTarget.classList.add('baza-hover');
}, true);
document.addEventListener('click', function (e) {
  if (!editMode || inChrome(e.target)) return;
  e.preventDefault();
  e.stopPropagation();
  select(e.target);
}, true);
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && editMode) setEditMode(false);
});

function boot() {
  initToggle();
  refresh().then(function () {
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var t = muts[i].target;
        if (!(t.closest && t.closest('#baza-edit-panel'))) { scheduleApply(); return; }
      }
    }).observe(document.body, { childList: true, subtree: true });
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

window.BazaEdit = {
  selectorFor: selectorFor, fingerprintFor: fingerprintFor,
  saveOverride: saveOverride, refresh: refresh, setEditMode: setEditMode,
  getSelected: function () { return selected; },
  _overrides: function () { return OVERRIDES; }
};
})();
