// Applies the saved theme before first paint to avoid a flash of the wrong
// theme. Loaded synchronously in <head> (after the highlight.js stylesheet link,
// which this switches for dark mode), so it must stay tiny and dependency-free.
(function () {
    try {
        var theme = localStorage.getItem('meshwiki-theme');
        if (theme) document.documentElement.setAttribute('data-theme', theme);
        if (theme === 'dark') {
            var link = document.getElementById('hljs-theme');
            if (link) {
                link.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css';
            }
        }
    } catch (e) {
        // localStorage can throw (private mode, blocked storage); default theme is fine.
    }
})();
