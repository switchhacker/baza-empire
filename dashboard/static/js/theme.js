/* Theme toggle. Reads theme cookie at load, sets <html data-theme="...">,
 * renders a button into [data-theme-mount] (or the body's first <header>/.nav
 * if no mount is specified), and POSTs /settings/theme on click. */
(function () {
  function getCookie(name) {
    return document.cookie.split('; ').reduce(function (acc, c) {
      var parts = c.split('=');
      return parts[0] === name ? decodeURIComponent(parts.slice(1).join('=')) : acc;
    }, null);
  }
  function setCookie(name, value, days) {
    var max = days * 24 * 60 * 60;
    document.cookie = name + '=' + encodeURIComponent(value) + '; path=/; max-age=' + max + '; SameSite=Lax';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function postTheme(theme) {
    return fetch('/settings/theme', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: theme}),
      credentials: 'same-origin',
    });
  }

  function toggle() {
    var current = document.documentElement.getAttribute('data-theme') || 'dark';
    var next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setCookie('theme', next, 365);
    postTheme(next).catch(function () { /* silent — cookie still wins */ });
    var btn = document.querySelector('[data-theme-button]');
    if (btn) btn.textContent = next === 'dark' ? '☀' : '☾';
  }

  function mount() {
    var initial = getCookie('theme') || document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(initial);

    var host = document.querySelector('[data-theme-mount]');
    if (!host) {
      host = document.querySelector('header') || document.querySelector('.nav') || document.querySelector('.topbar');
    }
    if (!host) return;

    var btn = document.createElement('button');
    btn.className = 'theme-toggle';
    btn.setAttribute('data-theme-button', '');
    btn.title = 'Toggle theme';
    btn.textContent = initial === 'dark' ? '☀' : '☾';
    btn.addEventListener('click', toggle);
    host.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
