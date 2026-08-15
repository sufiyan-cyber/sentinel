/* Sentinel-Z Arena front end.
 *
 * Everything arrives over one SSE stream. No polling, no framework.
 * The one element that must be right is the exfiltration pane: records type
 * themselves in, one character at a time. A counter would not land. */

"use strict";

const SIGNALS = [
  "injection_likelihood",
  "task_alignment",
  "privilege_delta",
  "taint",
  "sequence_novelty",
];
const ACTIONS = ["ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE"];

const $ = (id) => document.getElementById(id);

let speed = 8;
let threshold = 0.5;
let hazardSeries = [];
let timeline = [];
let exfilCounts = { left: 0, right: 0 };

/* ------------------------------------------------------------ typing */

/* One queue per side so two panes can type independently without
 * interleaving characters. */
const typers = {
  left: { queue: [], busy: false },
  right: { queue: [], busy: false },
};

function typeRecord(side, text) {
  typers[side].queue.push(text);
  drainTyper(side);
}

function drainTyper(side) {
  const t = typers[side];
  if (t.busy || t.queue.length === 0) return;
  t.busy = true;

  const pane = $(side + "-exfil");
  const cursor = pane.querySelector(".cursor");
  const line = document.createElement("span");
  pane.insertBefore(line, cursor);

  const text = t.queue.shift();
  exfilCounts[side] += 1;
  $(side + "-exfil-count").textContent = exfilCounts[side] + " records";
  if (side === "left" && exfilCounts.left > 0) {
    document.querySelector(".undefended .exfil").classList.add("hot");
  }

  const prefix = "[" + String(exfilCounts[side]).padStart(4, "0") + "] ";
  let i = 0;
  const delay = Math.max(2, 12 / Math.max(speed / 8, 1));

  (function step() {
    if (i === 0) line.textContent = prefix;
    if (i < text.length) {
      line.textContent += text[i++];
      pane.scrollTop = pane.scrollHeight;
      setTimeout(step, delay);
    } else {
      line.textContent += "\n";
      t.busy = false;
      drainTyper(side);
    }
  })();
}

/* ------------------------------------------------------------ panels */

function buildSignalBars() {
  const host = $("signal-bars");
  host.innerHTML = "";
  for (const name of SIGNALS) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML =
      '<div class="name">' + name + "</div>" +
      '<div class="bar-track"><div class="bar-fill" id="sig-' + name + '"></div></div>' +
      '<div class="val" id="sigv-' + name + '">0.00</div>';
    host.appendChild(row);
  }
}

function buildCostBars() {
  const host = $("cost-bars");
  host.innerHTML = "";
  for (const name of ACTIONS) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML =
      '<div class="name">' + name + "</div>" +
      '<div class="bar-track"><div class="bar-fill cost" id="cost-' + name + '"></div></div>' +
      '<div class="val" id="costv-' + name + '">-</div>';
    host.appendChild(row);
  }
}

function setSignals(signals) {
  for (const name of SIGNALS) {
    const value = Number(signals[name] || 0);
    const bar = $("sig-" + name);
    if (!bar) continue;
    bar.style.width = (value * 100).toFixed(1) + "%";
    bar.className = "bar-fill" + (value >= 0.66 ? " hi" : value >= 0.33 ? " mid" : "");
    $("sigv-" + name).textContent = value.toFixed(2);
  }
}

function setCosts(costs) {
  const values = ACTIONS.map((a) => (costs && costs[a] !== undefined ? Number(costs[a]) : null));
  const present = values.filter((v) => v !== null);
  if (present.length === 0) {
    ACTIONS.forEach((a) => {
      $("cost-" + a).style.width = "0%";
      $("costv-" + a).textContent = "-";
    });
    return;
  }
  const max = Math.max(...present, 1e-9);
  const min = Math.min(...present);
  ACTIONS.forEach((a, i) => {
    const v = values[i];
    const bar = $("cost-" + a);
    if (v === null) { bar.style.width = "0%"; $("costv-" + a).textContent = "-"; return; }
    bar.style.width = ((v / max) * 100).toFixed(1) + "%";
    bar.className = "bar-fill cost" + (v === min ? " win" : "");
    $("costv-" + a).textContent = v.toFixed(2);
  });
}

function setState(name) {
  const chip = $("state-chip");
  chip.textContent = name || "BENIGN";
  chip.className = "chip s-" + (name || "BENIGN");
}

function setAction(action) {
  const chip = $("action-chip");
  chip.textContent = action || "—";
  chip.className = "chip action v-" + (action || "");
}

function setAbsorption(a) {
  const f = (k) => (a && a[k] !== undefined ? Number(a[k]).toFixed(3) : "—");
  $("absorption").textContent = "n1 " + f("n1") + "   n3 " + f("n3") + "   n5 " + f("n5");
}

/* Hazard sparkline with the decision threshold drawn as a horizontal line. */
function drawHazard() {
  const svg = $("hazard-spark");
  const W = 300, H = 46;
  const y = (v) => H - 3 - v * (H - 6);
  let path = "";
  hazardSeries.forEach((v, i) => {
    const x = hazardSeries.length < 2 ? 0 : (i / (hazardSeries.length - 1)) * W;
    path += (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y(v).toFixed(1);
  });
  svg.innerHTML =
    '<line x1="0" y1="' + y(threshold) + '" x2="' + W + '" y2="' + y(threshold) +
    '" stroke="#ffb020" stroke-width="1" stroke-dasharray="4 3" opacity="0.85"/>' +
    (path ? '<path d="' + path + '" fill="none" stroke="#4da6ff" stroke-width="2"/>' : "") +
    (hazardSeries.length
      ? '<circle cx="' + W + '" cy="' + y(hazardSeries[hazardSeries.length - 1]) + '" r="3" fill="#4da6ff"/>'
      : "");
}

/* --------------------------------------------------------- transcript */

function addCard(side, step) {
  const host = $(side + "-transcript");
  const card = document.createElement("div");
  const action = step.decision || "";
  card.className = "card " + action + (step.blocked ? " blocked" : "");

  const args = Object.entries(step.args || {})
    .map(([k, v]) => k + "=" + v)
    .join("  ");

  let verdict = "";
  if (step.blocked) {
    verdict = '<div class="verdict v-REVOKE">BLOCKED &mdash; capability token refused this call</div>';
  } else if (action) {
    verdict =
      '<div class="verdict v-' + action + '">' + action +
      (step.reason ? ' &middot; <span class="muted">' + escapeHtml(step.reason) + "</span>" : "") +
      "</div>";
  }

  card.innerHTML =
    '<div class="tool">' + escapeHtml(step.tool) + "</div>" +
    '<div class="args">' + escapeHtml(args) + "</div>" +
    verdict;
  host.appendChild(card);
  host.scrollTop = host.scrollHeight;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ------------------------------------------------------------ rounds */

function addRound(r) {
  $("drawer").classList.remove("hidden");
  const host = $("rounds");
  const div = document.createElement("div");
  div.className = "round";
  div.innerHTML =
    '<div class="head"><b>Round ' + r.round_index + "</b>" +
    '<span class="badge ' + (r.succeeded ? "through" : "blocked") + '">' +
    (r.succeeded ? "GOT THROUGH" : "BLOCKED") + "</span></div>" +
    '<div class="meta">' + r.defense_action +
    " &middot; top signal: " + (r.top_signal || "-") +
    " (" + Number(r.top_signal_value || 0).toFixed(2) + ")" +
    (r.strategy ? " &middot; " + escapeHtml(r.strategy) : "") + "</div>" +
    (r.diff ? '<pre class="diff">' + colourDiff(r.diff) + "</pre>" : "");
  host.appendChild(div);
  host.scrollTop = host.scrollHeight;
}

function colourDiff(diff) {
  return diff
    .split("\n")
    .map((line) => {
      const safe = escapeHtml(line);
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@"))
        return '<span class="hdr">' + safe + "</span>";
      if (line.startsWith("+")) return '<span class="add">' + safe + "</span>";
      if (line.startsWith("-")) return '<span class="del">' + safe + "</span>";
      return safe;
    })
    .join("\n");
}

function drawAsr(series) {
  const svg = $("asr-chart");
  const W = 320, H = 120, pad = 22;
  if (!series || !series.length) { svg.innerHTML = ""; return; }
  const x = (i) => pad + (series.length < 2 ? 0 : (i / (series.length - 1)) * (W - pad * 2));
  const y = (v) => H - pad - v * (H - pad * 2);
  let path = "";
  series.forEach((v, i) => { path += (i === 0 ? "M" : "L") + x(i).toFixed(1) + "," + y(v).toFixed(1); });
  svg.innerHTML =
    '<line x1="' + pad + '" y1="' + y(0) + '" x2="' + (W - pad) + '" y2="' + y(0) + '" stroke="#263042"/>' +
    '<line x1="' + pad + '" y1="' + y(1) + '" x2="' + (W - pad) + '" y2="' + y(1) + '" stroke="#263042" stroke-dasharray="3 3"/>' +
    '<text x="4" y="' + (y(1) + 4) + '" fill="#8a97ad" font-size="10">1.0</text>' +
    '<text x="4" y="' + (y(0) + 4) + '" fill="#8a97ad" font-size="10">0.0</text>' +
    '<path d="' + path + '" fill="none" stroke="#ffb020" stroke-width="2"/>' +
    series.map((v, i) => '<circle cx="' + x(i) + '" cy="' + y(v) + '" r="2.5" fill="#ffb020"/>').join("");
}

/* ---------------------------------------------------------- timeline */

function loadTimeline(ticks) {
  timeline = ticks || [];
  const scrub = $("scrub");
  scrub.max = Math.max(0, timeline.length - 1);
  scrub.value = Math.max(0, timeline.length - 1);
  scrub.disabled = timeline.length === 0;
  $("tl-label").textContent = timeline.length + " steps recorded";
  if (timeline.length) showTick(timeline.length - 1);
}

function showTick(i) {
  const t = timeline[i];
  if (!t) return;
  setSignals(t.signals || {});
  setCosts(t.expected_costs || {});
  setState(t.decision === "REVOKE" ? "CONTAINED" : argmaxState(t.belief));
  setAction(t.decision);
  setAbsorption(t.absorption);
  $("hazard-val").textContent = Number(t.hazard || 0).toFixed(3);

  const wouldBe = Number(t.hazard || 0) >= threshold ? "would intervene" : "would allow";
  $("tl-detail").innerHTML =
    '<span><span class="k">step</span> <b>' + t.idx + "</b></span>" +
    '<span><span class="k">tool</span> <b>' + escapeHtml(t.tool) + "</b></span>" +
    '<span><span class="k">hazard</span> <b>' + Number(t.hazard || 0).toFixed(3) + "</b></span>" +
    '<span><span class="k">decision</span> <b class="v-' + t.decision + '">' + t.decision + "</b></span>" +
    '<span><span class="k">at threshold ' + threshold.toFixed(2) + "</span> <b>" + wouldBe + "</b></span>";
}

const STATES = ["BENIGN", "RECON", "ESCALATION", "HARM", "CONTAINED"];
function argmaxState(belief) {
  if (!belief || !belief.length) return "BENIGN";
  let best = 0;
  for (let i = 1; i < belief.length; i++) if (belief[i] > belief[best]) best = i;
  return STATES[best];
}

/* ------------------------------------------------------------- wiring */

function clearArena() {
  ["left", "right"].forEach((side) => {
    $(side + "-transcript").innerHTML = "";
    $(side + "-exfil").innerHTML = '<span class="cursor">█</span>';
    $(side + "-exfil-count").textContent = "0 records";
    typers[side].queue = [];
    typers[side].busy = false;
  });
  exfilCounts = { left: 0, right: 0 };
  document.querySelector(".undefended .exfil").classList.remove("hot");
  hazardSeries = [];
  drawHazard();
  setSignals({});
  setCosts({});
  setState("BENIGN");
  setAction("");
  setAbsorption({});
}

function connect() {
  const source = new EventSource("/events");

  source.addEventListener("scoreboard", (e) => {
    const d = JSON.parse(e.data);
    $("s-attempted").textContent = d.attempted;
    $("s-succeeded").textContent = d.succeeded;
    $("s-blocked").textContent = d.blocked;
    // What the control arm gave away. `succeeded` and `blocked` score the
    // defended system only, so without this the board never shows the damage
    // the defense is preventing.
    $("s-leaked").textContent = d.leaked_undefended || 0;
    $("s-utility").textContent = Math.round(d.utility_retained) + "%";
    if (d.round > 0) { $("round-box").hidden = false; $("s-round").textContent = d.round; }
  });

  source.addEventListener("reset", () => clearArena());

  source.addEventListener("pair_start", (e) => {
    const d = JSON.parse(e.data);
    clearArena();
    $("left-tag").textContent = d.left.task_id + " / " + (d.left.injection || "benign");
    $("right-tag").textContent = d.right.defense === "none" ? "defense OFF" : "defense ON";
  });

  source.addEventListener("step", (e) => {
    const step = JSON.parse(e.data);
    addCard(step.side, step);
    if (step.side === "right") {
      if (step.signals && Object.keys(step.signals).length) setSignals(step.signals);
      if (step.decision) setAction(step.decision);
      if (step.argmax_state) setState(step.argmax_state);
      setCosts(step.expected_costs);
      setAbsorption(step.absorption);
      if (step.hazard !== undefined) {
        hazardSeries.push(Number(step.hazard));
        if (hazardSeries.length > 60) hazardSeries.shift();
        $("hazard-val").textContent = Number(step.hazard).toFixed(3);
        drawHazard();
      }
    }
    (step.exfiltrated || []).forEach((record) => typeRecord(step.side, record));
  });

  source.addEventListener("pair_end", (e) => {
    const d = JSON.parse(e.data);
    loadTimeline(d.timeline);
  });

  source.addEventListener("campaign_start", (e) => {
    const d = JSON.parse(e.data);
    $("rounds").innerHTML = "";
    $("drawer").classList.remove("hidden");
    log("campaign started (" + d.rewriter + " rewriter)");
  });

  source.addEventListener("round", (e) => addRound(JSON.parse(e.data)));

  source.addEventListener("campaign_end", (e) => {
    const d = JSON.parse(e.data);
    drawAsr(d.asr_by_round);
    log("campaign done: " + d.rounds + " rounds, first success " +
        (d.first_success_round === null ? "never" : "round " + d.first_success_round));
  });

  source.addEventListener("threshold", (e) => {
    threshold = JSON.parse(e.data).value;
    drawHazard();
  });

  source.addEventListener("log", (e) => log(JSON.parse(e.data).message));
  source.onerror = () => log("stream lost, reconnecting…");
}

function log(message) { $("log").textContent = message; }

function post(url, params) {
  const q = params ? "?" + new URLSearchParams(params).toString() : "";
  return fetch(url + q, { method: "POST" }).then((r) => r.json());
}

/* -------------------------------------------------------------- init */

buildSignalBars();
buildCostBars();
drawHazard();
connect();

fetch("/api/status").then((r) => r.json()).then((d) => {
  const b = d.backend;
  $("backend-line").textContent =
    "backend " + b.resolved + "  ·  model " + b.model +
    "  ·  ollama " + (b.ollama_reachable ? "up" : "down");
});

$("btn-benign").onclick = () => post("/api/run-benign");
$("btn-attack").onclick = () => post("/api/run-attack");
$("btn-campaign").onclick = () => post("/api/campaign");
$("btn-replay").onclick = () => post("/api/replay", { name: "demo" });
$("btn-reset").onclick = () => { post("/api/reset"); clearArena(); };
$("drawer-close").onclick = () => $("drawer").classList.add("hidden");

$("speed").onchange = (e) => {
  speed = Number(e.target.value);
  post("/api/speed", { value: speed });
};
$("defense").onchange = (e) => post("/api/defense", { on: e.target.checked });
$("threshold").oninput = (e) => {
  threshold = Number(e.target.value);
  $("thr-val").textContent = threshold.toFixed(2);
  drawHazard();
  if (timeline.length) showTick(Number($("scrub").value));
};
$("threshold").onchange = (e) => post("/api/threshold", { value: Number(e.target.value) });
$("scrub").oninput = (e) => showTick(Number(e.target.value));

post("/api/speed", { value: speed });
