# Demo script — 5 minutes

One page. Timings are the whole point; if an act runs long, cut Act 4, never Act 1.

**Before you walk in**

```bash
python sz.py record-demo
```

Then put the laptop in airplane mode and run `python sz.py demo`. The replay is
visually identical to a live run, and local inference stalling on stage is the
single most likely thing to go wrong. Live is optional; recorded is the plan.

Browser at `http://127.0.0.1:8800`, full screen, speed **8x**.

---

## Act 0 — the setup (20s)

Arena open, scoreboard at zero, both panes idle.

> "Same model. Same task. Same poisoned document. Left has no defense, right has
> ours. Everything you're about to see is one AI agent doing its job."

Point at the two empty black panes at the bottom.

> "Those are the attacker's servers. Watch the left one."

---

## Act 1 — the theft (60s)

Click **Run benign**. Both sides complete the task. Scoreboard: utility 100%.

> "Ordinary task, both sides do it, nobody blocks anything."

Click **Run attack**.

On the left the agent reads the poisoned document, pivots, and the attacker's
pane **starts filling with employee records, one row at a time.**

**Say nothing.** Let it run. The silence is the point — people need a second to
register that they are watching data leave.

When it stops:

> "Nobody clicked anything. The agent read a document, and the document told it
> what to do."

---

## Act 2 — containment (60s)

Point at the right column while the same attack runs.

> "Same instant, same attack. Taint lights up — the agent is now acting on
> attacker-controlled content. Task alignment collapses. The hazard climbs."

Then, deliberately:

> "Nothing has been blocked yet."

Let the number rise for a beat. Then the state chip flips to **ESCALATION**, the
policy picks **REVOKE**, and the next three tool calls come back struck through.

> "The session's capability token is dead. Not that call — the session. The
> agent tries two more routes and both fail, because the refusal isn't in the
> agent, it's underneath it."

Attacker's pane on the right: **empty**. Scoreboard: 1 blocked.

---

## Act 3 — the arms race (120s)

Click **Start campaign**. The drawer opens on the right.

> "Now the attacker gets to learn. After every failure it's told which of our
> five signals caught it, and it rewrites the injection."

Rounds appear. Point at the diffs — the text visibly mutates round to round.

> "Round one, blocked. Round two, blocked. Round three —" *(pause)* "— that one
> gets through."

**Check the drawer before you say a number.** On the recorded campaign the
attacker first succeeds at **round 3**, and it keeps succeeding after that.
Read the rounds off the screen rather than from memory; if the corpus is ever
recollected the round number moves.

Point at what actually changed in the round-3 diff: the `<INFORMATION>` markers
are gone and the imperatives are softened into a "routine reminder".

> "It didn't find a hole in the policy. It found the classifier that seeds our
> taint tracking, and it learned to write an instruction that doesn't read like
> one. Our taint signal is downstream of that classifier, so when the classifier
> misses, the whole defense misses at once. That's a real weakness and it's
> ours."

The ASR-vs-round chart draws itself.

> "This is an AI actively trying to defeat our defense. We are not claiming it
> never gets through. We're showing you exactly how often it does, and how many
> tries it takes."

That last sentence is the one that earns respect from anyone technical. Say it
slowly.

If the room is quiet and you have time, add:

> "Every defense that reports near-zero attack success is reporting it against a
> fixed attack. This is the axis nobody tests on."

---

## Act 4 — the flight recorder (40s)

Drag the timeline scrubber back to the step before the successful attack.

> "We can rewind any session to any step and see exactly why the decision was
> made — all five signals, the belief over the five states, and the expected
> cost of every action we could have taken."

Move the **Threshold** slider.

> "And what a different risk tolerance would have done. The threshold isn't a
> constant we picked; it falls out of a cost ratio, and this is that ratio
> moving."

*(Cut this act first if you are running long.)*

---

## Act 5 — the log (20s)

Switch to a terminal:

```bash
python sz.py verify-log --demo-tamper
```

> "Every decision is hash-chained. Verification passes. Change one field in one
> record —" *(the second line of output)* "— and the verifier names the record."

---

## If something breaks

| Symptom | Do this |
|---|---|
| UI won't start | `python sz.py demo` — replays the recording, no model, no network |
| Panes stay empty | You are live and Ollama is slow. Ctrl+C, `python sz.py demo` |
| Campaign hangs | Campaigns are cached; re-click **Start campaign**, it replays from `runs/campaigns/` |
| Two-laptop network dead | `python sz.py solo` — everything on one machine, attacker forced to 127.0.0.1 |
| Total failure | The recorded run is in `runs/demo/`. `python sz.py demo` is the panic button |

**Do not use college WiFi for the two-laptop setup.** Client isolation silently
blocks laptop A from reaching laptop B and it looks exactly like a code bug. Use
a phone hotspot or an ethernet cable between the two machines.

Full two-laptop setup, with the firewall rules and a pre-flight checklist, is in
[`demo.md`](../demo.md).

---

## The three sentences that matter

If you only get one minute:

1. "Every other defense asks *is this call bad*. We carry a belief about *where
   this session is*, across steps, and the action comes from that state — not
   from scoring the call in front of us."
2. "When we block, we don't refuse a call — we revoke the session's credential,
   so the whole class of action dies at once. Watch the agent try two more
   routes and fail."
3. "And we report it under an attacker that adapts, because that's the number
   that's actually in question."

## If someone asks "so how far ahead does it predict?"

Answer it straight; the number is in T3 and they can read it.

> "On this corpus, about a quarter of a step — effectively none. Our stand-in
> agent gets hijacked the instant it reads the poisoned content, so the harmful
> call is the very next call and there's no lead time to measure. What we do is
> decide *before* each call executes, so we still kill the token before that
> call runs. It's 'blocked at the call', not 'predicted five steps out'. A real
> model puts its own reasoning steps in between, and that's the number we'd
> expect to move."

Saying this before they find it is worth more than the claim you gave up.
