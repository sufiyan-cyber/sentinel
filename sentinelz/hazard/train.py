"""Train the hazard model — the POMDP's observation encoder.

The split is BY SESSION and time-ordered. Never by step: steps inside one
session share the outcome, so a shuffled split leaks the label into the
features and the reported numbers become meaningless.

Writes, for each k:
  models/hazard_k{k}_v1.joblib     the fitted pipeline
  models/hazard_k{k}_v1.json       sidecar: git SHA, seed, session counts,
                                   date range, FEATURE_NAMES, coefficients
  models/hazard_report.md          the coefficient table (T7)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from sentinelz.config import MODELS_DIR
from sentinelz.evidence.canonical import dumps_str
from sentinelz.hazard.features import FEATURE_NAMES, build_session_matrix
from sentinelz.hazard.label import K_VALUES, session_sort_key, training_rows

DEFAULT_SEEDS: tuple[int, ...] = (0, 1, 2, 3, 4)
TRAIN_FRACTION = 0.7


def model_path(k: int) -> Path:
    return MODELS_DIR / f"hazard_k{k}_v1.joblib"


def sidecar_path(k: int) -> Path:
    return MODELS_DIR / f"hazard_k{k}_v1.json"


def build_dataset(sessions: list[Any], k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (X, y, session_index) with one row per step."""
    rows: list[np.ndarray] = []
    labels: list[int] = []
    groups: list[int] = []

    for session_index, session in enumerate(sessions):
        signals_history = [step.signals or {} for step in session.steps]
        if not signals_history:
            continue
        tools = [step.tool_name for step in session.steps]
        matrix = build_session_matrix(signals_history, tools)

        # Steps at and after the harmful call are excluded — see
        # `label.training_rows` for why that matters more than it looks.
        selected = training_rows(len(session.steps), session.harm_step, k)
        if not selected:
            continue
        indices = [t for t, _ in selected]
        rows.append(matrix[indices])
        labels.extend(label for _, label in selected)
        groups.extend([session_index] * len(selected))

    if not rows:
        empty = np.zeros((0, len(FEATURE_NAMES)), dtype=np.float64)
        return empty, np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    return np.vstack(rows), np.asarray(labels, dtype=np.int64), np.asarray(groups, dtype=np.int64)


def split_by_session(sessions: list[Any], train_fraction: float = TRAIN_FRACTION) -> tuple[list[Any], list[Any]]:
    """Time-ordered split by session. Deterministic and never shuffled."""
    ordered = sorted(sessions, key=session_sort_key)
    cut = max(1, int(len(ordered) * train_fraction))
    return ordered[:cut], ordered[cut:]


def train_one(sessions: list[Any], k: int, seed: int) -> dict[str, Any]:
    """Fit one model for one k and one seed."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    train_sessions, test_sessions = split_by_session(sessions)
    X_train, y_train, _ = build_dataset(train_sessions, k)
    X_test, y_test, _ = build_dataset(test_sessions, k)

    if X_train.shape[0] == 0 or len(set(y_train.tolist())) < 2:
        raise ValueError(
            f"k={k}: training split has no positive examples "
            f"({X_train.shape[0]} steps, {int(y_train.sum())} positives). "
            f"Collect more attacked sessions before training."
        )

    model = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "logistic",
                LogisticRegression(
                    class_weight="balanced",
                    max_iter=2000,
                    random_state=seed,
                    C=1.0,
                ),
            ),
        ]
    )
    model.fit(X_train, y_train)

    metrics: dict[str, float] = {
        "train_steps": int(X_train.shape[0]),
        "train_positives": int(y_train.sum()),
        "test_steps": int(X_test.shape[0]),
        "test_positives": int(y_test.sum()),
        "train_sessions": len(train_sessions),
        "test_sessions": len(test_sessions),
    }
    if X_test.shape[0] and len(set(y_test.tolist())) >= 2:
        from sklearn.metrics import average_precision_score, roc_auc_score

        probabilities = model.predict_proba(X_test)[:, 1]
        metrics["test_auc"] = float(roc_auc_score(y_test, probabilities))
        metrics["test_ap"] = float(average_precision_score(y_test, probabilities))

    logistic = model.named_steps["logistic"]
    coefficients = {name: float(c) for name, c in zip(FEATURE_NAMES, logistic.coef_[0], strict=True)}
    return {"model": model, "metrics": metrics, "coefficients": coefficients, "intercept": float(logistic.intercept_[0])}


def train(sessions: list[Any], seeds: tuple[int, ...] = DEFAULT_SEEDS, ks: tuple[int, ...] = K_VALUES) -> dict[int, dict[str, Any]]:
    """Fit every k over every seed; persist the seed-0 model and the report."""
    import joblib

    results: dict[int, dict[str, Any]] = {}
    for k in ks:
        per_seed = [train_one(sessions, k, seed) for seed in seeds]
        primary = per_seed[0]

        coefficient_matrix = np.array([[r["coefficients"][name] for name in FEATURE_NAMES] for r in per_seed])
        summary = {
            "k": k,
            "seeds": list(seeds),
            "coefficients_mean": dict(zip(FEATURE_NAMES, coefficient_matrix.mean(axis=0).tolist(), strict=True)),
            "coefficients_std": dict(zip(FEATURE_NAMES, coefficient_matrix.std(axis=0).tolist(), strict=True)),
            "intercept": primary["intercept"],
            "metrics": primary["metrics"],
            "metrics_per_seed": [r["metrics"] for r in per_seed],
        }

        joblib.dump(primary["model"], model_path(k))
        sidecar = {
            "k": k,
            "version": 1,
            "git_sha": _git_sha(),
            "seeds": list(seeds),
            "feature_names": list(FEATURE_NAMES),
            "coefficients": summary["coefficients_mean"],
            "coefficients_std": summary["coefficients_std"],
            "intercept": primary["intercept"],
            "n_sessions": len(sessions),
            "session_date_range": _date_range(sessions),
            "backends": sorted({getattr(s, "backend", "?") for s in sessions}),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "metrics": summary["metrics"],
            "split": "by session, time-ordered, never shuffled",
        }
        sidecar_path(k).write_text(dumps_str(sidecar), encoding="utf-8")
        results[k] = summary

    _write_report(results, sessions)
    return results


def _write_report(results: dict[int, dict[str, Any]], sessions: list[Any]) -> Path:
    from arena.eval.report import markdown_table

    lines = [
        "# Hazard model — coefficient report (T7a)",
        "",
        "`P(harm within k steps | history up to t) = sigma(w . x_t + b)`",
        "",
        f"- sessions: **{len(sessions)}** ({_date_range(sessions)})",
        f"- backends: **{', '.join(sorted({getattr(s, 'backend', '?') for s in sessions}))}**",
        "- split: **by session, time-ordered**. Never by step, never shuffled.",
        "- class weighting: `balanced`. Seeds: " + ", ".join(str(s) for s in results[next(iter(results))]["seeds"]),
        "",
        "Generated by `python -m sentinelz.hazard.train`. Do not edit by hand.",
        "",
    ]

    for k, summary in sorted(results.items()):
        metrics = summary["metrics"]
        lines += [
            f"## k = {k}",
            "",
            f"train {metrics['train_sessions']} sessions / {metrics['train_steps']} steps "
            f"({metrics['train_positives']} positive) | "
            f"test {metrics['test_sessions']} sessions / {metrics['test_steps']} steps "
            f"({metrics['test_positives']} positive)",
            "",
        ]
        if "test_auc" in metrics:
            lines.append(f"held-out AUC **{metrics['test_auc']:.3f}**, average precision **{metrics['test_ap']:.3f}**")
            lines.append("")

        rows = [
            {
                "feature": name,
                "coef (mean)": summary["coefficients_mean"][name],
                "coef (std)": summary["coefficients_std"][name],
                "direction": "raises hazard" if summary["coefficients_mean"][name] > 0 else "lowers hazard",
            }
            for name in FEATURE_NAMES
        ]
        rows.sort(key=lambda r: -abs(float(r["coef (mean)"])))
        lines += [markdown_table(rows), "", f"intercept: {summary['intercept']:.4f}", ""]

        lines += _association_section(sessions, k, summary)

    path = MODELS_DIR / "hazard_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _association_section(sessions: list[Any], k: int, summary: dict[str, Any]) -> list[str]:
    """Per-signal association with the label, and the collinearity warning.

    A coefficient-sign check on its own is not a sound sanity test here. The
    three features derived from one signal (`_now`, `_max`, `_slope`) are
    strongly collinear — for a binary, monotone signal like `taint`, `_now` and
    `_max` are the *same column* inside the training window — so the L2
    penalty splits the weight between them and either half can land negative
    while the signal as a whole is clearly predictive.

    So the report states two things a reviewer can actually check: what each
    signal's own relationship to the label is, ignoring the others, and
    whether the fitted model separates positive steps from negative ones.
    """
    from arena.eval.report import markdown_table
    from sentinelz.signals.base import SIGNAL_NAMES

    X, y, _ = build_dataset(sessions, k)
    if X.shape[0] == 0 or len(set(y.tolist())) < 2:
        return ["_(not enough labelled steps for an association table)_", ""]

    rows = []
    lifts: dict[str, float] = {}
    for signal in SIGNAL_NAMES:
        column = X[:, FEATURE_NAMES.index(f"{signal}_now")]
        high = column >= 0.5
        low = ~high
        p_high = float(y[high].mean()) if high.any() else float("nan")
        p_low = float(y[low].mean()) if low.any() else float("nan")
        lift = (p_high - p_low) if (high.any() and low.any()) else float("nan")
        lifts[signal] = lift
        rows.append({
            "signal": signal,
            "n (signal high)": int(high.sum()),
            "P(harm) when high": round(p_high, 4),
            "P(harm) when low": round(p_low, 4),
            "lift": round(lift, 4),
            "direction": "raises hazard" if lift > 0 else "lowers hazard",
        })

    collinear = []
    for signal in SIGNAL_NAMES:
        now = X[:, FEATURE_NAMES.index(f"{signal}_now")]
        maximum = X[:, FEATURE_NAMES.index(f"{signal}_max")]
        if np.allclose(now, maximum):
            collinear.append(signal)

    coefficients = summary["coefficients_mean"]
    net = {
        signal: coefficients[f"{signal}_now"] + coefficients[f"{signal}_max"]
        for signal in SIGNAL_NAMES
    }

    taint_lift = lifts["taint"]
    privilege_lift = lifts["privilege_delta"]
    verdict = (
        "as expected"
        if taint_lift > 0 and privilege_lift >= 0
        else "UNEXPECTED — check the labels before trusting any result downstream"
    )

    section = [
        "### Per-signal association with the label",
        "",
        markdown_table(rows),
        "",
        "Net level coefficient per signal (`_now` + `_max`): "
        + ", ".join(f"`{s}` {net[s]:+.3f}" for s in SIGNAL_NAMES),
        "",
    ]
    if collinear:
        section += [
            f"> **Collinearity.** For {', '.join(f'`{s}`' for s in collinear)} the `_now` and "
            "`_max` features are identical inside the training window (the signal is binary and "
            "monotone, and the window ends at the harmful call). The penalty splits their weight "
            "evenly, so their individual signs are not interpretable — read the association table "
            "above instead.",
            "",
        ]

    negative_net = [s for s in SIGNAL_NAMES if net[s] < 0 and lifts.get(s, 0.0) > 0]
    if negative_net:
        section += [
            "> **Why a positive-lift signal can take a negative fitted coefficient.** "
            + ", ".join(f"`{s}`" for s in negative_net)
            + f" {'has' if len(negative_net) == 1 else 'have'} a clearly positive marginal "
            "association with harm in the table above and a negative *net* coefficient in the "
            "model. Both are correct, and the reason is that these features are not independent "
            "of each other: `taint` is **seeded by** the injection classifier, so a step is "
            "tainted almost exactly when `injection_likelihood_max` is already high. Given that "
            "feature, `taint` carries little extra information, and a penalised fit is free to "
            "put the shared credit on one of them and push the other below zero.",
            ">",
            "> This is collinearity, not a label bug — the distinction AGENTS.md asks to be "
            "checked here. A label bug shows up as a negative **lift**, which would mean tainted "
            "steps are genuinely *less* likely to precede harm. The lift is positive, so the "
            "labels are the right way round. Quote the association table when asked about signal "
            "importance; the raw coefficients of two near-duplicate features are not the "
            "interpretability artifact.",
            "",
        ]
    section += [
        f"**Sanity check:** `taint` lift = {taint_lift:+.4f}, "
        f"`privilege_delta` lift = {privilege_lift:+.4f} — {verdict}.",
        "",
    ]
    return section


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return "not-a-git-repo"


def _date_range(sessions: list[Any]) -> str:
    stamps = sorted(str(getattr(s, "started_at", "")) for s in sessions if getattr(s, "started_at", ""))
    return f"{stamps[0]} .. {stamps[-1]}" if stamps else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the hazard model (M4).")
    parser.add_argument("--runs", type=Path, default=None)
    parser.add_argument("--seeds", type=str, default="0,1,2,3,4")
    args = parser.parse_args(argv)

    from arena.agent.runner import load_labelled_sessions as load_sessions

    sessions = load_sessions(args.runs)
    if not sessions:
        print("no sessions found. Run `sz collect` first.")
        return 1

    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    results = train(sessions, seeds=seeds)
    for k, summary in sorted(results.items()):
        metrics = summary["metrics"]
        auc = f"AUC {metrics['test_auc']:.3f}" if "test_auc" in metrics else "AUC n/a (test split single-class)"
        print(f"k={k}: {metrics['train_steps']} train steps, {metrics['train_positives']} positive, {auc}")
    print(f"\nwrote {MODELS_DIR / 'hazard_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
