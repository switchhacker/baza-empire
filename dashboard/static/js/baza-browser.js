/**
 * BazaBrowser — Lightweight persistent web browser for the Baza Empire dashboard.
 * Manages tabs, navigation, bookmarks, history, and proxy routing.
 * All iframes persist in the DOM — switching tabs shows/hides, never reloads.
 */

class BazaTab {
    constructor(id, url = '') {
        this.id = id;
        this.url = url || '';
        this.title = url ? new URL(url, location.origin).hostname : 'New Tab';
        this.favicon = '';
        this.iframe = null;
        this.loading = false;
        this.historyStack = url ? [url] : [];
        this.historyIndex = url ? 0 : -1;
        this.suspended = false;
        this.lastActive = Date.now();
    }
}

class BazaBrowser {
    constructor(container) {
        this.container = container;
        this.tabs = [];
        this.activeTabId = null;
        this.bookmarks = [];
        this.searchEngine = 'https://duckduckgo.com/?q=';
        this.proxyBase = '/api/browser/proxy?url=';
        this.maxTabs = 8;
        this.suspendAfterMs = 10 * 60 * 1000; // 10 min
        this._suspendTimer = null;
        this._tabIdCounter = 0;
        this._built = false;
    }

    // ── Init ─────────────────────────────────────────────────────────────────
    init() {
        if (this._built) return;
        this._buildUI();
        this._bindKeys();
        this._restoreState();
        if (!this.tabs.length) this.createTab('');
        this._startSuspendTimer();
        this._built = true;
    }

    // ── UI Build ─────────────────────────────────────────────────────────────
    _buildUI() {
        this.container.innerHTML = `
        <div class="bb-toolbar">
            <button class="bb-btn" id="bb-back" title="Back (Alt+←)">◀</button>
            <button class="bb-btn" id="bb-fwd" title="Forward (Alt+→)">▶</button>
            <button class="bb-btn" id="bb-reload" title="Reload (Ctrl+R)">↻</button>
            <input class="bb-address" id="bb-address" type="text" placeholder="Search or enter URL..." spellcheck="false">
            <button class="bb-btn bb-star" id="bb-bookmark" title="Bookmark">☆</button>
            <button class="bb-btn" id="bb-newtab" title="New tab (Ctrl+T)">+</button>
            <button class="bb-btn" id="bb-menu" title="Menu">≡</button>
        </div>
        <div class="bb-tabstrip" id="bb-tabstrip"></div>
        <div class="bb-content" id="bb-content"></div>
        <div class="bb-menu-dropdown" id="bb-menu-dropdown" style="display:none">
            <div class="bb-menu-item" data-action="bookmarks">☆ Bookmarks</div>
            <div class="bb-menu-item" data-action="history">⏱ History</div>
            <div class="bb-menu-item" data-action="downloads">↓ Downloads</div>
            <div class="bb-menu-item" data-action="devtools">⚙ Dev Tools</div>
            <div class="bb-menu-item" data-action="clear-history">✕ Clear History</div>
        </div>
        <div class="bb-panel-overlay" id="bb-panel" style="display:none">
            <div class="bb-panel-header">
                <span id="bb-panel-title">Bookmarks</span>
                <button class="bb-btn" onclick="bazaBrowser._closePanel()">✕</button>
            </div>
            <div class="bb-panel-body" id="bb-panel-body"></div>
        </div>
        `;
        // Bind toolbar
        this.container.querySelector('#bb-back').onclick = () => this.goBack();
        this.container.querySelector('#bb-fwd').onclick = () => this.goForward();
        this.container.querySelector('#bb-reload').onclick = () => this.reload();
        this.container.querySelector('#bb-newtab').onclick = () => this.createTab('');
        this.container.querySelector('#bb-bookmark').onclick = () => this.toggleBookmark();
        this.container.querySelector('#bb-menu').onclick = () => this._toggleMenu();

        const addr = this.container.querySelector('#bb-address');
        addr.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                this.handleInput(addr.value.trim());
                addr.blur();
            }
        });
        addr.addEventListener('focus', () => addr.select());

        // Menu items
        this.container.querySelectorAll('.bb-menu-item').forEach(el => {
            el.onclick = () => {
                this._closeMenu();
                this._handleMenuAction(el.dataset.action);
            };
        });
    }

    // ── Tab Management ───────────────────────────────────────────────────────
    createTab(url) {
        // Suspend oldest if at max
        if (this.tabs.length >= this.maxTabs) {
            const oldest = this.tabs.filter(t => t.id !== this.activeTabId)
                .sort((a, b) => a.lastActive - b.lastActive)[0];
            if (oldest) this.suspendTab(oldest.id);
        }

        const id = 'bt-' + (++this._tabIdCounter);
        const tab = new BazaTab(id, url);
        this.tabs.push(tab);

        // Create iframe
        const iframe = document.createElement('iframe');
        iframe.className = 'bb-iframe';
        iframe.id = id;
        iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation allow-downloads');
        iframe.style.display = 'none';
        iframe.addEventListener('load', () => this._onIframeLoad(id));
        this.container.querySelector('#bb-content').appendChild(iframe);
        tab.iframe = iframe;

        if (url) this._loadUrl(tab, url);
        this.switchTab(id);
        this._renderTabStrip();
        this.saveState();
        return tab;
    }

    closeTab(tabId) {
        const idx = this.tabs.findIndex(t => t.id === tabId);
        if (idx === -1) return;
        const tab = this.tabs[idx];
        if (tab.iframe) tab.iframe.remove();
        this.tabs.splice(idx, 1);

        if (this.tabs.length === 0) {
            this.createTab('');
            return;
        }
        if (this.activeTabId === tabId) {
            const next = this.tabs[Math.min(idx, this.tabs.length - 1)];
            this.switchTab(next.id);
        }
        this._renderTabStrip();
        this.saveState();
    }

    switchTab(tabId) {
        const tab = this.tabs.find(t => t.id === tabId);
        if (!tab) return;

        // Resume if suspended
        if (tab.suspended) this.resumeTab(tabId);

        // Hide all, show target
        this.tabs.forEach(t => { if (t.iframe) t.iframe.style.display = 'none'; });
        if (tab.iframe) tab.iframe.style.display = 'block';
        this.activeTabId = tabId;
        tab.lastActive = Date.now();

        // Update address bar
        this._updateAddressBar(tab);
        this._renderTabStrip();
        this.saveState();
    }

    // ── Navigation ───────────────────────────────────────────────────────────
    navigate(url) {
        const tab = this._activeTab();
        if (!tab) return;
        this._loadUrl(tab, url);
        this.saveState();
    }

    handleInput(text) {
        if (!text) return;
        let url;
        if (/^https?:\/\//i.test(text) || /^localhost/i.test(text)) {
            url = text.startsWith('http') ? text : 'http://' + text;
        } else if (/^[a-z0-9][-a-z0-9]*\.[a-z]{2,}/i.test(text)) {
            url = 'https://' + text;
        } else {
            url = this.searchEngine + encodeURIComponent(text);
        }
        this.navigate(url);
    }

    goBack() {
        const tab = this._activeTab();
        if (!tab || tab.historyIndex <= 0) return;
        tab.historyIndex--;
        this._loadUrl(tab, tab.historyStack[tab.historyIndex], true);
    }

    goForward() {
        const tab = this._activeTab();
        if (!tab || tab.historyIndex >= tab.historyStack.length - 1) return;
        tab.historyIndex++;
        this._loadUrl(tab, tab.historyStack[tab.historyIndex], true);
    }

    reload() {
        const tab = this._activeTab();
        if (!tab || !tab.iframe) return;
        tab.iframe.src = tab.iframe.src;
    }

    _loadUrl(tab, url, isHistoryNav = false) {
        if (!tab.iframe) return;
        tab.loading = true;
        tab.url = url;

        // Decide: proxy or direct
        const useProxy = this._shouldProxy(url);
        const iframeSrc = useProxy ? this.proxyBase + encodeURIComponent(url) : url;
        tab.iframe.src = iframeSrc;

        if (!isHistoryNav) {
            // Truncate forward history
            tab.historyStack = tab.historyStack.slice(0, tab.historyIndex + 1);
            tab.historyStack.push(url);
            tab.historyIndex = tab.historyStack.length - 1;
        }

        this._updateAddressBar(tab);
        this._renderTabStrip();

        // Log to history
        this._logHistory(url, tab.title);
    }

    _shouldProxy(url) {
        if (!url) return false;
        try {
            const u = new URL(url, location.origin);
            // Don't proxy localhost/dashboard URLs
            if (u.hostname === 'localhost' || u.hostname === '127.0.0.1') return false;
            if (u.hostname === location.hostname) return false;
            // Proxy everything external
            return true;
        } catch { return false; }
    }

    _onIframeLoad(tabId) {
        const tab = this.tabs.find(t => t.id === tabId);
        if (!tab) return;
        tab.loading = false;

        // Try to read title from iframe (same-origin only)
        try {
            const doc = tab.iframe.contentDocument;
            if (doc && doc.title) tab.title = doc.title.substring(0, 40);
        } catch {
            // Cross-origin — use hostname from URL
            try { tab.title = new URL(tab.url).hostname; } catch {}
        }
        this._renderTabStrip();
        this._updateAddressBar(tab);
    }

    // ── Suspend / Resume ─────────────────────────────────────────────────────
    suspendTab(tabId) {
        const tab = this.tabs.find(t => t.id === tabId);
        if (!tab || tab.suspended) return;
        if (tab.iframe) tab.iframe.remove();
        tab.iframe = null;
        tab.suspended = true;
        this._renderTabStrip();
        this.saveState();
    }

    resumeTab(tabId) {
        const tab = this.tabs.find(t => t.id === tabId);
        if (!tab || !tab.suspended) return;

        const iframe = document.createElement('iframe');
        iframe.className = 'bb-iframe';
        iframe.id = tabId;
        iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation allow-downloads');
        iframe.style.display = 'none';
        iframe.addEventListener('load', () => this._onIframeLoad(tabId));
        this.container.querySelector('#bb-content').appendChild(iframe);
        tab.iframe = iframe;
        tab.suspended = false;

        if (tab.url) this._loadUrl(tab, tab.url, true);
    }

    _startSuspendTimer() {
        this._suspendTimer = setInterval(() => {
            const now = Date.now();
            this.tabs.forEach(t => {
                if (t.id !== this.activeTabId && !t.suspended &&
                    now - t.lastActive > this.suspendAfterMs) {
                    this.suspendTab(t.id);
                }
            });
        }, 60000); // check every minute
    }

    // ── UI Rendering ─────────────────────────────────────────────────────────
    _renderTabStrip() {
        const strip = this.container.querySelector('#bb-tabstrip');
        strip.innerHTML = this.tabs.map(t => {
            const active = t.id === this.activeTabId ? ' bb-tab-active' : '';
            const suspended = t.suspended ? ' bb-tab-suspended' : '';
            const loading = t.loading ? ' bb-tab-loading' : '';
            const title = t.title || 'New Tab';
            const icon = t.loading ? '◌' : (t.suspended ? '⏸' : '●');
            return `<div class="bb-tab${active}${suspended}${loading}" data-id="${t.id}" onclick="bazaBrowser.switchTab('${t.id}')">
                <span class="bb-tab-icon">${icon}</span>
                <span class="bb-tab-title">${this._escHtml(title)}</span>
                <span class="bb-tab-close" onclick="event.stopPropagation();bazaBrowser.closeTab('${t.id}')">✕</span>
            </div>`;
        }).join('') + '<div class="bb-tab bb-tab-new" onclick="bazaBrowser.createTab(\'\')">+</div>';
    }

    _updateAddressBar(tab) {
        const addr = this.container.querySelector('#bb-address');
        if (addr && tab) addr.value = tab.url || '';
        // Update bookmark star
        const star = this.container.querySelector('#bb-bookmark');
        if (star) star.textContent = this.bookmarks.some(b => b.url === tab?.url) ? '★' : '☆';
    }

    // ── Bookmarks ────────────────────────────────────────────────────────────
    async toggleBookmark() {
        const tab = this._activeTab();
        if (!tab || !tab.url) return;
        const existing = this.bookmarks.findIndex(b => b.url === tab.url);
        if (existing >= 0) {
            const bm = this.bookmarks[existing];
            await fetch('/api/browser/bookmarks', { method: 'DELETE', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ id: bm.id }) });
            this.bookmarks.splice(existing, 1);
        } else {
            const res = await fetch('/api/browser/bookmarks', { method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ url: tab.url, title: tab.title }) });
            const bm = await res.json();
            this.bookmarks.push(bm);
        }
        this._updateAddressBar(tab);
    }

    async _loadBookmarks() {
        try {
            const res = await fetch('/api/browser/bookmarks');
            this.bookmarks = await res.json();
        } catch { this.bookmarks = []; }
    }

    // ── History ──────────────────────────────────────────────────────────────
    async _logHistory(url, title) {
        if (!url || url === 'about:blank') return;
        try {
            await fetch('/api/browser/history', { method: 'POST', headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ url, title }) });
        } catch {}
    }

    // ── Menu ─────────────────────────────────────────────────────────────────
    _toggleMenu() {
        const dd = this.container.querySelector('#bb-menu-dropdown');
        dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
    }
    _closeMenu() {
        this.container.querySelector('#bb-menu-dropdown').style.display = 'none';
    }

    async _handleMenuAction(action) {
        if (action === 'bookmarks') {
            await this._loadBookmarks();
            this._showPanel('Bookmarks', this.bookmarks.map(b =>
                `<div class="bb-panel-item" onclick="bazaBrowser.navigate('${this._escAttr(b.url)}');bazaBrowser._closePanel()">
                    <span class="bb-panel-item-title">${this._escHtml(b.title || b.url)}</span>
                    <span class="bb-panel-item-url">${this._escHtml(b.url)}</span>
                </div>`
            ).join('') || '<div style="color:#555;padding:20px;text-align:center">No bookmarks yet</div>');
        } else if (action === 'history') {
            try {
                const res = await fetch('/api/browser/history?limit=50');
                const hist = await res.json();
                this._showPanel('History', hist.map(h =>
                    `<div class="bb-panel-item" onclick="bazaBrowser.navigate('${this._escAttr(h.url)}');bazaBrowser._closePanel()">
                        <span class="bb-panel-item-title">${this._escHtml(h.title || h.url)}</span>
                        <span class="bb-panel-item-url">${this._escHtml(h.url)}</span>
                        <span class="bb-panel-item-time">${h.visited_at || ''}</span>
                    </div>`
                ).join('') || '<div style="color:#555;padding:20px;text-align:center">No history</div>');
            } catch { this._showPanel('History', '<div style="color:#555;padding:20px">Could not load</div>'); }
        } else if (action === 'clear-history') {
            if (confirm('Clear all browsing history?')) {
                await fetch('/api/browser/history', { method: 'DELETE' });
            }
        } else if (action === 'devtools') {
            // Toggle a simple console view
            const tab = this._activeTab();
            if (tab) this._showPanel('Dev Tools', '<div style="color:#555;padding:20px">Console capture coming soon</div>');
        }
    }

    _showPanel(title, html) {
        const panel = this.container.querySelector('#bb-panel');
        this.container.querySelector('#bb-panel-title').textContent = title;
        this.container.querySelector('#bb-panel-body').innerHTML = html;
        panel.style.display = 'flex';
    }
    _closePanel() {
        this.container.querySelector('#bb-panel').style.display = 'none';
    }

    // ── State Persistence (sessionStorage) ───────────────────────────────────
    saveState() {
        try {
            const state = {
                tabs: this.tabs.map(t => ({
                    id: t.id, url: t.url, title: t.title,
                    historyStack: t.historyStack, historyIndex: t.historyIndex,
                    suspended: t.suspended,
                })),
                activeTabId: this.activeTabId,
                counter: this._tabIdCounter,
            };
            sessionStorage.setItem('bazaBrowserState', JSON.stringify(state));
        } catch {}
    }

    _restoreState() {
        try {
            const raw = sessionStorage.getItem('bazaBrowserState');
            if (!raw) return;
            const state = JSON.parse(raw);
            this._tabIdCounter = state.counter || 0;
            for (const ts of (state.tabs || [])) {
                const tab = new BazaTab(ts.id, ts.url);
                tab.title = ts.title || 'Tab';
                tab.historyStack = ts.historyStack || [];
                tab.historyIndex = ts.historyIndex ?? -1;
                tab.suspended = true; // all tabs start suspended on restore
                this.tabs.push(tab);
            }
            // Activate the last active tab (it will resume)
            if (state.activeTabId && this.tabs.find(t => t.id === state.activeTabId)) {
                this.switchTab(state.activeTabId);
            } else if (this.tabs.length) {
                this.switchTab(this.tabs[0].id);
            }
            this._renderTabStrip();
        } catch {}
    }

    // ── Keyboard Shortcuts ───────────────────────────────────────────────────
    _bindKeys() {
        document.addEventListener('keydown', e => {
            // Only handle if browser panel is visible
            if (this.container.style.display === 'none') return;

            if (e.ctrlKey && e.key === 't') { e.preventDefault(); this.createTab(''); }
            if (e.ctrlKey && e.key === 'w') { e.preventDefault(); if (this.activeTabId) this.closeTab(this.activeTabId); }
            if (e.ctrlKey && e.key === 'l') { e.preventDefault(); this.container.querySelector('#bb-address')?.focus(); }
            if (e.ctrlKey && e.key === 'r') { e.preventDefault(); this.reload(); }
            if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); this.goBack(); }
            if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); this.goForward(); }
            // Ctrl+1-9 switch tabs
            if (e.ctrlKey && e.key >= '1' && e.key <= '9') {
                e.preventDefault();
                const idx = parseInt(e.key) - 1;
                if (idx < this.tabs.length) this.switchTab(this.tabs[idx].id);
            }
        });
    }

    // ── Helpers ──────────────────────────────────────────────────────────────
    _activeTab() { return this.tabs.find(t => t.id === this.activeTabId); }
    _escHtml(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
    _escAttr(s) { return (s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;'); }
}

// Global instance — created by shell.html
let bazaBrowser = null;
