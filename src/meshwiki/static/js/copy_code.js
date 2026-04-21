(function() {
    function attachButtons(root) {
        root.querySelectorAll('pre').forEach(function(pre) {
            if (pre.querySelector('.copy-btn')) return;
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'copy-btn';
            btn.textContent = 'Copy';
            btn.setAttribute('aria-label', 'Copy code');
            btn.addEventListener('click', function(e) {
                var code = pre.querySelector('code');
                if (!code) return;
                var text = code.textContent || '';
                navigator.clipboard.writeText(text).then(function() {
                    btn.textContent = 'Copied!';
                    setTimeout(function() {
                        btn.textContent = 'Copy';
                    }, 1500);
                }).catch(function() {
                    btn.textContent = 'Error';
                    setTimeout(function() {
                        btn.textContent = 'Copy';
                    }, 1500);
                });
            });
            pre.style.position = 'relative';
            pre.appendChild(btn);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            attachButtons(document);
        });
    } else {
        attachButtons(document);
    }

    document.body.addEventListener('htmx:afterSwap', function(e) {
        attachButtons(e.target);
    });
})();