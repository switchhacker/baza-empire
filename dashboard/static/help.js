/* Hover-help: elements with data-help="<key>" get a ? badge whose hover/tap
   shows numbered steps from /static/help_content.json. Popover lives on
   document.body (never trapped by display:none ancestors). */
(function () {
  let REG = null, pop = null, hideTimer = null;

  async function reg() {
    if (!REG) REG = await (await fetch('/static/help_content.json')).json();
    return REG;
  }

  let docClickListener = null;

  function onDocClick(e) {
    if (pop && !pop.contains(e.target) && !e.target.classList.contains('help-badge')) {
      hide();
    }
  }

  function hide() {
    if (pop) {
      pop.remove();
      pop = null;
    }
    if (docClickListener) {
      document.removeEventListener('click', docClickListener);
      docClickListener = null;
    }
  }

  async function show(badge, key) {
    const r = await reg(); const e = r[key];
    if (!e) return;
    hide();
    pop = document.createElement('div');
    pop.className = 'help-pop';
    const ol = e.steps.map(s => `<li>${s}</li>`).join('');
    const link = (e.link && (/^https?:\/\//i.test(e.link) || e.link.startsWith('/'))) ? `<div style="margin-top:6px"><a href="${e.link}">more →</a></div>` : '';
    pop.innerHTML = `<h4>${e.title}</h4><ol>${ol}</ol>${link}`;
    document.body.appendChild(pop);
    const b = badge.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(b.left, innerWidth - pop.offsetWidth - 12)) + 'px';
    pop.style.top = (b.bottom + 6 + pop.offsetHeight > innerHeight
      ? b.top - pop.offsetHeight - 6 : b.bottom + 6) + 'px';
    pop.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    pop.addEventListener('mouseleave', () => hideTimer = setTimeout(hide, 200));
    docClickListener = onDocClick;
    document.addEventListener('click', docClickListener);
  }

  function attach(el) {
    if (el.dataset.helpDone) return;
    el.dataset.helpDone = '1';
    const badge = document.createElement('span');
    badge.className = 'help-badge'; badge.textContent = '?';
    badge.setAttribute('role', 'button');
    el.insertAdjacentElement('afterend', badge);
    badge.addEventListener('mouseenter', () => show(badge, el.dataset.help));
    badge.addEventListener('mouseleave', () => hideTimer = setTimeout(hide, 200));
    badge.addEventListener('click', ev => { ev.stopPropagation(); pop ? hide() : show(badge, el.dataset.help); });
  }

  function scan() { document.querySelectorAll('[data-help]').forEach(attach); }
  document.addEventListener('DOMContentLoaded', scan);
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
})();
