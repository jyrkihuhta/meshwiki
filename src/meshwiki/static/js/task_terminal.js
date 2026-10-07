// Live grinder terminal for the <<TaskStatus>> macro.
//
// The macro renders only markup (a `.task-status-terminal` wrapper carrying
// data-terminal-page and, for finished tasks, data-terminal-done); this file
// finds those wrappers and streams /ws/terminal/<page> into an xterm.js
// instance. It used to be an inline <script> emitted by the macro, which the
// Content-Security-Policy and the HTML sanitizer no longer allow.
//
// Loaded once, with `defer`, from <head>. Terminals are (re)booted on initial
// load and after every htmx swap, because a page-fragment refresh replaces the
// whole wrapper element.
(function () {
    'use strict';

    var XTERM_JS = 'https://cdn.jsdelivr.net/npm/xterm@5/lib/xterm.js';
    var XTERM_CSS = 'https://cdn.jsdelivr.net/npm/xterm@5/css/xterm.css';

    // Sent by the server when a task has no live session.
    var NO_SESSION_MSG = '\r\n\x1b[2m[no active terminal session for this task]\x1b[0m\r\n';
    var WAITING_MSG = '[session ended — waiting for next grinder run...]';
    var RETRY_MAX = 10;
    var RETRY_DELAY_MS = 5000;

    // Load xterm once, however many terminals are on the page.
    var xtermWaiters = null;
    function withXterm(callback) {
        if (window.Terminal) {
            callback();
            return;
        }
        if (xtermWaiters) {
            xtermWaiters.push(callback);
            return;
        }
        xtermWaiters = [callback];
        var css = document.createElement('link');
        css.rel = 'stylesheet';
        css.href = XTERM_CSS;
        document.head.appendChild(css);
        var script = document.createElement('script');
        script.src = XTERM_JS;
        script.onload = function () {
            var waiters = xtermWaiters;
            xtermWaiters = null;
            waiters.forEach(function (fn) { fn(); });
        };
        document.head.appendChild(script);
    }

    function startTerminal(wrapper, container, page, done) {
        var term = new window.Terminal({
            cols: 160,
            rows: 50,
            disableStdin: true,
            convertEol: true,
            scrollback: 5000,
            fontFamily: 'Menlo,Monaco,"Courier New",monospace',
            fontSize: 13,
            theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
        });
        term.open(container);

        var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsPath = page.split('/').map(encodeURIComponent).join('/');
        var retries = 0;
        var banner = null;

        function showBanner(message) {
            banner = document.createElement('div');
            banner.style.cssText = 'position:absolute;top:50%;left:50%;' +
                'transform:translate(-50%,-50%);color:#888;' +
                'font-family:Menlo,Monaco,monospace;font-size:13px;' +
                'text-align:center;pointer-events:none;';
            banner.textContent = message;
            container.style.position = 'relative';
            container.appendChild(banner);
        }

        function clearBanner() {
            if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
            banner = null;
        }

        function connect() {
            var ws = new WebSocket(protocol + '//' + location.host + '/ws/terminal/' + wsPath);

            ws.onmessage = function (event) {
                // The wrapper is replaced by fragment refreshes and boosted
                // navigations; stop streaming into a terminal nobody can see.
                if (!wrapper.isConnected) {
                    ws.close(1000);
                    return;
                }
                if (event.data === NO_SESSION_MSG.trim()) {
                    if (!banner) showBanner(WAITING_MSG);
                } else {
                    clearBanner();
                    if (event.data !== NO_SESSION_MSG) term.write(event.data);
                }
            };

            ws.onclose = function (event) {
                if (done || event.code === 1000) {
                    term.write('\r\n\x1b[2m━━━ session ended ━━━\x1b[0m\r\n');
                    return;
                }
                if (!wrapper.isConnected) return;
                clearBanner();
                if (retries < RETRY_MAX) {
                    retries++;
                    showBanner(WAITING_MSG);
                    setTimeout(connect, RETRY_DELAY_MS);
                } else {
                    showBanner('[no more retries — reload page]');
                }
            };

            ws.onerror = function () {
                term.write('\r\n\x1b[31m[connection error]\x1b[0m\r\n');
            };
        }

        connect();
    }

    function bootTerminals() {
        var wrappers = document.querySelectorAll('.task-status-terminal[data-terminal-page]');
        Array.prototype.forEach.call(wrappers, function (wrapper) {
            if (wrapper._mwTerminalBooted) return;
            var container = wrapper.querySelector('.task-terminal-body');
            var page = wrapper.getAttribute('data-terminal-page');
            if (!container || !page) return;
            wrapper._mwTerminalBooted = true;
            var done = wrapper.hasAttribute('data-terminal-done');
            withXterm(function () { startTerminal(wrapper, container, page, done); });
        });
    }

    document.addEventListener('DOMContentLoaded', bootTerminals);
    // Fires after hx-boost navigations and page-fragment refreshes alike.
    document.addEventListener('htmx:afterSettle', bootTerminals);
    if (document.readyState !== 'loading') bootTerminals();
})();
