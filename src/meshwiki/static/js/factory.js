(function () {
  "use strict";

  const POLL_MS = 5000;
  const MAX_FEED = 20;

  const STAGE_COLORS = {
    planned:     "#6366f1",
    in_progress: "#0ea5e9",
    review:      "#f59e0b",
    merged:      "#10b981",
    failed:      "#ef4444",
    rejected:    "#dc2626",
  };

  // ── State ────────────────────────────────────────────────────────────────

  let feedItems = [];
  let prevByName = {};

  // ── DOM helpers ───────────────────────────────────────────────────────────

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function stageTasksEl(id)  { return document.getElementById("fc-tasks-" + id); }
  function badgeEl(id)       { return document.getElementById("fc-badge-" + id); }

  // ── Data fetching ─────────────────────────────────────────────────────────

  function poll() {
    var statuses = ["planned", "in_progress", "review", "merged", "failed", "rejected"];
    Promise.all(
      statuses.map(function (s) {
        return fetch("/api/v1/tasks?assignee=factory&status=" + s)
          .then(function (r) { return r.ok ? r.json() : []; })
          .catch(function () { return []; });
      })
    ).then(function (results) {
      var byStatus = {};
      statuses.forEach(function (s, i) { byStatus[s] = results[i] || []; });
      detectTransitions(byStatus);
      render(byStatus);
      drawPaths();
      var updEl = document.getElementById("fc-updated");
      if (updEl) {
        var now = new Date();
        updEl.textContent =
          "Updated " + now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      }
    }).catch(function () {}).finally(function () {
      setTimeout(poll, POLL_MS);
    });
  }

  function detectTransitions(byStatus) {
    var nextByName = {};
    Object.keys(byStatus).forEach(function (s) {
      (byStatus[s] || []).forEach(function (t) { nextByName[t.name] = s; });
    });
    Object.keys(nextByName).forEach(function (name) {
      var status = nextByName[name];
      var prev = prevByName[name];
      if (prev !== undefined && prev !== status) {
        feedItems.unshift({ name: name, from: prev, to: status, time: Date.now() });
      } else if (prev === undefined) {
        feedItems.unshift({ name: name, from: null, to: status, time: Date.now() });
      }
    });
    if (feedItems.length > MAX_FEED) feedItems.length = MAX_FEED;
    prevByName = nextByName;
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  function timeAgo(ms) {
    var s = Math.floor((Date.now() - ms) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  }

  function shortName(name) {
    return name.length > 32 ? name.slice(0, 30) + "…" : name;
  }

  function buildTaskCard(task, status) {
    var meta = task.metadata || {};
    var card = el("div", "fc-card fc-card--" + status);
    card.title = task.name;

    var nameDiv = el("div", "fc-card-name");
    nameDiv.textContent = shortName(task.name);
    card.appendChild(nameDiv);

    var metaDiv = el("div", "fc-card-meta");
    var dot = el("span", "fc-card-dot");
    var typeSpan = el("span");
    typeSpan.textContent = meta.type === "epic" ? "epic" : meta.parent_task ? "subtask" : "task";
    metaDiv.appendChild(dot);
    metaDiv.appendChild(typeSpan);

    if (meta.modified) {
      var sep = el("span");
      sep.textContent = "·";
      var timeSpan = el("span");
      timeSpan.textContent = new Date(meta.modified).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      metaDiv.appendChild(sep);
      metaDiv.appendChild(timeSpan);
    }
    card.appendChild(metaDiv);
    return card;
  }

  function buildEmptyMsg(text) {
    var d = el("div", "fc-empty");
    d.textContent = text;
    return d;
  }

  function buildOutcomeRow(cls, label, count) {
    var row = el("div", "fc-outcome-row fc-outcome-row--" + cls);
    var countEl = el("div", "fc-outcome-count");
    countEl.textContent = String(count);
    var labelEl = el("div", "fc-outcome-label");
    labelEl.textContent = label;
    row.appendChild(countEl);
    row.appendChild(labelEl);
    return row;
  }

  function buildStatusBadge(s) {
    var b = el("span", "fc-feed-status fc-feed-status--" + s);
    b.textContent = s.replace("_", " ");
    return b;
  }

  function render(byStatus) {
    var planned    = byStatus.planned     || [];
    var inProgress = byStatus.in_progress || [];
    var review     = byStatus.review      || [];
    var merged     = byStatus.merged      || [];
    var failed     = byStatus.failed      || [];
    var rejected   = byStatus.rejected    || [];

    // Backlog
    setStage("planned", planned.length ? planned.map(function (t) {
      return buildTaskCard(t, "planned");
    }) : [buildEmptyMsg("No tasks queued")]);

    // In Progress — epics first
    var ordered = inProgress
      .filter(function (t) { return (t.metadata || {}).type === "epic"; })
      .concat(inProgress.filter(function (t) { return (t.metadata || {}).type !== "epic"; }));
    setStage("in_progress", ordered.length ? ordered.map(function (t) {
      return buildTaskCard(t, "in_progress");
    }) : [buildEmptyMsg("Idle")]);

    // Review
    setStage("review", review.length ? review.map(function (t) {
      return buildTaskCard(t, "review");
    }) : [buildEmptyMsg("No open PRs")]);

    // Outcomes
    setStage("outcomes", [
      buildOutcomeRow("merged",   "✓ Merged",    merged.length),
      buildOutcomeRow("review",   "◎ Awaiting",  review.length),
      buildOutcomeRow("failed",   "✗ Failed",    failed.length),
      buildOutcomeRow("rejected", "⊘ Rejected",  rejected.length),
    ]);

    setBadge("planned",    planned.length);
    setBadge("in_progress",inProgress.length);
    setBadge("review",     review.length);
    setBadge("outcomes",   merged.length + failed.length + rejected.length);

    var progressStage = document.getElementById("fc-stage-in_progress");
    if (progressStage) {
      progressStage.classList.toggle("fc-stage--active", inProgress.length > 0);
    }

    renderFeed();
  }

  function setStage(id, nodes) {
    var container = stageTasksEl(id);
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);
    nodes.forEach(function (n) { container.appendChild(n); });
  }

  function setBadge(id, count) {
    var b = badgeEl(id);
    if (b) b.textContent = String(count);
  }

  function renderFeed() {
    var container = document.getElementById("fc-feed-scroll");
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);

    if (!feedItems.length) {
      var hint = el("span");
      hint.style.cssText = "font-size:0.72rem;color:#475569";
      hint.textContent = "Watching for transitions…";
      container.appendChild(hint);
      return;
    }

    feedItems.forEach(function (item) {
      var wrap = el("div", "fc-feed-item");

      var nameEl = el("span", "fc-feed-name");
      nameEl.textContent = shortName(item.name);
      wrap.appendChild(nameEl);

      if (item.from) {
        wrap.appendChild(buildStatusBadge(item.from));
        var arr = el("span");
        arr.textContent = " → ";
        arr.style.color = "#475569";
        wrap.appendChild(arr);
      }
      wrap.appendChild(buildStatusBadge(item.to));

      var ago = el("span");
      ago.style.cssText = "color:#334155;margin-left:0.25rem";
      ago.textContent = timeAgo(item.time);
      wrap.appendChild(ago);

      container.appendChild(wrap);
    });
  }

  // ── SVG paths ─────────────────────────────────────────────────────────────

  var CONNECTIONS = [
    ["fc-stage-planned",     "fc-stage-in_progress", STAGE_COLORS.planned],
    ["fc-stage-in_progress", "fc-stage-review",      STAGE_COLORS.in_progress],
    ["fc-stage-review",      "fc-stage-outcomes",    STAGE_COLORS.merged],
  ];

  function drawPaths() {
    var svgEl = document.getElementById("fc-svg");
    if (!svgEl) return;
    var svgRect = svgEl.getBoundingClientRect();

    // Build SVG markup for paths + particles using safe SVG DOM
    while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);

    var NS = "http://www.w3.org/2000/svg";

    CONNECTIONS.forEach(function (conn, idx) {
      var fromEl = document.getElementById(conn[0]);
      var toEl   = document.getElementById(conn[1]);
      var color  = conn[2];
      if (!fromEl || !toEl) return;

      var fR = fromEl.getBoundingClientRect();
      var tR = toEl.getBoundingClientRect();
      var x1 = fR.right  - svgRect.left;
      var y1 = fR.top + fR.height / 2 - svgRect.top;
      var x2 = tR.left   - svgRect.left;
      var y2 = tR.top + tR.height / 2 - svgRect.top;
      var cx = (x1 + x2) / 2;

      var pathId = "fc-conn-" + idx;
      var path = document.createElementNS(NS, "path");
      path.setAttribute("id", pathId);
      path.setAttribute("class", "fc-path");
      path.setAttribute("d", "M" + x1 + "," + y1 + " C" + cx + "," + y1 + " " + cx + "," + y2 + " " + x2 + "," + y2);
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "1.5");
      svgEl.appendChild(path);

      // Three staggered particles per connection
      [0, 0.33, 0.66].forEach(function (offset, pi) {
        var dur = (2.2 + pi * 0.4).toFixed(1);
        var begin = (offset * parseFloat(dur)).toFixed(2);

        var circle = document.createElementNS(NS, "circle");
        circle.setAttribute("class", "fc-particle");
        circle.setAttribute("r", "3");
        circle.setAttribute("fill", color);

        var motion = document.createElementNS(NS, "animateMotion");
        motion.setAttribute("dur", dur + "s");
        motion.setAttribute("begin", begin + "s");
        motion.setAttribute("repeatCount", "indefinite");

        var mpath = document.createElementNS(NS, "mpath");
        mpath.setAttributeNS("http://www.w3.org/1999/xlink", "href", "#" + pathId);
        motion.appendChild(mpath);
        circle.appendChild(motion);
        svgEl.appendChild(circle);
      });
    });
  }

  // ── Init ──────────────────────────────────────────────────────────────────

  window.addEventListener("resize", drawPaths);
  poll();
})();
