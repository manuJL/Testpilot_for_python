"""Step: GENERATE — write the pytest file for the target source."""

from __future__ import annotations

import ast
from collections.abc import Callable

from ..integrity import analyse, usability_reason
from ..llm import LLM
from ..parsing import extract_code
from ..prompts import MODULE_NAME, generate_messages
from .state import AgentState


def _rejection(code: str) -> str | None:
    """Why this file cannot be run, or None when it can.

    Syntax is only the first gate: a file that parses but never imports the
    module under test would run green against nothing, and one with no `test_`
    functions collects nothing at all.
    """
    try:
        ast.parse(code)
    except (SyntaxError, ValueError) as exc:
        # ast.parse raises ValueError (not SyntaxError) for NUL/null bytes.
        return f"it is not valid Python ({exc})"
    return usability_reason(code)


def generate_tests(llm: LLM, source: str) -> tuple[str, str]:
    """Ask the model for a test file.

    Returns (test_code, note). One corrective retry covers both a syntax error
    and an unusable suite (no test functions, or no import of the target);
    returning "" then routes the run straight to report as an error instead of
    executing a file that proves nothing.
    """
    messages = generate_messages(source)
    raw = llm.chat("generate", messages)
    code = extract_code(raw)

    if not code.strip():
        return "", "generate: model returned no code"

    problem = _rejection(code)
    note = ""
    if problem:
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"That file cannot be used: {problem}. "
                    "Output the complete corrected test file, one Python code block only. "
                    f"It must `import {MODULE_NAME}` and define at least one "
                    "`def test_...` that calls the functions under test."
                ),
            },
        ]
        raw = llm.chat("generate", retry_messages)
        code = extract_code(raw)
        problem = _rejection(code)
        if problem:
            return "", f"generate: unusable test file after retry ({problem})"
        note = "generate: recovered on retry"

    return code, note


def node_generate(llm: LLM) -> Callable[[AgentState], dict]:
    def generate(state: AgentState) -> dict:
        test_code, note = generate_tests(llm, state["current_source"])
        stats = analyse(test_code)
        update: dict = {
            "test_code": test_code,
            "history": [
                {
                    "step": "generate",
                    "iteration": state.get("iteration", 0),
                    "detail": note or f"{stats.describe()} written",
                }
            ],
        }
        if note:
            update["notes"] = [note]
        return update

    return generate
