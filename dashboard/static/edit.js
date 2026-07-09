/* Baza Visual Editor — overrides engine + Edit Mode.
   Spec: docs/superpowers/specs/2026-07-08-nav-fixes-and-visual-editor-design.md (B1).
   Loaded on EVERY dashboard page via _nav.html. Two halves:
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
    var s = el.tagName.toLowerCase() + '[data-tab="' + dt + '"]';
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
        }).filter(Boolean);
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
  if (!selected) return Promise.resolve();
  return fetch(API, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      page: PAGE, selector: selectorFor(selected), kind: kind, value: value,
      fingerprint: fingerprintFor(selected)
    })
  }).then(function () { return refresh(); });
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
