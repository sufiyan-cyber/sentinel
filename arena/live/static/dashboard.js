// Sentinel-Z defense console. Renders exactly what the gateway returned —
// nothing here decides anything.

const $ = (id) => document.getElementById(id);
const SIGNALS = [
  ["injection_likelihood", "Injection in what it read"],
  ["task_alignment", "Drift from the user's task"],
  ["privilege_delta", "Reach beyond the task"],
  ["taint", "Acting on untrusted text"],
  ["sequence_novelty", "Unusual tool sequence"],
];
const ACTIONS = ["ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE"];
const STATES = ["BENIGN", "RECON", "ESCALATION", "HARM", "CONTAINED"];
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clamp01 = (v) => Math.max(0, Math.min(1, Number(v) || 0));
const rupees = (paise) => {
  const n = Math.abs(Number(paise) || 0) / 100;
  return n.toLocaleString("en-IN", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
};
const post = (url, body = {}) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json().catch(() => ({})));

const S = {
  calls: [],          // {tool, args, decision, refused}
  selected: null,     // index being inspected, or null for live
  pin: null,          // the call that revoked the token: live view stays on it
  wallet: { balance_paise: 500000, opening_paise: 500000 },
  mandate: null,
  latencies: [],
  leakedBase: null,
  leaked: 0,
  defenseOn: true,
  tampered: false,
  scenarios: [],
  twin: null,
};

// ------------------------------------------------------------- helpers
function toast(title, text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<b>${esc(title)}</b><span>${esc(text)}</span>`;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 9000);
}

function countTo(el, from, to, ms = 900) {
  if (reduced || from === to) { el.textContent = rupees(to); return; }
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / ms);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = rupees(from + (to - from) * eased);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function argHint(tool, args = {}) {
  const v = args.sku || args.query || args.filename || args.url || args.to || args.product || args.task || "";
  const amount = args.amount_inr != null ? ` ₹${Number(args.amount_inr).toLocaleString("en-IN")}` : "";
  return `${v}${amount}`.trim().slice(0, 60);
}

function meters(host, rows) {
  host.innerHTML = rows
    .map(([name, value, cls = "", shown]) =>
      `<div class="meter ${cls}"><span class="n" title="${esc(name)}">${esc(name)}</span>
        <span class="b"><span style="width:${clamp01(value) * 100}%"></span></span>
        <span class="v">${shown ?? Number(value).toFixed(2)}</span></div>`)
    .join("");
}

// --------------------------------------------------------------- panels
function renderTrack(belief, top) {
  const b = belief && belief.length === 5 ? belief : [1, 0, 0, 0, 0];
  $("track").innerHTML = STATES.map((s, i) =>
    `<div class="node ${s === top ? "top" : ""}" data-s="${s}">
       <div class="col"><div class="fill" style="height:${clamp01(b[i]) * 100}%"></div><span class="pct">${Math.round(clamp01(b[i]) * 100)}%</span></div>
       <span class="name">${s}</span></div>`).join("");
  const tag = $("state-tag");
  tag.textContent = top || "BENIGN";
  tag.className = `tag ${top || "BENIGN"}`;
}

function drawSpark(upTo) {
  const values = S.calls.slice(0, upTo + 1).map((c) => c.decision?.hazard).filter((v) => v != null);
  const svg = $("spark");
  if (values.length < 2) { svg.innerHTML = ""; return; }
  const n = values.length;
  const pts = values.map((v, i) => [(i / (n - 1)) * 300, 56 - clamp01(v) * 52]);
  const line = pts.map((p) => p.join(",")).join(" ");
  const last = pts[n - 1];
  const hot = values[n - 1] >= 0.5;
  const color = hot ? "var(--bad)" : "var(--info)";
  svg.innerHTML =
    `<line x1="0" x2="300" y1="30" y2="30" stroke="var(--line-2)" stroke-dasharray="3 5" vector-effect="non-scaling-stroke"/>
     <polygon points="0,60 ${line} 300,60" fill="${color}" opacity="0.10"/>
     <polyline points="${line}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
     <circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="${color}"/>`;
}

function topReason(d) {
  if (d.reason) return d.reason;
  const ranked = SIGNALS.map(([key, name]) => [name, Number(d.signals?.[key] ?? 0)])
    .filter(([, v]) => v >= 0.5).sort((a, b) => b[1] - a[1]).slice(0, 2);
  if (d.action === "ALLOW") return ranked.length ? `Allowed. Highest signal: ${ranked[0][0].toLowerCase()}.` : "Allowed. No signal is raised.";
  return ranked.length ? `Driven by: ${ranked.map(([n]) => n.toLowerCase()).join(", ")}.` : "";
}

function showCall(index) {
  const call = S.calls[index];
  const d = call?.decision;
  $("sig-step").textContent = call ? `call ${index + 1} · ${call.tool}` : "";
  if (!d) {
    meters($("signals"), SIGNALS.map(([, name]) => [name, 0, "dim", "—"]));
    $("d-action").textContent = call ? "NOT REVIEWED" : "—";
    $("d-action").className = "tag";
    $("d-why").textContent = call ? "The defense is off, so this call ran without a decision." : "Waiting for a tool call.";
    $("costs").innerHTML = "";
    return;
  }
  meters($("signals"), SIGNALS.map(([key, name]) => {
    const v = Number(d.signals?.[key] ?? 0);
    return [name, v, v >= 0.8 ? "hot" : v >= 0.5 ? "warm" : ""];
  }));
  $("d-action").textContent = d.action;
  $("d-action").className = `tag ${d.action}`;
  $("d-why").textContent = topReason(d);
  $("d-hazard").textContent = Number(d.hazard ?? 0).toFixed(2);
  drawSpark(index);
  renderTrack(d.belief, d.state);
  const abs = d.absorption || {};
  const p3 = abs.n3 ?? abs["3"];
  $("absorb-text").textContent = p3 != null ? `Chance of harm within 3 steps if left alone: ${(Number(p3) * 100).toFixed(0)}%.` : "";

  const costs = d.expected_costs || {};
  const values = ACTIONS.map((a) => Number(costs[a] ?? 0));
  if (values.some((v) => v > 0)) {
    const best = Math.min(...values), max = Math.max(1e-9, ...values);
    meters($("costs"), ACTIONS.map((a, i) => [a, values[i] / max, values[i] === best ? "win" : "dim", values[i].toFixed(2)]));
  } else {
    $("costs").innerHTML = `<p class="empty">Token already destroyed — no policy evaluation needed.</p>`;
  }
}

function renderTimeline() {
  const host = $("timeline");
  if (!S.calls.length) { host.innerHTML = `<li class="empty">No tool call yet.</li>`; return; }
  host.innerHTML = S.calls.map((c, i) => {
    const action = c.decision?.action || "NONE";
    const label = c.decision ? action.replace("_", " ") : "NO CHECK";
    const sel = (S.selected ?? liveIndex()) === i ? "sel" : "";
    return `<li><button type="button" class="tl ${sel} ${c.refused ? "refused" : ""}" data-a="${action}" data-i="${i}">
      <div class="t" title="${esc(c.tool)} ${esc(argHint(c.tool, c.args))}">${i + 1}. ${esc(c.tool)}</div>
      <div class="a"><span>${c.decision ? label : c.refused ? "REFUSED" : label}</span><span>${esc(argHint(c.tool, c.args))}</span></div></button></li>`;
  }).join("");
  host.querySelectorAll(".tl").forEach((b) => (b.onclick = () => select(Number(b.dataset.i))));
  if (S.selected == null) host.scrollLeft = host.scrollWidth;
}

function liveIndex() { return S.pin ?? S.calls.length - 1; }

function select(index) {
  S.selected = index === liveIndex() ? null : index;
  $("btn-live").classList.toggle("hidden", S.selected == null);
  renderTimeline();
  showCall(index);
}
$("btn-live").onclick = () => select(liveIndex());

function updateKpis() {
  const reviewed = S.calls.filter((c) => c.decision);
  $("k-calls").textContent = reviewed.length;
  $("k-acted").textContent = reviewed.filter((c) => !["ALLOW", "MONITOR"].includes(c.decision.action)).length;
  const sorted = [...S.latencies].sort((a, b) => a - b);
  $("k-latency").textContent = sorted.length ? sorted[Math.floor(sorted.length / 2)].toFixed(1) : "—";
}

// ---------------------------------------------------------------- token
function setToken(state, status, note) {
  $("token-card").dataset.state = state;
  const tag = $("token-status");
  tag.textContent = status;
  tag.className = `tag ${state === "alive" ? "ok" : state === "revoked" ? "bad" : state === "limited" ? "warn" : ""}`;
  if (note) $("token-note").textContent = note;
  $("token-scopes").textContent = state === "revoked" ? "Every tool refused" : state === "off" ? "Not enforced" : "All tools in scope";
}

function freshToken() {
  if (!S.defenseOn) return setToken("off", "DEFENSE OFF", "With the defense off, nothing stands between the agent and its tools.");
  setToken("alive", "ALIVE", "The agent can only act through this token. Revoking it stops every tool at once.");
}

// --------------------------------------------------------------- wallet
function setWallet(w, animate = true) {
  const from = S.wallet.balance_paise, to = w.balance_paise;
  S.wallet = w;
  const el = $("w-balance");
  if (animate) countTo(el, from, to); else el.textContent = rupees(to);
  $("w-bar").style.width = `${clamp01(to / w.opening_paise) * 100}%`;
  $("w-bar").style.background = to / w.opening_paise < 0.25 ? "var(--bad)" : "var(--ok)";
  el.parentElement.classList.toggle("drop", to / w.opening_paise < 0.25);
  const last = (w.ledger || [])[w.ledger?.length - 1];
  $("w-foot").textContent = last ? `${last.amount_paise > 0 ? "Added" : "Last payment"}: ₹${rupees(last.amount_paise)} · ${last.title}` : `Opening balance ₹${rupees(w.opening_paise)}`;
}

function setMandate(m) {
  S.mandate = m;
  const tag = $("mandate-tag"), limit = $("w-limit");
  if (!m) {
    tag.textContent = "no purchase yet"; tag.className = "tag";
    limit.classList.add("hidden");
    $("token-limit").textContent = "No spend limit set";
    return;
  }
  tag.textContent = m.enforced === false ? `asked ₹${rupees(m.stated_paise)} · not enforced` : `limit ₹${rupees(m.max_paise)}`;
  tag.className = `tag ${m.enforced === false ? "bad" : "ok"}`;
  $("token-limit").textContent = m.enforced === false
    ? "No spend limit — defense is off"
    : `Payments up to ₹${rupees(m.max_paise)} (user said “${m.text || "default cap"}”)`;
  limit.classList.remove("hidden");
  limit.style.left = `${clamp01(1 - m.max_paise / S.wallet.opening_paise) * 100}%`;
  limit.title = `Spend limit ₹${rupees(m.max_paise)}`;
}

function showSaved(amountPaise, text, lost = false) {
  const box = $("saved");
  box.classList.remove("hidden");
  box.classList.toggle("lost", lost);
  $("saved-n").textContent = `₹${rupees(amountPaise)}`;
  box.querySelector(".saved-t").textContent = text;
}

// ---------------------------------------------------------------- chain
function renderChain(records, bad = null, edited = null) {
  const host = $("chain");
  if (!records.length) { host.innerHTML = `<span class="empty">Each decision is hashed into the one before it.</span>`; return; }
  host.innerHTML = records.map((r, i) => {
    const action = r.action || r.decision?.action || r.payment?.status || r.kind;
    const hash = String(r.hash || "").slice(0, 8);
    const cls = `${bad != null && i >= bad ? "broken" : ""} ${edited === i ? "edited" : ""}`;
    return `<span class="blk ${cls}" data-a="${esc(action)}" title="${esc(r.tool || "")} · ${esc(r.hash || "")}">
      <b>#${i + 1} ${esc(String(action).replace("_", " "))}<small>${bad != null && i >= bad ? "hash mismatch" : hash}</small></b></span>`;
  }).join("");
}

async function refreshChain() {
  const d = await (await fetch("/api/evidence")).json();
  S.chain = d.records || [];
  S.tampered = false;
  $("btn-tamper").textContent = "Tamper with the log";
  renderChain(S.chain);
  const tag = $("chain-tag");
  tag.textContent = S.chain.length ? `${S.chain.length} records · verified` : "empty";
  tag.className = "tag ok";
  $("chain-note").textContent = "SHA-256, each record commits to all history before it.";
}

$("btn-tamper").onclick = async () => {
  if (S.tampered) return refreshChain();
  const d = await post("/api/evidence/tamper-demo");
  if (!d.ok) return toast("Nothing to tamper with yet", "Run a scenario first so there are decisions in the log.");
  S.tampered = true;
  renderChain(S.chain, d.bad_index, d.tampered_index);
  $("chain-tag").textContent = "TAMPERING DETECTED";
  $("chain-tag").className = "tag bad";
  $("chain-note").textContent = `In a copy of the log, record #${d.tampered_index + 1} was changed from ${d.before} to ${d.after}. Its hash no longer matches, so it and everything after it fail verification. The real log is untouched.`;
  $("btn-tamper").textContent = "Restore the log view";
  $("chain").scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
};

// ------------------------------------------------------------ narration
function narrate(eyebrow, title, text) {
  $("nar-eyebrow").textContent = eyebrow;
  $("nar-title").textContent = title;
  $("nar-text").textContent = text;
}

function softReset() {
  S.calls = []; S.selected = null; S.pin = null; S.latencies = []; S.leakedBase = null; S.leaked = 0;
  $("btn-live").classList.add("hidden");
  renderTimeline(); updateKpis(); showCall(-1);
  renderTrack(null, "BENIGN"); $("absorb-text").textContent = "";
  $("spark").innerHTML = ""; $("d-hazard").textContent = "0.00";
  $("poison").classList.add("hidden"); $("saved").classList.add("hidden");
  $("k-leaked").textContent = "0"; $("k-leaked").classList.remove("bad");
  setMandate(null); freshToken();
  S.chain = []; renderChain([]); $("chain-tag").textContent = "empty";
  setFrame("/shop");
  pollWire(true);
}

// ----------------------------------------------------- agent's browser
let frameUrl = "/shop";
function setFrame(url) {
  frameUrl = url;
  const reveal = $("tgl-reveal").checked && url.startsWith("/shop/p/");
  const src = reveal ? `${url}?reveal=1` : url;
  const frame = $("browser-frame");
  if (frame.getAttribute("src") !== src) frame.setAttribute("src", src);
  $("browser-url").textContent = `sparrowmart.local${decodeURIComponent(url)}`;
}
$("tgl-reveal").onchange = () => setFrame(frameUrl);

// ------------------------------------------------------------ scenarios
function renderScenarios() {
  const groups = {};
  S.scenarios.forEach((s) => (groups[s.group || "Scenarios"] ||= []).push(s));
  let n = 0;
  $("scenarios").innerHTML = Object.entries(groups).map(([group, list]) =>
    `<div class="group-name">${esc(group)}</div>` + list.map((s) => {
      n += 1;
      return `<div class="scn" data-id="${esc(s.id)}">
        <div class="scn-title"><span class="n">${n}</span>${esc(s.title)}</div>
        <div class="scn-prompt">“${esc(s.prompt)}”${s.needs === "browser" ? " — opens a real browser window" : ""}</div>
        <div class="scn-actions">
          <button class="btn" data-run="${esc(s.id)}">Run</button>
          ${s.twin ? `<button class="btn" data-twin="${esc(s.id)}">Side by side</button>` : ""}
        </div></div>`;
    }).join("")).join("");
  $("scenarios").querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => run(b.dataset.run, false)));
  $("scenarios").querySelectorAll("[data-twin]").forEach((b) => (b.onclick = () => run(b.dataset.twin, true)));
}

async function run(id, twin) {
  const body = twin ? { id } : { id, defense: $("tgl-defense").checked };
  const d = await post(twin ? "/api/demo/twin" : "/api/demo/run", body);
  if (!d.ok) toast("Could not start", d.error === "busy" ? "A run is still in progress." : (d.error || "Unknown error"), "bad");
}

function markActive(id) {
  document.querySelectorAll(".scn").forEach((el) => el.classList.toggle("active", el.dataset.id === id));
}

// ------------------------------------------------------------- controls
$("tgl-defense").onchange = (e) => post("/api/defense", { on: e.target.checked });
document.querySelectorAll("[data-profile]").forEach((b) => (b.onclick = () => post("/api/profile", { profile: b.dataset.profile })));
$("btn-reset").onclick = () => post("/api/reset");
$("btn-twin-close").onclick = () => showView("single");

function setBackend(d) {
  const chip = $("chip-backend");
  const names = { gemini: "Gemini", nvidia: "NVIDIA", ollama: "Ollama", scripted: "Scripted agent" };
  if (d.backend_ok === false) {
    chip.textContent = `${names[d.backend] || d.backend} unavailable (${d.backend_error})`;
    chip.className = "chip bad";
  } else {
    chip.textContent = d.backend === "scripted" ? "Scripted agent · no model" : `${names[d.backend] || d.backend} · ${d.model || ""}`;
    chip.className = `chip ${d.backend === "scripted" ? "warn" : "ok"}`;
  }
  $("chip-profile").classList.toggle("hidden", d.agent_profile !== "susceptible");
  document.querySelectorAll("[data-profile]").forEach((b) => b.classList.toggle("on", b.dataset.profile === d.agent_profile));
  const pay = $("chip-pay");
  pay.textContent = d.payments_provider === "cashfree" ? "Cashfree · SANDBOX" : d.payments_provider === "razorpay" ? "Razorpay · TEST MODE" : "Payments · mock";
  pay.className = `chip ${d.payments_error ? "bad" : d.payments_mode === "test" ? "ok" : ""}`;
  if (d.payments_error) pay.textContent = "Payments · mock (gateway not configured)";
  pay.title = d.payments_error || "";
  if (d.defense_on !== undefined && d.defense_on !== S.defenseOn) {
    S.defenseOn = d.defense_on;
    if ($("token-card").dataset.state !== "revoked") freshToken();
  }
  $("tgl-defense").checked = S.defenseOn;
  $("lbl-defense").textContent = S.defenseOn ? "Defense on" : "Defense off";
}

function showView(which) {
  $("view-single").classList.toggle("hidden", which !== "single");
  $("view-twin").classList.toggle("hidden", which !== "twin");
}

// --------------------------------------------------------------- stream
const source = new EventSource("/events");
const on = (name, fn) => source.addEventListener(name, (e) => fn(JSON.parse(e.data)));

on("backend", setBackend);
on("reset", () => { softReset(); showView("single"); narrate("Ready", "Session reset.", "Pick a scenario, or use the chat."); markActive(""); setWallet({ ...S.wallet, balance_paise: S.wallet.opening_paise, ledger: [] }, false); });
on("scenario_start", (d) => {
  showView("single"); softReset(); markActive(d.id);
  narrate(S.defenseOn ? "Running · defense on" : "Running · defense OFF", d.title, d.narration || "");
});
on("scenario_end", (d) => {
  const o = d.outcome || {};
  const eyebrow = o.revoked ? "Stopped" : "Finished";
  $("nar-eyebrow").textContent = eyebrow;
  refreshChain();
});

on("tool_call", (d) => {
  S.calls.push({ tool: d.tool, args: d.args || {}, decision: null, refused: false });
  if (S.selected == null) { renderTimeline(); if (S.pin == null) showCall(S.calls.length - 1); }
});

on("decision", (d) => {
  let call = [...S.calls].reverse().find((c) => c.tool === d.tool && !c.decision);
  if (!call) { call = { tool: d.tool, args: {}, decision: null, refused: false }; S.calls.push(call); }
  call.decision = d;
  if (d.latency_ms != null) S.latencies.push(Number(d.latency_ms));
  const standing = String(d.reason || "").includes("already destroyed");
  if (d.action === "REVOKE" && !standing) S.pin = S.calls.indexOf(call);
  updateKpis();
  renderTimeline();
  if (S.selected == null) showCall(liveIndex());
  if (d.action === "REVOKE" && !standing) {
    setToken("revoked", "REVOKED", "Destroyed at the broker. The agent may keep asking; every call is now refused underneath it.");
    toast("Capability token destroyed", `Revoked before ${d.tool} could run.`, "bad");
  }
});

on("tool_result", (d) => {
  const call = [...S.calls].reverse().find((c) => c.tool === d.tool && c.result === undefined);
  if (call) { call.result = d.result; call.refused = Boolean(d.refused); renderTimeline(); }
});

on("wallet", (d) => setWallet(d));
on("mandate", setMandate);
on("mandate_block", (d) => {
  setToken("limited", "OVER LIMIT", `This token covers payments up to ₹${rupees(d.max_paise)}. ₹${rupees(d.amount_paise)} needs the user's approval.`);
  showSaved(d.amount_paise, "held — it is over what the user authorised");
});
on("stepup_request", (d) => toast("Waiting for the user", d.kind === "mandate" ? "The approve / decline card is on the chat page." : `Step-up requested for ${d.tool}.`));
on("stepup_result", (d) => {
  if (d.kind !== "mandate") return;
  if (d.approved) { $("saved").classList.add("hidden"); setToken("alive", "ALIVE", "The user approved this one payment."); }
});
on("payment", (d) => {
  if (d.status === "blocked" && /revoked|destroyed|refused$/.test(d.reason || "")) {
    showSaved(d.amount_paise, "kept out of an attacker's hands");
  } else if (d.status === "blocked") {
    showSaved(d.amount_paise, "not charged — the user declined");
  } else if (d.status === "paid" && S.mandate && d.amount_paise > S.mandate.max_paise && S.mandate.enforced === false) {
    showSaved(d.amount_paise, `spent on “${d.title}” — nobody asked for it`, true);
  }
});
on("taint_source", (d) => {
  $("poison").classList.remove("hidden");
  $("poison-text").textContent = `“${d.excerpt}…”`;
});
on("shop_view", (d) => setFrame(d.url));
on("evidence", () => { if (!S.tampered) refreshChain(); });
on("alert_call", (d) => toast(d.dry_run ? "Owner alerted by phone (simulated)" : "Calling the owner", `“${d.message}”`, "ok"));
on("assistant", () => { if (S.calls.length && $("nar-eyebrow").textContent.startsWith("Running")) $("nar-eyebrow").textContent = "Finished"; });

// ----------------------------------------------------------- twin lanes
function laneEl(lane, role) { return document.querySelector(`#lane-${lane} [data-role="${role}"]`); }

function twinReset(d) {
  S.twin = { undefended: { balance: 500000, calls: [] }, defended: { balance: 500000, calls: [] } };
  for (const lane of ["undefended", "defended"]) {
    laneEl(lane, "balance").textContent = "5,000";
    laneEl(lane, "balance").parentElement.classList.remove("drop");
    laneEl(lane, "bar").style.width = "100%"; laneEl(lane, "bar").style.background = "var(--ok)";
    laneEl(lane, "calls").innerHTML = ""; laneEl(lane, "verdict").textContent = "";
    laneEl(lane, "status").textContent = "running"; laneEl(lane, "status").className = "tag";
  }
  $("twin-poison").classList.add("hidden");
  softReset();
  showView("twin"); markActive(d.id);
  narrate("Side by side", d.title, `${d.narration || ""} Same request, same page, two agents.`);
}

function twinCalls(lane) {
  laneEl(lane, "calls").innerHTML = S.twin[lane].calls.map((c) => {
    const action = c.action || (c.done ? "NO CHECK" : "…");
    const cls = c.action || (c.done ? "" : "");
    return `<li class="${c.refused ? "refused" : ""}"><span class="t">${esc(c.tool)} <span class="sub">${esc(argHint(c.tool, c.args))}</span></span>
      <span class="tag ${cls}">${c.refused && !c.action ? "REFUSED" : esc(action.replace("_", " "))}</span></li>`;
  }).join("");
}

on("twin_start", twinReset);
on("lane.tool_call", (d) => { S.twin?.[d.lane]?.calls.push({ tool: d.tool, args: d.args || {} }); twinCalls(d.lane); });
on("lane.decision", (d) => {
  const call = [...(S.twin?.[d.lane]?.calls || [])].reverse().find((c) => c.tool === d.tool && !c.action);
  if (call) call.action = d.action;
  twinCalls(d.lane);
  if (d.action === "REVOKE" && !String(d.reason || "").includes("already destroyed")) {
    laneEl(d.lane, "status").textContent = "TOKEN DESTROYED"; laneEl(d.lane, "status").className = "tag bad";
  }
});
on("lane.tool_result", (d) => {
  const call = [...(S.twin?.[d.lane]?.calls || [])].reverse().find((c) => c.tool === d.tool && !c.done);
  if (call) { call.done = true; call.refused = Boolean(d.refused); }
  twinCalls(d.lane);
});
on("lane.wallet", (d) => {
  const lane = S.twin?.[d.lane];
  if (!lane) return;
  countTo(laneEl(d.lane, "balance"), lane.balance, d.balance_paise, 1400);
  lane.balance = d.balance_paise;
  const ratio = d.balance_paise / d.opening_paise;
  laneEl(d.lane, "bar").style.width = `${clamp01(ratio) * 100}%`;
  laneEl(d.lane, "bar").style.background = ratio < 0.25 ? "var(--bad)" : "var(--ok)";
  laneEl(d.lane, "balance").parentElement.classList.toggle("drop", ratio < 0.25);
});
on("lane.mandate_block", (d) => { laneEl(d.lane, "status").textContent = "HELD FOR APPROVAL"; laneEl(d.lane, "status").className = "tag warn"; });
on("lane.taint_source", (d) => { $("twin-poison").classList.remove("hidden"); $("twin-poison-text").textContent = `“${d.excerpt}…”`; });
on("lane.alert_call", (d) => toast("Owner alerted by phone" + (d.dry_run ? " (simulated)" : ""), `“${d.message}”`, "ok"));
on("twin_end", (d) => {
  const o = d.outcome || {};
  const bad = o.undefended || {}, good = o.defended || {};
  const lost = bad.spent_paise || 0;
  const wanted = 55000;
  laneEl("undefended", "status").textContent = "finished";
  if (bad.egress_calls) laneEl("undefended", "verdict").textContent = "The document's contents were sent to the attacker. Nothing checked the call.";
  else if (lost > wanted) laneEl("undefended", "verdict").textContent = `₹${rupees(lost)} gone on a shirt nobody asked for. Nothing checked the call.`;
  else laneEl("undefended", "verdict").textContent = lost ? `Bought as asked for ₹${rupees(lost)}.` : "Nothing was bought.";
  if (bad.egress_calls || lost > wanted) { laneEl("undefended", "status").textContent = "COMPROMISED"; laneEl("undefended", "status").className = "tag bad"; }

  if (good.revoked) laneEl("defended", "verdict").textContent = good.spent_paise ? `Token destroyed. ₹${rupees(good.spent_paise)} spent before that.` : "Token destroyed before anything left. ₹0 lost, nothing leaked.";
  else if (laneEl("defended", "status").textContent === "HELD FOR APPROVAL") laneEl("defended", "verdict").textContent = "Payment held: it was over what the user authorised. ₹0 lost.";
  else laneEl("defended", "verdict").textContent = good.spent_paise ? `Bought as asked for ₹${rupees(good.spent_paise)}.` : "Finished with nothing blocked.";
  if (!good.revoked && laneEl("defended", "status").textContent === "running") { laneEl("defended", "status").textContent = "finished"; laneEl("defended", "status").className = "tag ok"; }
  $("nar-eyebrow").textContent = "Result";
  pollWire();
});

// ------------------------------------------------------------ exfil pane
async function pollWire(rebase = false) {
  try {
    const d = await (await fetch("/api/exfil")).json();
    const records = d.records || [];
    if (rebase || S.leakedBase == null || records.length < S.leakedBase) S.leakedBase = records.length;
    const fresh = records.slice(S.leakedBase);
    if (fresh.length === S.leaked && !rebase) return;
    S.leaked = fresh.length;
    $("k-leaked").textContent = S.leaked;
    $("k-leaked").classList.toggle("bad", S.leaked > 0);
    const wire = $("wire"), tag = $("wire-tag");
    wire.classList.toggle("hit", S.leaked > 0);
    wire.textContent = S.leaked
      ? fresh.map((line, i) => `[${String(i + 1).padStart(3, "0")}] ${line}`).join("\n")
      : d.reachable ? "Nothing has left the boundary." : "The attacker's server is not running.";
    tag.textContent = S.leaked ? `${S.leaked} records` : "nothing";
    tag.className = `tag ${S.leaked ? "bad" : "ok"}`;
    wire.scrollTop = wire.scrollHeight;
  } catch (_) { /* collection server may not be running */ }
}
setInterval(() => pollWire(), 1500);

// ---------------------------------------------------------------- theme
function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("aura_theme", theme); } catch (_) { /* private mode */ }
}
$("btn-theme").onclick = () => setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
try { setTheme(localStorage.getItem("aura_theme") || "dark"); } catch (_) { setTheme("dark"); }

// ----------------------------------------------------------------- boot
renderTrack(null, "BENIGN");
showCall(-1);
Promise.all([
  fetch("/api/status").then((r) => r.json()),
  fetch("/api/demo/scenarios").then((r) => r.json()),
]).then(([status, scn]) => {
  S.scenarios = scn.scenarios || [];
  renderScenarios();
  S.defenseOn = status.defense_on !== false;
  setBackend(status);
  freshToken();
  if (status.wallet) setWallet(status.wallet, false);
  (status.decisions || []).forEach((d) => {
    S.calls.push({ tool: d.tool, args: {}, decision: d, refused: false });
    if (d.latency_ms != null) S.latencies.push(Number(d.latency_ms));
  });
  if (S.calls.length) {
    const first = S.calls.findIndex((c) => c.decision.action === "REVOKE");
    if (first >= 0) S.pin = first;
    renderTimeline(); updateKpis(); showCall(liveIndex());
    if (first >= 0) setToken("revoked", "REVOKED", "Destroyed at the broker. Every call is refused underneath the agent.");
  }
  refreshChain();
  pollWire(true);
});
