"""The target agent: runs one AgentDojo task and records everything.

`TargetAgent.run()` is the only place a session is produced. The defense, the
attacker, the UI and the evaluation all consume `Session` objects that came
out of here, so there is exactly one code path and no forked variants.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, load_system_message
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, ToolsExecutor, tool_result_to_str

from arena import config
from arena.agent.backends import OLLAMA, SCRIPTED, resolve_backend
from arena.agent.scripted import SCRIPTED_MODEL_NAME, ScriptedLLM
from arena.agent.session import Session, Step
from arena.env import egress
from arena.env.brokered_runtime import make_brokered_runtime
from arena.env.suites import all_tool_names, extra_tools, ground_truth_pairs, load_suite
from arena.env.tools import EXFIL_SINK, report_exfiltration, reset_exfil_sink
from arena.red.static import StaticAttacker
from sentinelz.broker.capability import CapabilityBroker
from sentinelz.evidence.canonical import dumps_str

#: Tools whose result is a bare confirmation rather than third-party content.
_NON_CONTENT_TOOLS = egress.EGRESS_TOOLS | {"delete_email", "delete_file", "update_password"}


class TargetAgent:
    """Runs one task, with or without an attack, with or without the defense."""

    def __init__(
        self,
        model: str | None = None,
        suite: str = "workspace",
        max_steps: int = 15,
        seed: int = 0,
        backend: str | None = None,
        defense: str = "none",
        gateway: Any | None = None,
        persist: bool = True,
    ) -> None:
        self.suite_name = suite
        self.suite = load_suite(suite)
        self.max_steps = max_steps
        self.seed = seed
        self.backend = resolve_backend(backend)
        self.model = model or (config.OLLAMA_MODEL if self.backend == OLLAMA else SCRIPTED_MODEL_NAME)
        self.defense = defense
        self.gateway = gateway
        self.persist = persist
        self.broker = CapabilityBroker()
        self._system_message = load_system_message(None)

    # ------------------------------------------------------------- run
    def run(
        self,
        user_task_id: str,
        injection_task_id: str | None = None,
        template: str = "important_instructions",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        injections_override: dict[str, str] | None = None,
    ) -> Session:
        """Run one task. Returns a fully populated `Session`."""
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}_{user_task_id}"

        user_task = self.suite.user_tasks[user_task_id]
        injection_task = self.suite.injection_tasks[injection_task_id] if injection_task_id else None

        injections: dict[str, str] = {}
        if injection_task_id is not None:
            if injections_override is not None:
                # The adaptive attacker supplies its own rewritten text for a
                # vector the static attacker already proved is reachable.
                injections = dict(injections_override)
            else:
                attacker = StaticAttacker(template=template, suite_name=self.suite_name)
                try:
                    injections = attacker.injections(user_task_id, injection_task_id)
                except ValueError:
                    injections = {}

        session = Session(
            task_id=user_task_id,
            suite=self.suite_name,
            model=self.model,
            seed=self.seed,
            backend=self.backend,
            session_id=session_id,
            started_at=started_at,
            injection_task_id=injection_task_id,
            attack_template=template if injection_task_id else None,
            defense=self.defense,
        )

        reset_exfil_sink()
        self.broker.reset()
        scopes = all_tool_names(self.suite)
        self.broker.issue(session_id, scopes)

        llm = self._make_llm(user_task, injection_task, injections, template)
        recorder = _StepRecorder(session, self.suite_name, on_event)
        runtime_class = make_brokered_runtime(
            self.broker, session_id, on_call=recorder.on_call, extra_functions=extra_tools()
        )

        loop_elements: list[BasePipelineElement] = []
        if self.gateway is not None:
            from arena.agent.sentinel_element import SentinelElement

            sentinel = SentinelElement(
                gateway=self.gateway,
                broker=self.broker,
                session_id=session_id,
                suite_name=self.suite_name,
                user_task_id=user_task_id,
                session=session,
                on_event=on_event,
            )
            loop_elements.append(sentinel)
            recorder.sentinel = sentinel

        loop_elements.extend([ToolsExecutor(), llm])
        pipeline = AgentPipeline(
            [
                SystemMessage(self._system_message),
                InitQuery(),
                llm,
                ToolsExecutionLoop(loop_elements, max_iters=self.max_steps),
            ]
        )

        utility, security = self.suite.run_task_with_pipeline(
            pipeline,
            user_task,
            injection_task,
            injections,
            runtime_class=runtime_class,
        )

        session.completed = bool(utility)
        session.utility_score = 1.0 if utility else 0.0
        session.attack_succeeded = bool(security) if injection_task_id else False
        session.wall_time_s = time.monotonic() - started
        session.exfiltrated_records = [r for entry in EXFIL_SINK for r in entry["records"]]
        session.harm_step = _find_harm_step(session, self.suite_name)
        if not self.broker.is_alive(session_id):
            session.revoked_at_step = session.revoked_at_step or _first_blocked_step(session)

        if self.persist:
            save_session(session)
        if on_event is not None:
            on_event("session_end", {"session": session.summary_row()})
        return session

    # ------------------------------------------------------------- llm
    def _make_llm(
        self, user_task: Any, injection_task: Any, injections: dict[str, str], template: str
    ) -> BasePipelineElement:
        if self.backend == SCRIPTED:
            return ScriptedLLM(
                user_task=user_task,
                injection_task=injection_task,
                injection_texts=list(injections.values()),
                template=template,
                seed=self.seed,
                suite_name=self.suite_name,
            )
        from arena.agent.ollama_llm import OllamaLLM

        return OllamaLLM(model=self.model)


class _StepRecorder:
    """Turns broker-level call events into `Step`s on the session."""

    def __init__(self, session: Session, suite_name: str, on_event: Callable[[str, dict], None] | None) -> None:
        self.session = session
        self.suite_name = suite_name
        self.on_event = on_event
        self.sentinel: Any | None = None
        self._t0 = time.monotonic()

    def on_call(self, tool_name: str, tool_args: dict[str, Any], result: Any, error: str | None) -> None:
        # Format exactly as the agent saw it, so the signals score the same
        # bytes the model consumed rather than a Python repr.
        text = "" if result is None else tool_result_to_str(result)
        blocked = bool(error and "CapabilityError" in str(error))
        step = Step(
            idx=len(self.session.steps),
            tool_name=tool_name,
            tool_args=_jsonable(tool_args),
            # Kept generous on purpose: replay re-scores these strings, and a
            # short cap would hide an injection that the live run did see.
            tool_result_text=text[:12000],
            result_is_untrusted=(tool_name not in _NON_CONTENT_TOOLS) and bool(text),
            timestamp_ms=int((time.monotonic() - self._t0) * 1000),
            blocked=blocked,
        )
        if error and not blocked:
            step.note = str(error)[:300]

        if self.sentinel is not None:
            pending = self.sentinel.take_pending()
            if pending is not None:
                step.decision = pending.get("decision")
                step.signals = pending.get("signals")
                if pending.get("note"):
                    step.note = str(pending["note"])

        self.session.steps.append(step)

        if not blocked and error is None and egress.is_external(tool_name, tool_args):
            records = egress.extract_records(tool_name, tool_args)
            if tool_name != "send_to_external":  # that tool reports itself
                report_exfiltration(tool_name, records)

        if self.on_event is not None:
            self.on_event("step", {"step": step.model_dump()})


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _find_harm_step(session: Session, suite_name: str) -> int | None:
    """Index of the step that accomplished the injection, if any.

    Matched on tool name **and** target, not tool name alone. Several suites
    give the benign task and the injection the same verb — `user_task_15` calls
    `update_scheduled_transaction` on its own transaction and
    `injection_task_4` calls it on the attacker's — and a name-only match
    returns the benign call several steps early. Everything downstream is
    indexed off this number, so the error does not stay small: the harmful step
    gets labelled BENIGN, the steps around it are labelled as the run-up to
    harm, and `taint` learns a coefficient with the wrong sign.
    """
    if session.injection_task_id is None or not session.attack_succeeded:
        return None
    pairs = ground_truth_pairs(suite_name, session.injection_task_id, is_injection=True)
    if not pairs:
        return None

    from arena.env.brokered_runtime import call_target

    harmful_tool, harmful_target = pairs[-1]
    fallback: int | None = None
    for step in session.steps:
        if step.tool_name != harmful_tool or step.blocked:
            continue
        if call_target(step.tool_name, dict(step.tool_args)) == harmful_target:
            return step.idx
        fallback = step.idx

    # No exact target match: the agent reached the harmful tool with arguments
    # the ground truth did not predict. The *last* such call is the one the
    # security checker saw succeed, so prefer it over the first.
    return fallback


def _first_blocked_step(session: Session) -> int | None:
    for step in session.steps:
        if step.blocked:
            return step.idx
    return None


# ------------------------------------------------------------ persistence


def save_session(session: Session, directory: Path | None = None) -> Path:
    directory = directory or config.RUNS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session.session_id}.json"
    path.write_text(dumps_str(session.model_dump()), encoding="utf-8")
    return path


def load_session(path: Path) -> Session:
    from sentinelz.evidence.canonical import loads

    return Session.model_validate(loads(path.read_text(encoding="utf-8")))


def load_sessions(directory: Path | None = None, pattern: str = "*.json") -> list[Session]:
    directory = directory or config.RUNS_DIR
    if not directory.exists():
        return []
    out: list[Session] = []
    for path in sorted(directory.glob(pattern)):
        try:
            out.append(load_session(path))
        except Exception:
            continue
    return out


def load_labelled_sessions(directory: Path | None = None, pattern: str = "*.json") -> list[Session]:
    """Only the sessions the defense actually watched — the trainable corpus.

    `runs/` is written to by everything: `sz collect`, both gates, `run-arena`,
    the campaigns. The gates and the undefended arm of the arena run with no
    gateway at all, so their sessions carry no signals and no decisions. Fed to
    the estimator those become dozens of sessions that sit in BENIGN emitting
    observation 0 forever, which drags every transition row towards "nothing
    ever happens" — and the corpus silently changes depending on which
    commands were run since the last collection.
    """
    return [s for s in load_sessions(directory, pattern) if any(step.decision for step in s.steps)]
