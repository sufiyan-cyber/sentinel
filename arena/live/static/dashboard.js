// Sentinel-Z live dashboard. Renders exactly what the gateway returned.

const $ = (id) => document.getElementById(id);
const { animate } = window.Motion || {};
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const canAnimate = Boolean(animate) && !reduced;

const SIGNALS = ["injection_likelihood", "task_alignment", "privilege_delta", "taint", "sequence_novelty"];
const ACTIONS = ["ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE"];

let calls = 0, allowed = 0, acted = 0;
const hazards = [];

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clamp01 = (v) => Math.max(0, Math.min(1, Number(v) || 0));

function initTheme() {
  const saved = localStorage.getItem("aura_theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);
  updateThemeIcon(saved);
}

function updateThemeIcon(theme) {
  const btn = $("btn-theme");
  if (!btn) return;
  btn.innerHTML = `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><use href="#${theme === 'dark' ? 'i-sun' : 'i-moon'}"/></svg>`;
}

const themeBtn = $("btn-theme");
if (themeBtn) {
  themeBtn.onclick = () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("aura_theme", next);
    updateThemeIcon(next);
  };
}

function meters(host, rows, digits = 2, opts = {}) {
  host.innerHTML = rows
    .map(([name, value, mod = ""]) =>
      `<div class="meter ${mod}">
         <span class="n">${esc(name)}</span>
         <span class="bar"><span style="width:${clamp01(opts.scale ? value / opts.scale : value) * 100}%"></span></span>
         <span class="v">${Number(value).toFixed(digits)}</span>
       </div>`)
    .join("");
}

function drawSpark() {
  const svg = $("spark");
  if (!svg || hazards.length < 2) { if (svg) svg.innerHTML = ""; return; }
  const n = hazards.length;
  const pts = hazards.map((v, i) => `${(i / (n - 1)) * 300},${52 - clamp01(v) * 48}`).join(" ");
  svg.innerHTML =
    `<defs><linearGradient id="hg" x1="0" x2="1">
       <stop offset="0" stop-color="#a855f7"/><stop offset="1" stop-color="#d946ef"/>
     </linearGradient></defs>
     <polyline points="${pts}" fill="none" stroke="url(#hg)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`;
}

function topSignals(signals, n = 3) {
  const ranked = SIGNALS.map((s) => [s, Number(signals?.[s] ?? 0)])
    .filter(([, v]) => v > 0.05)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n);
  if (!ranked.length) return "no signal above threshold";
  return ranked.map(([s, v]) => `${s} ${v.toFixed(2)}`).join(" · ");
}

function render(d) {
  calls += 1;
  if (d.action === "ALLOW") allowed += 1; else acted += 1;
  $("k-calls").textContent = calls;
  $("k-allowed").textContent = allowed;
  $("k-acted").textContent = acted;

  meters($("signals"), SIGNALS.map((s) => [s, Number(d.signals?.[s] ?? 0)]));

  const action = $("d-action");
  action.textContent = d.action;
  action.className = `tag ${d.action}`;
  $("d-why").textContent = d.reason || `driven by ${topSignals(d.signals)}`;

  const state = $("k-state");
  state.textContent = d.state || "BENIGN";
  state.className = `tag ${d.state || "BENIGN"}`;
  $("pill-policy").textContent = `policy ${d.policy || "—"}`;

  const hazard = Number(d.hazard ?? 0);
  $("d-hazard").textContent = hazard.toFixed(3);
  hazards.push(hazard);
  if (hazards.length > 60) hazards.shift();
  drawSpark();

  const costs = d.expected_costs || {};
  const values = ACTIONS.map((a) => Number(costs[a] ?? 0));
  const best = Math.min(...values);
  const max = Math.max(1e-9, ...values);
  meters(
    $("costs"),
    ACTIONS.map((a, i) => [a, values[i], values[i] === best ? "win" : "dim"]),
    2,
    { scale: max }
  );

  const abs = d.absorption || {};
  meters($("absorption"), [["n = 1", Number(abs.n1 ?? 0)], ["n = 3", Number(abs.n3 ?? 0)], ["n = 5", Number(abs.n5 ?? 0)]], 3);

  const row = document.createElement("tr");
  if (d.action === "REVOKE") row.className = "hit";
  row.innerHTML = `<td class="mono">${calls}</td>
    <td class="mono">${esc(d.tool)}</td>
    <td><span class="tag ${d.action}">${d.action}</span></td>
    <td class="why">${esc(d.state || "")}</td>
    <td class="mono">${hazard.toFixed(3)}</td>`;
  $("log").prepend(row);
  if (canAnimate) animate(row, { opacity: [0, 1] }, { duration: 0.2 });
}

// ---------------------------------------------------------------- stream
const source = new EventSource("/events");

function setBackend(d) {
  const b = $("pill-backend");
  if (d.backend === "nvidia") {
    b.textContent = `NVIDIA (Llama-3.2-11B)`;
    b.className = "pill ok";
  } else if (d.backend === "gemini") {
    b.textContent = `Gemini (${d.model || "gemini-3.6-flash"})`;
    b.className = "pill ok";
  } else if (d.backend === "ollama") {
    b.textContent = `Ollama (${d.model || "llama3.1:8b"})`;
    b.className = "pill ok";
  } else {
    b.textContent = "Scripted (Offline)";
    b.className = "pill warn";
  }

  if (d.defense_on === false) {
    $("pill-policy").textContent = "defense off";
    $("pill-policy").className = "pill danger";
  }
}

source.addEventListener("backend", (e) => setBackend(JSON.parse(e.data)));
source.addEventListener("decision", (e) => render(JSON.parse(e.data)));
source.addEventListener("reset", () => location.reload());

fetch("/api/status")
  .then((r) => r.json())
  .then((d) => {
    (d.decisions || []).forEach(render);
    setBackend(d);
  });

// ------------------------------------------------------------ exfil pane
let seen = -1;
async function pollWire() {
  try {
    const d = await (await fetch("/api/exfil")).json();
    const records = d.records || [];
    if (records.length === seen) return;
    seen = records.length;
    $("k-leaked").textContent = seen;
    $("wire").innerHTML = records.length
      ? records.map((line, i) => `[${String(i + 1).padStart(4, "0")}] ${esc(line)}`).join("\n")
      : '<span class="none">nothing has left the boundary</span>';
    $("wire").scrollTop = $("wire").scrollHeight;
  } catch (_) {
    /* collection server may not be running */
  }
}
setInterval(pollWire, 1500);
pollWire();

initTheme();
