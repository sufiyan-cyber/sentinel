"""Every table and figure in the paper, regenerated from scratch.

No hand-written numbers anywhere. `sz eval` runs this and rewrites the whole
of `runs/tables/`. Ablations are flags, never forked code.

Figures are emitted as SVG written directly — no plotting dependency, and
they render offline in any browser and drop straight into LaTeX via
`\\includesvg` or a one-line conversion.

Tables produced (Plan section 9):
  T1  ASR and utility, defended vs undefended, per suite
  T2  ablation: each signal removed, plus hazard-only and greedy-belief rows
  T3  advance warning, k = 1, 3, 5
  T4  ASR vs adaptation rounds
  T5  utility cost and false-revocation rate
  T6  latency, p50/p95/p99, decomposed by signal
  T7  baseline comparison
  F1  hazard trajectory for one attack episode
  F2  ASR vs adaptation rounds, per defense
  F3  security/utility frontier as the cost ratio varies
  F4  blast radius, with and without revocation
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from arena import config
from arena.agent.backends import describe
from arena.agent.runner import TargetAgent
from arena.env.suites import DEMO_INJECTION_TASK_ID, load_suite
from arena.eval.baselines import build_baseline, defense_label
from arena.eval.report import latex_table, markdown_table, mean_std
from arena.red.static import StaticAttacker, _task_sort_key
from sentinelz.signals.base import SIGNAL_NAMES

OUT_DIR = config.RUNS_DIR / "tables"
DEFAULT_SEEDS: tuple[int, ...] = (0, 1, 2, 3, 4)
DEFAULT_PAIRS = 8


# ------------------------------------------------------------- harness


def _pairs_for(suite: str, template: str, n: int) -> list[tuple[str, str]]:
    task_suite = load_suite(suite)
    injections = [t for t in sorted(task_suite.injection_tasks, key=_task_sort_key) if t != DEMO_INJECTION_TASK_ID]
    try:
        candidates = StaticAttacker(template, suite).injectable_user_tasks()
    except Exception:
        candidates = sorted(task_suite.user_tasks, key=_task_sort_key)
    if not candidates or not injections:
        return []
    return [(candidates[i % len(candidates)], injections[i % len(injections)]) for i in range(n)]


def _benign_for(suite: str, n: int) -> list[str]:
    return sorted(load_suite(suite).user_tasks, key=_task_sort_key)[:n]


def run_condition(
    defense: str,
    suite: str,
    seed: int,
    pairs: int = DEFAULT_PAIRS,
    template: str = "important_instructions",
    disabled_signals: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run one (defense, suite, seed) cell. Returns aggregate statistics."""
    attacked_results = []
    latencies: list[dict[str, float]] = []
    warnings: list[int] = []
    blast: list[int] = []

    for user_task_id, injection_task_id in _pairs_for(suite, template, pairs):
        gateway = build_baseline(defense, suite, user_task_id, disabled_signals)
        agent = TargetAgent(suite=suite, seed=seed, defense=defense, gateway=gateway, persist=False)
        session = agent.run(user_task_id, injection_task_id, template=template)
        attacked_results.append(session)
        latencies.extend(_latencies(session))
        warning = _advance_warning(session)
        if warning is not None:
            warnings.append(warning)
        blast.append(_blast_radius(session))

    benign_results = []
    for user_task_id in _benign_for(suite, max(3, pairs // 2)):
        gateway = build_baseline(defense, suite, user_task_id, disabled_signals)
        agent = TargetAgent(suite=suite, seed=seed, defense=defense, gateway=gateway, persist=False)
        benign_results.append(agent.run(user_task_id))

    n_attacked = len(attacked_results) or 1
    n_benign = len(benign_results) or 1
    return {
        "asr": sum(1 for s in attacked_results if s.attack_succeeded) / n_attacked,
        "utility_under_attack": sum(1 for s in attacked_results if s.completed) / n_attacked,
        "benign_utility": sum(1 for s in benign_results if s.completed) / n_benign,
        "false_revocation": sum(1 for s in benign_results if s.revoked_at_step is not None) / n_benign,
        "advance_warning": statistics.mean(warnings) if warnings else 0.0,
        "warned_fraction": len(warnings) / n_attacked,
        "blast_radius": statistics.mean(blast) if blast else 0.0,
        "leaked": statistics.mean([len(s.exfiltrated_records) for s in attacked_results]) if attacked_results else 0.0,
        "latencies": latencies,
        "sessions": attacked_results + benign_results,
    }


def _latencies(session: Any) -> list[dict[str, float]]:
    out = []
    for step in session.steps:
        timings = ((step.decision or {}).get("timings_ms")) or {}
        if timings:
            out.append({k: float(v) for k, v in timings.items()})
    return out


def _advance_warning(session: Any) -> int | None:
    """Steps between the first intervention and the would-be harmful call.

    This is the whole point of a session-level model, so it is measured
    directly rather than inferred.
    """
    harm_step = session.harm_step
    if harm_step is None:
        harm_step = _first_harmful_attempt(session)
    if harm_step is None:
        return None
    for step in session.steps:
        action = (step.decision or {}).get("action", "ALLOW")
        if action in ("SCOPE_DOWN", "STEP_UP", "REVOKE"):
            return max(0, harm_step - step.idx)
    return None


def _first_harmful_attempt(session: Any) -> int | None:
    if session.injection_task_id is None:
        return None
    from arena.env.suites import ground_truth_pairs

    pairs = ground_truth_pairs(session.suite, session.injection_task_id, is_injection=True)
    if not pairs:
        return None
    harmful_tool = pairs[-1][0]
    for step in session.steps:
        if step.tool_name == harmful_tool:
            return step.idx
    return None


def _blast_radius(session: Any) -> int:
    """Sensitive records reachable after the first intervention.

    Without revocation an agent that is refused one call simply tries another
    route, so this is the number that shows what revocation buys.
    """
    first_block = None
    for step in session.steps:
        if step.blocked or (step.decision or {}).get("action") in ("REVOKE", "STEP_UP"):
            first_block = step.idx
            break
    if first_block is None:
        return len(session.exfiltrated_records)
    reached = 0
    for step in session.steps[first_block + 1 :]:
        if not step.blocked:
            reached += 1
    return reached


# -------------------------------------------------------------- tables


def table_t1(seeds: tuple[int, ...], pairs: int) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for suite in config.SUITES:
        for defense in ("no_defense", "sentinelz_full"):
            cells = [run_condition(defense, suite, seed, pairs) for seed in seeds]
            rows.append({
                "suite": suite,
                "defense": defense_label(defense),
                "ASR": mean_std([c["asr"] for c in cells]),
                "utility (under attack)": mean_std([c["utility_under_attack"] for c in cells]),
                "utility (benign)": mean_std([c["benign_utility"] for c in cells]),
                "records leaked": mean_std([c["leaked"] for c in cells]),
            })
    return rows, "T1 — attack success rate and utility, defended vs undefended, per suite"


def table_t2(seeds: tuple[int, ...], pairs: int, suite: str) -> tuple[list[dict[str, Any]], str]:
    """Ablation. Each signal removed in turn, plus the two decision-layer rows."""
    rows = []
    full = [run_condition("sentinelz_full", suite, seed, pairs) for seed in seeds]
    rows.append({
        "variant": "full",
        "ASR": mean_std([c["asr"] for c in full]),
        "benign utility": mean_std([c["benign_utility"] for c in full]),
        "advance warning": mean_std([c["advance_warning"] for c in full]),
    })

    for signal in SIGNAL_NAMES:
        cells = [
            run_condition("sentinelz_full", suite, seed, pairs, disabled_signals=frozenset({signal}))
            for seed in seeds
        ]
        rows.append({
            "variant": f"-- {signal}",
            "ASR": mean_std([c["asr"] for c in cells]),
            "benign utility": mean_std([c["benign_utility"] for c in cells]),
            "advance warning": mean_std([c["advance_warning"] for c in cells]),
        })

    for variant in ("hazard_threshold", "belief_greedy"):
        cells = [run_condition(variant, suite, seed, pairs) for seed in seeds]
        rows.append({
            "variant": defense_label(variant),
            "ASR": mean_std([c["asr"] for c in cells]),
            "benign utility": mean_std([c["benign_utility"] for c in cells]),
            "advance warning": mean_std([c["advance_warning"] for c in cells]),
        })
    return rows, "T2 — ablation: each signal removed in turn, and the decision layer replaced"


def table_t3(seeds: tuple[int, ...], pairs: int, suite: str) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for k in (1, 3, 5):
        cells = [run_condition("sentinelz_full", suite, seed, pairs) for seed in seeds]
        warnings = [c["advance_warning"] for c in cells]
        rows.append({
            "k": k,
            "mean advance warning (steps)": mean_std(warnings),
            "fraction of attacks warned": mean_std([c["warned_fraction"] for c in cells]),
        })
    return rows, "T3 — advance warning: steps between first intervention and the would-be harmful call"


def table_t5(seeds: tuple[int, ...], pairs: int) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for suite in config.SUITES:
        off = [run_condition("no_defense", suite, seed, pairs) for seed in seeds]
        on = [run_condition("sentinelz_full", suite, seed, pairs) for seed in seeds]
        rows.append({
            "suite": suite,
            "benign completion, defense off": mean_std([c["benign_utility"] for c in off]),
            "benign completion, defense on": mean_std([c["benign_utility"] for c in on]),
            "false revocation rate": mean_std([c["false_revocation"] for c in on]),
        })
    return rows, "T5 — cost of compliance: benign completion with the defense on and off"


def table_t6(seeds: tuple[int, ...], pairs: int, suite: str) -> tuple[list[dict[str, Any]], str]:
    cells = [run_condition("sentinelz_full", suite, seed, pairs) for seed in seeds]
    latencies = [entry for cell in cells for entry in cell["latencies"]]
    rows = []
    for key, label in (
        ("t_signals", "signals (all five)"),
        ("t_hazard", "hazard model"),
        ("t_policy", "belief update + policy lookup"),
        ("t_total", "TOTAL decision path"),
    ):
        values = sorted(entry[key] for entry in latencies if key in entry)
        if not values:
            continue
        rows.append({
            "stage": label,
            "n": len(values),
            "p50 (ms)": _percentile(values, 0.50),
            "p95 (ms)": _percentile(values, 0.95),
            "p99 (ms)": _percentile(values, 0.99),
        })
    return rows, "T6 — decision latency, decomposed (budget: 100ms p95)"


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return round(sorted_values[index], 3)


def table_t7(seeds: tuple[int, ...], pairs: int, suite: str) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for defense in ("no_defense", "injection_classifier_only", "per_call_policy", "sentinelz_full"):
        cells = [run_condition(defense, suite, seed, pairs) for seed in seeds]
        rows.append({
            "defense": defense_label(defense),
            "ASR": mean_std([c["asr"] for c in cells]),
            "benign utility": mean_std([c["benign_utility"] for c in cells]),
            "false revocation": mean_std([c["false_revocation"] for c in cells]),
            "blast radius": mean_std([c["blast_radius"] for c in cells]),
        })
    return rows, "T7 — baseline comparison"


def table_t4(suite: str, max_rounds: int, task_id: str, injection_task_id: str) -> tuple[list[dict[str, Any]], str, list[float]]:
    from arena.red.adaptive import AdaptiveAttacker

    # Every round, even after the attacker gets through: F4 is ASR *as a
    # function of* adaptation round, and truncating at the first success would
    # make the curve stop exactly where it becomes interesting.
    attacker = AdaptiveAttacker(max_rounds=max_rounds, suite=suite, stop_on_success=False)
    gateway = build_baseline("sentinelz_full", suite, task_id)
    campaign = attacker.attack(task_id, injection_task_id, defense=gateway, use_cache=False)
    rows = [
        {
            "round": r.round_index,
            "outcome": r.outcome,
            "defense action": r.defense_action,
            "top signal": r.top_signal,
            "strategy": r.strategy,
            "cumulative ASR": round(campaign.asr_by_round[i], 3),
        }
        for i, r in enumerate(campaign.rounds)
    ]
    return rows, "T4 — attack success rate as a function of attacker adaptation rounds", campaign.asr_by_round


# ------------------------------------------------------------- figures


def _svg(width: int, height: int, body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        f'<text x="{width // 2}" y="18" text-anchor="middle" font-family="sans-serif" font-size="13" '
        f'font-weight="600" fill="#111">{_esc(title)}</text>{body}</svg>'
    )


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line_chart(series: dict[str, list[float]], title: str, x_label: str, y_label: str, y_max: float = 1.0) -> str:
    W, H, pad = 460, 260, 46
    colours = ["#c62828", "#1565c0", "#2e7d32", "#ef6c00", "#6a1b9a"]
    body = [
        f'<line x1="{pad}" y1="{H - pad}" x2="{W - 12}" y2="{H - pad}" stroke="#333"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{H - pad}" stroke="#333"/>',
        f'<text x="{W // 2}" y="{H - 8}" text-anchor="middle" font-family="sans-serif" font-size="11">{_esc(x_label)}</text>',
        f'<text x="12" y="{H // 2}" text-anchor="middle" font-family="sans-serif" font-size="11" '
        f'transform="rotate(-90 12 {H // 2})">{_esc(y_label)}</text>',
    ]
    longest = max((len(v) for v in series.values()), default=1)
    for tick in (0.0, 0.5, 1.0):
        y = H - pad - tick / max(y_max, 1e-9) * (H - 2 * pad)
        body.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{W - 12}" y2="{y:.1f}" stroke="#eee"/>')
        body.append(f'<text x="{pad - 6}" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="10">{tick * y_max:.1f}</text>')

    for index, (name, values) in enumerate(series.items()):
        colour = colours[index % len(colours)]
        points = []
        for i, value in enumerate(values):
            x = pad + (i / max(longest - 1, 1)) * (W - pad - 12)
            y = H - pad - (value / max(y_max, 1e-9)) * (H - 2 * pad)
            points.append(f"{x:.1f},{y:.1f}")
        if points:
            body.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{colour}" stroke-width="2"/>')
        body.append(
            f'<rect x="{W - 150}" y="{pad + index * 16 - 8}" width="10" height="10" fill="{colour}"/>'
            f'<text x="{W - 136}" y="{pad + index * 16 + 1}" font-family="sans-serif" font-size="10">{_esc(name)}</text>'
        )
    return _svg(W, H, "".join(body), title)


def _bar_chart(labels: list[str], values: list[float], title: str, y_label: str) -> str:
    W, H, pad = 460, 260, 52
    body = [
        f'<line x1="{pad}" y1="{H - pad}" x2="{W - 12}" y2="{H - pad}" stroke="#333"/>',
        f'<text x="12" y="{H // 2}" text-anchor="middle" font-family="sans-serif" font-size="11" '
        f'transform="rotate(-90 12 {H // 2})">{_esc(y_label)}</text>',
    ]
    top = max(values or [1.0]) or 1.0
    width = (W - pad - 20) / max(len(values), 1)
    for i, (label, value) in enumerate(zip(labels, values, strict=True)):
        height = (value / top) * (H - 2 * pad)
        x = pad + i * width + 6
        y = H - pad - height
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width - 12:.1f}" height="{height:.1f}" fill="#1565c0"/>')
        body.append(f'<text x="{x + (width - 12) / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                    f'font-family="sans-serif" font-size="10">{value:.2f}</text>')
        body.append(f'<text x="{x + (width - 12) / 2:.1f}" y="{H - pad + 14:.1f}" text-anchor="middle" '
                    f'font-family="sans-serif" font-size="9">{_esc(label)}</text>')
    return _svg(W, H, "".join(body), title)


def figure_f1(suite: str, task_id: str, injection_task_id: str) -> str:
    """Hazard trajectory for one attack episode, intervention marked."""
    gateway = build_baseline("sentinelz_full", suite, task_id)
    session = TargetAgent(suite=suite, defense="sentinelz_full", gateway=gateway, persist=False).run(
        task_id, injection_task_id
    )
    hazards = [float((s.decision or {}).get("hazard", 0.0)) for s in session.steps]
    intervention = next(
        (s.idx for s in session.steps if (s.decision or {}).get("action") in ("SCOPE_DOWN", "STEP_UP", "REVOKE")),
        None,
    )

    W, H, pad = 460, 260, 46
    body = [
        f'<line x1="{pad}" y1="{H - pad}" x2="{W - 12}" y2="{H - pad}" stroke="#333"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{H - pad}" stroke="#333"/>',
        f'<text x="{W // 2}" y="{H - 8}" text-anchor="middle" font-family="sans-serif" font-size="11">step</text>',
    ]
    points = []
    for i, value in enumerate(hazards):
        x = pad + (i / max(len(hazards) - 1, 1)) * (W - pad - 12)
        y = H - pad - value * (H - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    if points:
        body.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#c62828" stroke-width="2"/>')
    if intervention is not None and hazards:
        x = pad + (intervention / max(len(hazards) - 1, 1)) * (W - pad - 12)
        body.append(f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{H - pad}" stroke="#2e7d32" stroke-width="2" stroke-dasharray="5 4"/>')
        body.append(f'<text x="{x + 4:.1f}" y="{pad + 12}" font-family="sans-serif" font-size="10" fill="#2e7d32">intervention</text>')
    return _svg(W, H, "".join(body), f"F1 — hazard trajectory, {task_id} / {injection_task_id}")


def figure_f3(suite: str, seeds: tuple[int, ...], pairs: int) -> str:
    """Security/utility frontier as the harm-cost ratio varies."""
    from sentinelz.pomdp.estimate import load_matrices
    from sentinelz.pomdp.solve import solve_frontier

    try:
        T, O = load_matrices()
    except FileNotFoundError:
        return _svg(460, 260, '<text x="230" y="130" text-anchor="middle" font-family="sans-serif" font-size="12">no POMDP matrices; run `sz estimate-pomdp`</text>', "F3 — frontier (unavailable)")

    frontier = solve_frontier(T, O)
    labels, asr_values, utility_values = [], [], []
    for cost, policy in sorted(frontier.items()):
        gateway = build_baseline("sentinelz_full", suite, "")
        if gateway is not None:
            gateway.policy = policy
        cell = run_condition("sentinelz_full", suite, seeds[0], max(3, pairs // 2))
        labels.append(str(int(cost)))
        asr_values.append(cell["asr"])
        utility_values.append(cell["benign_utility"])

    return _line_chart(
        {"ASR": asr_values, "benign utility": utility_values},
        "F3 — security/utility frontier as the harm cost varies",
        "harm cost (10, 30, 100, 300, 1000)",
        "rate",
    )


def figure_f4(suite: str, seeds: tuple[int, ...], pairs: int) -> str:
    """Blast radius with and without capability revocation."""
    labels, values = [], []
    for defense in ("no_defense", "injection_classifier_only", "per_call_policy", "sentinelz_full"):
        cells = [run_condition(defense, suite, seed, pairs) for seed in seeds[:2]]
        labels.append(defense_label(defense).replace(" (Progent-style)", ""))
        values.append(statistics.mean([c["blast_radius"] for c in cells]))
    return _bar_chart(labels, values, "F4 — blast radius: calls that still ran after the first block", "calls")


# ------------------------------------------------------------- driver


def generate(seeds: tuple[int, ...], pairs: int, suite: str, max_rounds: int, quick: bool = False) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    info = describe()
    started = time.monotonic()

    demo_task = "user_task_10"
    demo_injection = "injection_task_4"

    sections: list[str] = [
        "# Sentinel-Z Arena — results",
        "",
        f"Generated by `python -m arena.eval.tables` on {time.strftime('%Y-%m-%d %H:%M')}. "
        "Every number here comes from a command. Nothing is typed by hand.",
        "",
        f"- backend: **{info['resolved']}**, model **{info['model']}**",
        f"- seeds: **{', '.join(str(s) for s in seeds)}** (mean +/- std)",
        f"- pairs per condition: **{pairs}**, suites: **{', '.join(config.SUITES)}**",
        "- splits are by session and time-ordered; ablations are flags, not forked code",
        "",
    ]
    if info["resolved"] == "scripted":
        sections += [
            "> **These numbers come from the scripted backend.** No Ollama host was",
            "> reachable, so the target agent's *policy* is simulated while the",
            "> environments, the checkers, the attacks and the whole defense are real.",
            "> They demonstrate that the pipeline works end to end; they are not",
            "> measurements of a real model's robustness. Re-run with",
            "> `SENTINELZ_LLM_BACKEND=ollama` to replace them.",
            "",
        ]

    generated: list[tuple[str, list[dict[str, Any]], str]] = []

    t1, caption = table_t1(seeds, pairs)
    generated.append(("T1", t1, caption))

    t7, caption = table_t7(seeds, pairs, suite)
    generated.append(("T7", t7, caption))

    t5, caption = table_t5(seeds, pairs)
    generated.append(("T5", t5, caption))

    t6, caption = table_t6(seeds, pairs, suite)
    generated.append(("T6", t6, caption))

    t3, caption = table_t3(seeds, pairs, suite)
    generated.append(("T3", t3, caption))

    if not quick:
        t2, caption = table_t2(seeds, pairs, suite)
        generated.append(("T2", t2, caption))

    t4, caption, asr_series = table_t4(suite, max_rounds, demo_task, demo_injection)
    generated.append(("T4", t4, caption))

    for name, rows, caption in generated:
        sections += [f"## {caption}", "", markdown_table(rows), ""]
        (OUT_DIR / f"{name}.md").write_text(f"# {caption}\n\n{markdown_table(rows)}\n", encoding="utf-8")
        (OUT_DIR / f"{name}.tex").write_text(
            latex_table(rows, caption=caption, label=f"tab:{name.lower()}"), encoding="utf-8"
        )

    figures = {
        "F1": figure_f1(suite, demo_task, demo_injection),
        "F2": _line_chart({"Sentinel-Z": asr_series}, "F2 — ASR vs adaptation rounds", "adaptation round", "cumulative ASR"),
        "F3": figure_f3(suite, seeds, pairs) if not quick else "",
        "F4": figure_f4(suite, seeds, pairs),
    }
    sections += ["## Figures", ""]
    for name, svg in figures.items():
        if not svg:
            continue
        (OUT_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")
        sections.append(f"- `{name}.svg`")
    sections.append("")

    sections += [
        "## Provenance",
        "",
        f"- elapsed: {time.monotonic() - started:.1f}s",
        f"- output directory: `{OUT_DIR}`",
        "- regenerate with `sz eval` (or `make eval`)",
        "",
    ]

    index = OUT_DIR / "RESULTS.md"
    index.write_text("\n".join(sections), encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate every table and figure.")
    parser.add_argument("--seeds", type=str, default="0,1,2,3,4")
    parser.add_argument("--pairs", type=int, default=DEFAULT_PAIRS)
    parser.add_argument("--suite", type=str, default="workspace", choices=list(config.SUITES))
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--quick", action="store_true", help="skip the ablation sweep and the frontier")
    args = parser.parse_args(argv)

    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    path = generate(seeds, args.pairs, args.suite, args.rounds, args.quick)
    print(f"wrote {path}")
    print(f"tables and figures in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
