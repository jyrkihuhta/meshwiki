(function () {
  "use strict";

  var TASKS_POLL_MS  = 5000;
  var STATUS_POLL_MS = 8000;
  var MAX_ACTIVITY   = 40;

  var prevByName  = {};
  var activityLog = [];

  var GRAPH_STATUS_LABELS = {
    intake: "Intake", decomposing: "Decomposing", dispatching: "Dispatching",
    grinding: "Grinding", reviewing: "Reviewing", completed: "Completed",
    failed: "Failed", escalated: "Escalated",
  };

  var STAGE_COLORS = {
    planned: "#6366f1", in_progress: "#0ea5e9", review: "#f59e0b", outcomes: "#10b981",
  };

  // ── DOM helpers ───────────────────────────────────────────────────────────

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }
  function svgEl(tag) { return document.createElementNS("http://www.w3.org/2000/svg", tag); }

  function setText(id, text) {
    var e = document.getElementById(id);
    if (e) e.textContent = text;
  }

  function setBody(id, nodes) {
    var c = document.getElementById(id);
    if (!c) return;
    while (c.firstChild) c.removeChild(c.firstChild);
    if (Array.isArray(nodes)) nodes.forEach(function (n) { c.appendChild(n); });
    else c.appendChild(nodes);
  }

  // ── Tasks API poll ────────────────────────────────────────────────────────

  function pollTasks() {
    var statuses = ["planned", "in_progress", "review", "merged", "failed", "rejected"];
    Promise.all(statuses.map(function (s) {
      return fetch("/api/v1/tasks?assignee=factory&status=" + s)
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    })).then(function (results) {
      var by = {};
      statuses.forEach(function (s, i) { by[s] = results[i] || []; });
      detectTransitions(by);
      renderPipeline(by);
      var u = document.getElementById("fc-updated");
      if (u) u.textContent = "Updated " + new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
    }).catch(function () {}).finally(function () { setTimeout(pollTasks, TASKS_POLL_MS); });
  }

  function detectTransitions(by) {
    var next = {};
    Object.keys(by).forEach(function (s) {
      (by[s] || []).forEach(function (t) { next[t.name] = s; });
    });
    Object.keys(next).forEach(function (name) {
      var s = next[name], p = prevByName[name];
      if (p !== undefined && p !== s) pushActivity({name: name, from: p, to: s, time: Date.now()});
      else if (p === undefined) pushActivity({name: name, from: null, to: s, time: Date.now()});
    });
    prevByName = next;
  }

  function pushActivity(item) {
    activityLog.unshift(item);
    if (activityLog.length > MAX_ACTIVITY) activityLog.length = MAX_ACTIVITY;
    renderActivity();
  }

  // ── Pipeline ──────────────────────────────────────────────────────────────

  function renderPipeline(by) {
    setText("fc-count-planned",    String((by.planned     || []).length));
    setText("fc-count-in_progress",String((by.in_progress || []).length));
    setText("fc-count-review",     String((by.review      || []).length));
    setText("fc-count-merged",     String((by.merged      || []).length));
    setText("fc-count-failed",     String((by.failed      || []).length));
    setText("fc-count-rejected",   String((by.rejected    || []).length));

    var node = document.getElementById("fc-node-in_progress");
    if (node) node.classList.toggle("fc-node--active", (by.in_progress || []).length > 0);

    drawPaths();
  }

  // ── SVG animated paths between pipeline nodes ─────────────────────────────

  var CONNECTIONS = [
    ["fc-node-planned",     "fc-node-in_progress", STAGE_COLORS.planned],
    ["fc-node-in_progress", "fc-node-review",      STAGE_COLORS.in_progress],
    ["fc-node-review",      "fc-node-outcomes",    STAGE_COLORS.outcomes],
  ];

  function drawPaths() {
    var svg = document.getElementById("fc-svg");
    if (!svg) return;
    var svgR = svg.getBoundingClientRect();
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    CONNECTIONS.forEach(function (conn, idx) {
      var fromEl = document.getElementById(conn[0]);
      var toEl   = document.getElementById(conn[1]);
      var color  = conn[2];
      if (!fromEl || !toEl) return;

      var fR = fromEl.getBoundingClientRect();
      var tR = toEl.getBoundingClientRect();
      var x1 = fR.right  - svgR.left;
      var y1 = fR.top + fR.height / 2 - svgR.top;
      var x2 = tR.left   - svgR.left;
      var y2 = tR.top + tR.height / 2 - svgR.top;
      var cx = (x1 + x2) / 2;

      var pathId = "fc-p-" + idx;
      var path = svgEl("path");
      path.setAttribute("id", pathId);
      path.setAttribute("class", "fc-path");
      path.setAttribute("d", "M" + x1 + "," + y1 + " C" + cx + "," + y1 + " " + cx + "," + y2 + " " + x2 + "," + y2);
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "1.5");
      svg.appendChild(path);

      [0, 0.33, 0.66].forEach(function (offset, pi) {
        var dur = (2.5 + pi * 0.5).toFixed(1);
        var begin = (offset * parseFloat(dur)).toFixed(2);

        var circle = svgEl("circle");
        circle.setAttribute("class", "fc-particle");
        circle.setAttribute("r", "3");
        circle.setAttribute("fill", color);

        var motion = svgEl("animateMotion");
        motion.setAttribute("dur", dur + "s");
        motion.setAttribute("begin", begin + "s");
        motion.setAttribute("repeatCount", "indefinite");

        var mpath = svgEl("mpath");
        mpath.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#" + pathId);
        motion.appendChild(mpath);
        circle.appendChild(motion);
        svg.appendChild(circle);
      });
    });
  }

  // ── Status API poll ───────────────────────────────────────────────────────

  function pollStatus() {
    fetch("/api/factory/status")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var res = data.resources || {};
        var graphs = data.active_graphs || [];
        setText("fc-kpi-graphs",   String(graphs.length));
        setText("fc-kpi-grinders", String(res.active_grinders || 0));
        setText("fc-kpi-cost",     "$" + ((res.total_cost_usd || 0).toFixed(3)));
        var cap = res.max_concurrent_parent_tasks, active = res.active_parent_tasks || 0;
        setText("fc-kpi-cap", cap != null ? String(Math.max(0, cap - active)) : "—");
        renderGraphs(graphs);
        renderBots(data.bots || []);
      })
      .catch(function () {})
      .finally(function () { setTimeout(pollStatus, STATUS_POLL_MS); });
  }

  // ── Graph cards ───────────────────────────────────────────────────────────

  var STATUS_COLORS = {
    intake: "#818cf8", decomposing: "#fbbf24", dispatching: "#38bdf8",
    grinding: "#0ea5e9", reviewing: "#f59e0b", completed: "#34d399",
    failed: "#f87171", escalated: "#f87171",
  };

  function buildRing(pct, color) {
    var r = 20, cx = 24, cy = 24;
    var circ = 2 * Math.PI * r;
    var offset = circ * (1 - Math.min(pct, 100) / 100);

    var svg = svgEl("svg");
    svg.setAttribute("width", "48");
    svg.setAttribute("height", "48");
    svg.setAttribute("viewBox", "0 0 48 48");
    svg.setAttribute("class", "fc-ring");

    var bg = svgEl("circle");
    bg.setAttribute("cx", String(cx)); bg.setAttribute("cy", String(cy));
    bg.setAttribute("r", String(r)); bg.setAttribute("fill", "none");
    bg.setAttribute("stroke", "rgba(255,255,255,0.06)");
    bg.setAttribute("stroke-width", "3");
    svg.appendChild(bg);

    var arc = svgEl("circle");
    arc.setAttribute("cx", String(cx)); arc.setAttribute("cy", String(cy));
    arc.setAttribute("r", String(r)); arc.setAttribute("fill", "none");
    arc.setAttribute("stroke", color || "#0ea5e9");
    arc.setAttribute("stroke-width", "3");
    arc.setAttribute("stroke-dasharray", String(circ));
    arc.setAttribute("stroke-dashoffset", String(offset));
    arc.setAttribute("stroke-linecap", "round");
    arc.setAttribute("transform", "rotate(-90 " + cx + " " + cy + ")");
    svg.appendChild(arc);

    return svg;
  }

  function renderGraphs(graphs) {
    setText("fc-graphs-badge", String(graphs.length));
    if (!graphs.length) { setBody("fc-graphs-body", makeEmpty("No active graphs")); return; }
    setBody("fc-graphs-body", graphs.map(buildGraphCard));
  }

  function buildGraphCard(g) {
    var total  = g.subtasks_total || 0;
    var done   = (g.subtasks_completed || 0) + (g.subtasks_failed || 0);
    var pct    = total > 0 ? Math.round(done / total * 100) : 0;
    var color  = STATUS_COLORS[g.graph_status] || "#64748b";

    var card = el("div", "fc-graph-card");

    var ringWrap = el("div", "fc-ring-wrap");
    ringWrap.appendChild(buildRing(pct, color));
    var pctLabel = el("div", "fc-ring-pct");
    pctLabel.textContent = total > 0 ? pct + "%" : "—";
    ringWrap.appendChild(pctLabel);
    card.appendChild(ringWrap);

    var info = el("div", "fc-graph-info");
    var title = el("div", "fc-graph-title");
    title.textContent = truncate(g.title || g.thread_id, 28);
    info.appendChild(title);

    var badge = el("span", "fc-graph-status fc-graph-status--" + (g.graph_status || "unknown"));
    badge.textContent = GRAPH_STATUS_LABELS[g.graph_status] || g.graph_status;
    info.appendChild(badge);

    var stats = el("div", "fc-graph-stats");
    if (total > 0) {
      var sub = el("span", "fc-graph-stat");
      sub.textContent = done + "/" + total + " subtasks";
      stats.appendChild(sub);
    }
    if (g.active_grinders > 0) {
      var gr = el("span", "fc-graph-stat fc-graph-stat--active");
      gr.textContent = "⚙ " + g.active_grinders;
      stats.appendChild(gr);
    }
    if (g.cost_usd > 0) {
      var cost = el("span", "fc-graph-stat fc-graph-stat--cost");
      cost.textContent = "$" + g.cost_usd.toFixed(3);
      stats.appendChild(cost);
    }
    info.appendChild(stats);
    card.appendChild(info);
    return card;
  }

  // ── Bots ──────────────────────────────────────────────────────────────────

  function renderBots(bots) {
    setText("fc-bots-badge", String(bots.length));
    if (!bots.length) { setBody("fc-bots-body", makeEmpty("No bots registered")); return; }
    setBody("fc-bots-body", bots.map(buildBotRow));
  }

  function buildBotRow(bot) {
    var row = el("div", "fc-bot-row");

    var dot = el("span", "fc-bot-dot " + (bot.running ? "fc-bot-dot--on" : "fc-bot-dot--off"));
    row.appendChild(dot);

    var name = el("span", "fc-bot-name");
    name.textContent = bot.name;
    row.appendChild(name);

    var stats = el("span", "fc-bot-stats");
    stats.textContent = bot.total_runs + "r · " + bot.total_actions + "a";
    row.appendChild(stats);

    if (bot.last_ran_at) {
      var ago = el("span", "fc-bot-ago");
      ago.textContent = timeAgo(bot.last_ran_at * 1000);
      row.appendChild(ago);
    }

    if (bot.last_details) {
      var det = el("div", "fc-bot-detail");
      det.textContent = truncate(bot.last_details, 44);
      row.appendChild(det);
    }

    return row;
  }

  // ── Activity ──────────────────────────────────────────────────────────────

  function renderActivity() {
    var c = document.getElementById("fc-activity-body");
    if (!c) return;
    while (c.firstChild) c.removeChild(c.firstChild);
    if (!activityLog.length) {
      var hint = el("span", "fc-activity-hint");
      hint.textContent = "Watching for transitions…";
      c.appendChild(hint);
      return;
    }
    activityLog.forEach(function (item) {
      var wrap = el("span", "fc-activity-item");

      var name = el("span", "fc-activity-name");
      name.textContent = truncate(item.name, 24);
      wrap.appendChild(name);

      if (item.from) {
        wrap.appendChild(buildBadge(item.from));
        var arr = el("span", "fc-activity-arrow"); arr.textContent = "→";
        wrap.appendChild(arr);
      }
      wrap.appendChild(buildBadge(item.to));

      var ago = el("span", "fc-activity-ago");
      ago.textContent = timeAgo(item.time);
      wrap.appendChild(ago);

      c.appendChild(wrap);
    });
  }

  function buildBadge(s) {
    var b = el("span", "fc-badge fc-badge--" + s);
    b.textContent = s.replace("_", " ");
    return b;
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  function truncate(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  function timeAgo(ms) {
    var s = Math.floor((Date.now() - ms) / 1000);
    if (s < 5)    return "just now";
    if (s < 60)   return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h";
  }

  function makeEmpty(text) {
    var d = el("div", "fc-empty"); d.textContent = text; return d;
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  window.addEventListener("resize", drawPaths);
  pollTasks();
  pollStatus();
})();
