// Aura chat client.
//
// One rule shapes this file: the user is never shown the defense. A blocked
// call surfaces as a step that didn't complete, exactly as it would in a real
// product. The interception is visible on /dashboard, and nowhere else.

const $ = (id) => document.getElementById(id);
const thread = $("thread");
const scroll = $("scroll");

const { animate, stagger } = window.Motion || {};
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const canAnimate = Boolean(animate) && !reduced;

let trace = null;
let busy = false;

const TOOL = {
  list_files:        { icon: "i-folder", verb: "Listing your documents" },
  read_file:         { icon: "i-file",   verb: "Reading" },
  get_unread_emails: { icon: "i-mail",   verb: "Checking your inbox" },
  search_emails:     { icon: "i-search", verb: "Searching your mail" },
  search_web:        { icon: "i-globe",  verb: "Searching the web" },
  fetch_url:         { icon: "i-globe",  verb: "Opening" },
  send_email:        { icon: "i-mail",   verb: "Sending an email" },
  send_to_external:  { icon: "i-upload", verb: "Uploading data" },
};

function label(tool, args) {
  const t = TOOL[tool] || { verb: tool };
  if (tool === "read_file") return `${t.verb} ${args.filename || ""}`.trim();
  if (tool === "fetch_url") return `${t.verb} ${args.url || ""}`.trim();
  if (tool === "search_web" || tool === "search_emails") return `${t.verb} for “${args.query || ""}”`;
  if (tool === "send_email") return `${t.verb} to ${args.to || ""}`;
  if (tool === "send_to_external") return `${t.verb} to ${args.url || ""}`;
  return t.verb;
}

function icon(id, cls = "", size = 17) {
  return `<svg class="ic ${cls}" width="${size}" height="${size}" viewBox="0 0 20 20" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true"><use href="#${id}"/></svg>`;
}

function enter(el, y = 8) {
  if (!canAnimate) return;
  animate(el, { opacity: [0, 1], transform: [`translateY(${y}px)`, "translateY(0)"] },
    { duration: 0.24, easing: [0.2, 0.7, 0.3, 1] });
}

function stick() {
  scroll.scrollTop = scroll.scrollHeight;
}

function clearHero() {
  $("hero")?.remove();
  $("starters")?.remove();
}

// ------------------------------------------------------------- rendering
function addBubble(role, text) {
  const turn = document.createElement("div");
  turn.className = `turn ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  turn.appendChild(bubble);
  thread.appendChild(turn);
  enter(turn);
  stick();
}

function newTrace() {
  trace = document.createElement("div");
  trace.className = "trace";
  thread.appendChild(trace);
  return trace;
}

function addStep(text) {
  if (!trace) newTrace();
  const step = document.createElement("div");
  step.className = "step";
  step.innerHTML = `${icon("i-loader", "spin")}<span class="what"></span>`;
  step.querySelector(".what").textContent = text;
  trace.appendChild(step);
  enter(step, 6);
  stick();
  return step;
}

function finishStep(step, ok) {
  step.className = `step ${ok ? "done" : "failed"}`;
  step.querySelector(".ic").outerHTML = icon(ok ? "i-check" : "i-x");
  if (!ok) {
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = "didn't complete";
    step.appendChild(note);
  }
}

// --------------------------------------------------------------- sending
async function send(text) {
  if (busy || !text.trim()) return;
  clearHero();
  addBubble("me", text);
  $("input").value = "";
  $("input").style.height = "auto";
  busy = true;
  $("btn-send").disabled = true;
  window.auraOrb?.busy(true);
  newTrace();
  await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}

// ---------------------------------------------------------------- stream
const source = new EventSource("/events");

function setBackend(d) {
  const b = $("pill-backend");
  b.textContent = d.backend === "llm" ? d.model : "scripted backend";
  b.className = d.backend === "llm" ? "pill ok" : "pill warn";
  if (d.defense_on === undefined) return;
  const p = $("pill-defense");
  p.innerHTML = `<span class="led"></span>${d.defense_on ? "Sentinel-Z active" : "Sentinel-Z off"}`;
  p.className = d.defense_on ? "pill ok" : "pill danger";
  $("tgl-defense").checked = d.defense_on;
}

source.addEventListener("backend", (e) => setBackend(JSON.parse(e.data)));

source.addEventListener("tool_call", (e) => {
  const d = JSON.parse(e.data);
  const step = addStep(label(d.tool, d.args));
  step.dataset.tool = d.tool;
  step.dataset.pending = "1";
});

source.addEventListener("tool_result", (e) => {
  const d = JSON.parse(e.data);
  if (!trace) return;
  const open = [...trace.querySelectorAll('.step[data-pending="1"]')];
  const step = open.find((s) => s.dataset.tool === d.tool) || open[open.length - 1];
  if (!step) return;
  delete step.dataset.pending;
  finishStep(step, !d.refused);
});

source.addEventListener("stepup_request", (e) => {
  const d = JSON.parse(e.data);
  clearHero();
  const box = document.createElement("div");
  box.className = "confirm";
  box.innerHTML = `<div class="t">Confirm this action</div>
    <div class="d">Aura wants to run <strong></strong> before continuing.</div>
    <div class="row">
      <button class="btn primary" data-ok="1">Allow</button>
      <button class="btn" data-ok="0">Not now</button>
    </div>`;
  box.querySelector("strong").textContent = d.tool;
  box.querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      box.remove();
      await fetch("/api/stepup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: b.dataset.ok === "1" }),
      });
    };
  });
  thread.appendChild(box);
  enter(box);
  stick();
});

source.addEventListener("assistant", (e) => {
  clearHero();
  addBubble("ai", JSON.parse(e.data).text);
  trace = null;
});

source.addEventListener("done", () => {
  busy = false;
  $("btn-send").disabled = false;
  window.auraOrb?.busy(false);
  trace = null;
});

source.addEventListener("upload", (e) => {
  const d = JSON.parse(e.data);
  clearHero();
  if (!trace) newTrace();
  const step = addStep(d.kind === "email" ? `Added email: ${d.name}` : `Attached ${d.name}`);
  finishStep(step, true);
  trace = null;
});

source.addEventListener("reset", () => location.reload());

// ---------------------------------------------------------------- wiring
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send($("input").value);
});

$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send($("input").value);
  }
});
$("input").addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 180) + "px";
});

document.querySelectorAll(".example").forEach((card) => {
  card.onclick = () => send(card.dataset.q);
});

function wireUpload(input, kind) {
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const content = await file.text();
    await fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, content, kind }),
    });
    input.value = "";
  };
}
wireUpload($("file-doc"), "document");
wireUpload($("file-mail"), "email");
$("btn-doc").onclick = () => $("file-doc").click();
$("btn-mail").onclick = () => $("file-mail").click();

$("tgl-defense").onchange = async (e) => {
  await fetch("/api/defense", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: e.target.checked }),
  });
};

$("btn-reset").onclick = () => fetch("/api/reset", { method: "POST" });

// ------------------------------------------------------------------ boot
const hour = new Date().getHours();
$("greeting").textContent =
  hour < 12 ? "Good morning." : hour < 18 ? "Good afternoon." : "Good evening.";

fetch("/api/status").then((r) => r.json()).then(setBackend);

if (canAnimate) {
  animate(
    ".hero, .composer, .eyebrow, .example",
    { opacity: [0, 1], transform: ["translateY(10px)", "translateY(0)"] },
    { duration: 0.4, delay: stagger(0.045), easing: [0.2, 0.7, 0.3, 1] }
  );
}
