"""Step: GENERATE — write the pytest file for the target source."""

from __future__ import annotations

import ast
from collections.abc import Callable

from ..llm import LLM
from ..parsing import extract_code
from ..prompts import generate_messages
from .state import AgentState


def generate_tests(llm: LLM, source: str) -> tuple[str, str]:
    """Ask the model for a test file.

    Returns (test_code, note). Retries once with syntax feedback when the first
    answer is not valid Python.
    """
    messages = generate_messages(source)
    raw = llm.chat("generate", messages)
    code = extract_code(raw)

    note = ""
    if not code.strip():
        note = "generate: model returned no code"
        return "", note

    try:
        ast.parse(code)
    # ast.parse raises ValueError (not SyntaxError) for NUL bytes / null chars.
    except (SyntaxError, ValueError) as exc:
        retry_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"That file has a syntax error: {exc}. "
                    "Output the complete corrected test file, one Python code block only."
                ),
            },
        ]
        raw = llm.chat("generate", retry_messages)
        code = extract_code(raw)
        try:
            ast.parse(code)
            note = "generate: recovered from syntax error on retry"
        except (SyntaxError, ValueError) as exc2:
            note = f"generate: still invalid Python after retry ({exc2})"
            return "", note

    if "def test_" not in code and "class Test" not in code:
        note = "generate: no test functions found in output"
    return code, note


def node_generate(llm: LLM) -> Callable[[AgentState], dict]:
    def generate(state: AgentState) -> dict:
        test_code, note = generate_tests(llm, state["current_source"])
        update: dict = {
            "test_code": test_code,
            "history": [
                {
                    "step": "generate",
                    "iteration": state.get("iteration", 0),
                    "detail": note or f"{test_code.count('def test_')} test functions written",
                }
            ],
        }
        if note:
            update["notes"] = [note]
        return update

    return generate
