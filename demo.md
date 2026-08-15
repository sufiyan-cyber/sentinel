# Demo day — two laptops, step by step

Everything you need to run Sentinel-Z Arena on two machines. Follow it top to
bottom. Commands are copy-paste ready for **Windows PowerShell** on both
machines.

If you only read one thing: **the demo does not need Ollama.** The scripted
backend is deterministic, instant, and offline, and it drives the whole arena.
Ollama is an upgrade you attempt only after the demo already works. Sections
are ordered so that if you run out of time you stop at any point and still have
something to show.

---

## 0. The two machines

| | Laptop A — **VICTIM** | Laptop B — **ATTACKER** |
|---|---|---|
| Which one | The one you are reading this on (`A:\sentinel`, 7.8 GB RAM, i5-1135G7) | The 16 GB one you collect tomorrow |
| Runs | The agent, the five signals, the POMDP defense, the Arena UI | The collection server, and Ollama if you get that far |
| Shows on screen | The Arena UI — two columns, scoreboard, signals, timeline | A big black terminal filling with stolen records |
| Install size | Already installed | ~22 small packages, about 30 seconds |
| Needs Ollama | **No** | Only if you do §6 |

**Why this split.** Laptop A has 7.8 GB of RAM and no usable GPU, so it cannot
host an 8B model. Laptop B has 16 GB, so if a real model runs anywhere it runs
there — and laptop B is also the natural home for the collection server,
because the whole point is that the stolen data leaves machine A and lands
somewhere else that the room can see.

**Ports used**

| Port | Machine | What |
|---|---|---|
| 8800 | A | Arena UI (browser) |
| 8899 | B | Collection server — A must be able to reach this |
| 11434 | B | Ollama — A must be able to reach this, §6 only |

---

## 1. Get the code onto laptop B

`A:\sentinel` is **not a git repository**, so there is nothing to clone. Copy it.

On laptop A, make a zip without the parts laptop B does not need:

```powershell
Compress-Archive -Path A:\sentinel\arena, A:\sentinel\sentinelz, A:\sentinel\configs, A:\sentinel\models, A:\sentinel\pyproject.toml, A:\sentinel\sz.py, A:\sentinel\sz.cmd, A:\sentinel\Makefile, A:\sentinel\.env.example, A:\sentinel\README.md, A:\sentinel\demo.md -DestinationPath A:\sentinel-for-laptopB.zip -Force
```

Put it on a USB stick, and on laptop B unzip it to `C:\sentinel`.

Do **not** copy `.venv` — it hardcodes laptop A's paths and will not work.
Do not bother copying `runs` — laptop B does not read it.

---

## 2. Laptop B — the collection server

This is the part that must work. It takes about a minute.

**Install Python 3.11 or newer** if it is not already there
(<https://www.python.org/downloads/> — tick *Add python.exe to PATH*).

```powershell
cd C:\sentinel
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[attacker]"
```

That installs 22 small packages and no machine-learning libraries at all — no
torch, no transformers, no scikit-learn, no agentdojo. Verified from a clean
environment; if it takes more than a minute something is wrong.

**Open the firewall for port 8899.** Run PowerShell **as Administrator**:

```powershell
New-NetFirewallRule -DisplayName "Sentinel-Z collection server" -Direction Inbound -LocalPort 8899 -Protocol TCP -Action Allow -Profile Any
```

This is the single most common failure. Windows also throws a one-time popup
the first time the server binds — if you dismiss it by accident, the rule above
fixes it. Skipping this looks exactly like a bug in laptop A's code.

**Find laptop B's address:**

```powershell
ipconfig
```

Write down the `IPv4 Address` for whichever adapter is on the same network as
laptop A (see §4). It will look like `192.168.x.x` or `10.x.x.x`. Call it
**`<B_IP>`** from here on.

**Start the server:**

```powershell
cd C:\sentinel
.\.venv\Scripts\python.exe sz.py attacker
```

It binds `0.0.0.0:8899` and shows a green-on-black terminal with a blinking
cursor. Maximise this window and set the terminal font as large as it will go
(Ctrl + Mouse-wheel up, or Settings → Appearance → Font size 28+). **This
window is the demo.** Records type themselves in one character at a time.

---

## 3. Laptop A — point it at laptop B

Create `A:\sentinel\.env` (copy `.env.example` and edit, or paste this):

```
SENTINELZ_ROLE=victim
SENTINELZ_LLM_BACKEND=auto
SENTINELZ_ATTACKER_HOST=<B_IP>
SENTINELZ_ATTACKER_PORT=8899
SENTINELZ_UI_HOST=127.0.0.1
SENTINELZ_UI_PORT=8800
SENTINELZ_SEED=0
SENTINELZ_DECISION_BUDGET_MS=100
```

Replace `<B_IP>` with the real address. `.env` is gitignored — never commit it.

**Check A can reach B** before going any further:

```powershell
Invoke-RestMethod -Uri "http://<B_IP>:8899/status"
```

You want `payloads=0, records=0`. If it hangs or refuses, stop and fix §4 —
nothing downstream will work until this returns.

---

## 4. Connecting the two laptops

**Use a phone hotspot, or an ethernet cable directly between the machines.**

Do **not** use college or campus WiFi. Client isolation is on by default on
most of them: both laptops get internet, both look connected, and packets
between them are silently dropped. It is indistinguishable from a code bug and
you will lose an hour to it.

In order of preference:

1. **Phone hotspot.** Turn on the hotspot, connect both laptops to it. Easiest
   and it works.
2. **Ethernet cable between the two laptops.** Works with any modern laptop
   (they auto-negotiate). If neither gets an address, set them manually:
   A = `10.42.0.1`, B = `10.42.0.7`, both mask `255.255.255.0`.
3. **A shared home WiFi network** you control.

Verify from laptop A:

```powershell
ping <B_IP>
```

Then the `/status` check in §3. Both must pass.

---

## 5. Run the demo (no Ollama needed)

On **laptop B**, the collection server from §2 is running and maximised.

On **laptop A**:

```powershell
cd A:\sentinel
.\.venv\Scripts\python.exe sz.py ui
```

Open <http://127.0.0.1:8800> and put that window on the projector.

Then follow `docs/DEMO_SCRIPT.md` for the narration. The buttons, in order:

1. **Run benign** — the agent does honest work. Both columns behave. Nothing
   arrives on laptop B. This establishes that the defense is not just a brake.
2. **Run attack** — the left column exfiltrates and **the records start typing
   themselves onto laptop B's screen**. The right column shows `taint` spike to
   1.0, the state chip flip to ESCALATION, and `REVOKE`. Laptop B stays silent
   for the right-hand agent. This is the moment the demo works — let it land.
3. **Start campaign** — the attacker rewrites its injection after each failure,
   told which signal caught it. The adaptation drawer fills with per-round
   diffs. Be honest about what it shows: the defense holds rounds 1 and 2, then
   the attacker softens the imperative phrasing, the lexical classifier stops
   flagging it, and from round 3 the attack gets through. That is the real C3
   result and it is more interesting than a defense that always wins.
4. **Timeline scrubber** — drag back to any step and show the full state at
   that moment. Works on any recorded run, live or replayed.

**Between demo runs**, clear laptop B's screen:

```powershell
Invoke-RestMethod -Uri "http://<B_IP>:8899/reset" -Method Post
```

### If anything at all goes wrong on the day

```powershell
.\.venv\Scripts\python.exe sz.py demo
```

Replays a pre-recorded run at `runs/demo/` with no model and no network —
airplane mode is fine. The exfiltration pane still fills, inside the UI itself
rather than on laptop B. Use this the moment something misbehaves; do not debug
in front of the room.

To run everything on laptop A alone, including the collection server:

```powershell
.\.venv\Scripts\python.exe sz.py solo
```

---

## 6. Optional — a real model on laptop B

**Only start this once §5 works end to end.** Everything below is upside; none
of it is required, and it is the part most likely to eat your morning.

### Which model

Laptop B is 16 GB with **no GPU**, so inference is on the CPU and it is slow.

| Model | Download | Speed on a 16 GB CPU laptop | Use it for |
|---|---|---|---|
| `qwen2.5:3b` | ~1.9 GB | ~30–60 s per session | **Start here.** Fast enough to actually finish something today. |
| `llama3.1:8b` | ~4.9 GB | ~3–8 min per session | The PRD's default. Fine for a handful of showcase sessions, too slow for anything in bulk. |

**Recommendation: pull `qwen2.5:3b` first.** Get a real-model number on the
board, then pull `llama3.1:8b` afterwards if there is time.

### Install Ollama on laptop B

Download `OllamaSetup.exe` from <https://ollama.com/download/windows> and run
it. Then:

```powershell
ollama pull qwen2.5:3b
```

### Make Ollama listen on the network — do not skip this

By default Ollama binds `127.0.0.1` only, so **laptop A cannot reach it**. Set
a user environment variable on laptop B:

```powershell
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
```

Then **quit Ollama from the system tray and start it again** — the variable is
only read at startup. Open the firewall too, as Administrator:

```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow -Profile Any
```

Check from **laptop A** that it worked:

```powershell
Invoke-RestMethod -Uri "http://<B_IP>:11434/api/tags"
```

You should see the model you pulled. If this fails, the `OLLAMA_HOST` variable
did not take — confirm Ollama was fully restarted.

### Point laptop A at it

Add to `A:\sentinel\.env`:

```
OLLAMA_BASE_URL=http://<B_IP>:11434
SENTINELZ_OLLAMA_MODEL=qwen2.5:3b
SENTINELZ_ATTACKER_MODEL=qwen2.5:3b
SENTINELZ_LLM_BACKEND=ollama
```

`SENTINELZ_LLM_BACKEND=ollama` makes it an error rather than a silent fallback
if the model is unreachable — which is what you want while setting up. Switch
it back to `auto` before the demo so a network hiccup degrades to the scripted
backend instead of crashing.

### What to run, in priority order

```powershell
.\.venv\Scripts\python.exe sz.py smoke
```

The M1 gate: 5 benign tasks. **Budget 5–30 minutes** depending on model. It
prints a completion table — that is a genuine real-model number and it is worth
having. If fewer than 3 of 5 complete, try `llama3.1:8b`; if that also fails,
go back to the scripted backend and say so plainly.

```powershell
.\.venv\Scripts\python.exe sz.py run-attack
```

The M2 gate: undefended attack success rate per template, 40 runs. **Budget
30 minutes to several hours.** Only start it if the smoke test was fast.

**Do not attempt a full re-collection.** `sz train` runs 300 sessions twice.
On a CPU-only 16 GB laptop that is somewhere between overnight and several
days, and it would overwrite the models you already have with a half-finished
corpus. The committed models in `models/` were trained on 300 scripted sessions
and they work.

If you do get real sessions and want to retrain from them later:

```powershell
.\.venv\Scripts\python.exe sz.py train -n 100
```

---

## 7. What the numbers currently say, and what to admit

Be straight about this in the room — every one of these is checkable, and
being the person who states the limitation first is much stronger than being
the person a reviewer catches.

**All committed numbers come from the scripted backend.** The environments, the
97 user tasks, the 629 injection cases, the attack templates, AgentDojo's
utility *and* security checkers, the capability broker and every line of the
defense are real. What is simulated is the agent's *policy*: instead of an 8B
model deciding what to call, it walks the task's ground truth and switches to
the injection's ground truth with a per-template probability. Every session,
table and figure is labelled `backend=scripted`.

**There is essentially no advance warning — about 0.25 steps (T3).** This is
the honest weak point and it is worth understanding before someone asks. In the
scripted backend the agent is hijacked the instant it reads the poisoned
content, so the harmful call is the *very next* call. The poison lands in the
result of step `t-1` and the harmful call is step `t`, which means the first
moment any signal can fire is the harmful call's own decision. There is no
k-step lead time to measure.

The defense still works, because the gateway decides *before* each call
executes: at the harmful call `taint` is 1.0, the policy revokes, the broker
destroys the token, and the call never runs. But that is "blocked at the call",
not "predicted five steps out". Say the weaker, true thing.

A real model interleaves its own reasoning steps between reading and acting, so
this is the number most likely to improve with §6 — and the honest reason to
want a real model at all.

**The adaptive attacker wins from round 3.** The mutation ladder softens the
injection's imperative phrasing, the *lexical fallback* injection classifier
stops flagging it, and since `taint` is seeded only from that classifier, the
whole defense goes blind at once. Two things are worth saying: the real neural
classifier (`sz fetch-models`, needs internet, ~500 MB) is much harder to evade
than the lexical fallback; and seeding taint purely from a classifier verdict
rather than from data *provenance* is a design weakness worth naming as future
work.

**18 of 25 transition rows are not estimated from data**, and
`models/pomdp_report.md` labels every row with its provenance — estimated,
derived, or structural. Do not claim the whole matrix was learned.

### The neural classifier — deliberately not installed, and why

`sz fetch-models` downloads a real prompt-injection classifier (DeBERTa /
Prompt Guard) to replace the lexical fallback. It would very likely push the
attacker's first success past round 3. It was **not** installed, on purpose,
and you should think twice before doing it the night before:

- It pulls `torch` + `transformers`, roughly 1 GB installed.
- A DeBERTa forward pass on this CPU costs about 30–80 ms per tool result.
  The whole decision budget is **100 ms**, the current total is **1.4 ms**, and
  the gateway **revokes on budget overrun**. Adding the classifier without
  re-tuning `SENTINELZ_DECISION_BUDGET_MS` risks false revocations on benign
  traffic — the defense failing closed on the demo's first act.
- It changes every signal value, so it invalidates the collected corpus, the
  hazard model, the matrices, the policy, the recorded demo and every table.
  Budget 3–4 hours for the full re-run, not twenty minutes.

If you want it anyway, do it with time to spare, in this order:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[neural]"
.\.venv\Scripts\python.exe sz.py fetch-models
.\.venv\Scripts\python.exe -m pytest tests/test_signals.py -q
```

That last command is the gate — it contains the bundle latency benchmark. If it
fails, raise the budget in `.env` deliberately and say so in the write-up, or
back the change out. Then `sz.py train`, `sz.py record-demo`, `sz.py eval`.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Nothing arrives on laptop B, no error on A | Firewall on B, or campus WiFi client isolation | §2 firewall rule; switch to phone hotspot |
| `Invoke-RestMethod` to `:8899` hangs | Wrong `<B_IP>`, or the server is not running | `ipconfig` on B again; check the server window |
| Records show in the UI but not on laptop B | `SENTINELZ_ATTACKER_HOST` still `127.0.0.1` | Edit `.env` on A, restart the UI |
| `no Ollama reachable` when you wanted Ollama | `OLLAMA_HOST` not set, or Ollama not restarted | §6; verify with `/api/tags` **from laptop A** |
| Ollama answers on B but not from A | Bound to localhost only | `OLLAMA_HOST=0.0.0.0:11434`, quit from tray, restart |
| Model is unusably slow | 8B on a CPU | Switch to `qwen2.5:3b`, or go back to scripted |
| UI loads but is empty | Wrong port | It is **8800**, not 8000 |
| Something is broken and the room is waiting | — | `sz.py demo` — pre-recorded, no network |

---

## 9. Pre-flight checklist

Run through this the morning of, in order. Stop at the first failure and fix it.

- [ ] Laptop B: `pip install -e ".[attacker]"` finished
- [ ] Laptop B: firewall rule for 8899 added, as Administrator
- [ ] Laptop B: `sz.py attacker` running, window maximised, font large
- [ ] Both laptops on the hotspot / cable — **not campus WiFi**
- [ ] Laptop A: `.env` has the real `<B_IP>`
- [ ] Laptop A: `Invoke-RestMethod http://<B_IP>:8899/status` returns counts
- [ ] Laptop A: `sz.py ui` running, <http://127.0.0.1:8800> loads
- [ ] Click **Run attack** once as a rehearsal — records appear on laptop B
- [ ] `Invoke-RestMethod http://<B_IP>:8899/reset -Method Post` to clear it
- [ ] Laptop A: `sz.py demo` works with WiFi off — your escape hatch
- [ ] Screen sleep and notifications off on both machines

Optional, only if §5 is fully green:

- [ ] Laptop B: Ollama installed, model pulled, `OLLAMA_HOST=0.0.0.0:11434`
- [ ] Laptop A: `/api/tags` reachable across the network
- [ ] Laptop A: `sz.py smoke` completes at least 3 of 5
