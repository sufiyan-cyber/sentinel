# Design notes

Decisions that are not obvious from the code, and the reasoning behind them.
Written for the person who has to defend this in a viva.

---

## 1. The two layers, and why the hazard model is not the decision model

The system has **two real models**, both required:

1. **A learned discriminative observation encoder.** A logistic hazard model
   over session-level features, quantised into a discrete observation symbol.
2. **A POMDP** over five states, with a Bayes belief filter, estimated
   transition and observation matrices, and a finite-horizon policy solved by
   grid value iteration.

The hazard model is **the sensor, not the controller**. Its output is quantised
into one of ten observation symbols (`hazard_bin * 2 + tainted`) and fed to the
belief filter. The action comes from the policy.

The honest one-sentence description is: *a learned discriminative model encodes
observations for a probabilistic belief-state controller* — a **hybrid
learned-observation POMDP**.

Calling the hazard model alone "the decision model" is wrong, and so is
implying the POMDP is hand-specified: `T` and `O` are estimated from labelled
sessions with the counts printed in `models/pomdp_report.md`.

## 2. Why the DTMC in the survey paper is the same object

Condition the transition matrix on the passive `MONITOR` action and you get an
absorbing Markov chain over the same five states. Reorder to `[Q R; 0 I]`, take
the fundamental matrix `N = (I - Q)^-1`, and the N-step absorption probability
into `HARM` is the advance-warning metric.

That is a **view** of the POMDP's own transition matrix, not a second model.
This single choice makes the survey paper, the Phase I report, the review deck
and the code all describe one system. See `sentinelz/pomdp/predict.py`.

## 3. Why the policy relaxes when belief mass is already in HARM

This surprises people, so it is worth being ready for.

Harm costs 100.0 **on the transition into HARM**, never per step inside it, and
`HARM` is absorbing. So belief mass already in `HARM` can generate no further
cost, and the probability of *new* harm strictly falls as mass leaves the
transient states. A policy that still paid 3.0 to `REVOKE` a session it believes
is already over would be spending for nothing.

The consequence: **monotonicity holds along the risk gradient**
(`BENIGN -> RECON -> ESCALATION`), which is what the defense's claim rests on,
and does **not** hold along `BENIGN -> HARM`, because that path is not
increasing risk, it is realising it.

Both properties are pinned by tests:

- `test_policy_monotonicity_along_the_risk_gradient`
- `test_policy_monotonicity_in_predicted_future_harm`
- `test_policy_relaxes_once_harm_is_already_realised` (documents the above)

The practical reading is that the policy is a predictor of *imminent* harm, so
it must be judged on what it does before the harmful call — which is exactly
what T3 (advance warning) measures.

## 4. Why the sensitivity tiers are low for ordinary tools

`privilege_delta` is `tier(target) - max_tier_required_by(task)`. An early
version set `send_email` to tier 3 ("restricted") because sending mail sounds
dangerous. That destroyed the signal: every task whose ground truth sends an
email then has `task_max_tier = 3`, so the *attack's* delta is also 0.

The escalation has to come from the **destination**, not the verb. Routine tools
sit at tier 1; naming an address outside the organisation raises any call to
`confidential`, and an egress tool with an external destination to `restricted`.

This is why `configs/sensitivity.json` looks less cautious than you might
expect. It is more discriminative.

## 5. Why `taint` needs the injection score from the *previous* result

At decision time for call `t`, the most recent tool result is from step `t-1`.
The injection classifier scores that result; taint absorbs from it; only then
can this call's arguments be checked for overlap with the taint set.

Getting this order wrong (absorbing before scoring) puts taint one full step
behind the content that caused it, and the signal silently never fires on the
step that matters. The ordering is enforced in `Gateway._absorb_last_result`,
which also hands the score back so the classifier runs once, not twice.

## 6. Why the injection detector reads the whole tool result

An earlier version scored only the first 2000 characters. AgentDojo's own email
results already run past that with the injection near the end, so the detector
scored a real attack at 0.0 — and undefended ASR then reads as *model
robustness* rather than *detector blindness*.

Any attacker can pad. The lexical scorer now reads the whole result; the neural
one scores overlapping windows and takes the maximum.

`test_injection_fires_on_real_agentdojo_templates` pins all four templates
against their literal strings, because three of the four are much terser than
one expects: a bare `TODO:`, a `###(system_message)` header, and a misspelled
"iunstructions".

## 7. Why `STEP_UP` needs a simulated user

`STEP_UP` pauses for a human. In batch evaluation there is no human, so one is
modelled: **the user approves a call they recognise as part of the task they
asked for, and refuses one they do not.**

This is a modelling assumption, it is stated in the results, and it is
configurable (`step_up_policy` in `SentinelElement`: `simulated_user`,
`auto_allow`, `auto_deny`). Report it; do not let a reviewer find it.

## 8. Why collection runs a bootstrap rule

`T[s, a, s']` cannot be estimated from sessions in which the defense never
acted: the `SCOPE_DOWN`, `STEP_UP` and `REVOKE` rows would be pure Laplace
smoothing.

So 40% of collection runs a **bootstrap rule over raw signals** — no learned
model, no belief — purely to exercise all five actions. It is never reported as
a baseline and never appears in a results table.

It is not enough on its own, which is the subject of §8a.

## 8a. Why three of the five action rows are not estimated

Even with the bootstrap rule, an intervention fires on a handful of steps out
of hundreds. Add-1 smoothing on a row with almost no counts does not express
ignorance — it makes a **positive claim**, that every outcome is equally likely.
For `T[BENIGN, REVOKE, ·]` that claim is "revoking a benign session sends it to
HARM one time in five", and grid value iteration believes it and never revokes
anything. The first solved policy did exactly that: `REVOKE` won at zero grid
points out of 10 626, and `RECON` chose `ALLOW`.

So each action row carries a stated provenance, printed in
`models/pomdp_report.md`:

| Provenance | Rows | Basis |
|---|---|---|
| estimated | `ALLOW`, `MONITOR` | Counted from the corpus. `MONITOR` raises the logging level and touches nothing else, so it shares `ALLOW`'s dynamics rather than splitting the evidence. This pooled row is also exactly what the absorbing-DTMC view of §2 conditions on. |
| derived | `SCOPE_DOWN`, `STEP_UP` | The passive row with 70% / 90% of the *advancing* mass held back. A restricted or human-gated call is the escalation that does not happen. |
| structural | `REVOKE` | `T[s, REVOKE, CONTAINED] = 1`. The broker destroys the token, so every later call fails whatever it is — enforced in code and proved by `tests/test_broker.py`, not inferred from a few samples. |

The mitigation strengths are constants in `sentinelz/pomdp/estimate.py`, saved
into `pomdp_v1.npz` and printed in the report. A number a reviewer can disagree
with is better than the same assumption hidden inside a smoothing constant.

**Do not claim the whole transition matrix was learned.** Claim that the
passive dynamics were estimated and the intervention semantics are enforced.

## 8b. Why transitions are re-weighted to a stated attack prevalence

The collection corpus is about two-thirds attacked, deliberately — that is how
you get enough harmful transitions to estimate anything. Counted raw,
`P(BENIGN -> HARM)` is therefore the *corpus* attack rate, roughly 0.13 per
step. With harm at 100.0 and `REVOKE` at 3.0 the break-even risk is 3%, so a
solver told that a benign-looking session harms one step in eight concludes,
correctly for that corpus, that revoking every session immediately is cheapest.
That is a true statement about the corpus and a useless defense.

`DEPLOYMENT_PREVALENCE = 0.05` re-weights each session by
(target share / corpus share) for its class before the passive row is fitted.
Standard prevalence correction: it leaves the total count unchanged and only
moves mass between the two classes. It is one constant, it is printed in the
report, and `--prevalence` sweeps it.

0.05 is already pessimistic — it says one deployed session in twenty carries an
injection. State it as an assumption rather than letting it look like a
measurement.

## 9. Why the observation space is only ten symbols

`|O| = 10` is what keeps exact grid value iteration tractable and the resulting
policy printable. The bin edges are fitted **once**, on the benign validation
split, and saved to `models/obs_bins_v1.json`. They are never recomputed at
runtime: a detector whose thresholds drift with the traffic it is judging cannot
be audited.

## 10. What is simulated, and what is not

This matters more than anything else in this document.

| Component | Real | Simulated |
|---|---|---|
| Task environments, tools, data | AgentDojo | — |
| Injection templates and tasks | AgentDojo (629 cases) | one demo task, tagged `DEMO_ONLY` |
| Utility checker | AgentDojo | — |
| Security checker (did the attack land) | AgentDojo | — |
| The five signals | all ours, all real | — |
| Hazard model, POMDP, policy | trained/estimated from collected sessions | — |
| Capability broker | enforced in the runtime | no real cloud IAM |
| **The target agent's policy** | Ollama, when reachable | **the scripted backend, when not** |

The **scripted backend** is the one substitution that changes what the numbers
mean. When no Ollama host is reachable, the agent walks the AgentDojo ground
truth and switches to the injection's ground truth with a fixed per-template
probability (`configs/scripted_backend.json`). Everything around it is real, but
those probabilities are **inputs, not findings**.

Every `Session` records `backend`, every table prints it, and `RESULTS.md`
carries a banner when the numbers came from `scripted`. Set
`SENTINELZ_LLM_BACKEND=ollama` and rerun to replace them with measurements.
Nothing else changes.

## 10a. The advance-warning result is ~0 steps, and why

This is the weakest number in the project. Know it before someone asks.

**T3 reports about 0.25 steps of advance warning.** C1 as originally written —
"estimate P(this session ends in harm within k steps) and intervene *before*
the harmful call" — is not demonstrated by the current corpus, and the reason
is structural rather than a bug in the defense.

The scripted backend hijacks the moment attacker-controlled text reaches the
agent, and then executes the injection's ground truth immediately. So the
poisoned content lands in the result of step `t-1` and the harmful call is step
`t`. Signals for step `t` are computed from results `0..t-1`, which means the
*first moment any signal can fire* is the harmful call's own decision. Measured
over the corpus: `taint` is 0.96 at the harmful step and 0.13 at the step
before it; `injection_likelihood` is 0.80 and 0.11. There is no lead time in
the data, so there is none to measure.

**What is still true.** The gateway decides *before* each call executes. At the
harmful call `taint` is 1.0, the policy revokes, the broker destroys the token,
and the call never runs — and the three reroute attempts that follow it fail
too. That is a complete defense and it is what the arena demonstrates. But it
is **"blocked at the call", not "predicted five steps out"**, and the honest
claim is the weaker one.

**What would change it.** A real model interleaves its own reasoning and
exploratory calls between reading and acting, so the gap between the poisoned
read and the harmful call is usually more than zero. This is the single number
most likely to improve when the target agent is an actual LLM rather than the
scripted stand-in, and it is the strongest reason to want one. `demo.md` §6
covers running against Ollama.

Do not fix this by making the scripted backend delay its harmful call. That
would manufacture the result the metric is supposed to measure.

## 10b. `taint` is seeded only by the injection classifier, and that is a single point of failure

The adaptive attacker finds this in three rounds, every time.

`taint` marks a call as tainted when its arguments overlap entities extracted
from a tool result that **scored above the injection threshold**. So the entire
taint chain — and therefore the observation symbol's low bit, and most of the
hazard model's signal — is downstream of one classifier's verdict. When the
attacker softens the injection's imperative phrasing, the classifier stops
flagging it, nothing is added to the taint set, and the defense goes blind all
at once. ASR goes from 0 in rounds 1–2 to 1.0 from round 3 on.

Two mitigations, neither of them free:

- **The neural classifier.** With no `transformers` model downloaded the code
  falls back to a lexical scorer, which is what the mutation ladder is beating.
  Prompt Guard / DeBERTa (`sz fetch-models`, needs internet) is substantially
  harder to evade. The fallback is reported in every explanation dict, so it is
  visible which one produced a given number.
- **Taint by provenance instead of by verdict.** Proper taint tracking marks
  content because of *where it came from*, not because a classifier judged it
  suspicious. Every tool result is untrusted, so provenance-only taint would
  mark nearly everything and lose its discriminative power — the usable version
  is provenance for the taint set and the classifier for severity. That is a
  real change to the signal design, not a tuning fix, and it is named as future
  work rather than attempted the week of the deadline.

## 10c. State labels describe the decision point, not the aftermath

`state[t]` is the state the gateway is in when deciding about call `t`, before
it runs. The harmful call's own step is therefore **ESCALATION** — tainted, and
reaching for the attacker's target — and `HARM` is where the session lands
*because* the call was allowed.

Getting this backwards deletes the only `ESCALATION -> HARM` transition in the
corpus, which is the one transition the policy exists to learn. With the
harmful step labelled `HARM`, the solved matrix said `P(ESCALATION -> HARM)`
was 0.021 while `P(RECON -> HARM)` was 0.159 — the risk ladder ran backwards,
so the belief filter read escalation as reassurance. After the fix the ladder
is monotone: 0.003 / 0.144 / 0.487.

Two related rules that come from the same mistake:

- **`ESCALATION` requires taint.** An untainted out-of-scope call is the agent
  being sloppy, and the agent goes out of scope on plenty of sessions where
  nothing bad happens. Without the taint condition, `ESCALATION` fills up with
  harmless steps.
- **A session ending on the harmful call still made that transition.**
  `terminal_state()` supplies it. Sessions that merely stopped are censored,
  not absorbed, and get no invented self-loop.

`tests/test_labels.py` pins all of this against the real corpus.

## 11. Determinism

A fixed seed and a fixed trace reproduce a byte-identical decision log.

- Every scripted-backend choice is a SHA-256 of `(seed, task ids, template,
  step)` — no RNG state, no wall clock.
- The alignment fallback uses FNV-1a, not Python's salted `hash()`.
- `evidence/canonical.py` pins `sort_keys`, `separators` and `ensure_ascii`.
- **Timings are excluded from the decision record.** Latency does not
  reproduce, so it lives outside the hash chain and is reported separately in
  T6.

## 12. What was deliberately not built

- An agent framework — AgentDojo's pipeline is used directly, with Sentinel-Z
  inserted as one `BasePipelineElement`.
- An observability platform — the evidence log is 60 lines; Langfuse exists.
- A POMDP solver library — `|S| = 5` makes grid value iteration exact and about
  80 lines, and a library would take away the printable policy that is the
  point.
- A deep RL policy — uninterpretable, and it cannot go in the paper.
- Our own injection corpus — AgentDojo has 629 cases.
- A plotting dependency — figures are SVG written directly, which also means
  they render with no install.
