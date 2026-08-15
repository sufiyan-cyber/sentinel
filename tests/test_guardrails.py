"""AST checks enforcing the rules in AGENTS.md.

If one of these fails, fix the code. Never weaken or delete the test. If a
guardrail itself seems wrong, stop and ask — these encode decisions that other
artifacts (saved models, stored hashes, the paper) depend on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ("sentinelz", "arena")

#: Modules in the decision path. These carry the strictest rules.
DECISION_PATH = (
    "sentinelz/gateway.py",
    "sentinelz/policy/decide.py",
    "sentinelz/signals/bundle.py",
    "sentinelz/signals/injection.py",
    "sentinelz/signals/alignment.py",
    "sentinelz/signals/privilege.py",
    "sentinelz/signals/taint.py",
    "sentinelz/signals/novelty.py",
    "sentinelz/pomdp/belief.py",
    "sentinelz/pomdp/observe.py",
    "sentinelz/hazard/predict.py",
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for package in PACKAGES:
        files.extend(sorted((REPO_ROOT / package).rglob("*.py")))
    return files


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    if isinstance(func, ast.Name):
        return func.id
    return ""


# ------------------------------------------------- canonical json only


def test_no_json_dumps_outside_canonical():
    """Every serialisation that could be hashed must go through
    `evidence.canonical.dumps`, which pins sort_keys and separators. A stray
    `json.dumps` silently produces a different byte string and invalidates
    stored hashes."""
    offenders = []
    for path in _python_files():
        if _relative(path).endswith("evidence/canonical.py"):
            continue
        for call in _calls(_parse(path)):
            name = _call_name(call)
            if name in {"json.dumps", "json.dump"}:
                offenders.append(f"{_relative(path)}:{call.lineno}")
    assert not offenders, f"use sentinelz.evidence.canonical.dumps instead: {offenders}"


def test_canonical_dumps_pins_its_parameters():
    source = (REPO_ROOT / "sentinelz/evidence/canonical.py").read_text(encoding="utf-8")
    for required in ("sort_keys=True", 'separators=(",", ":")', "ensure_ascii=True"):
        assert required in source, f"canonical.dumps must pin {required}"


# ------------------------------------------------- no learning at decision time


def test_no_fit_in_the_decision_path():
    """Training must not happen inside a request. A `.fit(` on the decision
    path means the model is changing under the traffic it is judging."""
    offenders = []
    for relative in DECISION_PATH:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        for call in _calls(_parse(path)):
            name = _call_name(call)
            if name.endswith(".fit") or name.endswith(".partial_fit"):
                offenders.append(f"{relative}:{call.lineno} -> {name}")
    assert not offenders, f"no fitting in the decision path: {offenders}"


def test_no_llm_call_in_the_decision_path():
    """N2. A signal that calls a model over HTTP cannot meet a 100ms budget
    and cannot run offline."""
    banned_calls = {"ollama.chat", "ollama.generate", "openai.ChatCompletion.create", "httpx.post", "httpx.get", "requests.post", "requests.get"}
    banned_imports = {"ollama", "openai", "httpx", "requests", "urllib.request"}
    offenders = []
    for relative in DECISION_PATH:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        tree = _parse(path)
        for call in _calls(tree):
            if _call_name(call) in banned_calls:
                offenders.append(f"{relative}:{call.lineno} -> {_call_name(call)}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in banned_imports:
                        offenders.append(f"{relative}:{node.lineno} -> import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in banned_imports:
                offenders.append(f"{relative}:{node.lineno} -> from {node.module}")
    assert not offenders, f"no network or LLM calls in the decision path: {offenders}"


# ------------------------------------------------- splits are never shuffled


def test_no_shuffled_splits():
    """Sessions are ordered. `train_test_split` with the default shuffle, or
    any `.shuffle(`, leaks the outcome into the features because steps within
    one session share a label."""
    offenders = []
    for path in _python_files():
        tree = _parse(path)
        for call in _calls(tree):
            name = _call_name(call)
            if name.endswith("train_test_split"):
                offenders.append(f"{_relative(path)}:{call.lineno} -> train_test_split")
            if name.endswith(".shuffle") and "shuffle" not in _relative(path):
                offenders.append(f"{_relative(path)}:{call.lineno} -> {name}")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "model_selection" in node.module:
                for alias in node.names:
                    if alias.name in {"train_test_split", "KFold", "StratifiedKFold"}:
                        offenders.append(f"{_relative(path)}:{node.lineno} -> {alias.name}")
    assert not offenders, f"split by session, time-ordered, never shuffled: {offenders}"


def test_split_by_session_is_time_ordered():
    from sentinelz.hazard.train import split_by_session

    class FakeSession:
        def __init__(self, stamp: str) -> None:
            self.started_at = stamp
            self.session_id = stamp

    sessions = [FakeSession(f"2026-08-14T10:{i:02d}:00") for i in range(10)]
    train, test = split_by_session(list(reversed(sessions)), 0.7)
    assert [s.started_at for s in train] == [s.started_at for s in sessions[:7]]
    assert [s.started_at for s in test] == [s.started_at for s in sessions[7:]]
    assert max(s.started_at for s in train) <= min(s.started_at for s in test)


# ------------------------------------------------- never default to ALLOW


def test_every_gateway_exception_path_returns_revoke():
    """AGENTS.md: never default to ALLOW on an error."""
    source = (REPO_ROOT / "sentinelz/gateway.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    assert handlers, "gateway must handle exceptions explicitly"

    evaluate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )
    for handler in [n for n in ast.walk(evaluate) if isinstance(n, ast.ExceptHandler)]:
        returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
        assert returns, "an except block in evaluate() must return, not fall through"
        for node in returns:
            rendered = ast.dump(node)
            assert "_revoke_decision" in rendered, f"exception path must REVOKE, got: {ast.unparse(node)}"


def test_gateway_revoke_decision_really_revokes():
    from sentinelz.gateway import Gateway

    gateway = Gateway(mode="observe")
    decision = gateway._revoke_decision("forced")
    assert decision.action == "REVOKE"
    assert decision.action_index == 4


# ------------------------------------------------- frozen vocabulary


def test_the_five_signals_are_not_renamed():
    from sentinelz.signals.base import SIGNAL_NAMES

    assert SIGNAL_NAMES == (
        "injection_likelihood",
        "task_alignment",
        "privilege_delta",
        "taint",
        "sequence_novelty",
    )


def test_the_five_actions_and_states_are_not_renamed():
    from sentinelz.pomdp.states import ACTION_NAMES, STATE_NAMES

    assert STATE_NAMES == ("BENIGN", "RECON", "ESCALATION", "HARM", "CONTAINED")
    assert ACTION_NAMES == ("ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE")


def test_feature_names_are_frozen_and_match_their_count():
    from sentinelz.hazard.features import FEATURE_NAMES, N_FEATURES

    assert len(FEATURE_NAMES) == N_FEATURES == 17
    assert FEATURE_NAMES[0] == "injection_likelihood_now"
    assert FEATURE_NAMES[-2:] == ("step_index", "n_distinct_tools")
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


# ------------------------------------------------- ollama address


def test_no_hardcoded_localhost_for_ollama():
    """AGENTS.md: Ollama may be on another machine. The address always comes
    from OLLAMA_BASE_URL. The single default lives in arena/config.py."""
    offenders = []
    for path in _python_files():
        relative = _relative(path)
        if relative == "arena/config.py":
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "11434" in line and "OLLAMA_BASE_URL" not in line:
                offenders.append(f"{relative}:{lineno}")
    assert not offenders, f"read OLLAMA_BASE_URL instead of hardcoding a host: {offenders}"


# ------------------------------------------------- models load once


def test_models_are_constructed_in_init_not_per_call():
    """N3. A model loaded inside `score()` or `evaluate()` would be reloaded on
    every tool call and blow the budget."""
    loaders = {"joblib.load", "np.load", "numpy.load", "HazardModel.load", "GridPolicy.load"}
    offenders = []
    for relative in DECISION_PATH:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        tree = _parse(path)
        for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if function.name in {"__init__", "load", "build_gateway", "_try_load_classifier", "_try_load_encoder"}:
                continue
            for call in _calls(ast.Module(body=function.body, type_ignores=[])):
                if _call_name(call) in loaders:
                    offenders.append(f"{relative}:{call.lineno} in {function.name}() -> {_call_name(call)}")
    assert not offenders, f"load models once at startup: {offenders}"


@pytest.mark.parametrize("relative", DECISION_PATH)
def test_decision_path_modules_parse(relative):
    path = REPO_ROOT / relative
    assert path.exists(), f"{relative} is listed in DECISION_PATH but does not exist"
    _parse(path)
