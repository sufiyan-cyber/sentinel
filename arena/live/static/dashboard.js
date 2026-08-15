// Sentinel-Z live dashboard. Renders exactly what the gateway returned —
// no smoothing, no re-derivation. Every number here came off the wire.

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
  if (hazards.length < 2) { svg.innerHTML = ""; return; }
  const n = hazards.length;
  const pts = hazards.map((v, i) => `${(i / (n - 1)) * 300},${52 - clamp01(v) * 48}`).join(" ");
  svg.innerHTML =
    `<defs><linearGradient id="hg" x1="0" x2="1">
       <stop offset="0" stop-color="#a855f7"/><stop offset="1" stop-color="#d946ef"/>
     </linearGradient></defs>
     <polyline points="${pts}" fill="none" stroke="url(#hg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
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
  // The POMDP path leaves `reason` empty — `decide()` in sentinelz/policy/
  // decide.py defaults it, and gateway.py doesn't pass one. Rather than print
  // nothing on the row that matters most, fall back to the signals that
  // actually drove it, labelled so nobody mistakes it for the gateway's words.
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
  b.textContent = d.backend === "llm" ? d.model : "scripted backend";
  b.className = d.backend === "llm" ? "pill ok" : "pill warn";
  if (d.defense_on === false) {
    $("pill-policy").textContent = "defense off";
    $("pill-policy").className = "pill danger";
  }
}

source.addEventListener("backend", (e) => setBackend(JSON.parse(e.data)));
source.addEventListener("decision", (e) => render(JSON.parse(e.data)));
source.addEventListener("reset", () => location.reload());

// Opening the dashboard mid-session must not show an empty board: replay what
// the gateway has already decided, then let the stream take over.
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
    /* the collection server may not be running */
  }
}
setInterval(pollWire, 1500);
pollWire();
