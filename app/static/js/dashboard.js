// 首页数据看板渲染 (零依赖): 柱状图 + 肌群条 + 跑步类型饼图 + 日/周/月粒度切换。
// 数据一次性内嵌于 #dashboard-data, 切换粒度纯前端, 无额外请求。
(function () {
  "use strict";
  var root = document.getElementById("dashboard");
  var dataEl = document.getElementById("dashboard-data");
  if (!root || !dataEl) return;

  var periods;
  try {
    periods = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }

  var GRAN_UNIT = { day: "日", week: "周", month: "月" };
  // 饼图配色 (跑步类型); 与整体色系协调
  var PIE_COLORS = ["#2f6f4e", "#4a90d9", "#e0975a", "#8e6fb0", "#5bb0a0", "#c65b6b", "#9aa0a6"];

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function fmtInt(n) {
    return Math.round(n).toLocaleString("en-US");
  }

  // ── 柱状图 ──
  function renderBars(elId, buckets, field, opts) {
    var el = document.getElementById(elId);
    if (!el) return;
    var max = 0;
    buckets.forEach(function (b) { if (b[field] > max) max = b[field]; });
    if (max <= 0) max = 1;
    var html = "";
    buckets.forEach(function (b) {
      var v = b[field] || 0;
      var pct = (v / max * 100).toFixed(1);
      var shown = opts.decimals ? (v ? v.toFixed(opts.decimals) : "") : (v ? fmtInt(v) : "");
      var title = b.label + ": " + (opts.decimals ? v.toFixed(opts.decimals) : fmtInt(v)) + " " + opts.unit;
      if (opts.extra) title += opts.extra(b);
      html +=
        '<div class="bar-slot">' +
        '<span class="bar-val">' + esc(shown) + "</span>" +
        '<div class="bar ' + opts.cls + '" style="height:' + pct + '%" title="' + esc(title) + '"></div>' +
        '<span class="bar-lab">' + esc(b.label) + "</span>" +
        "</div>";
    });
    el.innerHTML = html;
  }

  // ── 肌群横向条 ──
  function renderMuscle(groups) {
    var el = document.getElementById("chart-muscle");
    if (!el) return;
    if (!groups || !groups.length) {
      el.innerHTML = '<p class="muted small">该区间暂无力量数据。</p>';
      return;
    }
    var max = groups[0].volume_kg || 1;
    var html = "";
    groups.forEach(function (g) {
      var pct = (g.volume_kg / max * 100).toFixed(1);
      html +=
        '<div class="hbar-row">' +
        '<span class="hbar-lab">' + esc(g.group) + "</span>" +
        '<div class="hbar-track"><div class="hbar-fill" style="width:' + pct + '%"></div></div>' +
        '<span class="hbar-val">' + fmtInt(g.volume_kg) + "</span>" +
        "</div>";
    });
    el.innerHTML = html;
  }

  // ── 跑步类型饼图 (SVG donut, 按距离分布) ──
  function renderPie(runTypes) {
    var el = document.getElementById("chart-runtype");
    if (!el) return;
    var total = 0;
    (runTypes || []).forEach(function (t) { total += t.distance_km; });
    if (!total) {
      el.innerHTML = '<p class="muted small">该区间暂无跑步数据。</p>';
      return;
    }
    var cx = 60, cy = 60, r = 50, circ = 2 * Math.PI * r;
    var offset = 0;
    var segs = "";
    var legend = "";
    runTypes.forEach(function (t, i) {
      var frac = t.distance_km / total;
      var color = PIE_COLORS[i % PIE_COLORS.length];
      var dash = (frac * circ).toFixed(2) + " " + (circ - frac * circ).toFixed(2);
      // stroke-dashoffset 负值使弧段顺时针从 12 点方向排布
      segs +=
        '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" ' +
        'stroke="' + color + '" stroke-width="18" ' +
        'stroke-dasharray="' + dash + '" stroke-dashoffset="' + (-offset * circ).toFixed(2) + '">' +
        "<title>" + esc(t.label) + ": " + t.distance_km.toFixed(1) + " km / " + t.count + " 次 (" + Math.round(frac * 100) + "%)</title>" +
        "</circle>";
      legend +=
        '<div class="pie-leg-row">' +
        '<span class="pie-dot" style="background:' + color + '"></span>' +
        '<span class="pie-leg-lab">' + esc(t.label) + "</span>" +
        '<span class="pie-leg-val">' + t.distance_km.toFixed(1) + " km · " + Math.round(frac * 100) + "%</span>" +
        "</div>";
      offset += frac;
    });
    el.innerHTML =
      '<svg viewBox="0 0 120 120" class="pie-svg" role="img" aria-label="跑步类型分布饼图 (按距离)">' +
      '<g transform="rotate(-90 ' + cx + " " + cy + ')">' + segs + "</g>" +
      '<text x="' + cx + '" y="' + (cy - 2) + '" class="pie-center-num">' + total.toFixed(1) + "</text>" +
      '<text x="' + cx + '" y="' + (cy + 14) + '" class="pie-center-lab">km 跑量</text>' +
      "</svg>" +
      '<div class="pie-legend">' + legend + "</div>";
  }

  function render(gran) {
    var p = periods[gran];
    if (!p) return;
    var unit = GRAN_UNIT[gran];
    document.getElementById("chart-unit-strength").textContent = "(kg/" + unit + ")";
    document.getElementById("chart-unit-running").textContent = "(km/" + unit + ")";
    document.getElementById("chart-unit-muscle").textContent = "(近 12 " + unit + "合计 kg)";
    document.getElementById("chart-unit-runtype").textContent = "(近 12 " + unit + ")";

    renderBars("chart-strength", p.buckets, "strength_volume_kg", { cls: "strength", unit: "kg" });
    renderBars("chart-running", p.buckets, "running_km", {
      cls: "run", unit: "km", decimals: 1,
      extra: function (b) { return " · " + b.run_count + " 次"; },
    });
    renderMuscle(p.muscle_groups);
    renderPie(p.run_types);

    var t = p.totals;
    document.getElementById("dash-totals").innerHTML =
      "力量 <strong>" + fmtInt(t.strength_volume_kg) + "</strong> kg · 跑步 <strong>" +
      (t.running_km).toFixed(1) + "</strong> km / <strong>" + t.run_count + "</strong> 次";

    Array.prototype.forEach.call(root.querySelectorAll(".gran-btn"), function (btn) {
      var on = btn.getAttribute("data-gran") === gran;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  Array.prototype.forEach.call(root.querySelectorAll(".gran-btn"), function (btn) {
    btn.addEventListener("click", function () { render(btn.getAttribute("data-gran")); });
  });

  render(root.getAttribute("data-default") || "week");
})();
