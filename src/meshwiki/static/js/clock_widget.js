(function() {
    'use strict';

    function getClockElements() {
        return document.querySelectorAll('[data-clock]');
    }

    function formatDigit(n) {
        return String(n).padStart(2, '0');
    }

    function getDisplayTime(el) {
        var tz = el.getAttribute('data-timezone');
        var now;
        try {
            if (tz) {
                now = new Date().toLocaleString('en-US', { timeZone: tz });
            } else {
                now = new Date().toLocaleString('en-US');
            }
        } catch (e) {
            return 'Invalid timezone';
        }
        if (now === 'Invalid timezone') return now;

        var date = new Date(now);
        if (isNaN(date.getTime())) return 'Invalid timezone';

        var h = date.getHours();
        var m = date.getMinutes();
        var s = date.getSeconds();
        return formatDigit(h) + ':' + formatDigit(m) + ':' + formatDigit(s);
    }

    function tickAll() {
        getClockElements().forEach(function(el) {
            el.textContent = getDisplayTime(el);
        });
    }

    function init() {
        tickAll();
        setInterval(tickAll, 1000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();