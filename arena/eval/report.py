"""Shared table rendering. Every number printed anywhere comes through here."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any


def markdown_table(rows: Sequence[dict[str, Any]], columns: Sequence[str] | None = None) -> str:
    """Render `rows` as a GitHub-flavoured markdown table."""
    if not rows:
        return "_(no rows)_"
    cols = list(columns) if columns else list(rows[0].keys())
    widths = {c: max(len(str(c)), *(len(_fmt(r.get(c, ""))) for r in rows)) for c in cols}
    header = "| " + " | ".join(str(c).ljust(widths[c]) for c in cols) + " |"
    rule = "|" + "|".join("-" * (widths[c] + 2) for c in cols) + "|"
    body = [
        "| " + " | ".join(_fmt(r.get(c, "")).ljust(widths[c]) for c in cols) + " |"
        for r in rows
    ]
    return "\n".join([header, rule, *body])


def latex_table(rows: Sequence[dict[str, Any]], columns: Sequence[str] | None = None, caption: str = "", label: str = "") -> str:
    """Render `rows` as a booktabs table."""
    if not rows:
        return "% (no rows)"
    cols = list(columns) if columns else list(rows[0].keys())
    spec = "l" * 1 + "r" * (len(cols) - 1)
    lines = [
        "\\begin{table}[t]",
        "\\centering",
        f"\\begin{{tabular}}{{{spec}}}",
        "\\toprule",
        " & ".join(_tex(str(c)) for c in cols) + " \\\\",
        "\\midrule",
    ]
    lines += [" & ".join(_tex(_fmt(r.get(c, ""))) for c in cols) + " \\\\" for r in rows]
    lines += ["\\bottomrule", "\\end{tabular}"]
    if caption:
        lines.append(f"\\caption{{{_tex(caption)}}}")
    if label:
        lines.append(f"\\label{{{label}}}")
    lines.append("\\end{table}")
    return "\n".join(lines)


def mean_std(values: Sequence[float]) -> str:
    """`mean ± std` over seeds. Never a bare number."""
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.3f}"
    return f"{statistics.mean(values):.3f} ± {statistics.stdev(values):.3f}"


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "-"
    return str(value)


def _tex(text: str) -> str:
    for a, b in (("_", r"\_"), ("%", r"\%"), ("&", r"\&"), ("±", r"$\pm$")):
        text = text.replace(a, b)
    return text
