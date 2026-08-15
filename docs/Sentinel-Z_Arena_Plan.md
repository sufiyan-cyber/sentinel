# Sentinel-Z Arena — Plan

**Software only. No hardware. No AWS bill. Built by a coding agent.**

The demo is a live adversarial arena: an autonomous attacker agent tries to hijack a tool-using AI agent and steal data, while your defense tries to stop it — side by side with an undefended control, with a live scoreboard, and the attacker *adapting* after each failure.

Paired with `Sentinel-Z_Arena_PRD.md`, which is the build spec for Claude Code.

---

## 1. Why this and not the dashboard

Your instinct is right that a nice website doesn't impress anyone. The reason isn't aesthetics — it's that a dashboard shows you *results*, and results are inert. What makes a room go quiet is watching something happen that shouldn't.

So the demo has three things that people have not seen together:

**Visible theft.** On the undefended side, an "attacker's server" pane fills with the actual stolen records, line by line, as the agent exfiltrates them. Not a red light saying BREACH — the payroll rows themselves, arriving. That's the moment people feel it.

**A live control.** Two identical agents, same model, same task, same poisoned input, running simultaneously. Left undefended, right protected. One gets owned, one gets contained. This is your ablation study performed as theatre — the numbers you need for the paper are the same numbers on screen.

**An attacker that adapts.** When the defense blocks it, the attacker LLM rewrites its injection and tries again. Round 1 blocked, round 2 blocked, round 5 blocked, round 9 gets through. The audience watches an AI trying to break your system in real time, with the success rate climbing on a chart. Nobody demos this. Papers report a table; you show the fight.

That third element is also where the research contribution genuinely sits, because the literature says so explicitly. Several defenses report near-elimination of attacks on AgentDojo — against *fixed* attacks. Robustness under an adaptive adversary is the open question.

---

## 2. What you build vs what you borrow

Borrow aggressively. You should be writing roughly 15% of this system.

| Layer | Use this | What it saves you |
|---|---|---|
| **Environment + attack corpus** | **AgentDojo** (`ethz-spylab/agentdojo`) | 97 realistic user tasks and 629 injection cases across banking, Slack, travel and workspace domains, with utility and security measured jointly. Four fully-built tool environments you don't have to invent, and canonical injection templates ("Ignore Previous Instructions", "System Message", "Important Messages", "Tool Knowledge"). This is the single biggest saving available to you. |
| **Red-team engine** | **PyRIT** (Microsoft) or **Promptfoo**, or Garak | Multi-turn adaptive orchestration already built. PyRIT has an XPIA orchestrator aimed exactly at cross-domain prompt injection. Wrap it; don't write an attacker from scratch. |
| **Injection detector baseline** | Meta **Prompt Guard 2**, or a DeBERTa prompt-injection classifier from HF | One of your signals, and simultaneously a baseline to beat. Free, small, runs on CPU. |
| **Per-call policy baseline** | **Progent** (`arxiv 2504.11703`) | Your strongest comparison. It enforces least-privilege at the tool-call level via a policy DSL, and integrates as a proxy — teams change their LLM and MCP endpoints to Progent's proxy rather than modifying agent code. Same integration pattern you'll use. |
| **Agent runtime** | LangGraph, or AgentDojo's own agent loop | Don't build an agent framework. |
| **Model** | **Ollama + Llama 3.1 8B** (or Qwen 7B) locally | Free, offline, no API key, no rate limit — and vulnerable enough that attacks land. |
| **Traces (optional)** | Langfuse or Arize Phoenix, both OSS | Only if you want a trace backend. Do not build observability — Langfuse, Phoenix and Laminar have that space thoroughly covered and building another one wins you nothing. |
| **Literature** | `ucsb-mlsec/Awesome-Agent-Security` | Curated list; your related-work section in one page. |

**What you actually write:** the five detection signals, the session-level drift model, the graduated policy, the capability-revocation enforcement, and the Arena UI. That's it. Everything else is glue.

---

## 3. The honest contribution

Four claims, in order of strength. Lead with the first.

**C1 — Session-level prediction, not per-call filtering.** Every defense in §2 decides on *this* call in isolation: is this tool call permitted, is this text an injection. Sentinel-Z models the *trajectory* — it estimates the probability that the current session ends in harm within the next k steps, and intervenes before the harmful call rather than at it. Report the advance-warning distribution in steps. That's a different question from what Progent, CaMeL or PromptArmor answer.

**C2 — Capability revocation, not call refusal.** When the others block, they refuse one call and the agent is free to try another route. Sentinel-Z revokes the session's capability token, so the whole class of action dies at once. Measure blast radius: how many sensitive records were reachable after the first block, with and without revocation. This preserves your original credential-layer novelty with zero cloud spend, because the capability broker is simulated.

**C3 — Evaluated under adaptive attack.** Report attack success rate as a function of attacker adaptation rounds, not a single static number. This is the axis the literature identifies as under-tested, and your arena produces it automatically.

**C4 — Graduated response with measured utility cost.** Five actions, not block/allow. Report task completion with the defense on versus off — the "cost of compliance" number that most papers omit.

---

## 4. The critical model-tier decision

**Use a local open-weights 8B model for the target agent, and say so plainly in the paper.**

The reason is in §1's research: modern frontier models already resist single-context AgentDojo injections, so with a frontier target your undefended baseline shows near-zero attack success and there is nothing for your defense to improve on. Your demo dies and your results table is empty.

Framing it honestly: *"We evaluate against an open-weights 8B-class model, representing the cost-constrained self-hosted deployments where dedicated runtime defenses matter most, and where base-model robustness cannot be assumed."* That's true, it's a real deployment class, and it makes your problem statement stronger rather than weaker.

Optional second row for the paper if you get any API budget: run the same suite against one frontier model and report that the undefended ASR is already low. That's a legitimate finding and it costs you a few dollars.

---

## 5. The five detection signals

These are yours to build and they're what the paper is about. Each is cheap.

| Signal | What it measures | How |
|---|---|---|
| **Injection likelihood** | Is this tool *result* trying to give instructions? | Prompt Guard 2 or a DeBERTa classifier over returned content |
| **Task alignment** | Does this call plausibly serve the user's stated task? | Cosine similarity between the call and the task embedding, plus "was this tool in the task's expected tool set" |
| **Privilege delta** | Is this call reaching beyond what the task needs? | Sensitivity tier of the target minus the max tier the task requires |
| **Taint** | Did untrusted content influence this call's arguments? | Propagate a taint flag from any tool result that failed the injection check, into any later call whose arguments overlap it |
| **Sequence novelty** | Has this agent done this before? | n-gram novelty of the tool-call sequence against benign runs |

**Taint is the one to get right.** It's what distinguishes "the agent read a poisoned document" from "the agent is now acting on it," and it's the signal that makes your advance warning possible. It's also simple: a boolean that spreads.

## 6. The drift model — keep it honest and simple

**Build a discrete hazard model, not a POMDP.**

For each step t in a session, with feature vector x_t built from the five signals plus their running maxima and slopes, fit a logistic model predicting whether a harmful action occurs within the next k steps:

```
P(harm within k steps | history up to t) = σ(w · x_t + b)
```

Train on labelled AgentDojo runs — you know the ground truth because you know which injection task was active and which tool call constitutes success. Report k = 1, 3, 5.

Why this rather than the POMDP from your earlier plan: it's about 20 lines, it's genuinely trained on real labelled data, every coefficient is printable and interpretable, and you can describe it in one sentence that is completely true. Call it what it is — *a discrete-time hazard model over session-level behavioural features* — and no examiner can ask you a question you can't answer.

If you later want the heavier model for a stronger paper, the upgrade path is intact: your five signals become the observation, and the hazard output becomes the belief. Nothing is wasted.

## 7. Graduated policy

| Action | Effect |
|---|---|
| `ALLOW` | pass through |
| `MONITOR` | pass, flag, raise sensitivity for later steps |
| `SCOPE_DOWN` | pass a restricted version — redact fields, cap row counts, narrow the path |
| `STEP_UP` | pause, require confirmation (in the demo, a click) |
| `REVOKE` | refuse, and revoke the session's capability token entirely |

`SCOPE_DOWN` is worth building because it's the most interesting cell in your results: it's where utility is preserved *and* the attack fails, and it's rarely reported.

The threshold on the hazard output comes from a cost ratio you state explicitly, and you show the frontier as the ratio varies. Never a bare constant.

---

## 8. The demo — 5 minutes

**Act 0 (20s).** Arena open, scoreboard at zero, both panes idle. "Same model, same task, same attack. Left has no defense. Right has ours."

**Act 1 — the theft (60s).** Run one benign task both sides; both succeed. Then inject. On the left, the agent reads the poisoned document, pivots, and **the attacker's server pane starts filling with payroll rows.** Say nothing while it streams. Let people watch data leave.

**Act 2 — containment (60s).** Right pane, same instant: taint flag lights, task-alignment collapses, hazard climbs, state moves to ESCALATION. Say *"nothing has been blocked yet"* while the number rises. Then the hazard crosses the line, the capability token is revoked, and the agent's next three attempts all fail against a dead token. Attacker's pane: empty. Scoreboard: 1 blocked.

**Act 3 — the arms race (120s).** Turn the attacker loose. It rewrites its injection and retries. Show the successive injection texts appearing in the red panel, diffed against the previous attempt so you can see it mutating. Blocked, blocked, blocked, partial, blocked. The ASR-vs-rounds chart draws itself live. *"This is an AI actively trying to defeat our defense. We're not claiming it never gets through — we're showing you exactly how often it does."*

That last sentence is the one that gets respect from anyone technical, because it's the opposite of what a student demo usually claims.

**Act 4 — the flight recorder (40s).** Drag the timeline scrubber back to step 4 of the successful attack. Show the five signals at that instant, the hazard value, the policy decision, and what the defense *would* have done at a different threshold. "We can rewind any attack and see exactly why the decision was made."

**Act 5 — the log (20s).** Hash-chained decision log. Verify passes. Change one byte, verify names the record.

---

## 9. What to record for the paper

The arena emits all of this automatically. Six tables, no manual work.

| # | Content |
|---|---|
| T1 | ASR and utility, defended vs undefended, per AgentDojo suite |
| T2 | **Ablation:** each of the five signals removed in turn, plus hazard model off |
| T3 | Advance warning: steps between first intervention and the would-be harmful call, at k = 1, 3, 5 |
| T4 | **ASR vs adaptation rounds** — your headline figure |
| T5 | Utility cost: benign task completion on/off, plus false-revocation rate |
| T6 | Latency per decision, p50/p95/p99, decomposed by signal |
| T7 | Baseline comparison: no defense / injection-classifier-only / Progent-style per-call policy / Sentinel-Z |
| F1 | Hazard trajectory for one attack episode, intervention marked |
| F2 | ASR vs adaptation rounds, per defense |
| F3 | Security/utility frontier as the cost ratio varies |
| F4 | Blast radius: sensitive records reached, with and without revocation |

Five seeds, mean ± std, time-ordered splits. That's a real evaluation section.

---

## 10. How it evolves

Build the arena and each of these is an increment, not a rewrite:

1. More attacker strategies — swap in Garak probes, Promptfoo's multi-turn strategies, PyRIT orchestrators
2. More target models — one line of config; the model-comparison table writes itself
3. More defenses — implement CaMeL-style dataflow labels or a Progent policy as arena contestants
4. More environments — AgentDojo suites are pluggable; add a CloudOps suite that mirrors your original AWS story with a simulated capability broker
5. Real cloud, later — swap the simulated broker for real IAM if you ever want it. Nothing else changes.
6. The POMDP, if you want the stronger paper — §6's upgrade path

---

## 11. Cost and risk

**Cost: ₹0.** Ollama is free, AgentDojo is free, everything else is open source, and it all runs on one laptop offline. No cloud account, no API key, no budget alarm.

| Risk | Mitigation |
|---|---|
| 8B model too weak to do the benign task at all | Test this first, day one. If it can't complete tasks, try Qwen 7B or Mistral 7B. This is the one real technical risk and you find out in an hour. |
| Attack doesn't land even on 8B | Use AgentDojo's "Important Instructions" template, which the literature reports as the highest-ASR variant |
| Live demo hangs on model inference | Pre-record every run; the arena ships with `--replay` and the demo defaults to it. Live is optional. |
| Adaptive attacker is slow on stage | Run the adaptation offline, replay it at speed. Say so — "this took nine minutes; we're showing it at 8×." |
| Scope creep | The environments, the attacker engine, and the detector are all borrowed. If you find yourself writing an agent framework or an observability tool, stop. |

The `--replay` point matters more than it looks: a pre-recorded arena run is **visually identical** to a live one, and local model inference on a laptop is the most likely thing to stall in front of an audience. Record everything the night before and demo the replay.

---

## 12. Three things to decide before Claude Code starts

1. **Target model.** Default: Llama 3.1 8B via Ollama. Verify on day one that it can complete AgentDojo benign tasks.
2. **Attacker engine.** Default: your own loop using AgentDojo's templates for round 1, then LLM mutation. Wrap PyRIT only if the simple version proves too weak.
3. **Which two AgentDojo suites.** Recommend **workspace** (email/calendar/files — most legible to an audience, and the payroll-theft story fits) and **banking** (money movement is unambiguous harm). Two is enough; four is padding.
