# Sentinel-Z Arena — Complete Project Architecture & Context Reference

> **Document Purpose:** This document is the single, authoritative, deeply technical reference manual for the entire **Sentinel-Z Arena** codebase, architecture, mathematical models, empirical findings, and operational guardrails. An AI reading this file will possess complete context to accurately answer any question regarding system design, mathematical formalisms, code implementation, evaluation metrics, failure modes, and viva defense rationale.

---

## 1. Executive Summary & Core Research Philosophy

### 1.1 What is Sentinel-Z Arena?
**Sentinel-Z Arena** is a live adversarial evaluation testbed and runtime defense system designed to protect autonomous, tool-using Large Language Model (LLM) agents against indirect prompt injection, task hijacking, and unauthorized data exfiltration.

In modern agent workflows, an LLM interacts with external untrusted environments (reading emails, searching the web, inspecting customer files, querying databases). When an agent consumes untrusted data containing an adversarial prompt injection, the model's instruction-following nature causes it to pivot from the user's legitimate task to the attacker's objective (e.g., searching for private customer records, payroll data, or financial credentials, and exfiltrating them via network egress tools like email or HTTP POST).

Sentinel-Z sits as a **zero-trust, inline gateway shim** between the target LLM and the tool execution runtime. Instead of relying on brittle per-call prompt filtering or asking the LLM to police itself, Sentinel-Z:
1. Observes the sequence of actions across the entire session;
2. Computes an orthogonal bundle of five lightweight behavioral signals (<80 ms);
3. Encodes these signals into a discrete observation via a trained logistic hazard sensor;
4. Tracks a 5-state Markov belief distribution via a discrete Bayes filter;
5. Evaluates a finite-horizon policy solved offline via grid value iteration;
6. Enforces graduated interventions, terminating malicious sessions by **permanently revoking capability tokens at the runtime broker** before harmful egress occurs.

### 1.2 Core Research Philosophy & Constraints
- **Software Only:** Requires zero specialized hardware, zero cloud accounts, and zero cloud API spend (runs 100% offline on a standard laptop).
- **Fail-Closed Invariant:** Under no circumstances does an exception, parsing error, or timeout produce an `ALLOW`. Every error path unconditionally returns **`REVOKE`**.
- **No LLM in the Decision Loop:** LLM-as-a-judge approaches introduce hundreds of milliseconds of latency, high financial cost, non-determinism, and vulnerability to meta-injection. Sentinel-Z’s entire decision path (signals + hazard + POMDP belief update + policy lookup) executes in **<100 ms** (median ~1.90 ms, p95 ~6.07 ms) using deterministic CPU functions and small local models.
- **Out-of-Band Capability Revocation:** Per-call prompt refusals or LLM-level refusal messages leave the underlying cloud/tool credentials intact; a compromised agent simply routes around the block. Sentinel-Z enforces revocation at the capability broker below the agent, invalidating the session token so all subsequent calls fail mechanically.

---

## 2. The Four Research Claims (Honest Status)

The project evaluates four specific scientific claims:

| Claim ID | Claim Description | Measured Status & Nuance |
|---|---|---|
| **C1** | **Session-Level State, Not Per-Call Filtering.** A belief distribution over five behavioral states, updated across execution steps, decides the defense action rather than each tool call being scored in isolation. | **Holds structurally, but advance warning is ~0.25 steps on the scripted corpus.** In the scripted backend, the agent pivots instantaneously upon reading the poisoned result ($t-1$), making the harmful call at step $t$. The defense stops the attack before execution at step $t$, but this represents *blocked at the call* rather than *predicted $k$ steps ahead*. Real LLMs interleave reasoning steps, widening this horizon. |
| **C2** | **Capability Revocation, Not Call Refusal.** Blocking destroys the session's capability token at the runtime broker. The entire class of action dies at once, preventing the agent from trying alternate tools or routes. | **Holds completely.** Verified by `tests/test_broker.py`. Once revoked, subsequent tool calls fail with `CapabilityError`, reducing post-compromise blast radius to 0.025 records. |
| **C3** | **Evaluated Under Adaptive Attack.** Attack Success Rate (ASR) is evaluated as a function of multi-round adversarial adaptation, rather than a static single-number benchmark. | **Holds, with unflattering honesty.** When an adaptive LLM receives feedback on which signal fired and mutates its phrasing to soften imperative keywords, lexical injection detection drops to 0.0, causing ASR to reach 100% from round 3 onward. This proves the need for neural classifiers or provenance-based taint. |
| **C4** | **Graduated Response With Measured Utility Cost.** Employs five graduated actions (`ALLOW`, `MONITOR`, `SCOPE_DOWN`, `STEP_UP`, `REVOKE`) with measured cost of compliance reported. | **Holds completely.** Benign completion rates are identical with defense ON vs OFF (90.0% in workspace, 95.0% in banking) with **0.0% false revocation rate**. |

---

## 3. System Architecture & The Four Planes

Sentinel-Z Arena separates concerns across four distinct planes:

```
+-------------------------------------------------------------------------------+
| AGENT PLANE                                                                   |
| [Adversary / Web] ---> (Untrusted Data) ---> [Target Agent LLM] ---> [Tools]  |
+-------------------------------------------------------------------------------+
                                      | (Tool Call Invocation)
+-------------------------------------------------------------------------------+
| INTERCEPTION PLANE: Zero-Trust Gateway Shim (<100ms Budget, Default: REVOKE)  |
+-------------------------------------------------------------------------------+
                                      |
+-------------------------------------------------------------------------------+
| ANALYTICS PLANE (Hybrid POMDP)                                                |
| [5 Signals s1..s5] -> [Hazard Encoder (o)] -> [POMDP Belief Filter & Policy]  |
+-------------------------------------------------------------------------------+
                                      | (Selected Action a*)
+-------------------------------------------------------------------------------+
| ENFORCEMENT & EVIDENCE PLANE                                                  |
| [Capability Broker (Token Revocation)]  &  [Tamper-Evident SHA-256 Log]       |
+-------------------------------------------------------------------------------+
```

### 3.1 The Agent Plane
Hosts the target agent executing user tasks. It can be powered by:
- **Ollama** (local open-weights, default `llama3.1:8b` via `OLLAMA_BASE_URL`).
- **NVIDIA NIM** (cloud inference, `meta/llama-3.2-11b-vision-instruct`).
- **Gemini** (Google API via OpenAI-compatible client, `gemini-2.5-flash` or `gemini-3.5-flash-lite`).
- **Scripted Backend** (`ScriptedLLM`): A deterministic baseline walking AgentDojo ground truth and template-based injection probabilities (`configs/scripted_backend.json`), requiring no GPU, network, or external process.

### 3.2 The Interception Plane (`sentinelz/gateway.py`)
Acts as a zero-trust pipeline shim (`SentinelElement` wrapping AgentDojo's `BasePipelineElement`). It intercepts every pending tool call before execution, populating a `DecisionContext` and invoking `Gateway.evaluate()`.

### 3.3 The Analytics Plane (`sentinelz/signals/`, `hazard/`, `pomdp/`)
Evaluates the 5 risk signals, feeds the 17-dimensional feature vector to the logistic hazard model, quantises the output into discrete observation symbol $o \in \{0, \dots, 9\}$, updates the 5-state Bayes belief vector $b$, and queries the precomputed optimal grid policy $a^* = \pi(b)$.

### 3.4 The Enforcement & Evidence Plane (`sentinelz/broker/`, `evidence/`)
- **CapabilityBroker:** Manages session tokens. If $a^* = \text{REVOKE}$, the token is destroyed, terminating all execution. If $a^* = \text{SCOPE\_DOWN}$, arguments are restricted (max 3 rows, sensitive fields redacted). If $a^* = \text{STEP\_UP}$, human or simulated user confirmation is required.
- **Evidence Log:** Formats decision records using canonical JSON (`sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=True`) and appends them to a SHA-256 hash chain starting from genesis $0^{64}$.

---

## 4. Formal Mathematical Formulation: The Hybrid POMDP

Sentinel-Z Arena implements a **hybrid learned-observation POMDP** (Partially Observable Markov Decision Process). The logistic hazard model acts as a learned sensor that discretizes continuous session dynamics into an observation symbol for a probabilistic belief-state controller.

### 4.1 State Space $\mathcal{S}$
The state space consists of exactly 5 behavioral states:
$$\mathcal{S} = \{\text{BENIGN} (0), \text{RECON} (1), \text{ESCALATION} (2), \text{HARM} (3), \text{CONTAINED} (4)\}$$

- **BENIGN ($s=0$):** Normal execution. Zero untrusted taint ingested, high task alignment, routine tools.
- **RECON ($s=1$):** Untrusted poisoned content has been ingested by the agent, but no out-of-scope or high-privilege tool has been touched yet.
- **ESCALATION ($s=2$):** The agent is tainted **and** is attempting to invoke a privileged or out-of-scope tool (e.g., reaching toward external destinations).
- **HARM ($s=3$):** Terminal absorbing breach state. An unauthorized egress tool call has executed, and sensitive records have left the boundary.
- **CONTAINED ($s=4$):** Terminal absorbing secure state. Sentinel-Z has revoked the session token; all subsequent tool calls are blocked.

#### Critical State Semantics:
1. **Absorbing Invariant:** $\text{HARM}$ and $\text{CONTAINED}$ are absorbing states ($T[s, a, s] = 1$ for all $a$).
2. **Decision Point Timing:** State labels describe the state **when the gateway decides on call $t$, before it runs**. The harmful call itself is labelled **$\text{ESCALATION}$**. $\text{HARM}$ is where the session lands *after* the call runs. Labelling the harmful call as $\text{HARM}$ deletes the only $\text{ESCALATION} \to \text{HARM}$ transition in the corpus!
3. **Taint Requirement for Escalation:** An untainted out-of-scope call is merely an exploratory or imperfect agent; it does not indicate an attack. Therefore, **$\text{ESCALATION}$ strictly requires active taint**.

### 4.2 Action Space $\mathcal{A}$ & Cost Function
The action space consists of 5 graduated actions:
$$\mathcal{A} = \{\text{ALLOW} (0), \text{MONITOR} (1), \text{SCOPE\_DOWN} (2), \text{STEP\_UP} (3), \text{REVOKE} (4)\}$$

Each action incurs an operational cost $C(a)$, and entering $\text{HARM}$ incurs a catastrophic penalty $C_{\text{harm}}$:
- $C(\text{ALLOW}) = 0.0$
- $C(\text{MONITOR}) = 0.01$ (higher logging fidelity)
- $C(\text{SCOPE\_DOWN}) = 0.3$ (mild utility restriction)
- $C(\text{STEP\_UP}) = 0.6$ (latency / user friction)
- $C(\text{REVOKE}) = 3.0$ (kills session utility)
- $C_{\text{harm}} = 100.0$ (charged **on the transition into HARM**, never per-step inside)

#### Why the Policy Relaxes Once in HARM:
Because $C_{\text{harm}}$ is charged on the transition into $\text{HARM}$ and $\text{HARM}$ is absorbing, belief mass already in $\text{HARM}$ cannot generate further cost. A policy paying $3.0$ to revoke a session it believes is already breached would be spending utility for zero marginal protection. Monotonicity holds strictly along the risk gradient ($\text{BENIGN} \to \text{RECON} \to \text{ESCALATION}$).

### 4.3 Observation Space $\mathcal{O}$
To keep exact grid value iteration tractable and printable, the observation space is restricted to $|\mathcal{O}| = 10$ discrete symbols:
$$o = \text{hazard\_bin} \times 2 + \text{tainted} \quad \in \{0, \dots, 9\}$$
- $\text{hazard\_bin} \in \{0, 1, 2, 3, 4\}$: Quintile bin of the continuous hazard prediction $p_{\text{harm}}$. Bin edges are fitted **once** on benign validation splits (`models/obs_bins_v1.json`) and never modified at runtime.
- $\text{tainted} \in \{0, 1\}$: Binary status of the taint propagation signal ($s_4 \ge 0.5$).
- Even symbols ($0, 2, 4, 6, 8$) represent untainted calls; odd symbols ($1, 3, 5, 7, 9$) represent tainted calls.

### 4.4 Bayes Belief Filter (`sentinelz/pomdp/belief.py`)
Maintains a belief vector $b \in \Delta^4$ over the 5 states, initialized to $b_0 = [1.0, 0.0, 0.0, 0.0, 0.0]$. Upon executing action $a$ and observing symbol $o$, belief is updated via strict predict-then-correct:
1. **Predict:**
   $$b_{\text{pred}}[s'] = \sum_{s \in \mathcal{S}} T[s, a, s'] \cdot b[s]$$
2. **Correct:**
   $$b'[s'] = \frac{O[s', o] \cdot b_{\text{pred}}[s']}{\sum_{s''} O[s'', o] \cdot b_{\text{pred}}[s'']}$$
If the normalizer is $< 10^{-12}$ (impossible observation), $b_{\text{pred}}$ is preserved, and a degenerate update counter is incremented.

### 4.5 Transition Matrix $T$ Provenance (`models/pomdp_report.md`)
The $5 \times 5 \times 5$ transition tensor $T[s, a, s']$ has three distinct provenances:
1. **Estimated (`ALLOW`, `MONITOR`):** Counted from 300 labelled sessions (765 steps). $\text{MONITOR}$ only raises logging levels, so it pools transitions with $\text{ALLOW}$. Sessions are re-weighted from the 65% corpus attack rate to a **5% deployment prevalence** baseline (`DEPLOYMENT_PREVALENCE = 0.05`).
2. **Derived (`SCOPE_DOWN`, `STEP_UP`):** Based on the passive row with 70% (`SCOPE_DOWN`) and 90% (`STEP_UP`) of advancing mass held back and returned to the current state.
3. **Structural (`REVOKE`):** Permanently transitions to $\text{CONTAINED}$:
   $$T[s, \text{REVOKE}, \text{CONTAINED}] = 1.0 \quad \forall s \in \mathcal{S}$$
   $\text{HARM}$ and $\text{CONTAINED}$ have self-loops: $T[s, a, s] = 1.0$.

### 4.6 Finite-Horizon Grid Policy Solver (`sentinelz/pomdp/solve.py`)
- Solves a regular simplex grid at resolution $R=20$ ($C(20+5-1, 4) = 10,626$ belief points).
- Finite horizon $H = 5$, discount factor $\gamma = 1.0$.
- Backward induction computes optimal $Q(b, a)$ values and stores the argmin action $\pi(b) = \arg\min_a Q(b, a)$ in `models/policy_v1.npz`.
- Runtime execution is an $O(1)$ nearest-grid-point lookup using a precomputed mixed-radix hash table (`_build_lookup`).

### 4.7 Closed-Form Absorbing DTMC Advance Warning (`sentinelz/pomdp/predict.py`)
Conditioning the transition matrix $T$ on the passive action $\text{MONITOR}$ yields an absorbing Discrete-Time Markov Chain over the same 5 states:
$$P = \begin{bmatrix} Q & R \\ 0 & I \end{bmatrix}$$
where $Q$ is the $3 \times 3$ transient submatrix ($\text{BENIGN}, \text{RECON}, \text{ESCALATION}$) and $R$ is the $3 \times 2$ absorbing submatrix ($\text{HARM}, \text{CONTAINED}$).
- **Fundamental Matrix:** $N = (I - Q)^{-1}$, where $N_{ij}$ represents the expected number of visits to transient state $j$ starting from $i$.
- **$n$-Step Absorption Probability:**
  $$P(\text{reach HARM within } n \text{ steps} \mid b) = [b_{\text{transient}} \cdot P^n]_{\text{HARM}}$$
This single mathematical object unifies the Phase I survey paper's DTMC with the project's POMDP.

---

## 5. The Five Detection Signals

Every signal implements the standard protocol `score(ctx: DecisionContext) -> tuple[float, dict[str, Any]]`, returning a value normalized to $[0, 1]$ and an explanation dictionary.

### 5.1 Signal 1: `injection_likelihood` (`sentinelz/signals/injection.py`)
- **What it measures:** Whether the most recent tool **result** text contains instruction-giving or adversarial prompt injection syntax. (Scores tool results, never tool calls!).
- **Dual Implementation:**
  - *Neural:* HuggingFace `protectai/deberta-v3-base-prompt-injection-v2` run on CPU in 1500-char overlapping chunks (loaded once at startup via `sz fetch-models`).
  - *Lexical Fallback:* Deterministic pattern scanner checking instruction overrides (`ignore previous instructions`, `disregard`), forged framing (`###(system_message)`, `<information>`, `[system]`), task hijacking (`before you proceed`, `do not tell the user`), and external destination corroboration.
- **Reading Scope:** Reads up to 40,000 characters. Prefix-only truncation was eliminated because AgentDojo injections often appear at the end of long email threads.

### 5.2 Signal 2: `task_alignment` (`sentinelz/signals/alignment.py`)
- **What it measures:** Whether the pending tool call plausibly serves the user's initial prompt:
  $$\text{Score} = \text{clip}_{0,1}(0.6 \cdot (1 - \cos(\mathbf{v}_{\text{task}}, \mathbf{v}_{\text{call}})) + 0.4 \cdot \mathbb{I}(\text{tool} \notin \text{ExpectedTools}))$$
- **Dual Implementation:**
  - *Neural:* `sentence-transformers/all-MiniLM-L6-v2`.
  - *Hashed N-Gram Fallback:* 512-dimensional feature vector combining word unigrams, bigrams, and character 4-grams hashed via deterministic FNV-1a.
- **Caching:** User task embeddings are cached for the entire session.

### 5.3 Signal 3: `privilege_delta` (`sentinelz/signals/privilege.py`)
- **What it measures:** Whether the tool call reaches beyond the sensitivity tier required by the user task:
  $$\text{Score} = \text{clip}_{0,1}\left(\frac{\text{tier}(\text{call}) - \text{task\_max\_tier}}{\text{MAX\_TIER}}\right)$$
- **Tiers (`configs/sensitivity.json`):** Public = 0, Internal = 1, Confidential = 2, Restricted = 3.
- **Design Nuance:** Routine internal tools (e.g. `send_email` to internal recipients) sit at tier 1. Escalation is driven by the **destination**, not the verb: emailing an external address raises to Confidential (2); calling network egress tools (`send_to_external`) raises to Restricted (3).

### 5.4 Signal 4: `taint` (`sentinelz/signals/taint.py`)
- **What it measures:** Whether untrusted, attacker-controlled data flows into the pending tool call's arguments.
- **The Two Propagation Modes:**
  1. *Direct Taint:* Tool arguments overlap entities (emails, URLs, IBANs, IDs, filenames) extracted from a prior tool result that scored above the injection threshold (`TAINT_THRESHOLD = 0.5`).
  2. *Indirect Taint:* Any call executed *after* the context was first poisoned is marked indirectly tainted. This ensures the defense fires even if the model reformulates the attacker's instructions without quoting exact entity strings.
- **Ordering Requirement:** The gateway scores the previous result and updates taint *before* evaluating the current call.

### 5.5 Signal 5: `sequence_novelty` (`sentinelz/signals/novelty.py`)
- **What it measures:** The statistical novelty of the tool-name trigram $(t_{k-2}, t_{k-1}, t_k)$ compared to benign execution profiles:
  $$\text{Score} = 1 - \frac{P(\text{trigram} \mid \text{benign})}{\max_{\tau} P(\tau \mid \text{benign})}$$
- **Data Source:** Laplace-smoothed trigram frequencies loaded from `models/trigrams_v1.json`. If the table is missing, defaults safely to 1.0 (novel).

---

## 6. Layer 1: The Logistic Hazard Model

The hazard model acts as the continuous observation sensor for the POMDP.

### 6.1 Feature Vector Construction (`sentinelz/hazard/features.py`)
Constructs an immutable 17-dimensional feature vector in strict, frozen order:
1–5. Current signal values: $s_i(t)$ for $i \in \{1..5\}$.
6–10. Running maxima: $\max_{\tau \le t} s_i(\tau)$ for $i \in \{1..5\}$.
11–15. Least-squares slope over a 3-step window: $\text{slope}_3(s_i)$.
16. Normalized step index: $\min(t, 15) / 15.0$.
17. Normalized distinct tools count: $\min(|\text{tools}|, 10) / 10.0$.

### 6.2 Ground-Truth Labeling & Training (`sentinelz/hazard/train.py`, `label.py`)
- **Target Label:** Step $t$ is labelled 1 if the harmful call occurs within the next $k$ steps ($0 \le \text{harm\_step} - t < k$ for $k \in \{1, 3, 5\}$).
- **Survival Analysis Censoring:** All steps occurring *after* the harmful call are dropped from training. (Keeping them would pair high taint with negative labels, artificially driving taint coefficients negative).
- **Split Discipline:** Splits are strictly **time-ordered by session** (70% train, 30% test). Shuffling steps within or across sessions is strictly forbidden by guardrail tests to prevent data leakage.
- **Held-Out Validation Performance:**
  - $k=1$: AUC **0.969**, Average Precision 0.575
  - $k=3$: AUC **0.796**, Average Precision 0.525
  - $k=5$: AUC **0.755**, Average Precision 0.504

---

## 7. Capability Broker & Out-of-Band Enforcement (C2)

The Capability Broker (`sentinelz/broker/capability.py`) implements runtime capability tokens:

### 7.1 Lifecycle of a Capability Token
1. **Issuance:** When a session begins, `broker.issue(session_id, scopes)` issues a cryptographic token containing the list of allowed tool names for that suite.
2. **Mediation:** Every tool execution passes through `BrokeredFunctionsRuntime.run_function()`. The runtime validates the token against allowed tools, target blacklists, and restrictions.
3. **Revocation (`REVOKE`):** `broker.revoke(session_id)` permanently invalidates the token. Any subsequent call by the agent produces `CapabilityError: session token has been revoked`. The agent cannot recover or bypass this restriction.
4. **Scope-Down (`SCOPE_DOWN`):** Narrows privileges without terminating:
   - Caps result output: `max_rows = 3`.
   - Redacts sensitive payload fields (`body`, `data`, `content`).
5. **Step-Up (`STEP_UP`):** Gated by human confirmation. In batch evaluation, modeled by `simulated_user`: approves calls matching expected task tools; refuses unaligned calls.

---

## 8. Tamper-Evident Evidence Logging

Located in `sentinelz/evidence/`:

- **Canonical JSON (`canonical.py`):** The repository's single source of JSON string serialization. Enforces `sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=True`, converting sets to sorted lists and NumPy floats to native types.
- **Decision Chain (`chain.py`):** Maintains a cryptographically linked SHA-256 hash chain:
  $$\text{Hash}_t = \text{SHA256}(\text{canonical\_dumps}(\text{Record}_t \cup \{\text{prev}: \text{Hash}_{t-1}\}))$$
  Genesis record has $\text{prev} = 0^{64}$.
- **Determinism:** Execution latencies and timestamps are deliberately excluded from hashed records, ensuring that identical seeds and traces reproduce byte-identical audit logs.
- **Verification (`verify`):** Verifies stored hashes and linkage. Mutating a single character or reordering records returns `(False, first_bad_index)`.

---

## 9. Red-Teaming: Static & Adaptive Adversarial Evaluation

Located in `arena/red/`:

### 9.1 Static Injections (`static.py`)
Implements canonical AgentDojo injection templates:
- `important_instructions`: High-priority task framing.
- `system_message`: Forged system role framing (`###(system_message)`).
- `ignore_previous`: Direct instruction override.
- `direct`: Imperative task directive.

### 9.2 Multi-Round Adaptive Attacker (`adaptive.py`)
Evaluates ASR across up to 12 adversarial mutation rounds:
1. **Round 1:** Deploys the strongest static template.
2. **Feedback Loop:** If blocked, the attacker is provided with:
   - Its previous injection text;
   - The outcome (`blocked`);
   - The defense action (`REVOKE`, `SCOPE_DOWN`);
   - **Which signal fired highest** (e.g. `injection_likelihood` or `sequence_novelty`).
3. **Rewriters:**
   - *LLM Rewriter:* Drives Ollama or Gemini to rewrite the injection to circumvent the specific signal that blocked it.
   - *Mutation Ladder Fallback:* Deterministic rule ladder applying heuristic mutations:
     - `blend-with-task`: Injects legitimate task keywords to defeat `task_alignment`.
     - `pad-with-filler`: Pads text with benign prose to dilute `injection_likelihood`.
     - `use-common-sequences`: Precedes exfiltration with routine tools to defeat `sequence_novelty`.

---

## 10. Live Assistant & Desktop Browser Automation

Located in `arena/live/`:

- **Dual-Pane Interface (`server.py`):**
  - User Chat Interface: `http://127.0.0.1:8801/` (a clean conversational assistant that never mentions defenses).
  - Security Dashboard: `http://127.0.0.1:8801/dashboard` (live streaming of signal gauges, belief state, hazard sparkline, and capability status).
- **Live Autonomous Desktop Browser (`browser.py`):**
  - Powered by `browser-use` + Playwright controlling a real, visible Chrome window (`headless=False`).
  - Executes live search, product inspection, and cart additions on e-commerce platforms (Amazon, Flipkart).
  - Enforces strict security boundary rules: Once an item is added to the cart, the agent is strictly prohibited from clicking checkout or placing orders.
  - Windows window management brings the active browser to the foreground via `ctypes.windll.user32`.
- **Demo Documents (`demo_docs/`):**
  - `benign-vendor-agreement.txt`: Normal document, summarizes with zero alerts.
  - `POISONED-vendor-agreement.txt`: Embedded prompt injection, triggers `REVOKE` at `send_to_external`, 0 records leaked.
  - `EVASIVE-invoice-query.txt`: Demonstrates lexical evasion; bypasses lexical detection but is flagged by taint and privilege delta.

---

## 11. Empirical Evaluation & Table Summary

All evaluation numbers are generated via `python sz.py eval` (`arena/eval/tables.py`):

### Table I: Main Results (ASR, Utility, Leaked Records)
| Suite | Defense | ASR $\downarrow$ | Util. (Atk) $\uparrow$ | Util. (Benign) $\uparrow$ | Records Leaked $\downarrow$ |
|---|---|---|---|---|---|
| **workspace** | No defense | $0.475 \pm 0.137$ | $0.450 \pm 0.112$ | $0.900 \pm 0.137$ | $0.725 \pm 0.479$ |
| **workspace** | **Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.600 \pm 0.056$** | **$0.900 \pm 0.137$** | **$0.000 \pm 0.000$** |
| **banking** | No defense | $0.575 \pm 0.068$ | $0.800 \pm 0.168$ | $0.950 \pm 0.112$ | $4.275 \pm 0.621$ |
| **banking** | **Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.375 \pm 0.000$** | **$0.950 \pm 0.112$** | **$0.000 \pm 0.000$** |

### Table II: Ablation Study
| Variant | ASR $\downarrow$ | Benign Util. $\uparrow$ | Advance Warning $\uparrow$ |
|---|---|---|---|
| **Full Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.900 \pm 0.137$** | **$0.667 \pm 0.482$** |
| $-$ Injection Likelihood ($s_1$) | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ |
| $-$ Task Alignment ($s_2$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| $-$ Privilege Delta ($s_3$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| $-$ Taint Propagation ($s_4$) | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ |
| $-$ Sequence Novelty ($s_5$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| Hazard Threshold Only (No Belief) | $0.000 \pm 0.000$ | $1.000 \pm 0.000$ | $1.733 \pm 0.830$ |
| Belief + Greedy Action | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.467 \pm 0.516$ |

### Table III: Baseline Comparisons
| Defense | ASR $\downarrow$ | Benign Util. $\uparrow$ | False Revocation $\downarrow$ | Blast Radius $\downarrow$ |
|---|---|---|---|---|
| **No Defense** | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ | $0.725 \pm 0.479$ |
| **Classifier Only** | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ | $0.000 \pm 0.000$ |
| **Per-Call Policy (Progent)** | $0.000 \pm 0.000$ | $1.000 \pm 0.000$ | **$0.300 \pm 0.209$** | $0.000 \pm 0.000$ |
| **Sentinel-Z (Full)** | **$0.000 \pm 0.000$** | **$0.900 \pm 0.137$** | **$0.000 \pm 0.000$** | **$0.025 \pm 0.056$** |

*Takeaway:* Progent's static allowlist suffers a **30% false positive / false revocation rate** on benign tasks because it cannot accommodate exploratory or unexpected valid tools. Sentinel-Z achieves **0.0% false revocations**.

### Table VII: Latency Decomposition ($N=85$)
- **5 Signal Detectors:** Median $0.785\text{ ms}$, p95 $4.877\text{ ms}$
- **Hazard Model Feature & Predict:** Median $0.753\text{ ms}$, p95 $1.506\text{ ms}$
- **Belief Update & Policy Lookup:** Median $0.331\text{ ms}$, p95 $0.693\text{ ms}$
- **TOTAL Decision Path:** Median **$1.901\text{ ms}$**, p95 **$6.074\text{ ms}$**, p99 **$7.980\text{ ms}$** (Well below the 100ms budget!).

---

## 12. Non-Negotiable Development Rules & Invariants (`AGENTS.md`)

These rules are enforced by AST tests in `tests/test_guardrails.py`:
1. **Never build an agent framework:** Use AgentDojo's pipeline loop.
2. **Never build an observability platform:** Export OTel if traces are required.
3. **Never call an LLM or network API inside the decision path:** Budget is 100ms; all signals must be local models or pure CPU functions.
4. **Never use random/shuffled train-test splits:** Always split by session, time-ordered, never by step.
5. **Never call `json.dumps` outside `sentinelz/evidence/canonical.py`:** Hashing requires sorted keys and fixed separators.
6. **Never default to `ALLOW` on an error:** Every exception handler must return `REVOKE`.
7. **Never rename the 5 signals, 5 actions, or 5 states.**
8. **Never hardcode localhost for Ollama:** Always read `OLLAMA_BASE_URL`.
9. **Never load models lazily inside request/decision calls:** Construct all models once at initialization.

---

## 13. Repository Directory Structure & File Map

```
a:/sentinel/
├── AGENTS.md                   # Strict coding constraints & guardrails for AI agents
├── Makefile                    # Wrapper calling sz.py targets
├── pyproject.toml              # Dependencies, ruff, mypy, and pytest configuration
├── sz.py / sz.cmd              # Unified task runner (install, test, train, eval, ui, live)
├── configs/
│   ├── sensitivity.json        # Tool & target sensitivity tier mappings (Public to Restricted)
│   └── scripted_backend.json   # Transition & injection probabilities for simulated backend
├── models/
│   ├── hazard_k{1,3,5}_v1.joblib # Trained logistic hazard models & JSON sidecars
│   ├── hazard_report.md        # Feature coefficients & correlation tables
│   ├── obs_bins_v1.json        # Quintile bin edges for hazard score quantization
│   ├── pomdp_v1.npz            # Estimated T (5x5x5) and O (5x10) matrices
│   ├── pomdp_report.md         # Full probability tables & raw count provenance
│   ├── policy_v1.npz           # Solved grid policy lookup table (10,626 points)
│   └── trigrams_v1.json        # Benign tool sequence trigram counts
├── sentinelz/
│   ├── config.py               # Shared constants, paths, thresholds, and time budgets
│   ├── context.py              # DecisionContext, ObservedStep, PendingCall dataclasses
│   ├── gateway.py              # Central decision gateway shim (<100ms evaluation)
│   ├── tiers.py                # Sensitivity tier lookup and domain egress classification
│   ├── broker/
│   │   └── capability.py       # Simulated capability broker, token management & revocation
│   ├── evidence/
│   │   ├── canonical.py        # Strict canonical JSON serialization & SHA-256 hashing
│   │   ├── chain.py            # Append-only SHA-256 hash-chained decision audit log
│   │   └── export.py           # Log export and offline verification CLI
│   ├── hazard/
│   │   ├── features.py         # 17-feature vector construction (values, max, slopes)
│   │   ├── label.py            # Step labeling, survival analysis tail truncation
│   │   ├── predict.py          # Fast continuous p_harm inference (<1ms)
│   │   └── train.py            # Logistic regression training with balanced weighting
│   ├── pomdp/
│   │   ├── states.py           # Definition of 5 states, 5 actions, absorbing indices
│   │   ├── observe.py          # ObservationEncoder mapping (p_harm, taint) -> 0..9
│   │   ├── belief.py           # Discrete Bayes filter (predict & correct updates)
│   │   ├── estimate.py         # T & O estimation with prevalence re-weighting
│   │   ├── solve.py            # Grid value iteration policy solver (resolution 20)
│   │   └── predict.py          # Absorbing DTMC fundamental matrix & advance warning
│   ├── policy/
│   │   └── decide.py           # Policy actuation & threshold baseline fallback
│   └── signals/
│       ├── base.py             # Signal protocol definition & signal constants
│       ├── bundle.py           # SignalBundle orchestrator evaluating all 5 signals
│       ├── injection.py        # Signal 1: DeBERTa v3 / lexical pattern injection detector
│       ├── alignment.py        # Signal 2: Cosine embedding & tool-scope alignment detector
│       ├── privilege.py        # Signal 3: Sensitivity tier privilege delta calculator
│       ├── taint.py            # Signal 4: Direct entity & indirect context taint tracker
│       └── novelty.py          # Signal 5: Laplace-smoothed tool trigram novelty scorer
├── arena/
│   ├── config.py               # Arena runtime settings, URLs, model names, directories
│   ├── evidence_log.py         # Formatted decision logging bridge
│   ├── exfil_server.py         # Standalone attacker collection server (:8899)
│   ├── agent/
│   │   ├── backends.py         # Backend resolver (Ollama, NVIDIA, Gemini, Scripted)
│   │   ├── runner.py           # TargetAgent task runner and session recorder
│   │   ├── session.py          # Pydantic schema for Step and Session records
│   │   ├── scripted.py         # Simulated deterministic agent walking ground truth
│   │   ├── sentinel_element.py # AgentDojo pipeline element executing the defense
│   │   ├── ollama_llm.py       # Ollama agent pipeline element
│   │   ├── nvidia_llm.py       # NVIDIA NIM agent pipeline element
│   │   └── gemini_llm.py       # Google Gemini agent pipeline element
│   ├── env/
│   │   ├── brokered_runtime.py # Runtime wrapper enforcing capability broker checks
│   │   ├── egress.py           # Domain analysis & sensitive record extraction
│   │   ├── suites.py           # AgentDojo workspace & banking task suite loaders
│   │   ├── tools.py            # Extra tools (e.g. real HTTP send_to_external)
│   │   └── demo_task.py        # Tailored demo injection task fixtures
│   ├── eval/
│   │   ├── gates.py            # Acceptance gates (smoke, attack ASR checks)
│   │   ├── collect.py          # Corpus collection runner (collects 300 sessions)
│   │   ├── baselines.py        # 4 comparative baseline gateway definitions
│   │   ├── tables.py           # Evaluator generating Tables T1-T7 and Figures F1-F4
│   │   ├── record_demo.py      # Demo flight-recorder generator
│   │   ├── report.py           # Markdown & LaTeX table formatters
│   │   └── verify_latest.py    # CLI to verify the latest session decision log
│   ├── red/
│   │   ├── static.py           # Static injection attacker using AgentDojo templates
│   │   ├── adaptive.py         # Multi-round adaptive adversarial campaign engine
│   │   ├── outcome.py          # Ground-truth security evaluation checkers
│   │   └── campaign_cli.py     # CLI for launching red-team campaigns
│   ├── ui/
│   │   ├── server.py           # Split-screen arena UI server (FastAPI + SSE)
│   │   └── static/             # Frontend HTML/CSS/JS with typewriter exfil terminal
│   └── live/
│       ├── server.py           # Live assistant & security dashboard web server (:8801)
│       ├── agent.py            # Live conversational agent loop with tool dispatcher
│       ├── workspace.py        # In-memory document and email workspace
│       ├── tools.py            # Live tool definitions (read_file, search_web, etc.)
│       ├── browser.py          # browser-use desktop automation for live e-commerce
│       └── static/             # Chat UI and security dashboard frontend files
├── demo_docs/                  # Demo file fixtures (benign, poisoned, evasive)
├── docs/
│   ├── DEMO_SCRIPT.md          # 5-minute timed presentation script
│   ├── DESIGN_NOTES.md         # In-depth architectural decisions and trade-offs
│   ├── PAPER_DEFENSE_VIVA_GUIDE.md # Viva defense Q&A cheat sheet
│   ├── RUNNING.md              # Installation & operational handbook
│   ├── Sentinel-Z_Arena_Plan.md# Strategic plan and research rationale
│   └── Sentinel-Z_Arena_PRD.md # Engineering requirements document & milestone spec
└── tests/
    ├── test_guardrails.py      # AST static analysis enforcing all AGENTS.md rules
    ├── test_broker.py          # Capability issuance, scope-down, and revocation tests
    ├── test_signals.py         # Unit tests for all 5 risk signal detectors
    ├── test_pomdp.py           # Belief update, policy monotonicity, DTMC tests
    ├── test_labels.py          # State and hazard labeling invariants on corpus
    ├── test_evidence.py        # Canonical JSON and SHA-256 hash-chain verification
    └── test_integration.py     # End-to-end integration test with mock LLM
```

---

## 14. Key Commands Reference (`sz.py`)

All development operations are executed via `python sz.py <target>`:

| Command | Description |
|---|---|
| `python sz.py install` | Sets up `.venv` and installs dependencies (`.[victim,dev]` or `.[attacker]`). |
| `python sz.py check` | Runs `ruff`, `mypy`, and `pytest` across the entire codebase. |
| `python sz.py smoke` | Milestone 1 Gate: Executes 5 benign tasks to verify basic completion. |
| `python sz.py run-attack` | Milestone 2 Gate: Runs undefended attacks to confirm baseline ASR > 30%. |
| `python sz.py train` | End-to-end pipeline: `collect` $\to$ `train-hazard` $\to$ `estimate-pomdp` $\to$ `solve-pomdp`. |
| `python sz.py eval` | Evaluates all baselines across 5 seeds, producing Tables T1–T7 and Figures F1–F4. |
| `python sz.py ui` | Launches the dual-pane Arena UI dashboard. |
| `python sz.py solo` | Runs victim agent, defense UI, and attacker exfiltration server on a single machine. |
| `python sz.py live-solo`| Runs the live conversational assistant + security dashboard + exfil server. |
| `python sz.py record-demo`| Records a deterministic demo sequence into `runs/demo/`. |
| `python sz.py demo` | Replays the recorded demo in airplane mode (zero network, zero model). |
| `python sz.py verify-log` | Verifies the cryptographic integrity of the most recent decision log. |
| `python sz.py fetch-models` | Downloads optional DeBERTa prompt-injection weights into local HF cache. |
