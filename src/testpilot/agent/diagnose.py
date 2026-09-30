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
    """Return a normalized verdict dict: {verdict, reasoning, instructions}.

    A reply we cannot parse is a *model failure*, not an ambiguity in the
    user's code — so it is retried once and then flagged with an `error` key
    (status `error`, exit code 2). Reporting it as a genuine `unclear` verdict
    would tell the user "honest stop: I can't tell who's wrong" when the truth
    is "the model never answered".
    """
    # raw_output is always populated by the sandbox for a completed run; the
    # explicit fallback is for a RunResult built by a test stub.
    output = run.raw_output or "(no output)"
    messages = diagnose_messages(source, test_code, output)
    raw = llm.chat("diagnose", messages)

    try:
        data = extract_json(raw)
    except ValueError:
        # One corrective retry: a malformed reply is usually a formatting slip
        # (prose around the JSON, an unquoted key), and the feedback below is
        # far cheaper than writing the whole run off as inconclusive.
        retry = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "That reply was not valid JSON, so it cannot be used. "
                    "Reply with EXACTLY ONE object and no other text, shaped like: "
                    '{"verdict": "code_bug", "reasoning": "...", "instructions": "..."}. '
                    'Use "unclear" for the verdict if you genuinely cannot tell '
                    'who is wrong.'
                ),
            },
        ]
        try:
            data = extract_json(llm.chat("diagnose", retry))
        except ValueError:
            return {
                "verdict": "unclear",  # keeps the router pointed at REPORT
                "error": "the diagnosis step never returned parseable JSON "
                         "(the model did not answer)",
                "reasoning": f"diagnose returned unparseable output twice: {raw[:200]}",
                "instructions": "",
            }

    verdict = str(data.get("verdict", "unclear")).strip().lower()
    if verdict not in VALID_VERDICTS:
        # Same class of failure: the model invented a fourth verdict, so we
        # cannot claim it judged the code ambiguous.
        return {
            "verdict": "unclear",  # keeps the router pointed at REPORT
            "error": f"the diagnosis step returned an unknown verdict: {verdict!r}",
            "reasoning": str(data.get("reasoning", "")).strip(),
            "instructions": "",
        }

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
