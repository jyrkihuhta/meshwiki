(function () {
    "use strict";

    var container = document.getElementById("graph-container");
    var svg = d3.select("#graph-svg");
    var statsEl = document.getElementById("graph-stats");
    var wsStatusEl = document.getElementById("ws-status");
    var unavailableEl = document.getElementById("graph-unavailable");

    // Tooltip element
    var tooltip = document.createElement("div");
    tooltip.className = "graph-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.setAttribute("aria-hidden", "true");
    document.body.appendChild(tooltip);

    var tooltipVisible = false;
    var tooltipFadeTimeout = null;

    // Search state
    var searchQuery = "";
    var matchedNodes = [];
    var highlightedNode = null;
    var searchInput, searchResults, searchClear, searchContainer;

    function formatTimestamp(ts) {
        if (!ts || ts === 0) return "Unknown";
        var date = new Date(ts * 1000);
        var now = new Date();
        var diffMs = now - date;
        var diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        if (diffDays === 0) return "Today";
        if (diffDays === 1) return "Yesterday";
        if (diffDays < 7) return diffDays + " days ago";
        if (diffDays < 30) return Math.floor(diffDays / 7) + " weeks ago";
        if (diffDays < 365) return Math.floor(diffDays / 30) + " months ago";
        return Math.floor(diffDays / 365) + " years ago";
    }

    function showTooltip(node, event) {
        if (tooltipFadeTimeout) {
            clearTimeout(tooltipFadeTimeout);
            tooltipFadeTimeout = null;
        }

        var tags = node.tags || [];
        var tagsHtml = "";
        if (tags.length > 0) {
            tagsHtml = "<div class=\"graph-tooltip-tags\">";
            tags.forEach(function (tag) {
                tagsHtml += "<span class=\"graph-tooltip-tag\">" + escapeHtml(tag) + "</span>";
            });
            tagsHtml += "</div>";
        }

        tooltip.innerHTML = "<div class=\"graph-tooltip-title\">" + escapeHtml(node.id) + "</div>" +
            "<div class=\"graph-tooltip-row\"><span class=\"graph-tooltip-label\">Backlinks</span><span class=\"graph-tooltip-value\">" + (node.backlinks_count || 0) + "</span></div>" +
            "<div class=\"graph-tooltip-row\"><span class=\"graph-tooltip-label\">Modified</span><span class=\"graph-tooltip-modified\">" + formatTimestamp(node.modified) + "</span></div>" +
            (tags.length > 0 ? "<div class=\"graph-tooltip-row\" style=\"flex-direction: column; align-items: flex-start;\"><span class=\"graph-tooltip-label\">Tags</span>" + tagsHtml + "</div>" : "");

        positionTooltip(event);
        tooltip.classList.remove("fade-out");
        tooltip.classList.add("visible");
        tooltip.setAttribute("aria-hidden", "false");
        tooltipVisible = true;
    }

    function hideTooltip() {
        if (tooltipFadeTimeout) {
            clearTimeout(tooltipFadeTimeout);
        }
        tooltip.classList.remove("visible");
        tooltip.classList.add("fade-out");
        tooltip.setAttribute("aria-hidden", "true");
        tooltipVisible = false;
        tooltipFadeTimeout = setTimeout(function () {
            tooltip.classList.remove("fade-out");
            tooltipFadeTimeout = null;
        }, 150);
    }

    function positionTooltip(event) {
        var offsetX = 15;
        var offsetY = 15;
        var tooltipRect = tooltip.getBoundingClientRect();
        var viewportWidth = window.innerWidth;
        var viewportHeight = window.innerHeight;

        var x = event.clientX + offsetX;
        var y = event.clientY + offsetY;

        if (x + tooltipRect.width > viewportWidth - 20) {
            x = event.clientX - tooltipRect.width - offsetX;
        }
        if (y + tooltipRect.height > viewportHeight - 20) {
            y = event.clientY - tooltipRect.height - offsetY;
        }

        if (x < 10) x = 10;
        if (y < 10) y = 10;

        tooltip.style.left = x + "px";
        tooltip.style.top = y + "px";
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function getThemeColor(varName, fallback) {
        return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || fallback;
    }

    function getSize() {
        var rect = container.getBoundingClientRect();
        return { width: rect.width, height: rect.height };
    }

    var size = getSize();
    var width = size.width;
    var height = size.height;
    svg.attr("width", width).attr("height", height);

    var nodes = [];
    var links = [];

    var color = d3.scaleOrdinal(d3.schemeTableau10);

    var g = svg.append("g");
    var linkGroup = g.append("g").attr("class", "links");
    var nodeGroup = g.append("g").attr("class", "nodes");

    svg.append("defs").append("marker")
        .attr("id", "arrowhead")
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 20)
        .attr("refY", 0)
        .attr("markerWidth", 6)
        .attr("markerHeight", 6)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", "var(--color-text-muted)");

    var simulation = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(120))
        .force("charge", d3.forceManyBody().strength(-300))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collision", d3.forceCollide().radius(30))
        .on("tick", ticked);

    var zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on("zoom", function (event) { g.attr("transform", event.transform); });
    svg.call(zoom);

    var linkSel = linkGroup.selectAll("line");
    var nodeSel = nodeGroup.selectAll("g.node");

    // Search UI
    function initSearchUI() {
        searchContainer = document.createElement("div");
        searchContainer.className = "graph-search-container";
        searchContainer.innerHTML =
            '<div class="graph-search-input-wrapper">' +
            '<input type="text" class="graph-search-input" placeholder="Search nodes..." autocomplete="off" />' +
            '<button class="graph-search-clear" type="button" aria-label="Clear search">&times;</button>' +
            "</div>" +
            '<div class="graph-search-results"></div>';
        container.style.position = "relative";
        container.appendChild(searchContainer);

        searchInput = searchContainer.querySelector(".graph-search-input");
        searchResults = searchContainer.querySelector(".graph-search-results");
        searchClear = searchContainer.querySelector(".graph-search-clear");

        searchInput.addEventListener("input", handleSearchInput);
        searchInput.addEventListener("keydown", handleSearchKeydown);
        searchClear.addEventListener("click", clearSearch);

        document.addEventListener("click", function (e) {
            if (!searchContainer.contains(e.target)) {
                searchResults.classList.remove("visible");
            }
        });
    }

    function handleSearchInput(e) {
        var query = e.target.value.trim();
        searchClear.classList.toggle("visible", query.length > 0);

        if (query.length === 0) {
            clearSearch();
            return;
        }

        searchQuery = query;
        performSearch(query);
    }

    function handleSearchKeydown(e) {
        if (e.key === "Enter") {
            e.preventDefault();
            var focused = searchResults.querySelector(".focused");
            if (focused) {
                selectResult(focused);
            } else if (matchedNodes.length > 0) {
                panToNode(matchedNodes[0].id);
            }
            searchResults.classList.remove("visible");
        } else if (e.key === "Escape") {
            searchResults.classList.remove("visible");
            searchInput.blur();
        } else if (e.key === "ArrowDown") {
            e.preventDefault();
            navigateResults(1);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            navigateResults(-1);
        }
    }

    function performSearch(query) {
        var lowerQuery = query.toLowerCase();
        matchedNodes = nodes.filter(function (n) {
            return n.id.toLowerCase().includes(lowerQuery);
        });

        if (matchedNodes.length === 0) {
            searchResults.innerHTML =
                '<div class="graph-search-result-item" style="color: var(--color-text-muted); cursor: default;">No matches</div>';
        } else {
            searchResults.innerHTML = matchedNodes
                .slice(0, 10)
                .map(function (n, i) {
                    var highlighted = highlightMatch(n.id, query);
                    return (
                        '<div class="graph-search-result-item" data-name="' +
                        n.id +
                        '" data-index="' +
                        i +
                        '">' +
                        highlighted +
                        "</div>"
                    );
                })
                .join("");
        }

        searchResults.classList.add("visible");

        searchResults.querySelectorAll(".graph-search-result-item[data-name]").forEach(function (item) {
            item.addEventListener("click", function () {
                selectResult(item);
            });
        });

        updateHighlights();
    }

    function highlightMatch(text, query) {
        var lowerText = text.toLowerCase();
        var lowerQuery = query.toLowerCase();
        var idx = lowerText.indexOf(lowerQuery);
        if (idx === -1) return text;
        return (
            text.slice(0, idx) +
            "<mark>" +
            text.slice(idx, idx + query.length) +
            "</mark>" +
            text.slice(idx + query.length)
        );
    }

    function navigateResults(direction) {
        var items = searchResults.querySelectorAll(".graph-search-result-item[data-name]");
        if (items.length === 0) return;

        var focusedIdx = -1;
        items.forEach(function (item, i) {
            if (item.classList.contains("focused")) {
                focusedIdx = i;
                item.classList.remove("focused");
            }
        });

        var newIdx = focusedIdx + direction;
        if (newIdx < 0) newIdx = items.length - 1;
        if (newIdx >= items.length) newIdx = 0;

        items[newIdx].classList.add("focused");
        items[newIdx].scrollIntoView({ block: "nearest" });
    }

    function selectResult(item) {
        var name = item.getAttribute("data-name");
        panToNode(name);
        searchResults.classList.remove("visible");
    }

    function panToNode(name) {
        var nodeData = nodes.find(function (n) {
            return n.id === name;
        });
        if (!nodeData || nodeData.x === undefined || nodeData.y === undefined) return;

        highlightedNode = name;

        var nodeEls = nodeGroup.selectAll("g.node").filter(function (d) {
            return d.id === name;
        });
        nodeEls.classed("panning", true);

        var scale = zoom.scaleExtent()[1] * 0.8;
        var tx = width / 2 - nodeData.x * scale;
        var ty = height / 2 - nodeData.y * scale;

        svg.transition()
            .duration(500)
            .call(
                zoom.transform,
                d3.zoomIdentity.translate(tx, ty).scale(scale),
                function () {
                    setTimeout(function () {
                        nodeEls.classed("panning", false);
                    }, 500);
                }
            );

        updateHighlights();
    }

    function updateHighlights() {
        if (searchQuery.length === 0) {
            nodeGroup.selectAll("g.node").classed("dimmed", false).classed("match", false);
            linkGroup.selectAll("line").attr("stroke-opacity", 0.6);
            return;
        }

        var lowerQuery = searchQuery.toLowerCase();
        var matchedIds = matchedNodes.map(function (n) {
            return n.id;
        });

        nodeGroup.selectAll("g.node").each(function (d) {
            var isMatch = matchedIds.indexOf(d.id) !== -1;
            d3.select(this).classed("match", isMatch).classed("dimmed", !isMatch);
        });

        linkGroup.selectAll("line").attr("stroke-opacity", function (l) {
            var srcId = typeof l.source === "object" ? l.source.id : l.source;
            var tgtId = typeof l.target === "object" ? l.target.id : l.target;
            return matchedIds.indexOf(srcId) !== -1 || matchedIds.indexOf(tgtId) !== -1 ? 0.8 : 0.15;
        });
    }

    function clearSearch() {
        searchQuery = "";
        matchedNodes = [];
        highlightedNode = null;
        searchInput.value = "";
        searchClear.classList.remove("visible");
        searchResults.classList.remove("visible");
        searchResults.innerHTML = "";
        updateHighlights();
    }

    // Drag behavior
    function drag(sim) {
        return d3.drag()
            .on("start", function (event, d) {
                if (!event.active) sim.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            })
            .on("drag", function (event, d) {
                d.fx = event.x;
                d.fy = event.y;
            })
            .on("end", function (event, d) {
                if (!event.active) sim.alphaTarget(0);
                d.fx = null;
                d.fy = null;
            });
    }

    var focusedNodeId = null;
    var exitFocusBtn = null;

    function getLinkId(link) {
        var src = typeof link.source === "object" ? link.source.id : link.source;
        var tgt = typeof link.target === "object" ? link.target.id : link.target;
        return { source: src, target: tgt };
    }

    function getNeighborIds(nodeId) {
        var neighborIds = new Set();
        neighborIds.add(nodeId);
        links.forEach(function (link) {
            var ids = getLinkId(link);
            if (ids.source === nodeId) neighborIds.add(ids.target);
            if (ids.target === nodeId) neighborIds.add(ids.source);
        });
        return neighborIds;
    }

    function isLinkInFocus(link) {
        if (!focusedNodeId) return true;
        var ids = getLinkId(link);
        var neighborIds = getNeighborIds(focusedNodeId);
        return neighborIds.has(ids.source) && neighborIds.has(ids.target);
    }

    function applyFocusMode() {
        if (focusedNodeId) {
            var neighborIds = getNeighborIds(focusedNodeId);
            nodeGroup.selectAll("g.node")
                .transition().duration(200)
                .style("opacity", function (d) {
                    return neighborIds.has(d.id) ? 1 : 0.1;
                })
                .select("circle")
                .transition().duration(200)
                .attr("r", function (d) {
                    return d.id === focusedNodeId ? 12 : 6;
                });
            linkGroup.selectAll("line")
                .transition().duration(200)
                .style("opacity", function (d) {
                    return isLinkInFocus(d) ? 0.8 : 0.05;
                });
            updateStats(true);
        }
    }

    function clearFocusMode() {
        focusedNodeId = null;
        if (exitFocusBtn) {
            exitFocusBtn.remove();
            exitFocusBtn = null;
        }
        nodeGroup.selectAll("g.node")
            .transition().duration(200)
            .style("opacity", 1)
            .select("circle")
            .transition().duration(200)
            .attr("r", 8);
        linkGroup.selectAll("line")
            .transition().duration(200)
            .style("opacity", 0.6);
        updateUrlParam(null);
        updateStats(false);
    }

    function enterFocusMode(nodeId) {
        focusedNodeId = nodeId;
        createExitFocusButton();
        applyFocusMode();
        updateUrlParam(nodeId);
    }

    function createExitFocusButton() {
        if (exitFocusBtn) exitFocusBtn.remove();
        exitFocusBtn = document.createElement("button");
        exitFocusBtn.id = "exit-focus-btn";
        exitFocusBtn.className = "exit-focus-btn";
        exitFocusBtn.textContent = "Exit focus";
        exitFocusBtn.style.cssText = "margin-left: auto; padding: 0.35rem 0.75rem; " +
            "border-radius: 6px; border: 1px solid var(--color-border); " +
            "background: var(--color-bg-secondary); color: var(--color-text); " +
            "cursor: pointer; font-size: 0.875rem;";
        var toolbar = document.querySelector(".graph-toolbar");
        if (toolbar) toolbar.appendChild(exitFocusBtn);
        exitFocusBtn.addEventListener("click", clearFocusMode);
    }

    function updateUrlParam(nodeId) {
        var url = new URL(window.location.href);
        if (nodeId) {
            url.searchParams.set("focus", nodeId);
        } else {
            url.searchParams.delete("focus");
        }
        history.replaceState(null, "", url.toString());
    }

    function getUrlFocusParam() {
        var params = new URLSearchParams(window.location.search);
        return params.get("focus");
    }

    function render() {
        var linkColor = getThemeColor("--color-text-muted", "#999");
        var textColor = getThemeColor("--color-text", "#333");
        var nodeStroke = getThemeColor("--color-bg", "#fff");

        linkSel = linkGroup.selectAll("line")
            .data(links, function (d) {
                var ids = getLinkId(d);
                return ids.source + "->" + ids.target;
            });
        linkSel.exit().remove();
        linkSel = linkSel.enter().append("line")
            .attr("stroke", linkColor)
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", 1.5)
            .attr("marker-end", "url(#arrowhead)")
            .merge(linkSel);

        nodeSel = nodeGroup.selectAll("g.node")
            .data(nodes, function (d) { return d.id; });
        nodeSel.exit().remove();

        var nodeEnter = nodeSel.enter().append("g")
            .attr("class", "node")
            .style("cursor", "pointer")
            .call(drag(simulation))
            .on("click", function (event, d) {
                window.location.href = "/page/" + encodeURIComponent(d.id);
            })
            .on("mouseover", function (event, d) {
                showTooltip(d, event);
            })
            .on("mousemove", function (event, d) {
                if (tooltipVisible) {
                    positionTooltip(event);
                }
            })
            .on("mouseout", function (event, d) {
                hideTooltip();
            })
            .on("dblclick", function (event, d) {
                event.stopPropagation();
                enterFocusMode(d.id);
            });

        nodeEnter.append("circle")
            .attr("r", 8)
            .attr("fill", function (d) { return color(d.id); })
            .attr("stroke", nodeStroke)
            .attr("stroke-width", 1.5);

        nodeEnter.append("text")
            .attr("dx", 12)
            .attr("dy", 4)
            .attr("font-size", "12px")
            .attr("fill", textColor)
            .text(function (d) { return d.id; });

        nodeSel = nodeEnter.merge(nodeSel);

        svg.on("dblclick", function () {
            if (focusedNodeId) clearFocusMode();
        });

        simulation.nodes(nodes);
        simulation.force("link").links(links);
        simulation.alpha(0.3).restart();

        var urlFocus = getUrlFocusParam();
        if (urlFocus && nodes.find(function (n) { return n.id === urlFocus; })) {
            enterFocusMode(urlFocus);
        } else {
            updateStats(false);
        }
    }

    function ticked() {
        linkSel
            .attr("x1", function (d) { return d.source.x; })
            .attr("y1", function (d) { return d.source.y; })
            .attr("x2", function (d) { return d.target.x; })
            .attr("y2", function (d) { return d.target.y; });

        nodeSel
            .attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });
    }

    function updateStats(isFocused) {
        if (isFocused && focusedNodeId) {
            var neighborIds = getNeighborIds(focusedNodeId);
            var visibleNodes = nodes.filter(function (n) { return neighborIds.has(n.id); }).length;
            var visibleLinks = links.filter(function (l) { return isLinkInFocus(l); }).length;
            statsEl.textContent = "Focus: " + focusedNodeId + " (" + visibleNodes + " pages, " + visibleLinks + " links)";
        } else {
            statsEl.textContent = nodes.length + " pages, " + links.length + " links";
        }
    }

    function flashNode(name) {
        var nodeStroke = getThemeColor("--color-bg", "#fff");
        nodeGroup.selectAll("g.node")
            .filter(function (d) { return d.id === name; })
            .select("circle")
            .transition().duration(200)
            .attr("r", 14)
            .attr("stroke", "#f59e0b")
            .attr("stroke-width", 3)
            .transition().duration(600)
            .attr("r", 8)
            .attr("stroke", nodeStroke)
            .attr("stroke-width", 1.5);
    }

    window.addEventListener("resize", function () {
        var s = getSize();
        width = s.width;
        height = s.height;
        svg.attr("width", width).attr("height", height);
        simulation.force("center", d3.forceCenter(width / 2, height / 2));
        simulation.alpha(0.1).restart();
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && focusedNodeId) {
            clearFocusMode();
        }
    });

    fetch("/api/graph")
        .then(function (r) { return r.json(); })
        .then(function (data) {
            nodes.push.apply(nodes, data.nodes);
            links.push.apply(links, data.links);
            render();
            initSearchUI();
        })
        .catch(function (err) {
            console.error("Failed to load graph:", err);
            unavailableEl.style.display = "block";
        });

    function handleEvent(msg) {
        switch (msg.type) {
            case "page_created":
                if (!nodes.find(function (n) { return n.id === msg.page; })) {
                    nodes.push({ id: msg.page });
                    render();
                    flashNode(msg.page);
                }
                break;

            case "page_updated":
                flashNode(msg.page);
                break;

            case "page_deleted":
                var idx = nodes.findIndex(function (n) { return n.id === msg.page; });
                if (idx !== -1) {
                    nodes.splice(idx, 1);
                    for (var i = links.length - 1; i >= 0; i--) {
                        var ids = getLinkId(links[i]);
                        if (ids.source === msg.page || ids.target === msg.page) {
                            links.splice(i, 1);
                        }
                    }
                    render();
                }
                break;

            case "link_created":
                if (!nodes.find(function (n) { return n.id === msg.from; })) {
                    nodes.push({ id: msg.from });
                }
                if (!nodes.find(function (n) { return n.id === msg.to; })) {
                    nodes.push({ id: msg.to });
                }
                links.push({ source: msg.from, target: msg.to });
                render();
                break;

            case "link_removed":
                for (var j = links.length - 1; j >= 0; j--) {
                    var lid = getLinkId(links[j]);
                    if (lid.source === msg.from && lid.target === msg.to) {
                        links.splice(j, 1);
                        break;
                    }
                }
                render();
                break;
        }
    }

    function connectWebSocket() {
        var protocol = location.protocol === "https:" ? "wss:" : "ws:";
        var ws = new WebSocket(protocol + "//" + location.host + "/ws/graph");

        ws.onopen = function () {
            wsStatusEl.textContent = "Live";
            wsStatusEl.classList.add("connected");
        };

        ws.onclose = function () {
            wsStatusEl.textContent = "Disconnected";
            wsStatusEl.classList.remove("connected");
            setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = function () {
            wsStatusEl.textContent = "Error";
            wsStatusEl.classList.remove("connected");
        };

        ws.onmessage = function (event) {
            var msg = JSON.parse(event.data);
            handleEvent(msg);
        };
    }

    connectWebSocket();
})();
