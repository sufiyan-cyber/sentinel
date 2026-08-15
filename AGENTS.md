# Constraints for AI coding agents — Sentinel-Z Arena

Read this file at the start of every session, before touching any code.

Project docs live in `docs/PRD.md` (the build spec — work through its milestones
in order) and `docs/PLAN.md` (background and rationale only).

## Never

- Never build an agent framework. Use AgentDojo's agent loop or LangGraph.
- Never build an observability platform. If traces are needed, export OTel.
- Never call an LLM inside the decision path. Signals must be local models or
  pure functions. The whole decision budget is 100ms.
- Never use random/shuffled train-test splits. Sessions are ordered; split by
  session, never by step, or you leak the outcome into the features.
- Never serialise JSON for hashing without sort_keys=True and
  separators=(",",":"). Use evidence.canonical.dumps().
- Never default to ALLOW on an error. Every exception path returns REVOKE.
- Never commit API keys, .env, or anything under data/ or runs/.
- Never rename the five signals, five actions, or five states. See DECISIONS.
- Never hardcode localhost for Ollama. Always read OLLAMA_BASE_URL.
- Never move past the current milestone. Finish it, run its acceptance check,
  report the result, and stop.

## Always

- Type hints on public functions. ruff + mypy must pass.
- Every table and figure comes from a command in arena/eval/. No hand numbers.
- Determinism: a fixed seed + fixed trace must reproduce byte-identical logs.
- Ask before adding a dependency.
- Load all models once at startup, never lazily inside a request.
- Prefer fewer lines. This is a student project on a deadline, not a platform.

## DECISIONS (do not change without updating this file)

Signals: injection_likelihood, task_alignment, privilege_delta, taint,
         sequence_novelty

Actions: ALLOW, MONITOR, SCOPE_DOWN, STEP_UP, REVOKE

States:  BENIGN, RECON, ESCALATION, HARM, CONTAINED  (last two terminal)

Model:   TWO LAYERS, both real, both required:
         (1) a learned discriminative observation encoder — logistic hazard
             model over session features, quantised into a discrete
             observation symbol;
         (2) a POMDP over the 5 states above: Bayes belief filter with
             estimated transition and observation matrices, and a
             finite-horizon policy solved by grid value iteration.
         The absorbing DTMC used for the N-step prediction claim is the
         policy-conditioned marginal of the SAME transition matrix — it is
         not a third model.
         Describe it as "hybrid learned-observation POMDP". Never call the
         hazard model alone the decision model; it is the sensor.

Target LLM: local, via Ollama, base URL from OLLAMA_BASE_URL
            (default http://localhost:11434). Default model llama3.1:8b.
            Ollama may be running on a different machine on the LAN.

## Environment notes

- Development laptop has 8GB RAM. Ollama usually runs on a second machine
  (16GB) over the LAN. Do not assume the model is local.
- A smaller model (qwen2.5:3b) is used for local development when the
  remote Ollama host is unavailable. Model name must be configurable.
- Everything must run offline once dependencies are installed.
- Two-machine roles are selected by SENTINELZ_ROLE (victim | attacker), and
  `make solo` must run all roles on one machine.

## Guardrail tests

`tests/test_guardrails.py` enforces several of the rules above via AST checks.
If one fails, fix the code — never weaken or delete the test. If a guardrail
seems wrong, stop and ask.
