// Global page behaviour: toasts, theme toggle, sidebar, mermaid, highlighting.
//
// This used to be an inline <script> in base.html. It lives in a static file so
// the Content-Security-Policy can forbid inline script. It sits at the end of
// <body> on purpose: hx-boost swaps the body and re-executes body scripts, and
// the per-page setup below relies on that. One-time listener registration is
// guarded by document.body._mwInited.

// ── One-time setup: persists across hx-boost navigations ─────────────────
// document.body element is NOT replaced on boost swaps (only innerHTML is),
// so _mwInited persists and prevents duplicate listener registration.
if (!document.body._mwInited) {
    document.body._mwInited = true;

    // Re-highlight code blocks after any HTMX content swap
    document.body.addEventListener('htmx:afterSwap', function(e) {
        if (typeof hljs !== 'undefined') {
            e.target.querySelectorAll('pre code').forEach(function(block) {
                hljs.highlightElement(block);
            });
        }
        // Restore frontmatter collapsed state after fragment swap (collapsed by default)
        var card = e.target.querySelector ? e.target.querySelector('.frontmatter-card') : null;
        if (card && localStorage.getItem('meshwiki-frontmatter-collapsed') !== '0') {
            card.classList.add('frontmatter-card--collapsed');
        }
    });

    // Frontmatter card toggle (delegated — survives fragment swaps)
    document.body.addEventListener('click', function(e) {
        var btn = e.target.closest('.frontmatter-card-toggle');
        if (!btn) return;
        var card = btn.closest('.frontmatter-card');
        if (!card) return;
        card.classList.toggle('frontmatter-card--collapsed');
        localStorage.setItem('meshwiki-frontmatter-collapsed',
            card.classList.contains('frontmatter-card--collapsed') ? '1' : '0');
    });

    // Toast from HTMX HX-Trigger header
    document.body.addEventListener('showToast', function(e) {
        if (e.detail) showToast(e.detail.message, e.detail.type);
    });

    // Loading bar
    document.body.addEventListener('htmx:beforeRequest', function() {
        var bar = document.getElementById('loading-bar');
        if (bar) bar.className = 'loading-bar active';
    });
    document.body.addEventListener('htmx:afterRequest', function() {
        var bar = document.getElementById('loading-bar');
        if (bar) { bar.className = 'loading-bar done'; setTimeout(function() { bar.className = 'loading-bar'; }, 500); }
    });

    // Close the page-view WebSocket before each boosted navigation so stale
    // handlers don't trigger fragment refreshes on the incoming page.
    document.body.addEventListener('htmx:beforeSwap', function() {
        if (window._meshwikiWS) {
            try { window._meshwikiWS.close(1000); } catch(e) {}
            window._meshwikiWS = null;
        }
    });

    // After each hx-boost navigation, render any .mermaid nodes that arrived in
    // the new page.  htmx:afterSettle fires after the DOM swap AND all inline
    // scripts have re-executed, so mermaid is fully initialized by this point.
    document.body.addEventListener('htmx:afterSettle', function() {
        if (typeof mermaid === 'undefined') return;
        var nodes = document.querySelectorAll('.mermaid:not([data-processed])');
        if (nodes.length) mermaid.run({ nodes: Array.from(nodes) });
    });
}

// ── Per-page setup: runs on initial load and after every hx-boost swap ───
// Toast notifications helper (redefined each time — cheap and safe)
window.showToast = function(message, type) {
    var container = document.getElementById('toast-container');
    var el = document.createElement('div');
    el.className = 'toast toast--' + (type || 'success');
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function() {
        el.classList.add('toast--fade-out');
        el.addEventListener('animationend', function() { el.remove(); });
    }, 4000);
};

// Theme toggle (element is replaced on each swap, so re-bind each time)
(function(){
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var current = document.documentElement.getAttribute('data-theme') || 'light';
    btn.textContent = current === 'dark' ? 'Light' : 'Dark';
    btn.addEventListener('click', function() {
        var theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('meshwiki-theme', theme);
        btn.textContent = theme === 'dark' ? 'Light' : 'Dark';
        var hljsLink = document.getElementById('hljs-theme');
        if (hljsLink) {
            hljsLink.href = theme === 'dark'
                ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css'
                : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css';
        }
        if (typeof mermaid !== 'undefined') {
            mermaid.initialize({ startOnLoad: false, theme: theme === 'dark' ? 'dark' : 'default' });
            mermaid.run();
        }
    });
})();

// Mobile nav toggle
(function(){
    var toggle = document.querySelector('.nav-toggle');
    var nav = document.querySelector('.nav');
    if (toggle && nav) toggle.addEventListener('click', function() {
        nav.classList.toggle('nav--open');
    });
})();

// Highlight code blocks on this page
if (typeof hljs !== 'undefined') hljs.highlightAll();

// Handle toast from redirect query param (e.g. ?toast=saved)
(function(){
    var url = new URL(window.location);
    var toast = url.searchParams.get('toast');
    if (toast) {
        url.searchParams.delete('toast');
        window.history.replaceState({}, '', url);
        var messages = {saved: 'Page saved', deleted: 'Page deleted', restored: 'Page restored'};
        showToast(messages[toast] || toast, 'success');
    }
})();

// Restore frontmatter card collapsed state (collapsed by default)
(function(){
    var card = document.querySelector('.frontmatter-card');
    if (card && localStorage.getItem('meshwiki-frontmatter-collapsed') !== '0') {
        card.classList.add('frontmatter-card--collapsed');
    }
})();

// Sidebar resize handle
(function(){
    var resizer = document.getElementById('sidebar-resizer');
    var sidebar = document.getElementById('page-tree-sidebar');
    if (!resizer || !sidebar) return;
    var saved = localStorage.getItem('meshwiki-sidebar-width');
    if (saved) sidebar.style.width = saved;
    resizer.addEventListener('mousedown', function(e) {
        var startX = e.clientX;
        var startWidth = sidebar.getBoundingClientRect().width;
        resizer.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        function onMove(e) {
            var w = Math.max(120, Math.min(600, startWidth + (e.clientX - startX)));
            sidebar.style.width = w + 'px';
        }
        function onUp() {
            resizer.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('meshwiki-sidebar-width', sidebar.style.width);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
})();

// Page tree current page highlighting and expansion state persistence
(function(){
    var sidebar = document.getElementById('page-tree-sidebar') || document.getElementById('toc-sidebar');
    if (!sidebar) return;
    var currentPath = window.location.pathname;
    var storageKey = 'page-tree-expanded:' + currentPath;
    try {
        var savedState = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
        savedState.forEach(function(pageName) {
            var details = sidebar.querySelector('[data-page-name="' + pageName + '"]');
            if (details) details.setAttribute('open', '');
        });
    } catch(e) {}
    var links = sidebar.querySelectorAll('.page-tree-link');
    links.forEach(function(link) {
        var href = link.getAttribute('href');
        if (href === currentPath || href.replace('/page/', '/') === currentPath.replace('/page/', '/')) {
            link.classList.add('current');
            var details = link.closest('.page-tree-details');
            if (details) details.setAttribute('open', '');
        }
    });
    sidebar.addEventListener('toggle', function(e) {
        if (e.target.tagName !== 'DETAILS') return;
        var openPages = [];
        sidebar.querySelectorAll('.page-tree-details[open]').forEach(function(d) {
            var link = d.querySelector('.page-tree-link');
            if (link) openPages.push(link.getAttribute('href'));
        });
        try { sessionStorage.setItem(storageKey, JSON.stringify(openPages)); } catch(e) {}
    });
})();

// Render mermaid diagrams on this page.
// mermaid.initialize() is guarded so it only runs once (first hard load).
// On hx-boost navigations the per-page inline scripts re-execute, so this
// mermaid.run() fires for any diagrams in the new page; htmx:afterSettle
// (bound once in _mwInited above) catches any that weren't yet in DOM.
if (typeof mermaid !== 'undefined') {
    if (!window._mermaidInited) {
        window._mermaidInited = true;
        var _mTheme = localStorage.getItem('meshwiki-theme') || 'light';
        mermaid.initialize({ startOnLoad: false, theme: _mTheme === 'dark' ? 'dark' : 'default' });
    }
    mermaid.run();
}
