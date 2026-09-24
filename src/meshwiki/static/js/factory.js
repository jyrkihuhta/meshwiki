(function () {
  "use strict";

  var TASKS_POLL_MS  = 5000;
  var STATUS_POLL_MS = 8000;
  var MAX_ACTIVITY   = 40;

  var prevByName  = {};
  var activityLog = [];
  var wsActive    = false;  // true while /ws/factory is connected

  var GRAPH_STATUS_LABELS = {
    intake: "Intake", decomposing: "Decomposing", dispatching: "Dispatching",
    grinding: "Grinding", reviewing: "Reviewing", completed: "Completed",
    failed: "Failed", escalated: "Escalated",
  };

  var STATUS_COLORS = {
    intake: "#818cf8", decomposing: "#fbbf24", dispatching: "#38bdf8",
    grinding: "#0ea5e9", reviewing: "#f59e0b", completed: "#34d399",
    failed: "#f87171", escalated: "#f87171", unknown: "#475569",
  };

  // LangGraph states shown in the track (in order)
  var LG_STATES = ["intake", "decomposing", "dispatching", "grinding", "reviewing"];

  // ── DOM helpers ───────────────────────────────────────────────────────────

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }
  function svgEl(tag) { return document.createElementNS("http://www.w3.org/2000/svg", tag); }
  function setText(id, text) { var e = document.getElementById(id); if (e) e.textContent = text; }

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
      return fetch("/api/factory/tasks?status=" + s)
        .then(function (r) { return r.ok ? r.json() : []; })
        .catch(function () { return []; });
    })).then(function (results) {
      var by = {};
      statuses.forEach(function (s, i) { by[s] = results[i] || []; });
      detectTransitions(by);
      // Update anchor counts
      setText("fc-count-planned",    String((by.planned     || []).length));
      setText("fc-count-in_progress",String((by.in_progress || []).length));
      setText("fc-count-merged",     String((by.merged      || []).length));
      setText("fc-count-failed",     String((by.failed      || []).length));
      setText("fc-count-rejected",   String((by.rejected    || []).length));
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
      // When WS is active it is the canonical transition source; only update
      // prevByName here so the next poll diff stays accurate.
      if (!wsActive) {
        if (p !== undefined && p !== s) pushActivity({name: name, from: p, to: s, time: Date.now()});
        else if (p === undefined) pushActivity({name: name, from: null, to: s, time: Date.now()});
      }
    });
    prevByName = next;
  }

  function pushActivity(item) {
    activityLog.unshift(item);
    if (activityLog.length > MAX_ACTIVITY) activityLog.length = MAX_ACTIVITY;
    renderActivity();
  }

  // ── Status API poll ───────────────────────────────────────────────────────

  function pollStatus() {
    fetch("/api/factory/status")
      .then(function (r) {
        if (r.status === 404) return null;        // factory disabled
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        var pill = document.getElementById("fc-orch-pill");
        if (!data) {
          if (pill) { pill.textContent = "orchestrator offline"; pill.className = "fc-orch-pill fc-orch-pill--offline"; }
          // Leave KPIs as "—"
          return;
        }
        if (data.error) {
          if (pill) { pill.textContent = "orchestrator unreachable"; pill.className = "fc-orch-pill fc-orch-pill--offline"; }
        } else {
          if (pill) { pill.textContent = "orchestrator online"; pill.className = "fc-orch-pill fc-orch-pill--online"; }
        }

        var res   = data.resources || {};
        var graphs = data.active_graphs || [];
        var grinders = res.active_grinders;
        var cost     = res.total_cost_usd;
        var cap      = res.max_concurrent_parent_tasks;
        var active   = res.active_parent_tasks || 0;

        setText("fc-kpi-grinders", grinders != null ? String(grinders) : "—");
        setText("fc-kpi-cost",     cost     != null ? "$" + cost.toFixed(3) : "—");
        setText("fc-kpi-cap",      cap      != null ? String(Math.max(0, cap - active)) : "—");

        updateLangGraphTrack(graphs);
        renderGraphs(graphs);
        renderBots(data.bots || []);
        drawPaths();
      })
      .catch(function () {
        var pill = document.getElementById("fc-orch-pill");
        if (pill) { pill.textContent = "orchestrator offline"; pill.className = "fc-orch-pill fc-orch-pill--offline"; }
      })
      .finally(function () { setTimeout(pollStatus, STATUS_POLL_MS); });
  }

  // ── LangGraph track ───────────────────────────────────────────────────────

  function updateLangGraphTrack(graphs) {
    // Clear all nodes
    LG_STATES.forEach(function (s) {
      var dot   = document.getElementById("fc-lgd-" + s);
      var pills = document.getElementById("fc-lgt-" + s);
      if (dot) { dot.className = "fc-lg-dot"; dot.style.background = ""; dot.style.boxShadow = ""; }
      if (pills) { while (pills.firstChild) pills.removeChild(pills.firstChild); }
    });
    var grinderEl = document.getElementById("fc-lg-grinders");
    if (grinderEl) grinderEl.textContent = "";

    var totalGrinders = 0;

    graphs.forEach(function (g) {
      var state = g.graph_status;
      if (LG_STATES.indexOf(state) === -1) return;

      var color = STATUS_COLORS[state] || "#64748b";
      var dot   = document.getElementById("fc-lgd-" + state);
      var pills = document.getElementById("fc-lgt-" + state);

      if (dot) {
        dot.className = "fc-lg-dot fc-lg-dot--active";
        dot.style.background  = color;
        dot.style.boxShadow   = "0 0 14px " + color + "99";
        dot.style.borderColor = color;
      }

      if (pills) {
        var pill = el("span", "fc-lg-pill");
        pill.textContent = truncate(g.title || g.thread_id, 20);
        pill.style.background  = hexToRgba(color, 0.15);
        pill.style.color       = color;
        pill.style.borderColor = hexToRgba(color, 0.3);
        pills.appendChild(pill);
      }

      if (state === "grinding") totalGrinders += g.active_grinders || 0;
    });

    if (grinderEl && totalGrinders > 0) {
      grinderEl.textContent = "⚙ " + totalGrinders + " running";
      grinderEl.style.display = "";
    } else if (grinderEl) {
      grinderEl.style.display = "none";
    }
  }

  // ── SVG animated paths ────────────────────────────────────────────────────

  // Connect: backlog anchor → first LG node, last LG node → outcomes anchor
  var CONNECTIONS = [
    ["fc-node-planned",   "fc-lgn-intake",   "#6366f1"],
    ["fc-lgn-reviewing",  "fc-node-outcomes", "#10b981"],
  ];

  function drawPaths() {
    var svg = document.getElementById("fc-svg");
    if (!svg) return;
    var svgR = svg.getBoundingClientRect();
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    CONNECTIONS.forEach(function (conn, idx) {
      var fromEl = document.getElementById(conn[0]);
      var toEl   = document.getElementById(conn[1]);
      if (!fromEl || !toEl) return;
      var color = conn[2];

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

      [0, 0.4, 0.7].forEach(function (offset, pi) {
        var dur = (2.8 + pi * 0.6).toFixed(1);
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

  // ── Graph cards ───────────────────────────────────────────────────────────

  function renderGraphs(graphs) {
    setText("fc-graphs-badge", String(graphs.length));
    if (!graphs.length) { setBody("fc-graphs-body", makeEmpty("No active graphs")); return; }
    setBody("fc-graphs-body", graphs.map(buildGraphCard));
  }

  function buildGraphCard(g) {
    var total = g.subtasks_total || 0;
    var done  = (g.subtasks_completed || 0) + (g.subtasks_failed || 0);
    var pct   = total > 0 ? Math.round(done / total * 100) : 0;
    var color = STATUS_COLORS[g.graph_status] || "#64748b";

    var card = el("div", "fc-graph-card");

    // Ring
    var ringWrap = el("div", "fc-ring-wrap");
    ringWrap.appendChild(buildRing(pct, color));
    var pctLabel = el("div", "fc-ring-pct");
    pctLabel.textContent = total > 0 ? pct + "%" : "—";
    ringWrap.appendChild(pctLabel);
    card.appendChild(ringWrap);

    // Info
    var info = el("div", "fc-graph-info");

    var title = el("div", "fc-graph-title");
    title.textContent = truncate(g.title || g.thread_id, 28);
    info.appendChild(title);

    // Mini LangGraph track for this card
    info.appendChild(buildMiniTrack(g.graph_status));

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

  function buildMiniTrack(currentStatus) {
    var track = el("div", "fc-mini-track");
    var all = ["intake","decomposing","dispatching","grinding","reviewing","completed"];
    var currentIdx = all.indexOf(currentStatus);
    if (currentIdx < 0 && (currentStatus === "failed" || currentStatus === "escalated")) currentIdx = 5;

    all.forEach(function (s, i) {
      var dot = el("span", "fc-mini-dot");
      if (i < currentIdx) {
        dot.classList.add("fc-mini-dot--done");
      } else if (i === currentIdx) {
        dot.classList.add("fc-mini-dot--active");
        dot.style.background  = STATUS_COLORS[currentStatus] || "#64748b";
        dot.style.boxShadow   = "0 0 8px " + (STATUS_COLORS[currentStatus] || "#64748b") + "99";
      }
      dot.title = s;
      track.appendChild(dot);
      if (i < all.length - 1) {
        var line = el("span", "fc-mini-line");
        if (i < currentIdx) line.classList.add("fc-mini-line--done");
        track.appendChild(line);
      }
    });

    var statusLabel = el("span", "fc-mini-label");
    statusLabel.textContent = GRAPH_STATUS_LABELS[currentStatus] || currentStatus;
    statusLabel.style.color = STATUS_COLORS[currentStatus] || "#64748b";
    track.appendChild(statusLabel);

    return track;
  }

  function buildRing(pct, color) {
    var r = 20, cx = 24, cy = 24;
    var circ = 2 * Math.PI * r;
    var svg = svgEl("svg");
    svg.setAttribute("width","48"); svg.setAttribute("height","48");
    svg.setAttribute("viewBox","0 0 48 48"); svg.setAttribute("class","fc-ring");
    var bg = svgEl("circle");
    bg.setAttribute("cx",String(cx)); bg.setAttribute("cy",String(cy));
    bg.setAttribute("r",String(r)); bg.setAttribute("fill","none");
    bg.setAttribute("stroke","rgba(255,255,255,0.06)"); bg.setAttribute("stroke-width","3");
    svg.appendChild(bg);
    var arc = svgEl("circle");
    arc.setAttribute("cx",String(cx)); arc.setAttribute("cy",String(cy));
    arc.setAttribute("r",String(r)); arc.setAttribute("fill","none");
    arc.setAttribute("stroke", color); arc.setAttribute("stroke-width","3");
    arc.setAttribute("stroke-dasharray",String(circ));
    arc.setAttribute("stroke-dashoffset",String(circ * (1 - Math.min(pct,100)/100)));
    arc.setAttribute("stroke-linecap","round");
    arc.setAttribute("transform","rotate(-90 "+cx+" "+cy+")");
    svg.appendChild(arc);
    return svg;
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
    var name = el("span", "fc-bot-name"); name.textContent = bot.name;
    row.appendChild(name);
    var stats = el("span", "fc-bot-stats");
    stats.textContent = bot.total_runs + "r · " + bot.total_actions + "a";
    row.appendChild(stats);
    if (bot.last_ran_at) {
      var ago = el("span", "fc-bot-ago"); ago.textContent = timeAgo(bot.last_ran_at * 1000);
      row.appendChild(ago);
    }
    if (bot.last_details) {
      var det = el("div", "fc-bot-detail"); det.textContent = truncate(bot.last_details, 44);
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
      var hint = el("span", "fc-activity-hint"); hint.textContent = "Watching for transitions…";
      c.appendChild(hint); return;
    }
    activityLog.forEach(function (item) {
      var wrap = el("span", "fc-activity-item");
      var name = el("span", "fc-activity-name"); name.textContent = truncate(item.name, 24);
      wrap.appendChild(name);
      if (item.from) { wrap.appendChild(buildBadge(item.from)); var arr = el("span","fc-activity-arrow"); arr.textContent="→"; wrap.appendChild(arr); }
      wrap.appendChild(buildBadge(item.to));
      var ago = el("span", "fc-activity-ago"); ago.textContent = timeAgo(item.time);
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

  function truncate(s, n) { return s.length > n ? s.slice(0, n-1) + "…" : s; }

  function timeAgo(ms) {
    var s = Math.floor((Date.now() - ms) / 1000);
    if (s < 5)    return "just now";
    if (s < 60)   return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h";
  }

  function makeEmpty(text) { var d = el("div","fc-empty"); d.textContent=text; return d; }

  function hexToRgba(hex, alpha) {
    var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  // ── Activity history (server ring buffer) ────────────────────────────────

  function loadActivity() {
    fetch("/api/factory/activity")
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (items) {
        if (!items.length) return;
        // Server returns oldest-first; prepend each so newest ends up first.
        var capped = items.slice(-MAX_ACTIVITY);
        for (var i = capped.length - 1; i >= 0; i--) {
          activityLog.push(capped[i]);
        }
        activityLog.reverse();
        renderActivity();
      })
      .catch(function () {});
  }

  // ── Factory WebSocket (push-based transitions) ────────────────────────────

  var wsRetryMs = 1000;

  function openFactoryWS() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    var ws = new WebSocket(proto + "//" + location.host + "/ws/factory");

    ws.addEventListener("open", function () {
      wsActive = true;
      wsRetryMs = 1000;
    });

    ws.addEventListener("message", function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        if (msg.type === "transition") {
          // Keep prevByName in sync so poll-based diff won't re-detect this.
          prevByName[msg.name] = msg.to;
          pushActivity({name: msg.name, from: msg["from"], to: msg.to, time: msg.time});
        }
      } catch (e) {}
    });

    ws.addEventListener("close", function () {
      wsActive = false;
      // Exponential back-off, cap at 30 s.
      wsRetryMs = Math.min(wsRetryMs * 2, 30000);
      setTimeout(openFactoryWS, wsRetryMs);
    });

    ws.addEventListener("error", function () { ws.close(); });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  window.addEventListener("resize", drawPaths);
  loadActivity();
  openFactoryWS();
  pollTasks();
  pollStatus();
})();
