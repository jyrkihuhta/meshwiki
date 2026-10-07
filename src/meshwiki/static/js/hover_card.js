(function() {
    // Wiki links fetch /api/pages/<name>/preview on mouseenter (hx-get) and swap
    // it over #wiki-hover-card with outerHTML. That REPLACES the element, so it
    // must be looked up each time rather than cached at load.
    function getCard() {
        return document.getElementById('wiki-hover-card');
    }

    function isWikiLink(node) {
        return !!(node && node.closest && node.closest('a.wiki-link'));
    }

    // Only show a card the pointer is still waiting on: a slow response must not
    // pop a card up after the user has already moved away from the link.
    var overLink = false;

    document.addEventListener('mouseover', function(e) {
        if (isWikiLink(e.target)) overLink = true;
    });

    document.addEventListener('mouseout', function(e) {
        var link = e.target.closest ? e.target.closest('a.wiki-link') : null;
        if (!link) return;
        // Moving onto a child of the same link is not leaving it.
        if (e.relatedTarget && link.contains(e.relatedTarget)) return;
        overLink = false;
        var card = getCard();
        if (card) card.style.display = 'none';
    });

    document.addEventListener('mousemove', function(e) {
        var card = getCard();
        if (!card) return;
        card.style.position = 'fixed';
        card.style.left = (e.clientX + 15) + 'px';
        card.style.top = (e.clientY + 10) + 'px';
    });

    // Show the card on htmx:afterSettle, not afterSwap: the new card shares its id
    // with the one it replaces, so htmx re-applies the response's attributes
    // during the settle phase, which would wipe an inline display set at swap
    // time. Listen on document, not body: this script runs in <head>, where body
    // doesn't exist yet (htmx events bubble, so this still sees every swap).
    document.addEventListener('htmx:afterSettle', function(e) {
        if (e.target.id === 'wiki-hover-card' && overLink) {
            e.target.style.display = 'block';
        }
    });
})();
