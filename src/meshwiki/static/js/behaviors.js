// Delegated behaviours that used to be inline `onclick=` / `onsubmit=` handlers.
//
// The Content-Security-Policy forbids inline script, and the HTML sanitizer
// strips `on*=` attributes from rendered page content, so markup now only
// carries data-* hooks and this file supplies the behaviour. Everything is
// delegated from `document`, so it covers content swapped in by htmx (page
// fragments, boosted navigations) without re-binding. Loaded once, with `defer`,
// from <head>; head scripts are not re-run by hx-boost.
(function () {
    'use strict';

    // ── Confirm before submitting ────────────────────────────────────────────
    // <form data-confirm="Delete this page?">. Registered in the capture phase
    // so a cancelled confirm stops the event before htmx's own submit handler
    // (which would otherwise already have sent the boosted request).
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.hasAttribute || !form.hasAttribute('data-confirm')) return;
        if (!window.confirm(form.getAttribute('data-confirm'))) {
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);

    // ── <<NewPage(Template, "Label", Parent)>> macro ─────────────────────────
    // Opens the editor for the typed page name, pre-filled from the template.
    document.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('.new-page-button') : null;
        if (!btn) return;
        var input = btn.previousElementSibling;
        var name = input && input.value ? input.value.trim() : '';
        if (!name) return;

        var parent = btn.getAttribute('data-newpage-parent') || '';
        var template = btn.getAttribute('data-newpage-template') || '';
        var base = '/page/';
        if (parent) {
            // Page URLs use underscores for spaces; encode each path segment.
            base += parent.replace(/ /g, '_').split('/').map(encodeURIComponent).join('/') + '/';
        }
        var url = base + encodeURIComponent(name) + '/edit';
        if (template) url += '?template=' + encodeURIComponent(template);
        window.location.href = url;
    });

    // ── Task terminal: expand / collapse ─────────────────────────────────────
    document.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('.task-terminal-expand-btn') : null;
        if (!btn) return;
        var wrapper = btn.closest('.task-status-terminal');
        if (!wrapper) return;
        var expanded = wrapper.classList.toggle('terminal-expanded');
        btn.title = expanded ? 'Exit fullscreen' : 'Expand terminal';
        btn.textContent = expanded ? '✕' : '⛶';
        document.body.style.overflow = expanded ? 'hidden' : '';
    });
})();
