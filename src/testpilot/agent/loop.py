"""The orchestrator, as a LangGraph StateGraph.

    START → generate → run → (green? report)
                          → diagnose → patch ↺ run
                          → (unclear / out of budget) → report → END

LangGraph gives us: an explicit state machine instead of a while-loop, single
source of truth (`AgentState`), conditional routing, and a drawable graph we can
hand to judges (`testpilot graph`).
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from pathlib import Path

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from ..config import Config
from ..llm import LLM, LLMError, make_llm
from ..sandbox.base import Sandbox
from ..sandbox.local import LocalSubprocessSandbox
from .diagnose import node_diagnose
from .generate import node_generate
from .patch import node_patch
from .state import (
    STATUS_GAVE_UP,
    STATUS_GREEN,
    STATUS_UNCLEAR,
    AgentState,
    new_state,
)

# Node names (stable: referenced by the router, the CLI progress display and tests)
GENERATE = "generate"
RUN = "run"
DIAGNOSE = "diagnose"
PATCH = "patch"
REPORT = "report"


def node_run(sandbox: Sandbox, config: Config) -> Callable[[AgentState], dict]:
    def run(state: AgentState) -> dict:
        result = sandbox.run(
            state["current_source"],
            state["test_code"],
            timeout=config.sandbox_timeout,
        )
        return {
            "last_run": result,
            "history": [
                {
                    "step": "run",
                    "iteration": state.get("iteration", 0),
                    "detail": result.summary,
                }
            ],
        }

    return run


def node_report() -> Callable[[AgentState], dict]:
    def report(state: AgentState) -> dict:
        run = state.get("last_run")
        diagnosis = state.get("diagnosis") or {}

        if run is not None and run.all_green:
            status = STATUS_GREEN
            detail = f"all green: {run.summary}"
        elif diagnosis.get("verdict") == "unclear":
            status = STATUS_UNCLEAR
            detail = f"stopped as unclear: {diagnosis.get('reasoning', '')}"
        elif state.get("iteration", 0) >= state.get("max_iterations", 0):
            status = STATUS_GAVE_UP
            detail = f"budget exhausted after {state.get('iteration')} iterations"
        else:
            status = STATUS_GAVE_UP
            detail = "no further action possible"

        return {
            "status": status,
            "history": [{"step": "report", "iteration": state.get("iteration", 0), "detail": detail}],
        }

    return report


def _route_after_run(state: AgentState) -> str:
    """Green -> report; otherwise diagnose, unless the budget is spent."""
    run = state.get("last_run")
    if run is None:
        return DIAGNOSE
    if run.all_green:
        return REPORT
    if state.get("iteration", 0) >= state.get("max_iterations", 0):
        return REPORT
    return DIAGNOSE


def _route_after_diagnose(state: AgentState) -> str:
    verdict = (state.get("diagnosis") or {}).get("verdict", "unclear")
    if verdict == "unclear":
        return REPORT
    if state.get("iteration", 0) >= state.get("max_iterations", 0):
        return REPORT
    return PATCH


def build_graph(llm: LLM, sandbox: Sandbox, config: Config):
    """Compile the agent graph. Injected deps keep every node unit-testable."""
    graph = StateGraph(AgentState)

    graph.add_node(GENERATE, node_generate(llm))
    graph.add_node(RUN, node_run(sandbox, config))
    graph.add_node(DIAGNOSE, node_diagnose(llm))
    graph.add_node(PATCH, node_patch(llm))
    graph.add_node(REPORT, node_report())

    graph.add_edge(START, GENERATE)
    graph.add_edge(GENERATE, RUN)
    graph.add_conditional_edges(
        RUN,
        _route_after_run,
        {REPORT: REPORT, DIAGNOSE: DIAGNOSE},
    )
    graph.add_conditional_edges(
        DIAGNOSE,
        _route_after_diagnose,
        {PATCH: PATCH, REPORT: REPORT},
    )
    graph.add_edge(PATCH, RUN)  # patch -> re-run proves the fix
    graph.add_edge(REPORT, END)

    return graph.compile()


def run_file(
    path: str | Path,
    *,
    llm: LLM | None = None,
    sandbox: Sandbox | None = None,
    config: Config | None = None,
    on_event: Callable[[str, AgentState], None] | None = None,
) -> AgentState:
    """One full TestPilot pass over a Python file. Returns the final state."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"No such file: {source_path}")
    source = source_path.read_text(encoding="utf-8")
    if not source.strip():
        raise ValueError(f"{source_path} is empty")

    config = config or Config()
    llm = llm or make_llm(config)
    sandbox = sandbox or LocalSubprocessSandbox()

    initial = new_state(source_path, source, config)
    graph = build_graph(llm, sandbox, config)

    # Each budget cycle costs 3 node executions (diagnose, patch, run) plus
    # generate + run + report. Headroom keeps max_iterations >= 33 from blowing
    # the default 100-step LangGraph cap and raising GraphRecursionError.
    recursion_limit = 3 * config.max_iterations + 20

    # Always stream: it accumulates state incrementally, so a failure part-way
    # through still yields everything completed so far instead of losing it.
    reducers = {"history": operator.add, "notes": operator.add}
    final: AgentState = dict(initial)  # type: ignore[assignment]

    def _merge(node_output: dict) -> None:
        for key, value in node_output.items():
            if key in reducers and key in final:
                final[key] = reducers[key](final[key], value)
            else:
                final[key] = value

    def _stop(reason: str, detail: str) -> None:
        """End the run honestly without discarding the work already done.

        `history` and `notes` are operator.add reducers, so they must be
        APPENDED to. Assigning a fresh one-element list here would erase every
        node the graph had already executed — defeating the whole purpose of
        catching the exception instead of letting it propagate.
        """
        final["status"] = STATUS_GAVE_UP
        final["notes"] = list(final.get("notes") or []) + [reason]
        final["history"] = list(final.get("history") or []) + [
            {"step": "report", "iteration": final.get("iteration", 0), "detail": detail}
        ]
        if on_event:
            on_event("report", final)

    try:
        for update in graph.stream(
            initial, stream_mode="updates", config={"recursion_limit": recursion_limit}
        ):
            for node_name, node_output in update.items():
                if node_output:
                    _merge(node_output)
                if on_event:
                    on_event(node_name, final)
    except GraphRecursionError:
        # Budget/step limit exhausted — report honestly rather than crashing.
        _stop(
            f"gave up: hit graph step limit ({recursion_limit})",
            f"gave up: graph step limit ({recursion_limit}) reached",
        )
    except LLMError as exc:
        # A transient 429/5xx must not discard the run's work: keep the state
        # built so far and stop with an honest status instead of a traceback.
        _stop(f"llm error: {exc}", f"stopped: LLM error — {exc}")

    return final
