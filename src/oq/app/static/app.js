"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }) : "—";
};
const pct = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? (n * 100).toFixed(2) + "%" : "—";
};
const cls = (n) => (Number(n) > 0 ? "pos" : Number(n) < 0 ? "neg" : "muted");

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) {
    $("login").classList.remove("hidden");
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

function cell(row, text, className = "") {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  row.appendChild(td);
  return td;
}

function renderStatus(s) {
  $("mode").textContent = s.mode + (s.live_trading ? " · live" : "");
  const dot = $("dot");
  dot.className = "dot " + (s.kill_switch || s.halted ? "halted" : "live");
  const snap = s.snapshot || {};
  $("equity").textContent = fmt(snap.equity ?? s.equity);
  $("daypnl").textContent = pct(snap.day_pnl);
  $("daypnl").className = cls(snap.day_pnl);
  $("dd").textContent = pct(snap.drawdown);
  $("heat").textContent = pct(snap.heat);
  $("npos").textContent = (s.positions || []).length;

  const tbody = $("positions").querySelector("tbody");
  tbody.innerHTML = "";
  for (const p of s.positions || []) {
    const tr = document.createElement("tr");
    cell(tr, p.symbol);
    cell(tr, p.book, "muted");
    cell(tr, p.qty && Number(p.qty) < 0 ? "short" : "long", "muted");
    cell(tr, fmt(p.qty, 4));
    cell(tr, fmt(p.avg_px, 2));
    cell(tr, p.stop_px ? fmt(p.stop_px, 2) : "—", "muted");
    cell(tr, fmt(p.unrealized, 2), cls(p.unrealized));
    cell(tr, p.open_risk ? "risk " + fmt(p.open_risk, 0) : "—", "muted");
    tbody.appendChild(tr);
  }
  if (!(s.positions || []).length) {
    const tr = document.createElement("tr");
    const td = cell(tr, "no open positions", "muted");
    td.colSpan = 8;
    tbody.appendChild(tr);
  }
}

function renderChart(points) {
  const svg = $("chart");
  const w = 800, h = 240, pad = 8;
  const values = points.map((p) => Number(Array.isArray(p) ? p[1] : p.equity));
  if (values.length < 2) {
    svg.innerHTML = '<text x="20" y="40" fill="#8b97ad" font-size="12">no equity history yet</text>';
    $("chart-hint").textContent = "—";
    return;
  }
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const x = (i) => pad + (i / (values.length - 1)) * (w - pad * 2);
  const y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);

  let d = "";
  values.forEach((v, i) => { d += (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " "; });
  const area = d + `L${x(values.length - 1)} ${h - pad} L${x(0)} ${h - pad} Z`;
  const color = values[values.length - 1] >= values[0] ? "#3ddc97" : "#ff6b6b";

  svg.innerHTML = `
    <defs>
      <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${color}" stop-opacity="0.28"/>
        <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#g)"></path>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"></path>
    <line x1="${pad}" y1="${y(max)}" x2="${w - pad}" y2="${y(max)}" stroke="#232b3b" stroke-dasharray="3 4"></line>
    <line x1="${pad}" y1="${y(min)}" x2="${w - pad}" y2="${y(min)}" stroke="#232b3b" stroke-dasharray="3 4"></line>
  `;
  $("chart-hint").textContent = `${fmt(min)} → ${fmt(max)} · last ${fmt(values[values.length - 1])}`;
}

async function renderTrades() {
  const { trades } = await api("/trades?limit=40");
  const tbody = $("trades").querySelector("tbody");
  tbody.innerHTML = "";
  for (const t of trades) {
    const tr = document.createElement("tr");
    cell(tr, new Date(t.exit_ms).toLocaleString(), "muted");
    cell(tr, t.symbol);
    cell(tr, t.book, "muted");
    cell(tr, t.pos_side, "muted");
    cell(tr, fmt(t.qty, 4));
    cell(tr, fmt(t.entry_px, 2));
    cell(tr, fmt(t.exit_px, 2));
    cell(tr, fmt(t.pnl, 2), cls(t.pnl));
    cell(tr, t.r_multiple ? fmt(t.r_multiple, 2) + "R" : "—", cls(t.r_multiple));
    cell(tr, t.reason, "muted");
    tbody.appendChild(tr);
  }
  if (!trades.length) {
    const tr = document.createElement("tr");
    const td = cell(tr, "no trades yet", "muted");
    td.colSpan = 10;
    tbody.appendChild(tr);
  }
}

async function renderDecisions() {
  const { decisions } = await api("/decisions?limit=40");
  const tbody = $("decisions").querySelector("tbody");
  tbody.innerHTML = "";
  for (const d of decisions) {
    const tr = document.createElement("tr");
    cell(tr, new Date(d.ts_ms).toLocaleTimeString(), "muted");
    cell(tr, d.strategy_id);
    cell(tr, d.action, "muted");
    cell(tr, (d.symbols || []).join(", "));
    const verdict = document.createElement("td");
    verdict.innerHTML = `<span class="pill ${d.approved ? "ok" : "no"}">${d.approved ? "approved" : "rejected"}</span>`;
    tr.appendChild(verdict);
    const why = d.gates && d.gates.length && !d.approved ? d.gates[d.gates.length - 1].reason : "";
    const td = cell(tr, why || "—", "muted wrap");
    td.title = why;
    tbody.appendChild(tr);
  }
  if (!decisions.length) {
    const tr = document.createElement("tr");
    const td = cell(tr, "no decisions recorded yet", "muted");
    td.colSpan = 6;
    tbody.appendChild(tr);
  }
}

async function refresh() {
  try {
    const [status, equity] = await Promise.all([api("/status"), api("/equity?limit=600")]);
    renderStatus(status);
    renderChart((equity.points || []).map((p) => [p.ts_ms, p.equity]));
    await Promise.all([renderTrades(), renderDecisions()]);
  } catch (err) {
    if (err.message !== "unauthorized") console.error(err);
  }
}

async function control(path) {
  try {
    await api("/control/" + path, { method: "POST" });
    await refresh();
  } catch (err) {
    alert(err.message);
  }
}

$("btn-pause").onclick = () => control("pause");
$("btn-resume").onclick = () => control("resume");
$("btn-kill").onclick = () => { if (confirm("Engage kill switch and flatten?")) control("flatten").then(() => control("kill")); };
$("btn-flatten").onclick = () => { if (confirm("Flatten all positions?")) control("flatten"); };
$("btn-cycle").onclick = () => control("cycle");

$("btn-backtest").onclick = async () => {
  const btn = $("btn-backtest");
  btn.disabled = true;
  $("bt-status").textContent = "running…";
  try {
    const body = JSON.stringify({ days: Number($("bt-days").value), source: $("bt-source").value });
    const res = await api("/backtest", { method: "POST", body });
    $("bt-report").textContent = res.report;
    $("bt-status").textContent = `${res.trades} trades · ${res.decisions} decisions`;
  } catch (err) {
    $("bt-status").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
};

$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/auth/login", { method: "POST", body: JSON.stringify({ password: $("password").value }) });
    $("login").classList.add("hidden");
    $("password").value = "";
    await refresh();
  } catch (err) {
    $("login-error").textContent = "invalid password";
  }
};

refresh();
setInterval(refresh, 15000);
