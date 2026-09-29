"""Step: DIAGNOSE — decide whether the test or the code is wrong."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..llm import LLM
from ..parsing import extract_json
from ..prompts import diagnose_messages
from ..sandbox.base import RunResult
from .state import AgentState

VALID_VERDICTS = {"code_bug", "test_bug", "unclear"}


def diagnose(llm: LLM, source: str, test_code: str, run: RunResult) -> dict[str, Any]:
    """Return a normalized verdict dict: {verdict, reasoning, instructions}."""
    # raw_output is always populated by the sandbox for a completed run; the
    # explicit fallback is for a RunResult built by a test stub.
    output = run.raw_output or "(no output)"
    messages = diagnose_messages(source, test_code, output)
    raw = llm.chat("diagnose", messages)

    try:
        data = extract_json(raw)
    except ValueError:
        return {
            "verdict": "unclear",
            "reasoning": f"diagnose returned unparseable output: {raw[:200]}",
            "instructions": "",
        }

    verdict = str(data.get("verdict", "unclear")).strip().lower()
    if verdict not in VALID_VERDICTS:
        verdict = "unclear"

    return {
        "verdict": verdict,
        "reasoning": str(data.get("reasoning", "")).strip(),
        "instructions": str(data.get("instructions", "")).strip(),
    }


def node_diagnose(llm: LLM) -> Callable[[AgentState], dict]:
    def diagnose_node(state: AgentState) -> dict:
        run = state.get("last_run")
        if run is None:
            verdict = {
                "verdict": "unclear",
                "reasoning": "no test run to diagnose",
                "instructions": "",
            }
        else:
            verdict = diagnose(llm, state["current_source"], state["test_code"], run)

        return {
            "diagnosis": verdict,
            "history": [
                {
                    "step": "diagnose",
                    "iteration": state.get("iteration", 0),
                    "detail": f"{verdict['verdict']}: {verdict['reasoning']}",
                }
            ],
        }

    return diagnose_node
