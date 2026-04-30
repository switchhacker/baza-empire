/* Tab-hidden auto-lock for the private gallery.
 *
 * When the tab loses visibility, start a 30s timer. If the tab becomes
 * visible again before the timer fires, cancel. If the timer fires,
 * POST /api/datahub/private/lock and reload — landing the user back on
 * the unlock form.
 *
 * The page header has a [data-keep-toggle] button that flips the
 * `vision_keep_unlocked=1` cookie to disable this behavior for 12h.
 */
(function () {
  var TIMEOUT_MS = 30 * 1000;
  var hideTimer = null;

  function getCookie(name) {
    return document.cookie.split('; ').reduce(function (acc, c) {
      var p = c.split('='); return p[0] === name ? decodeURIComponent(p.slice(1).join('=')) : acc;
    }, null);
  }
  function setCookie(name, value, days) {
    document.cookie = name + '=' + encodeURIComponent(value) + '; path=/; max-age=' +
      (days * 24 * 60 * 60) + '; SameSite=Lax';
  }

  function stayUnlocked() { return getCookie('vision_keep_unlocked') === '1'; }

  function lockAndReload() {
    fetch('/api/datahub/private/lock', {
      method: 'POST', credentials: 'same-origin',
    }).finally(function () { window.location.reload(); });
  }

  document.addEventListener('visibilitychange', function () {
    if (stayUnlocked()) {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      return;
    }
    if (document.hidden) {
      hideTimer = setTimeout(lockAndReload, TIMEOUT_MS);
    } else if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  });

  // Header toggle: flip the cookie + tell the server to extend session
  // permanence. Off (default) = 30s auto-lock. On = stays unlocked 12h.
  function applyKeepToggle(on) {
    var btn = document.getElementById('keepToggle');
    var label = document.getElementById('keepLabel');
    if (!btn || !label) return;
    if (on) {
      btn.classList.add('on');
      label.textContent = 'Stay unlocked 12h';
      btn.firstChild.textContent = '🔓 ';
    } else {
      btn.classList.remove('on');
      label.textContent = 'Auto-lock 30s';
      btn.firstChild.textContent = '🔒 ';
    }
  }

  function bootToggle() {
    var btn = document.getElementById('keepToggle');
    if (!btn) return;
    applyKeepToggle(stayUnlocked());
    btn.addEventListener('click', function () {
      var nowOn = !btn.classList.contains('on');
      applyKeepToggle(nowOn);
      setCookie('vision_keep_unlocked', nowOn ? '1' : '0', 365);
      fetch('/api/vision/keep-unlocked', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({value: nowOn}), credentials: 'same-origin',
      }).catch(function () { /* silent — cookie already updated optimistically */ });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootToggle);
  } else {
    bootToggle();
  }
})();
