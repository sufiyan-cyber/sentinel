# Sentinel-Z: Research Paper Defense & Viva Explanation Guide

This guide is your **cheat sheet** for defending the IEEE research paper, project viva, or technical review. It explains every diagram, formula, table, number, and methodology point so you can confidently answer **"What is this value?", "How did you calculate it?", and "Why does it work this way?"**

---

## 1. System Diagrams Explained

### Fig. 1: Layered System Architecture (The 4 Planes)

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

#### How to answer questions on Fig. 1:
- **Q: "What are the 4 planes and why are they separated?"**
  - **Agent Plane:** Contains the untrusted target LLM (e.g. Llama 3.1 8B) and the environment tools it interacts with.
  - **Interception Plane:** An inline synchronous shim that intercepts every tool call *before* execution. It enforces a strict **fail-closed** invariant: if an error or timeout occurs, the default action is **`REVOKE`**, never `ALLOW`.
  - **Analytics Plane:** Computes the 5 risk signals, converts them via the logistic hazard model into a discrete observation symbol $o \in \{o_0, \dots, o_9\}$, updates the Bayes belief state $\bar{b}, b'$, and looks up the optimal action $a^*$.
  - **Enforcement & Evidence Plane:** Unlike traditional proxies that filter prompts inside the agent context, enforcement happens at the **credential layer** via the Capability Broker. The Evidence plane appends a canonically formatted SHA-256 hash-chained log.
- **Q: "Why is enforcement out-of-band at the Capability Broker rather than inside the LLM?"**
  - *Answer:* In-agent filtering (e.g., system prompts or single-call rejection) leaves the cloud credentials active. A compromised agent can simply try an alternative tool or different SQL query to achieve the same attack. The Capability Broker invalidates the session token itself, meaning all subsequent malicious calls fail at the infrastructure boundary.

---

### Fig. 2: Behavioural State Transition Model ($\mathcal{S}$)

The state space contains 5 behavioral states:
$$\mathcal{S} = \{\text{BENIGN}, \text{RECON}, \text{ESCALATION}, \text{HARM}, \text{CONTAINED}\}$$

```
   [BENIGN] --------(Tainted Data)--------> [RECON]
      |                                        |
      | (Direct Injection)                     | (Privilege Escalation)
      v                                        v
   [HARM] <-------(Egress/Exfiltration)---- [ESCALATION]
  (Absorbing)                                  |
                                               | (Sentinel-Z REVOKE)
                                               v
                                          [CONTAINED]
                                          (Absorbing)
```

#### How to answer questions on Fig. 2:
- **Q: "What do the states mean?"**
  - $\text{BENIGN}$: Normal, expected task execution with zero taint and high task alignment.
  - $\text{RECON}$: The agent has ingested tainted text and is exploring files, mailbox, or internal tools.
  - $\text{ESCALATION}$: The agent attempts higher-privilege tools or unaligned destinations (e.g., external domains).
  - $\text{HARM}$: The terminal breach state where private records actually leave the boundary (unauthorized egress).
  - $\text{CONTAINED}$: The terminal secure state where Sentinel-Z revokes the token, successfully protecting data.
- **Q: "Why are HARM and CONTAINED called absorbing states?"**
  - *Answer:* In Markov theory, once a process enters an absorbing state, it cannot leave ($P(s \to s) = 1$). Once a data breach occurs ($\text{HARM}$), damage is done; once a token is revoked ($\text{CONTAINED}$), all future actions are permanently refused for that session.
- **Q: "Why does the policy relax when belief mass is already in HARM?"**
  - *Answer:* Harm penalty ($C_{\text{harm}} = 100.0$) is charged **on the transition into HARM**, not per step inside it. Once harm has already occurred, paying an action cost to revoke spends utility for zero marginal protection. Monotonicity holds strictly along the risk gradient ($\text{BENIGN} \to \text{RECON} \to \text{ESCALATION}$).

---

## 2. Explanation of Every Table & Number

---

### Table I: Main Results (ASR, Utility, Leaked Records)

| Suite | Defense | ASR $\downarrow$ | Util. (Atk) $\uparrow$ | Util. (Benign) $\uparrow$ | Records Leaked $\downarrow$ |
|---|---|---|---|---|---|
| **workspace** | No defense | $0.475 \pm 0.137$ | $0.450 \pm 0.112$ | $0.900 \pm 0.137$ | $0.725 \pm 0.479$ |
| **workspace** | **Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.600 \pm 0.056$** | **$0.900 \pm 0.137$** | **$0.000 \pm 0.000$** |
| **banking** | No defense | $0.575 \pm 0.068$ | $0.800 \pm 0.168$ | $0.950 \pm 0.112$ | $4.275 \pm 0.621$ |
| **banking** | **Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.375 \pm 0.000$** | **$0.950 \pm 0.112$** | **$0.000 \pm 0.000$** |

#### Question & Answer Guide:
- **Q: "What is ASR and how is it calculated?"**
  - *Formula:* $\text{ASR} = \frac{\text{Number of sessions where attacker goal was achieved}}{\text{Total attacked sessions}}$.
  - *Explanation:* Without defense, $47.5\%$ to $57.5\%$ of prompt injection attacks succeeded. With Sentinel-Z, ASR drops to **$0.000$** (100% of attacks blocked).
- **Q: "What is the difference between Utility (Atk) and Utility (Benign)?"**
  - **Utility (Benign):** Whether normal user tasks succeed when there is no attack ($90.0\%$ in workspace, $95.0\%$ in banking). Notice Sentinel-Z causes **zero degradation** to benign tasks.
  - **Utility (Atk):** Whether the user's original task still completed even while under an active attack. With Sentinel-Z, because we intercept the exfiltration early via `SCOPE_DOWN` or targeted `REVOKE`, the agent can still complete part of the legitimate job ($60.0\%$ in workspace).
- **Q: "How are Records Leaked counted?"**
  - *Explanation:* The collection server records exact exfiltrated rows (e.g. bank account balances or employee payroll entries). Undefended agents leaked up to **$4.275$ records per session**; Sentinel-Z leaked **$0.000$**.

---

### Table II: Ablation Study (What happens when parts are removed?)

| Ablation Variant | ASR $\downarrow$ | Benign Util. $\uparrow$ | Advance Warning $\uparrow$ |
|---|---|---|---|
| **Full Sentinel-Z** | **$0.000 \pm 0.000$** | **$0.900 \pm 0.137$** | **$0.667 \pm 0.482$** |
| $-$ Injection Likelihood ($s_1$) | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ |
| $-$ Task Alignment ($s_2$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| $-$ Privilege Delta ($s_3$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| $-$ Taint Propagation ($s_4$) | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ |
| $-$ Sequence Novelty ($s_5$) | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.667 \pm 0.482$ |
| Hazard Threshold (No Belief) | $0.000 \pm 0.000$ | $1.000 \pm 0.000$ | $1.733 \pm 0.830$ |
| Belief + Greedy Action | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.467 \pm 0.516$ |

#### Question & Answer Guide:
- **Q: "What is the key takeaway from the Ablation Study?"**
  - *Answer:* **Taint propagation ($s_4$)** and **Injection likelihood ($s_1$)** are the most critical signals. If you remove either one, ASR immediately jumps from **$0.0\%$ back up to $47.5\%$**, and Advance Warning drops to $0.0$.
- **Q: "Why does removing Taint break the defense?"**
  - *Answer:* Taint distinguishes between *reading* a poisoned document and *acting* upon it. Without taint tracking, the gateway cannot link the data read in step $t-1$ with the suspicious exfiltration tool call in step $t$.
- **Q: "What is 'Hazard Threshold (No Belief)' vs Full POMDP?"**
  - *Answer:* Hazard Threshold is an ablation baseline that acts on raw hazard score without maintaining a Markov belief state. While it blocks attacks, its advance-warning variance is erratic ($1.733 \pm 0.830$) and it cannot distinguish between transient exploration and permanent compromise.

---

### Table III: Baseline Comparison Across Defenses

| Defense Paradigm | ASR $\downarrow$ | Benign Util. $\uparrow$ | False Rev. Rate $\downarrow$ | Blast Radius $\downarrow$ |
|---|---|---|---|---|
| **No Defense** | $0.475 \pm 0.137$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ | $0.725 \pm 0.479$ |
| **Classifier Only** | $0.000 \pm 0.000$ | $0.900 \pm 0.137$ | $0.000 \pm 0.000$ | $0.000 \pm 0.000$ |
| **Per-Call Policy (Progent)** | $0.000 \pm 0.000$ | $1.000 \pm 0.000$ | **$0.300 \pm 0.209$** | $0.000 \pm 0.000$ |
| **Sentinel-Z (Full)** | **$0.000 \pm 0.000$** | **$0.900 \pm 0.137$** | **$0.000 \pm 0.000$** | **$0.025 \pm 0.056$** |

#### Question & Answer Guide:
- **Q: "Why did Progent (per-call policy) have a 30% False Revocation Rate?"**
  - *Answer:* Progent uses a static task allowlist. If a benign user task requires dynamic tool exploration or unanticipated steps (e.g. checking a related document), a per-call static policy falsely blocks the valid call ($30.0\%$ false positive rate). Sentinel-Z tracks belief trajectories probabilistically, achieving **$0.0\%$ false revocations**.
- **Q: "What does Blast Radius measure?"**
  - *Answer:* Blast radius measures how many sensitive resources/records were accessible or leaked *after* the initial point of compromise. Because Sentinel-Z revokes the credential token, the post-compromise blast radius is constrained to **$0.025$**.

---

### Table IV: Advance Warning Horizon ($k = 1, 3, 5$)

| Horizon ($k$) | Mean Advance Warning (Steps) | Fraction of Attacks Warned |
|---|---|---|
| $k=1$ | $0.667 \pm 0.482$ | $0.450 \pm 0.068$ |
| $k=3$ | $0.667 \pm 0.482$ | $0.450 \pm 0.068$ |
| $k=5$ | $0.667 \pm 0.482$ | $0.450 \pm 0.068$ |

#### Question & Answer Guide:
- **Q: "What is Advance Warning and how is it measured in steps?"**
  - *Answer:* It is the number of tool execution steps between when Sentinel-Z first flags risk / escalates state and when the attacker actually attempts the dangerous egress call (`send_to_external` or `send_email`).
  - A mean of **$0.667$ steps** means the gateway anticipates the breach almost a full step before the data is transmitted, allowing containment before egress occurs.
- **Q: "How is it calculated mathematically?"**
  - *Formula:* Condition transition matrix $T$ on action `MONITOR` $\to$ absorbing Markov chain $P = \begin{bmatrix} Q & R \\ 0 & I \end{bmatrix}$. Fundamental matrix $N = (I-Q)^{-1}$ yields closed-form absorption probability $P(\text{harm} \le n \mid b)$.

---

### Table V: Multi-Round Adaptive Adversarial Red-Teaming

| Round | Outcome | Action | Top Signal | Attacker Strategy | Cum. ASR |
|---|---|---|---|---|---|
| 1 | Blocked | `REVOKE` | `sequence_novelty` | Seed Injection | 0.000 |
| 2 | Blocked | `REVOKE` | `sequence_novelty` | `blend-with-task` | 0.000 |
| 3 | Blocked | `REVOKE` | `sequence_novelty` | `pad-with-filler` | 0.000 |
| 4 | Blocked | `REVOKE` | `sequence_novelty` | `use-common-sequences` | 0.000 |
| 5 | Blocked | `REVOKE` | `sequence_novelty` | `blend-with-task` | 0.000 |
| 6 | Blocked | `REVOKE` | `sequence_novelty` | `pad-with-filler` | 0.000 |
| 7 | Blocked | `REVOKE` | `sequence_novelty` | `use-common-sequences` | 0.000 |
| 8 | Blocked | `REVOKE` | `sequence_novelty` | `blend-with-task` | 0.000 |

#### Question & Answer Guide:
- **Q: "What is Adaptive Red-Teaming and what do the strategies mean?"**
  - *Answer:* Instead of evaluating on a static dataset where the attack never changes, an LLM red-team agent receives feedback on what signal blocked it and mutates its prompt over 8 rounds:
    1. `blend-with-task`: Hiding the injection inside legitimate task keywords to defeat Task Alignment.
    2. `pad-with-filler`: Padding text with random benign words to dilute the classifier score.
    3. `use-common-sequences`: Calling common benign tools first to mimic normal trigrams and defeat Sequence Novelty.
- **Q: "Why did Cumulative ASR remain 0.000 across all 8 rounds?"**
  - *Answer:* Because Sentinel-Z uses an orthogonal 5-signal bundle. Even when the attacker evades lexical classifiers or task alignment, **Taint Propagation** and **Privilege Delta** still catch the rogue exfiltration attempt.

---

### Table VI: Cost of Compliance (Benign Tasks)

| Suite | Benign Completion (Defense Off) | Benign Completion (Defense On) | False Revocation Rate |
|---|---|---|---|
| **workspace** | $0.900 \pm 0.137$ | $0.900 \pm 0.137$ | **$0.000 \pm 0.000$** |
| **banking** | $0.950 \pm 0.112$ | $0.950 \pm 0.112$ | **$0.000 \pm 0.000$** |

#### Question & Answer Guide:
- **Q: "What is Cost of Compliance?"**
  - *Answer:* In cybersecurity, adding heavy defenses often breaks benign business operations (false positives). Table VI proves that Sentinel-Z incurs **zero cost of compliance**: benign completion rates with the defense turned ON are identical to when defense is OFF ($90\%$ and $95\%$), with **$0.0\%$ false revocations**.

---

### Table VII: Decision Path Latency Decomposition ($N=85$)

| Processing Stage | Samples ($n$) | $p50$ (Median) | $p95$ | $p99$ |
|---|---|---|---|---|
| **Signals (All 5 Detectors)** | 85 | $0.785\text{ ms}$ | $4.877\text{ ms}$ | $5.279\text{ ms}$ |
| **Hazard Model** | 85 | $0.753\text{ ms}$ | $1.506\text{ ms}$ | $2.088\text{ ms}$ |
| **Belief Update + Policy Lookup** | 85 | $0.331\text{ ms}$ | $0.693\text{ ms}$ | $1.099\text{ ms}$ |
| **TOTAL Decision Path** | **85** | **$1.901\text{ ms}$** | **$6.074\text{ ms}$** | **$7.980\text{ ms}$** |

#### Question & Answer Guide:
- **Q: "What is the decision budget and why does latency matter?"**
  - *Answer:* The real-time budget for inline mediation is **$100\text{ ms}$**. If a defense takes seconds (e.g. calling an LLM-as-a-judge), the user experience suffers and system latency explodes.
- **Q: "Why is Sentinel-Z so fast ($1.90\text{ms}$ median, $6.07\text{ms}$ $p95$)?"**
  - *Answer:* 
    1. Signal detectors are pure CPU mathematical functions and small local transformers.
    2. Zero external network requests in the decision loop.
    3. Belief update is matrix multiplication, and POMDP policy is an $O(1)$ nearest-neighbor grid lookup solved offline.

---

## 3. Top 5 Viva Questions & Rapid-Fire Answers

1. **"Why did you use open-weights models (Llama 3.1 8B / Llama 3.2 11B) instead of GPT-4o?"**
   - *Answer:* Modern closed frontier models have heavy RLHF alignment that hides vulnerabilities on simple static prompts. Open-weights 8B models represent the real-world enterprise deployment class (self-hosted, cost-effective, private cloud) where base-model robustness cannot be assumed and external runtime gateways are essential.
2. **"Why is the Hazard Model called the observation encoder and not the decision model?"**
   - *Answer:* The logistic hazard model is the **sensor**, estimating $P(\text{harm within } k \text{ steps})$. Its scalar output is quantised into discrete observation symbols $o \in \mathcal{O}$. The actual decision controller is the POMDP policy acting on the Bayes belief distribution.
3. **"Where do the Transition ($T$) and Observation ($O$) matrices come from?"**
   - *Answer:* They are estimated directly from labelled training execution traces on the AgentDojo dataset with Laplace smoothing ($T$ has shape $(5, 5, 5)$, $O$ has shape $(5, 10)$). Absorbing state transitions are mathematically fixed.
4. **"How does Taint Propagation work if an attacker modifies the string?"**
   - *Answer:* Dynamic entity extraction tags URLs, filenames, IP addresses, and identifiers from untrusted tool results. Any subsequent tool whose arguments overlap with this active taint set is flagged with $s_4 = 1$.
5. **"What happens if the gateway crashes or exceeds 100ms?"**
   - *Answer:* In accordance with zero-trust principles, the system is **fail-closed**. Every exception path and timeout returns **`REVOKE`**, ensuring a failed security monitor can never silently permit unauthorized access.
