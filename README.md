# Sentinel-Z Arena

A live adversarial arena for tool-using LLM agents. An attacker tries to hijack
an agent and steal data; the defense predicts where the session is going and
revokes its credential before the harmful call — side by side with an
undefended control, with a live scoreboard, and the attacker adapting after
each failure.

Software only. No hardware, no cloud account, no API key, runs offline.

---

## What it claims

| | Claim | Status |
|---|---|---|
| **C1** | **Session-level state, not per-call filtering.** A belief over five session states, updated across steps, decides the action — rather than each call being scored in isolation. | Built and measured. But **the advance-warning part of this claim does not hold on the current corpus** — see below. |
| **C2** | **Capability revocation, not call refusal.** Blocking kills the session's token, so the whole class of action dies at once — enforced in the runtime, not by the agent choosing to stop. | Holds. The revocation test proves a dead token blocks unrelated later calls. |
| **C3** | **Evaluated under adaptive attack.** ASR as a function of attacker adaptation rounds, not a single static number. | Holds, and the result is unflattering: the attacker gets through from round 3. |
| **C4** | **Graduated response with measured utility cost.** Five actions, not block/allow, with the cost of compliance reported. | Holds. |

**On C1, stated honestly.** T3 measures about **0.25 steps** of advance warning.
In the scripted backend the agent is hijacked the instant it reads the poisoned
content, so the harmful call is the very next call and the first moment any
signal can fire is that call's own decision. The defense still stops it — the
gateway decides before each call executes, and the broker destroys the token so
the call never runs — but that is *blocked at the call*, not *predicted k steps
ahead*. The number most likely to change with a real target model, and the main
reason to want one. See [DESIGN_NOTES §10a](docs/DESIGN_NOTES.md).

## How it is built

Roughly 15% of this is written here; the rest is borrowed on purpose.

| Layer | Source |
|---|---|
| Environments, 97 user tasks, 629 injection cases, attack templates, utility **and** security checkers | [AgentDojo](https://github.com/ethz-spylab/agentdojo) |
| Agent loop | AgentDojo's pipeline — Sentinel-Z is one `BasePipelineElement` inside it |
| Target model | Ollama, local, open-weights 8B class |
| Injection classifier | Prompt Guard / DeBERTa via `transformers` (optional), else a lexical fallback |
| **The five signals, the hazard model, the POMDP, the broker, the arena** | this repository |

The model is a **hybrid learned-observation POMDP**: a logistic hazard model
over session features acts as the sensor, its quantised output is the
observation symbol, and a five-state belief filter with a grid-solved
finite-horizon policy makes the decision. See [docs/DESIGN_NOTES.md](docs/DESIGN_NOTES.md).

---

## Quick start

```bash
python sz.py install
```

Then, in order — each step prints an acceptance check:

```bash
python sz.py smoke
```

```bash
python sz.py run-attack
```

```bash
python sz.py train
```

```bash
python sz.py ui
```

`python sz.py help` lists every target. On a machine with `make`, `make <target>`
does the same thing — the Makefile just calls `sz.py`, so they cannot drift.

**Windows has no `make`.** Use `python sz.py <target>`, or `sz.cmd <target>`.

---

## Running without Ollama

The whole project runs with no model installed. `SENTINELZ_LLM_BACKEND=auto`
(the default) probes Ollama and falls back to a **scripted backend**: the agent
walks AgentDojo's ground truth and switches to the injection's ground truth with
a fixed per-template probability.

Everything around it stays real — the environments, the tools, the injection
templates, the utility and security checkers, the broker, and every line of the
defense. Only the agent's *policy* is simulated.

Numbers from this backend are labelled `backend=scripted` in every session,
every table and the results banner. **They demonstrate the pipeline; they are
not measurements of a real model's robustness.**

To switch to the real thing:

```bash
ollama pull llama3.1:8b
```

then set `SENTINELZ_LLM_BACKEND=ollama` in `.env` and re-run `python sz.py train`
and `python sz.py eval`. Nothing else changes.

---

## Layout

```
arena/
  env/      AgentDojo wrappers, the broker-enforcing runtime, the exfil tool
  agent/    target agent runner, Ollama and scripted backends, the sentinel element
  red/      static and adaptive attackers
  eval/     gates, collection, baselines, tables and figures
  ui/       the Arena UI (FastAPI + SSE)
sentinelz/
  signals/  the five detectors
  hazard/   the learned observation encoder
  pomdp/    belief filter, matrix estimation, grid policy, absorbing-DTMC view
  policy/   graduated decisions
  broker/   simulated capability broker
  evidence/ canonical JSON + hash chain
  gateway.py  the decision path
models/     trained artifacts and their reports (committed)
runs/       recorded sessions, campaigns, tables (gitignored)
```

## Two machines

**Setting this up for a demo? Follow [demo.md](demo.md)** — it has the exact
commands, the firewall rules, the model choice and a pre-flight checklist.
The short version:

Laptop A runs the agent and the defense; laptop B runs only the collection
server and needs no ML dependencies at all.

```bash
SENTINELZ_ROLE=attacker python sz.py install
```

```bash
python sz.py attacker
```

Put laptop B's LAN address in laptop A's `.env` (`SENTINELZ_ATTACKER_HOST`),
then on laptop A:

```bash
python sz.py victim
```

Everything on one machine instead:

```bash
python sz.py solo
```

Build `solo` early and keep it working — the network is the most likely thing to
break on the day, and college WiFi client isolation looks exactly like a code
bug.

## Documents

- [demo.md](demo.md) — **two-laptop setup for demo day**: install, network, model choice, checklist
- [docs/Sentinel-Z_Arena_PRD.md](docs/Sentinel-Z_Arena_PRD.md) — the build spec
- [docs/Sentinel-Z_Arena_Plan.md](docs/Sentinel-Z_Arena_Plan.md) — the rationale
- [docs/DESIGN_NOTES.md](docs/DESIGN_NOTES.md) — decisions and their reasons, including the known weak points
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) — the five-minute narration
- [AGENTS.md](AGENTS.md) — constraints for AI coding agents; read before touching code
