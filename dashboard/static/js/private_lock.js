/* Tab-hidden auto-lock for the private gallery + Vision UI.
 *
 * When the tab loses visibility, start a 30s timer. If the tab becomes
 * visible again before the timer fires, cancel. If the timer fires,
 * POST /api/datahub/private/lock and reload — landing the user back on
 * the unlock form.
 *
 * The "🔓 Stay unlocked 12h" toggle in /vision sets the cookie
 * `vision_keep_unlocked=1`, which disables this behavior. Same toggle
 * applies on /datahub/private — both pages read the same cookie.
 */
(function () {
  var TIMEOUT_MS = 30 * 1000;
  var hideTimer = null;

  function stayUnlocked() {
    return document.cookie.split('; ').some(function (c) {
      return c === 'vision_keep_unlocked=1' || c.indexOf('vision_keep_unlocked=1;') === 0;
    });
  }

  function lockAndReload() {
    fetch('/api/datahub/private/lock', {
      method: 'POST', credentials: 'same-origin',
    }).then(function () {
      window.location.reload();
    }).catch(function () {
      window.location.reload();
    });
  }

  document.addEventListener('visibilitychange', function () {
    if (stayUnlocked()) {
      // User opted in to long sessions — clear any pending timer and skip.
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
})();
