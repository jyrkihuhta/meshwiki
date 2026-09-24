(function() {
    var card = document.getElementById('wiki-hover-card');
    if (!card) return;

    var closeTimer;

    document.addEventListener('mousemove', function(e) {
        card.style.position = 'fixed';
        card.style.left = (e.clientX + 15) + 'px';
        card.style.top = (e.clientY + 10) + 'px';
    });

    card.addEventListener('mouseleave', function() {
        card.style.display = 'none';
        clearTimeout(closeTimer);
    });

    document.body.addEventListener('htmx:afterSwap', function(e) {
        if (e.target.id === 'wiki-hover-card') {
            card.style.display = 'block';
        }
    });
})();
