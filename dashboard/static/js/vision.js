/* Vision UI client — fetches /api/vision/* endpoints, renders tree/grid/modal. */
(function () {
  var state = { path: '/Catalogue', page: 1, limit: 60 };

  function el(id) { return document.getElementById(id); }
  function setBreadcrumb(path) { el('breadcrumb').textContent = 'Vision ▸ ' + path.replace(/^\//,'').replace(/\//g, ' ▸ '); }

  // ── Tree ────────────────────────────────────────────────────────────────
  function renderTreeNode(n, depth) {
    var div = document.createElement('div');
    div.className = 'tree-node' + (n.path === state.path ? ' active' : '');
    div.style.paddingLeft = (8 + depth * 12) + 'px';
    var label = n.label;
    var count = (n.count == null) ? '' : n.count;
    div.innerHTML = '<span>' + label + '</span><span class="tree-count">' + count + '</span>';
    div.addEventListener('click', function (e) { e.stopPropagation(); navigate(n.path); });
    var wrap = document.createElement('div');
    wrap.appendChild(div);
    if (n.children && n.children.length) {
      var c = document.createElement('div'); c.className = 'tree-children';
      n.children.forEach(function (cc) { c.appendChild(renderTreeNode(cc, depth + 1)); });
      wrap.appendChild(c);
    }
    return wrap;
  }
  function refreshTree() {
    return fetch('/api/vision/tree').then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) return;
      var root = el('tree'); root.innerHTML = '';
      j.tree.forEach(function (n) { root.appendChild(renderTreeNode(n, 0)); });
    });
  }

  // ── Grid + pager ────────────────────────────────────────────────────────
  function renderAssets(assets) {
    var c = el('content');
    c.innerHTML = '';
    if (!assets.length) {
      c.innerHTML = '<div class="empty">No assets in this folder yet. ' +
        '<button class="btn" id="seedBtn" style="margin-left:12px">Specter: fill this folder</button></div>';
      var sb = el('seedBtn'); if (sb) sb.addEventListener('click', requestSeed);
      return;
    }
    var grid = document.createElement('div'); grid.className = 'grid';
    assets.forEach(function (a) {
      var div = document.createElement('div'); div.className = 'card';
      var img = document.createElement('img');
      img.src = '/api/vision/asset/' + a.id + '/thumb';
      img.alt = '';
      img.loading = 'lazy';
      div.appendChild(img);
      var b = document.createElement('span'); b.className = 'badge'; b.textContent = a.source;
      div.appendChild(b);
      div.addEventListener('click', function () { openAsset(a.id); });
      grid.appendChild(div);
    });
    c.appendChild(grid);
  }

  function renderPager(total, page, pages) {
    var p = el('pager'); p.innerHTML = '';
    if (pages <= 1) { p.textContent = total + ' items'; return; }
    var prev = document.createElement('button'); prev.textContent = '⟵ prev'; prev.disabled = page <= 1;
    prev.addEventListener('click', function () { state.page = page - 1; loadBrowse(); });
    var info = document.createElement('span'); info.textContent = 'page ' + page + ' / ' + pages + ' — ' + total + ' items';
    var next = document.createElement('button'); next.textContent = 'next ⟶'; next.disabled = page >= pages;
    next.addEventListener('click', function () { state.page = page + 1; loadBrowse(); });
    p.appendChild(prev); p.appendChild(info); p.appendChild(next);
  }

  function loadBrowse() {
    var q = '?path=' + encodeURIComponent(state.path) + '&page=' + state.page + '&limit=' + state.limit;
    return fetch('/api/vision/browse' + q).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) { el('content').innerHTML = '<div class="empty">' + (j.error || 'error') + '</div>'; return; }
      setBreadcrumb(state.path);
      renderAssets(j.assets);
      renderPager(j.total, j.page, j.pages);
      // Seed CTA if thin.
      var c = el('content');
      if (j.assets.length > 0 && j.total < (j.node.target || 6)) {
        var cta = document.createElement('div'); cta.className = 'seed-cta';
        cta.innerHTML = '<span>This folder is thin (' + j.total + ' / ' + (j.node.target || 6) + ').</span>' +
          '<button class="btn" id="seedBtn">Specter: fill this folder now</button>';
        c.appendChild(cta);
        el('seedBtn').addEventListener('click', requestSeed);
      }
    });
  }

  function navigate(path) { state.path = path; state.page = 1; refreshTree(); loadBrowse(); }

  // ── Modal ───────────────────────────────────────────────────────────────
  function openAsset(id) {
    fetch('/api/vision/asset/' + id).then(function (r) { return r.json(); }).then(function (j) {
      if (!j.ok) return;
      el('modalImg').src = '/api/vision/asset/' + id + '/thumb?full=1';
      var meta = el('modalMeta');
      var rows = ['<h3>Asset #' + j.asset.id + '</h3>'];
      rows.push('<div class="meta-row"><div class="meta-key">source</div><div class="meta-val">' + j.asset.source + '</div></div>');
      if (j.caption && j.caption.caption) rows.push('<div class="meta-row"><div class="meta-key">caption</div><div class="meta-val">' + j.caption.caption + '</div></div>');
      Object.keys(j.attributes).sort().forEach(function (k) {
        rows.push('<div class="meta-row"><div class="meta-key">' + k + '</div><div class="meta-val">' + j.attributes[k].value + '</div></div>');
      });
      meta.innerHTML = rows.join('');
      el('modal').classList.add('open');
    });
  }
  el('modal').addEventListener('click', function (e) {
    if (e.target.id === 'modal') el('modal').classList.remove('open');
  });

  // ── Seed CTA ────────────────────────────────────────────────────────────
  function requestSeed() {
    fetch('/api/vision/specter/seed', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: state.path}),
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (j.ok) alert('Specter queued. ETA ~' + (j.eta_seconds / 60) + ' min.');
      else alert('Error: ' + (j.error || 'unknown'));
    });
  }

  // ── Search ──────────────────────────────────────────────────────────────
  var searchTimer = null;
  el('search').addEventListener('input', function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    searchTimer = setTimeout(function () {
      if (!q) return loadBrowse();
      fetch('/api/vision/search?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (j) {
        if (!j.ok) return;
        setBreadcrumb('Search: ' + q);
        renderAssets(j.assets);
        renderPager(j.assets.length, 1, 1);
      });
    }, 280);
  });

  // ── Clock ───────────────────────────────────────────────────────────────
  function tickClock() {
    var d = new Date();
    el('clock').textContent = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  }
  setInterval(tickClock, 30 * 1000); tickClock();

  // ── Boot ────────────────────────────────────────────────────────────────
  refreshTree().then(loadBrowse);
})();
