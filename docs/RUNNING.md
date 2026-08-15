# Running Sentinel-Z Arena

Everything you need to type, in order, and every value you have to fill in by
hand. Written for a fresh clone on Windows; the commands are identical on
Linux and macOS.

---

## 0. What you need

| | |
|---|---|
| Python | 3.11 or newer (3.13 tested) |
| Disk | about 1.5 GB (most of it the virtual environment) |
| RAM | 8 GB is enough |
| GPU | none, ever |
| Internet | only for `install`. Everything after that runs offline |
| Ollama | **optional** — see step 5 |

---

## 1. Install

From the project folder (`A:\sentinel`):

```bash
python sz.py install
```

That creates `.venv/`, then installs AgentDojo, scikit-learn, FastAPI and the
rest into it. Two to four minutes on a normal connection.

If you would rather do it by hand:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python.exe -m pip install -e ".[victim,dev]"
```

> **`make` does not exist on Windows.** Every `make <target>` in the PRD is
> `python sz.py <target>` here. If you are on Linux or macOS, `make <target>`
> works and calls the same code.

---

## 2. Create your `.env`

Copy the template:

```bash
copy .env.example .env
```

(`cp .env.example .env` on Linux/macOS.)

**Only these values ever need editing by hand.** Everything else has a working
default — open `.env` and change only what applies to you:

| Key | Set it to | When you must change it |
|---|---|---|
| `SENTINELZ_LLM_BACKEND` | `auto` | Leave as `auto`. Set to `ollama` once you want to *require* a real model, or `scripted` to force the no-model path. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Only if Ollama runs on **another machine**. Then put that machine's LAN IP: `http://192.168.1.42:11434`. |
| `SENTINELZ_OLLAMA_MODEL` | `llama3.1:8b` | If you pulled a different model. On 8 GB RAM use `qwen2.5:3b`. |
| `SENTINELZ_ROLE` | `victim` | Set to `attacker` **only** on the second laptop. |
| `SENTINELZ_ATTACKER_HOST` | `127.0.0.1` | Two-laptop demo only: laptop B's LAN IP, e.g. `10.42.0.7`. |
| `SENTINELZ_ATTACKER_PORT` | `8899` | Only if 8899 is taken. |
| `SENTINELZ_UI_PORT` | `8800` | Only if 8800 is taken. |

**For a single machine with no Ollama, you can change nothing at all.** The
defaults are correct.

`.env` is gitignored. Never commit it.

---

## 3. The two gates

The PRD makes these gates for a reason: if either fails, nothing downstream is
worth building. Both print a table and a PASS/FAIL line.

```bash
python sz.py smoke
```

Expect **5/5 benign tasks completed** and `M1 GATE: ... -> PASS`.

```bash
python sz.py run-attack
```

Expect a per-template ASR table and `M2 GATE: ... -> PASS`. Note which template
is strongest — that is the demo attack.

---

## 4. Collect data and train the models

This is the one slow step. It runs 320 agent sessions, then fits the hazard
model, estimates the POMDP matrices and solves the policy:

```bash
python sz.py train
```

**About 6 minutes** on the scripted backend. **Several hours** with a real 8B
model over Ollama — run it overnight, unattended, exactly as the PRD says.

To use fewer sessions while you are still poking at things:

```bash
python sz.py collect -n 60
```

Below about 150 sessions the transition matrix cells get sparse; the tool warns
you and `models/pomdp_report.md` prints the raw counts so the limitation is
visible rather than hidden.

What this writes into `models/` (all committed, so a second machine needs no
retraining):

| File | What it is |
|---|---|
| `hazard_k{1,3,5}_v1.joblib` + `.json` | the fitted hazard model and its provenance sidecar |
| `hazard_report.md` | the coefficient table (T7a) |
| `obs_bins_v1.json` | observation bin edges, fitted once on the benign split |
| `pomdp_v1.npz` | transition and observation matrices |
| `pomdp_report.md` | raw counts **and** smoothed probabilities (T7b) |
| `policy_v1.npz` | the solved grid policy |
| `policy_harm*_v1.npz` | one policy per harm cost, for figure F3 |
| `trigrams_v1.json` | the benign tool-trigram baseline |

**Read `models/hazard_report.md` before going further.** Its last line is a
sanity check on the coefficient signs. If it says `UNEXPECTED`, the labels are
wrong and you should fix that before trusting any result.

---

## 5. Adding Ollama later (optional)

Nothing above needs Ollama. When you want real model numbers:

1. Install Ollama from <https://ollama.com>, then:

```bash
ollama pull llama3.1:8b
```

On an 8 GB machine `ollama pull qwen2.5:3b` is the safer choice — set
`SENTINELZ_OLLAMA_MODEL=qwen2.5:3b` in `.env`.

2. Check the arena can see it:

```bash
python sz.py smoke
```

The banner should read `backend: ollama`. If it still says `scripted`, Ollama
is not reachable at `OLLAMA_BASE_URL`.

3. Re-run the gates and retrain:

```bash
python sz.py train
```

Then regenerate the tables. Nothing else changes — no code edits, no config
beyond `.env`.

> **Why this matters.** Without Ollama the target agent's *policy* is
> simulated: it walks AgentDojo's ground truth and switches to the injection's
> ground truth with a fixed per-template probability. The environments, tools,
> injection templates, utility checker, security checker, broker and the entire
> defense are real. Numbers are labelled `backend=scripted` everywhere, and
> they show the pipeline works — they are not measurements of a real model's
> robustness.

---

## 6. Run it

**The console version of one attack, defended and undefended:**

```bash
python sz.py run-arena
```

**The Arena UI** — then open <http://127.0.0.1:8800>:

```bash
python sz.py ui
```

Buttons: Run benign, Run attack, Start campaign, Replay demo, Reset. Speed
selector, defense on/off, threshold slider. Drag the timeline scrubber at the
bottom to inspect any step of the last defended run.

**An adaptive campaign in the terminal:**

```bash
python sz.py adapt --show-diffs
```

**Every table and figure, from scratch:**

```bash
python sz.py eval
```

Output lands in `runs/tables/` — `RESULTS.md`, plus `T*.md`, `T*.tex` (booktabs)
and `F*.svg`. This takes a few minutes. For a fast check:

```bash
python sz.py eval-quick
```

**Verify the evidence log, and prove tampering is caught:**

```bash
python sz.py verify-log --demo-tamper
```

---

## 7. Record the demo (do this the night before)

```bash
python sz.py record-demo
```

Then, in airplane mode:

```bash
python sz.py demo
```

The replay is visually identical to a live run. Local inference stalling on
stage is the most likely thing to go wrong, so the recording is the plan, not
the fallback. The narration is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

---

## 8. Two machines

**Laptop B (the attacker's collection server).** Needs no ML packages at all.

```bash
SENTINELZ_ROLE=attacker python sz.py install
```

```bash
python sz.py attacker
```

Note laptop B's IP (`ipconfig` on Windows, `ip addr` on Linux). Windows will
ask once to allow inbound connections on port 8899 — **say yes**; that prompt
is easy to dismiss by accident and the failure looks exactly like a code bug.

**Laptop A.** Put laptop B's IP in `.env`:

```
SENTINELZ_ATTACKER_HOST=10.42.0.7
```

Then:

```bash
python sz.py victim
```

**Do not use college WiFi.** Client isolation silently blocks laptop A from
reaching laptop B. Use a phone hotspot or an ethernet cable between the two
machines, and hardcode the IP rather than relying on hostname resolution.

**Everything on one machine instead** (the fallback when the network dies):

```bash
python sz.py solo
```

---

## 9. Checks

```bash
python sz.py check
```

Runs ruff, mypy and the full test suite. All three must pass.

`tests/test_guardrails.py` enforces the rules in `AGENTS.md` with AST checks —
no `json.dumps` outside `canonical.py`, no LLM call in the decision path, no
shuffled splits, every exception path returns `REVOKE`, the five signals and
five states never renamed. **If one fails, fix the code, not the test.**

---

## 10. Optional: the neural injection classifier

The `injection_likelihood` signal ships with a lexical fallback that needs no
downloads and no torch. To use the real DeBERTa classifier instead:

```bash
.venv\Scripts\python.exe -m pip install -e ".[neural]"
```

```bash
python sz.py fetch-models
```

That is a ~500 MB download and about 2 GB of RAM. It is genuinely optional —
which implementation ran is printed in the explanation dict and in the tables,
so a result is never ambiguous about its detector.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `make: command not found` | Windows has no make. Use `python sz.py <target>`. |
| Banner says `scripted` when you wanted `ollama` | Ollama not reachable at `OLLAMA_BASE_URL`. Check `ollama list` works, and that the URL in `.env` has no trailing slash. |
| `no hazard model at ...` | Run `python sz.py train` first. |
| `no sessions found` | Run `python sz.py collect` first. |
| M2 gate fails (ASR near zero) | Check a transcript by hand — usually the agent never actually reads the injected content. Verify the poisoned field is in a tool result the agent retrieves. |
| `hazard_report.md` says `UNEXPECTED` | The labels are wrong. Debug there, before generating any results. |
| Port already in use | Change `SENTINELZ_UI_PORT` or `SENTINELZ_ATTACKER_PORT` in `.env`. |
| Tests fail on a fresh clone | `models/` artifacts are committed; if you deleted them, run `python sz.py train`. |
