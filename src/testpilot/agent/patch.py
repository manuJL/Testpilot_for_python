"""Step: PATCH — apply the fix to whichever side the diagnosis blamed."""

from __future__ import annotations

import ast
from collections.abc import Callable

from ..llm import LLM
from ..parsing import extract_code
from ..prompts import patch_code_messages, patch_test_messages
from .state import AgentState


def _valid_python(code: str) -> bool:
    if not code.strip():
        return False
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):  # ValueError: NUL bytes in model output
        return False
    return True


def _normalize(code: str) -> str:
    """extract_code() strips fences; restore the conventional single trailing newline."""
    return code.rstrip("\n") + "\n"


def patch_test(llm: LLM, source: str, test_code: str,
               reasoning: str, instructions: str) -> tuple[str, str]:
    """Return (new_test_code, note). Falls back to the old file on bad output."""
    raw = llm.chat("patch", patch_test_messages(source, test_code, reasoning, instructions))
    candidate = _normalize(extract_code(raw))
    if not _valid_python(candidate):
        return test_code, "patch: model returned invalid test code; kept previous tests"
    if candidate == _normalize(test_code):
        return test_code, "patch: model returned unchanged tests (no progress)"
    return candidate, "patch: tests rewritten"


def patch_code(llm: LLM, source: str, reasoning: str, instructions: str) -> tuple[str, str]:
    """Return (new_source, note). Never degrades a working file."""
    raw = llm.chat("patch", patch_code_messages(source, reasoning, instructions))
    candidate = _normalize(extract_code(raw))
    if not _valid_python(candidate):
        return source, "patch: model returned invalid source; kept previous source"
    if candidate == _normalize(source):
        return source, "patch: model returned unchanged source (no progress)"
    return candidate, "patch: source updated"


def node_patch(llm: LLM) -> Callable[[AgentState], dict]:
    def patch(state: AgentState) -> dict:
        diagnosis = state.get("diagnosis") or {}
        verdict = diagnosis.get("verdict", "unclear")
        reasoning = diagnosis.get("reasoning", "")
        instructions = diagnosis.get("instructions", "") or "Fix the reported failure."

        update: dict = {"iteration": state.get("iteration", 0) + 1}

        if verdict == "test_bug":
            new_tests, note = patch_test(
                llm, state["current_source"], state["test_code"], reasoning, instructions
            )
            update["test_code"] = new_tests
            # Sticky: OR with the previous value so a later no-op patch can never
            # clear an earlier successful one (otherwise --write silently no-ops).
            update["tests_patched"] = bool(state.get("tests_patched")) or (
                new_tests != state.get("test_code")
            )
        elif verdict == "code_bug":
            new_source, note = patch_code(
                llm, state["current_source"], reasoning, instructions
            )
            update["current_source"] = new_source
            update["code_patched"] = bool(state.get("code_patched")) or (
                new_source != state.get("current_source")
            )
        else:  # unclear — unreachable: _route_after_diagnose sends `unclear`
            # straight to REPORT, so node_patch is never entered with this
            # verdict. Kept only as a guard against a future router change;
            # note it deliberately does NOT increment `iteration`.
            note = "patch: verdict unclear, no patch applied"
            update["iteration"] = state.get("iteration", 0)

        update["history"] = [
            {
                "step": "patch",
                "iteration": update["iteration"],
                "detail": f"{verdict} -> {note}",
            }
        ]
        if note:
            update["notes"] = [note]
        return update

    return patch
