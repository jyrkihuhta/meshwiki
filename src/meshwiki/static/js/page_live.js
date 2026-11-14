// Live updates for the page view: refresh the page content when the page changes.
//
// Used to be an inline <script> in page/view.html (which interpolated the page
// name into the JS). The name now comes from data-page-name on #page-content.
// This file sits in the `extra_scripts` block at the end of <body>, so hx-boost
// re-executes it on each navigation to a page view.
(function () {
    'use strict';

    var content = document.getElementById('page-content');
    var currentPage = content ? content.getAttribute('data-page-name') : null;
    if (!currentPage) return;

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Stored on window so app.js's htmx:beforeSwap handler can close it on navigation.
    window._meshwikiWS = new WebSocket(proto + '//' + location.host + '/ws/graph');
    var ws = window._meshwikiWS;
    var debounce;

    ws.addEventListener('message', function (ev) {
        try {
            var msg = JSON.parse(ev.data);
            if (msg.type === 'page_updated' && msg.page === currentPage) {
                // Debounce: wait 800ms after the last event before refreshing.
                clearTimeout(debounce);
                debounce = setTimeout(function () {
                    htmx.trigger(document.getElementById('page-content'), 'metatable-refresh');
                }, 800);
            }
        } catch (e) {
            // Ignore malformed messages.
        }
    });

    // After htmx swaps in new content, re-initialise Mermaid diagrams. Registered
    // once: this file re-runs on every boosted navigation.
    if (!window._mwPageLiveSwapBound) {
        window._mwPageLiveSwapBound = true;
        document.addEventListener('htmx:afterSwap', function (e) {
            if (e.target && e.target.id === 'page-content' && typeof mermaid !== 'undefined') {
                var nodes = e.target.querySelectorAll('.mermaid:not([data-processed])');
                if (nodes.length) mermaid.run({ nodes: nodes });
            }
        });
    }
})();
