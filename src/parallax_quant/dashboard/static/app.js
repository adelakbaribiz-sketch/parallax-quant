"use strict";

// Parallax Quant research console. Renders /api/snapshot — every number is
// produced server-side by the engine's real strategy/risk code (see
// dashboard/snapshot.py). This file only draws.

const VIEWS = {
  "overview": ["00 / overview", "Overview"],
  "stat-arb": ["01 / pairs", "Statistical arbitrage"],
  "triangular": ["02 / cycles", "Triangular arbitrage"],
  "funding": ["03 / carry", "Funding-rate carry"],
  "risk": ["04 / risk", "Risk lab"],
  "config": ["05 / config", "Config & live-trading gate"],
};
const C = { lime: "#d6ff5a", violet: "#8f86ff", pos: "#49dfa0", neg: "#ff5d7a", warn: "#ffb547", muted: "#8b91a8", line: "#1e2232" };
const SVGNS = "http://www.w3.org/2000/svg";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (v, d = 0) => (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 2) => `${v >= 0 ? "" : "−"}${Math.abs(v).toFixed(d)}%`;
const fixed = (v, d = 3) => (v == null ? "—" : Number(v).toFixed(d));
const label = (s) => String(s).replace(/_/g, " ");

let snapshot = null;

// ---------------------------------------------------------------- charts

function el(tag, attrs = {}, parent) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(min, max, count = 4) {
  const span = max - min || 1;
  const step = Math.pow(10, Math.floor(Math.log10(span / count)));
  const err = (span / count) / step;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  const s = step * mult;
  const ticks = [];
  for (let t = Math.ceil(min / s) * s; t <= max + 1e-9; t += s) ticks.push(+t.toFixed(10));
  return ticks;
}

const tooltip = {
  show(evt, html) {
    const t = $("#tooltip");
    t.innerHTML = html;
    t.hidden = false;
    const x = Math.min(evt.clientX + 14, window.innerWidth - t.offsetWidth - 8);
    t.style.left = `${x}px`;
    t.style.top = `${evt.clientY + 14}px`;
  },
  hide() { $("#tooltip").hidden = true; },
};

/** Line chart. series: [{values:[number|null], color, width, name}], bands: [{from,to,color}],
 *  hlines: [{y,color,dash,label}], markers: [{x,y,color}] */
function lineChart({ series, bands = [], hlines = [], markers = [], height = 220, yFormat = (v) => v, xLabel = (i) => i }) {
  const W = 760, H = height, P = { l: 52, r: 14, t: 10, b: 24 };
  const svg = el("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img" });
  const n = Math.max(...series.map((s) => s.values.length));
  const all = series.flatMap((s) => s.values.filter((v) => v != null)).concat(hlines.map((h) => h.y));
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = (i) => P.l + (i / Math.max(1, n - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);

  for (const t of niceTicks(lo, hi)) {
    el("line", { class: "grid-line", x1: P.l, x2: W - P.r, y1: y(t), y2: y(t) }, svg);
    el("text", { x: P.l - 8, y: y(t) + 3.5, "text-anchor": "end" }, svg).textContent = yFormat(t);
  }
  for (const b of bands) {
    el("rect", { x: P.l, width: W - P.l - P.r, y: y(Math.min(b.to, hi)), height: Math.max(0, y(Math.max(b.from, lo)) - y(Math.min(b.to, hi))), fill: b.color }, svg);
  }
  for (const h of hlines) {
    el("line", { x1: P.l, x2: W - P.r, y1: y(h.y), y2: y(h.y), stroke: h.color, "stroke-dasharray": h.dash || "", "stroke-width": 1 }, svg);
    if (h.label) el("text", { x: W - P.r, y: y(h.y) - 4, "text-anchor": "end", style: `fill:${h.color}` }, svg).textContent = h.label;
  }
  for (const s of series) {
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    el("path", { d, fill: "none", stroke: s.color, "stroke-width": s.width || 1.6, "stroke-linejoin": "round", opacity: s.opacity ?? 1 }, svg);
  }
  for (const m of markers) el("circle", { cx: x(m.x), cy: y(m.y), r: 3.2, fill: m.color, stroke: "#08090d", "stroke-width": 1.2 }, svg);
  for (const t of niceTicks(0, n - 1, 6)) {
    el("text", { x: x(t), y: H - 6, "text-anchor": "middle" }, svg).textContent = xLabel(t);
  }

  // hover crosshair
  const cross = el("line", { y1: P.t, y2: H - P.b, stroke: C.muted, "stroke-dasharray": "2 3", opacity: 0 }, svg);
  const hit = el("rect", { x: P.l, y: P.t, width: W - P.l - P.r, height: H - P.t - P.b, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const px = ((evt.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - P.l) / (W - P.l - P.r)) * (n - 1));
    if (i < 0 || i >= n) return;
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("opacity", 0.8);
    const rows = series.map((s) => `<span style="color:${s.color}">■</span> ${esc(s.name || "")} ${s.values[i] == null ? "—" : esc(yFormat(s.values[i]))}`);
    tooltip.show(evt, `t=${esc(xLabel(i))}<br>${rows.join("<br>")}`);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); tooltip.hide(); });
  return svg;
}

/** Horizontal bars with optional threshold line. rows: [{label, value, color, note}] */
function hBars({ rows, max, threshold, format = (v) => v, height = 26 }) {
  const W = 760, L = 190, R = 90, H = rows.length * height + 18;
  const svg = el("svg", { class: "chart", viewBox: `0 0 ${W} ${H}` });
  const lo = Math.min(0, ...rows.map((r) => r.value), threshold ?? 0);
  const hi = Math.max(max ?? 0, ...rows.map((r) => r.value), threshold ?? 0);
  const x = (v) => L + ((v - lo) / (hi - lo || 1)) * (W - L - R);
  rows.forEach((r, i) => {
    const yy = 6 + i * height;
    el("text", { x: L - 10, y: yy + height / 2 + 2, "text-anchor": "end", style: "fill:#e7e9f2" }, svg).textContent = r.label;
    el("rect", { x: L, y: yy + 5, width: W - L - R, height: height - 12, fill: "#0b0c12", rx: 3 }, svg);
    const x0 = x(Math.min(0, r.value)), x1 = x(Math.max(0, r.value));
    const bar = el("rect", { x: x0, y: yy + 5, width: Math.max(1.5, x1 - x0), height: height - 12, fill: r.color, rx: 3 }, svg);
    if (r.note) {
      bar.addEventListener("mousemove", (evt) => tooltip.show(evt, esc(r.note)));
      bar.addEventListener("mouseleave", tooltip.hide);
    }
    el("text", { x: W - R + 10, y: yy + height / 2 + 2, style: `fill:${r.color}` }, svg).textContent = format(r.value);
  });
  if (lo < 0) el("line", { class: "zero", x1: x(0), x2: x(0), y1: 2, y2: H - 12 }, svg);
  if (threshold != null) {
    el("line", { x1: x(threshold), x2: x(threshold), y1: 0, y2: H - 10, stroke: C.warn, "stroke-dasharray": "4 3" }, svg);
    el("text", { x: x(threshold), y: H - 1, "text-anchor": "middle", style: `fill:${C.warn}` }, svg).textContent = `limit ${format(threshold)}`;
  }
  return svg;
}

/** Square heatmap. values: 2D, null on diagonal. colorFor(v) -> css color */
function heatmap({ labels, colLabels = labels, values, colorFor, format, cell = 64 }) {
  const L = 108, T = 26, n = labels.length;
  const W = L + n * cell + 4, H = T + n * cell + 4;
  const svg = el("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, style: `max-width:${W}px` });
  labels.forEach((lb, i) => {
    el("text", { x: L + i * cell + cell / 2, y: T - 9, "text-anchor": "middle" }, svg).textContent = colLabels[i];
    el("text", { x: L - 10, y: T + i * cell + cell / 2 + 3.5, "text-anchor": "end" }, svg).textContent = lb;
  });
  values.forEach((row, i) => row.forEach((v, j) => {
    const r = el("rect", { x: L + j * cell + 2, y: T + i * cell + 2, width: cell - 4, height: cell - 4, rx: 5,
      fill: v == null ? "#0b0c12" : colorFor(v, i, j), stroke: "#1e2232" }, svg);
    if (v != null) {
      el("text", { x: L + j * cell + cell / 2, y: T + i * cell + cell / 2 + 4, "text-anchor": "middle", style: "fill:#e7e9f2" }, svg).textContent = format(v);
      r.addEventListener("mousemove", (evt) => tooltip.show(evt, `${esc(labels[i])} → ${esc(labels[j])}: ${esc(format(v))}`));
      r.addEventListener("mouseleave", tooltip.hide);
    }
  }));
  return svg;
}

function histogram({ values, bins = 28, markers = [], height = 200, format = (v) => v }) {
  const W = 760, H = height, P = { l: 40, r: 14, t: 12, b: 24 };
  const svg = el("svg", { class: "chart", viewBox: `0 0 ${W} ${H}` });
  const lo = Math.min(...values, ...markers.map((m) => m.x)), hi = Math.max(...values, ...markers.map((m) => m.x));
  const w = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  values.forEach((v) => { counts[Math.min(bins - 1, Math.floor((v - lo) / w))] += 1; });
  const cmax = Math.max(...counts);
  const x = (v) => P.l + ((v - lo) / (hi - lo || 1)) * (W - P.l - P.r);
  const bw = (W - P.l - P.r) / bins;
  counts.forEach((c, i) => {
    const h = (c / cmax) * (H - P.t - P.b);
    const left = lo + i * w;
    el("rect", { x: P.l + i * bw + 1, y: H - P.b - h, width: bw - 2, height: h, rx: 2, fill: left + w <= 0 ? C.neg : C.violet, opacity: 0.85 }, svg);
  });
  el("line", { class: "zero", x1: P.l, x2: W - P.r, y1: H - P.b, y2: H - P.b }, svg);
  for (const t of niceTicks(lo, hi, 6)) el("text", { x: x(t), y: H - 6, "text-anchor": "middle" }, svg).textContent = format(t);
  for (const m of markers) {
    el("line", { x1: x(m.x), x2: x(m.x), y1: P.t - 4, y2: H - P.b, stroke: m.color, "stroke-dasharray": "4 3" }, svg);
    el("text", { x: x(m.x) + 5, y: P.t + 6, style: `fill:${m.color}` }, svg).textContent = m.label;
  }
  return svg;
}

// ---------------------------------------------------------------- helpers

function card(cls, title, hint, body) {
  return `<div class="card ${cls}"><div class="card-head"><h2>${esc(title)}</h2>${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</div>${body}</div>`;
}
function kpi(cls, labelText, value, sub) {
  return `<div class="card kpi ${cls}"><div class="label">${esc(labelText)}</div><div class="value">${esc(value)}</div><div class="sub">${esc(sub)}</div></div>`;
}
function mount(id, chart) { const host = document.getElementById(id); host.innerHTML = ""; host.appendChild(chart); }
function statusPill(ok, text) { return `<span class="pill ${ok === true ? "ok" : ok === false ? "bad" : "skip"}">${esc(text)}</span>`; }

// ---------------------------------------------------------------- views

function renderOverview(s) {
  const r = s.risk, sa = s.stat_arb, tri = s.triangular, f = s.funding;
  const actionable = sa.signals.filter((x) => x.actionable).length + tri.cycles.filter((c) => c.actionable).length + f.rows.filter((x) => x.status === "actionable").length;
  const candidates = sa.signals.length + tri.cycles.length + f.rows.filter((x) => x.status !== "skipped").length;
  const lastZ = [...sa.steps].reverse().find((x) => x.z != null);
  const live = s.config.live_trading_authorized;

  $("#view-overview").innerHTML = `
    <div class="grid">
      ${kpi("span-3 accent", `VaR ${(r.var.confidence * 100).toFixed(0)}% · ${r.var.horizon_days}d`, usd(r.var.var_usd), `CVaR ${usd(r.var.cvar_usd)} · ${r.var.paths.toLocaleString()} Monte Carlo paths`)}
      ${kpi("span-3", "Gross exposure", usd(r.gross_exposure_usd), `${(r.gross_exposure_usd / r.equity_usd * 100).toFixed(1)}% of ${usd(r.equity_usd)} paper equity`)}
      ${kpi("span-3", "Actionable / candidates", `${actionable} / ${candidates}`, "after each strategy's own net-of-cost gate")}
      ${kpi("span-3", "Effective independent bets", r.correlation.effective_independent_bets.toFixed(2), `of ${r.correlation.labels.length} positions (eigenvalue N_eff)`)}

      ${card("span-8", "Spread Z-score · " + sa.pair.join(" vs "), lastZ ? `last z = ${lastZ.z.toFixed(2)}` : "", `<div id="ov-z"></div>
        <div class="legend"><span><i style="background:${C.lime}"></i>z-score</span><span><i style="background:${C.violet}"></i>±entry ${sa.thresholds.entry}</span><span><i style="background:${C.muted}"></i>±exit ${sa.thresholds.exit}</span></div>`)}
      ${card("span-4", "Strategy allocation bands", "config.yaml", `<div class="alloc">${s.config.allocations.map((a) => `
        <div class="alloc-row"><span>${esc(label(a.strategy))}</span>
          <span class="alloc-track"><span class="alloc-fill" style="left:0;width:${a.min}%"></span><span class="alloc-fill band" style="left:${a.min}%;width:${a.max - a.min}%"></span></span>
          <span class="num" style="text-align:right">${a.min}–${a.max}%</span></div>`).join("")}</div>
        <p class="footnote">Solid = guaranteed minimum, hatched = discretionary band. Validated to sum ≤ 100% by <code>StrategiesConfig</code>.</p>`)}

      ${card("span-12", "Agent pipeline", "message bus: " + s.config.message_bus, `
        <div class="pipeline">
          <div class="stage"><div class="n">01 · SCANNER</div><div class="t">scan()</div><div class="d">Each strategy emits raw candidates from current market state.</div></div>
          <div class="stage"><div class="n">02 · OPPORTUNITY</div><div class="t">evaluate()</div><div class="d">Net out fees + slippage, size, attach reject reasons.</div></div>
          <div class="stage"><div class="n">03 · RISK</div><div class="t">limits · VaR · correlation</div><div class="d">Per-asset / per-exchange caps, Monte Carlo VaR, weight cuts.</div></div>
          <div class="stage"><div class="n">04 · EXECUTION</div><div class="t">execute()</div><div class="d">Routed to the adapter below — paper unless the gate opens.</div></div>
        </div>
        <div class="adapter-row">
          <div class="adapter ${live ? "" : "on"}">● PaperExecutionAdapter ${live ? "" : "— active"}</div>
          <div class="adapter ${live ? "on" : ""}">○ LiveCex / LiveDex ${live ? "— ARMED" : "— locked"}</div>
        </div>`)}

      ${card("span-6", "Today's candidates", "", `<div class="table-scroll"><table>
        <thead><tr><th>strategy</th><th>instrument</th><th class="num">net edge</th><th>status</th></tr></thead><tbody>
        ${tri.cycles.map((c) => `<tr><td>triangular</td><td class="mono">${esc(c.path.join("→"))}</td><td class="num ${c.net_edge_pct > 0 ? "pos" : "neg"}">${pct(c.net_edge_pct, 3)}</td><td>${statusPill(c.actionable, c.actionable ? "actionable" : "rejected")}</td></tr>`).join("")}
        ${f.rows.filter((x) => x.status !== "skipped").map((x) => `<tr><td>funding rate</td><td class="mono">${esc(x.symbol)}</td><td class="num ${x.net_edge_pct > 0 ? "pos" : "neg"}">${pct(x.net_edge_pct, 3)}</td><td>${statusPill(x.status === "actionable", x.status)}</td></tr>`).join("")}
        ${sa.signals.slice(-3).map((x) => `<tr><td>stat-arb · t${x.t}</td><td class="mono">${esc(sa.pair.join("/"))} z=${x.zscore.toFixed(2)}</td><td class="num ${x.net_edge_pct > 0 ? "pos" : "neg"}">${pct(x.net_edge_pct, 3)}</td><td>${statusPill(x.actionable, x.actionable ? "actionable" : "rejected")}</td></tr>`).join("")}
        </tbody></table></div>`)}
      ${card("span-6", "Stress scenarios", `drawdown limit ${r.max_drawdown_pct}%`, `<div id="ov-stress"></div>`)}
    </div>`;

  if (sa.steps.length) mount("ov-z", zChart(sa, 200));
  else $("#ov-z").innerHTML = `<p class="footnote">This seed's pair failed cointegration, so StatArbStrategy never scans it. Try another seed.</p>`;
  mount("ov-stress", stressChart(r));
}

function zChart(sa, height) {
  const z = sa.steps.map((x) => x.z);
  const th = sa.thresholds;
  return lineChart({
    height,
    series: [{ values: z, color: C.lime, name: "z", width: 1.4 }],
    bands: [{ from: -th.exit, to: th.exit, color: "rgba(139,145,168,0.08)" }],
    hlines: [
      { y: th.entry, color: C.violet, dash: "5 4", label: `+${th.entry}` }, { y: -th.entry, color: C.violet, dash: "5 4", label: `−${th.entry}` },
      { y: 0, color: "#2b3046" },
    ],
    markers: sa.signals.map((x) => ({ x: x.t, y: x.zscore, color: x.actionable ? C.lime : x.direction === "short" ? C.neg : C.pos })),
    yFormat: (v) => Number(v).toFixed(1),
  });
}

function stressChart(r) {
  const limit = -r.equity_usd * r.max_drawdown_pct / 100;
  return hBars({
    rows: r.stress.map((x) => ({ label: label(x.scenario), value: x.pnl_usd, color: x.breach ? C.neg : C.warn,
      note: `surviving equity ${usd(x.surviving_equity_usd)}${x.breach ? " — BREACHES limit" : ""}` })),
    threshold: limit,
    format: (v) => usd(v),
  });
}

function renderStatArb(s) {
  const sa = s.stat_arb;
  const eg = sa.engle_granger, jh = sa.johansen;
  const actionable = sa.signals.filter((x) => x.actionable).length;
  if (!sa.steps.length) {
    $("#view-stat-arb").innerHTML = card("", "Pair not selected", "", `<p>With seed ${s.meta.seed} the pair failed cointegration (Engle-Granger p=${fixed(eg.p_value, 4)}, Johansen ${jh.cointegrated ? "pass" : "fail"}). StatArbStrategy only trades pairs that pass both tests, so there is nothing to scan. Try another seed.</p>`);
    return;
  }
  $("#view-stat-arb").innerHTML = `
    <div class="grid">
      ${card("span-4", "Engle–Granger", "lookback window", `<dl class="stat-list">
        <dt>test statistic</dt><dd>${fixed(eg.statistic)}</dd><dt>5% critical</dt><dd>${fixed(eg.critical_95)}</dd>
        <dt>p-value</dt><dd>${fixed(eg.p_value, 4)}</dd><dt>verdict</dt><dd>${statusPill(eg.cointegrated, eg.cointegrated ? "cointegrated" : "rejected")}</dd></dl>`)}
      ${card("span-4", "Johansen trace", "both tests must pass", `<dl class="stat-list">
        <dt>trace statistic</dt><dd>${fixed(jh.statistic)}</dd><dt>5% critical</dt><dd>${fixed(jh.critical_95)}</dd>
        <dt>p-value</dt><dd>n/a (trace test)</dd><dt>verdict</dt><dd>${statusPill(jh.cointegrated, jh.cointegrated ? "cointegrated" : "rejected")}</dd></dl>`)}
      ${card("span-4", "Signal gate", `${sa.signals.length} crossings`, `<dl class="stat-list">
        <dt>entry |z| ≥</dt><dd>${sa.thresholds.entry}</dd><dt>exit |z| ≤</dt><dd>${sa.thresholds.exit}</dd>
        <dt>stop-loss</dt><dd>${sa.thresholds.stop}σ</dd><dt>actionable after costs</dt><dd>${actionable} / ${sa.signals.length}</dd></dl>`)}

      ${card("span-12", "Spread Z-score", "markers = scan() opportunities · lime = cleared evaluate()", `<div id="sa-z"></div>`)}
      ${card("span-7", "Prices", sa.pair.join(" · "), `<div id="sa-px"></div><div class="legend"><span><i style="background:${C.violet}"></i>${esc(sa.pair[0])}</span><span><i style="background:${C.lime}"></i>${esc(sa.pair[1])}</span></div>`)}
      ${card("span-5", "Kalman hedge ratio β", "Q, R from config", `<div id="sa-beta"></div>`)}
      ${card("span-12", "Signals", "StatArbStrategy.evaluate()", `<div class="table-scroll"><table>
        <thead><tr><th>t</th><th>side</th><th class="num">z</th><th class="num">net edge</th><th class="num">confidence</th><th class="num">size</th><th>result</th></tr></thead><tbody>
        ${sa.signals.slice(-14).reverse().map((x) => `<tr><td class="mono">${x.t}</td><td>${x.direction === "short" ? "short spread" : "long spread"}</td>
          <td class="num">${x.zscore.toFixed(3)}</td><td class="num ${x.net_edge_pct > 0 ? "pos" : "neg"}">${pct(x.net_edge_pct, 3)}</td>
          <td class="num">${x.confidence.toFixed(2)}</td><td class="num">${usd(x.notional_usd)}</td>
          <td>${x.actionable ? statusPill(true, "actionable") : `<span class="reason">${esc(x.reject_reason)}</span>`}</td></tr>`).join("")}
        </tbody></table></div>
        <p class="footnote">Showing the latest 14 of ${sa.signals.length}. Net edge uses the strategy's own placeholder cost aggregate (0.20% fees + 0.05% slippage) — the execution layer applies the real per-venue fee schedule.</p>`)}
    </div>`;
  mount("sa-z", zChart(sa, 240));
  mount("sa-px", lineChart({ height: 210, series: [
    { values: sa.steps.map((x) => x.a), color: C.violet, name: sa.pair[0] },
    { values: sa.steps.map((x) => x.b), color: C.lime, name: sa.pair[1], opacity: 0.9 },
  ], yFormat: (v) => Number(v).toFixed(0) }));
  mount("sa-beta", lineChart({ height: 210, series: [{ values: sa.steps.map((x) => x.beta), color: C.warn, name: "β" }], yFormat: (v) => Number(v).toFixed(3) }));
}

function renderTriangular(s) {
  const t = s.triangular;
  const cyc = t.cycles[0];
  const dev = (v) => {
    const a = Math.min(1, Math.abs(v) / 60);
    return v > 0 ? `rgba(214,255,90,${0.12 + a * 0.6})` : `rgba(143,134,255,${0.08 + a * 0.35})`;
  };
  $("#view-triangular").innerHTML = `
    <div class="grid">
      ${card("span-7", "Detected cycle", "Bellman–Ford on −log(rate)", cyc ? `
        <div class="cycle-path">${cyc.path.map((n, i) => `${i ? '<span class="arrow">→</span>' : ""}<span class="node ${i === 0 || i === cyc.path.length - 1 ? "start" : ""}">${esc(n)}</span>`).join("")}</div>
        <dl class="stat-list" style="margin-top:16px">
          <dt>gross edge (Π rates − 1)</dt><dd class="pos">${pct(cyc.gross_edge_pct, 4)}</dd>
          <dt>taker fees · ${cyc.path.length - 1} legs @ ${t.taker_fee_pct}%</dt><dd class="neg">−${cyc.fees_pct.toFixed(4)}%</dd>
          <dt>net edge</dt><dd class="${cyc.net_edge_pct > 0 ? "pos" : "neg"}">${pct(cyc.net_edge_pct, 4)}</dd>
          <dt>min required after fees</dt><dd>${t.min_profit_after_fees_pct}%</dd>
          <dt>size within depth limit</dt><dd>${cyc.notional.toLocaleString("en-US", { maximumFractionDigits: 2 })} ${esc(cyc.path[0])}</dd>
          <dt>verdict</dt><dd>${statusPill(cyc.actionable, cyc.actionable ? "actionable" : cyc.reject_reason)}</dd>
        </dl>` : `<p>No negative cycle clears the ${t.min_profit_after_fees_pct}% post-fee threshold for this seed.</p>`)}
      ${card("span-5", "How the loop opens", "", `<p style="margin-top:0;color:var(--muted)">Every quote is built from a reference USD price plus a ${"0.025%"} half-spread. The ETH/SOL cross market is then dislocated from its USD-implied value in both directions — a stale cross is the only thing that makes Π(rates) &gt; 1 around three legs. No two-leg round trip is profitable, so Bellman–Ford has to find the triangle.</p>
        <p class="footnote">Size is capped so no leg consumes more than <code>max_orderbook_depth_consumption_pct</code> of top-of-book, converted back into the cycle's start currency.</p>`)}
      ${card("span-12", "Quoted rate vs USD-implied cross", "bps · row = from, column = to", `<div id="tri-heat"></div>
        <p class="footnote">Edges sit about −2.5 bps (the spread). The lime cell is the cross quoted rich — the leg the cycle buys through.</p>`)}
    </div>`;
  mount("tri-heat", heatmap({ labels: t.currencies, values: t.deviation_bps, colorFor: dev, format: (v) => (v > 0 ? "+" : "") + v.toFixed(1), cell: 86 }));
}

function renderFunding(s) {
  const f = s.funding;
  const active = f.rows.filter((r) => r.status !== "skipped");
  $("#view-funding").innerHTML = `
    <div class="grid">
      ${card("span-12", "Funding book", `fees: spot ${f.fees.spot_pct}% + perp ${f.fees.futures_pct}% · 8h interval`, `<div class="table-scroll"><table>
        <thead><tr><th>symbol</th><th class="num">rate / 8h</th><th class="num">annualized</th><th class="num">net edge / period</th><th class="num">breakeven</th><th class="num">size</th><th>verdict</th></tr></thead><tbody>
        ${f.rows.map((r) => `<tr><td class="mono">${esc(r.symbol)}</td>
          <td class="num ${r.rate_pct_8h > 0 ? "pos" : "neg"}">${pct(r.rate_pct_8h, 4)}</td>
          <td class="num">${pct(r.annualized_pct, 1)}</td>
          <td class="num ${r.net_edge_pct > 0 ? "pos" : r.net_edge_pct != null ? "neg" : ""}">${r.net_edge_pct == null ? "—" : pct(r.net_edge_pct, 4)}</td>
          <td class="num">${r.breakeven_h == null ? "—" : r.breakeven_h.toFixed(1) + " h"}</td>
          <td class="num">${r.notional_usd == null ? "—" : usd(r.notional_usd)}</td>
          <td>${r.status === "actionable" ? statusPill(true, "actionable") : r.status === "skipped" ? statusPill(null, "skipped") : statusPill(false, "rejected")}
            ${r.reason ? `<div class="reason">${esc(r.reason)}</div>` : ""}</td></tr>`).join("")}
        </tbody></table></div>`)}
      ${card("span-7", "Hours to break even", `warning threshold ${f.breakeven_warning_h} h`, `<div id="fr-be"></div>`)}
      ${card("span-5", "Delta-neutral mechanics", "", `<dl class="stat-list">
        <dt>position</dt><dd>long spot + short perp</dd>
        <dt>leverage band</dt><dd>${f.leverage[0]}× – ${f.leverage[1]}×</dd>
        <dt>entry (example)</dt><dd>${f.liquidation_example.entry.toFixed(2)}</dd>
        <dt>short liquidation @ ${f.liquidation_example.leverage}×</dt><dd class="neg">${f.liquidation_example.liq_price.toFixed(2)}</dd>
        <dt>negative funding</dt><dd>skipped at scan()</dd></dl>
        <p class="footnote">Liquidation from <code>liquidation_price_short()</code>; breakeven from <code>breakeven_hours()</code> — both in <code>strategies/funding_rate.py</code>.</p>`)}
    </div>`;
  const capped = (v) => Math.min(v, f.breakeven_warning_h * 4);
  mount("fr-be", hBars({
    rows: active.map((r) => ({ label: r.symbol, value: capped(r.breakeven_h), color: r.breakeven_h > f.breakeven_warning_h ? C.neg : r.status === "actionable" ? C.lime : C.warn,
      note: `${r.breakeven_h.toFixed(1)} h${r.breakeven_h > capped(r.breakeven_h) ? " (bar capped)" : ""}` })),
    threshold: f.breakeven_warning_h,
    format: (v) => `${v.toFixed(0)}h`,
  }));
}

function renderRisk(s) {
  const r = s.risk;
  const corrColor = (v, i, j) => (i === j ? "rgba(143,134,255,0.18)" : Math.abs(v) >= r.correlation.threshold ? `rgba(255,93,122,${0.25 + Math.abs(v) * 0.5})` : `rgba(143,134,255,${0.06 + Math.abs(v) * 0.45})`);
  const grid = (conf) => r.var_grid.filter((g) => g.confidence === conf);
  const horizons = [...new Set(r.var_grid.map((g) => g.horizon_days))];
  const confs = [...new Set(r.var_grid.map((g) => g.confidence))];
  const varDaily = r.var_grid.find((g) => g.confidence === r.var.confidence && g.horizon_days === 1) || r.var;

  $("#view-risk").innerHTML = `
    <div class="grid">
      ${kpi("span-3 accent", `VaR ${(r.var.confidence * 100).toFixed(0)}%`, usd(r.var.var_usd), `${r.var.horizon_days}-day horizon`)}
      ${kpi("span-3", "CVaR / expected shortfall", usd(r.var.cvar_usd), "mean of the tail beyond VaR")}
      ${kpi("span-3", "Correlation cuts", String(r.correlation.findings.length), `pairs with |ρ| ≥ ${r.correlation.threshold}`)}
      ${kpi("span-3", "Stress breaches", String(r.stress.filter((x) => x.breach).length), `vs ${r.max_drawdown_pct}% max drawdown`)}

      ${card("span-7", "Daily P&L distribution", `${r.pnl_history.length} synthetic days · candidate book`, `<div id="rk-hist"></div>`)}
      ${card("span-5", "VaR / CVaR surface", "monte_carlo_var()", `<div class="table-scroll"><table>
        <thead><tr><th>confidence</th>${horizons.map((h) => `<th class="num">${h}d VaR</th><th class="num">${h}d CVaR</th>`).join("")}</tr></thead><tbody>
        ${confs.map((c) => `<tr><td class="mono">${(c * 100).toFixed(1)}%</td>${grid(c).map((g) => `<td class="num">${usd(g.var_usd)}</td><td class="num" style="color:var(--muted)">${usd(g.cvar_usd)}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div><p class="footnote">Cholesky-correlated paths over the sample covariance; horizon scaling assumes i.i.d. daily returns.</p>`)}

      ${card("span-7", "Strategy correlation", `N_eff = ${r.correlation.effective_independent_bets}`, `<div id="rk-corr" style="overflow-x:auto"></div>`)}
      ${card("span-5", "Candidate book", usd(r.gross_exposure_usd), `<div class="table-scroll"><table>
        <thead><tr><th>strategy</th><th>instrument</th><th class="num">notional</th></tr></thead><tbody>
        ${r.positions.map((p) => `<tr><td>${esc(label(p.strategy))}</td><td class="mono">${esc(p.symbol)}</td><td class="num">${usd(p.notional_usd)}</td></tr>`).join("")}
        </tbody></table></div>
        ${r.correlation.findings.map((x) => `<p class="footnote"><span class="neg">ρ=${x.correlation.toFixed(2)}</span> ${esc(x.a)} ↔ ${esc(x.b)} → weight × ${x.weight_multiplier}</p>`).join("")}`)}
      ${card("span-12", "Stress scenarios", "run_stress_tests()", `<div id="rk-stress"></div>`)}
    </div>`;
  mount("rk-hist", histogram({ values: r.pnl_history, markers: [{ x: -varDaily.var_usd, color: C.lime, label: `VaR ${usd(varDaily.var_usd)}` }], format: (v) => usd(v) }));
  const short = { stat_arb: "STAT", triangular: "TRI", funding_rate: "FUND", dex_cex_flashloan: "FLASH" };
  mount("rk-corr", heatmap({ labels: r.correlation.labels, colLabels: r.positions.map((p) => short[p.strategy] || p.strategy), values: r.correlation.matrix, colorFor: corrColor, format: (v) => v.toFixed(2), cell: 70 }));
  mount("rk-stress", stressChart(r));
}

function renderConfig(s) {
  const c = s.config;
  $("#view-config").innerHTML = `
    <div class="grid">
      ${card("span-6", "Live-trading gate", "config.is_live_trading_authorized()", `
        <dl class="stat-list">
          <dt>execution.mode</dt><dd>${esc(c.mode)}</dd>
          <dt>${esc(c.live_trading_env)}</dt><dd>${c.live_trading_authorized ? "matches" : "not set / no match"}</dd>
          <dt>result</dt><dd>${c.live_trading_authorized ? statusPill(false, "ARMED — real orders possible") : statusPill(true, "locked · paper only")}</dd>
        </dl>
        <p class="footnote">Both conditions are required. Editing YAML alone can never enable real orders, and the live adapters independently refuse to construct without the env var.</p>`)}
      ${card("span-6", "Venues", esc(c.source), `<div class="table-scroll"><table>
        <thead><tr><th>id</th><th>enabled</th><th>sandbox</th></tr></thead><tbody>
        ${c.exchanges.map((e) => `<tr><td class="mono">${esc(e.id)}</td><td>${statusPill(e.enabled ? true : null, e.enabled ? "on" : "off")}</td><td>${e.sandbox ? "sandbox" : "mainnet"}</td></tr>`).join("")}
        </tbody></table></div>`)}
      ${card("span-12", "Snapshot provenance", "", `<dl class="stat-list">
        <dt>seed</dt><dd>${s.meta.seed}</dd><dt>compute time</dt><dd>${s.meta.compute_ms} ms</dd>
        <dt>config source</dt><dd>${esc(c.source)}</dd><dt>data</dt><dd>${esc(s.meta.note)}</dd></dl>`)}
    </div>`;
}

// ---------------------------------------------------------------- shell

function renderAll() {
  renderOverview(snapshot);
  renderStatArb(snapshot);
  renderTriangular(snapshot);
  renderFunding(snapshot);
  renderRisk(snapshot);
  renderConfig(snapshot);
  const gate = $("#gate-badge");
  const live = snapshot.config.live_trading_authorized;
  gate.className = `gate ${live ? "armed" : "locked"}`;
  $("#gate-text").textContent = live ? "LIVE GATE ARMED" : `PAPER · gate locked`;
  $("#meta-line").textContent = `${snapshot.meta.compute_ms} ms`;
}

function route() {
  const view = (location.hash || "#overview").slice(1);
  const key = VIEWS[view] ? view : "overview";
  for (const id of Object.keys(VIEWS)) document.getElementById(`view-${id}`).hidden = id !== key;
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === key));
  $("#view-eyebrow").textContent = VIEWS[key][0];
  $("#view-title").textContent = VIEWS[key][1];
  tooltip.hide();
}

async function load(seed) {
  const err = $("#error");
  err.hidden = true;
  $("#meta-line").textContent = "computing…";
  try {
    const q = seed == null || seed === "" ? "" : `?seed=${encodeURIComponent(seed)}`;
    const res = await fetch(`/api/snapshot${q}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.statusText);
    snapshot = body;
    $("#seed-input").value = body.meta.seed;
    renderAll();
    route();
  } catch (e) {
    err.textContent = `Snapshot failed: ${e.message}`;
    err.hidden = false;
    $("#meta-line").textContent = "";
  }
}

window.addEventListener("hashchange", route);
$("#seed-form").addEventListener("submit", (e) => { e.preventDefault(); load($("#seed-input").value); });
load(new URLSearchParams(location.search).get("seed"));
