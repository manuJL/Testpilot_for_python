"""Shared fixtures for TestPilot's temporary test suite (unit + e2e)."""

from __future__ import annotations

import textwrap

import pytest

from testpilot.llm import LLMError, Message


class ScriptedLLM:
    """TEST-ONLY double for the LLM seam.

    Deliberately lives in testing/ — the shipped package (src/testpilot) has no
    fake backend and will not run without a real API key. This lets unit/e2e
    tests exercise the full graph without spending credits or needing network.
    """

    def __init__(self, responses: dict[str, list[str]] | None = None,
                 script: list[str] | None = None):
        self.responses = responses or {}
        self.script = list(script) if script is not None else []
        self.calls: list[tuple[str, list[Message]]] = []

    def chat(self, step: str, messages: list[Message]) -> str:
        self.calls.append((step, messages))
        if self.script:
            return self.script.pop(0)
        queue = self.responses.get(step)
        if not queue:
            raise LLMError(f"ScriptedLLM has no scripted response for step {step!r}")
        return queue.pop(0)

BUGGY_SOURCE = textwrap.dedent(
    '''\
    def add(a, b):
        """Return the sum of a and b."""
        return a - b
    '''
)

FIXED_SOURCE = textwrap.dedent(
    '''\
    def add(a, b):
        """Return the sum of a and b."""
        return a + b
    '''
)

GOOD_TEST = textwrap.dedent(
    """\
    import testpilot_target as t

    def test_add():
        assert t.add(2, 3) == 5
    """
)

WRONG_TEST = textwrap.dedent(
    """\
    import testpilot_target as t

    def test_add():
        assert t.add(2, 3) == -1
    """
)


def fenced(code: str, lang: str = "python") -> str:
    return f"```{lang}\n{code}```"


def code_bug_script() -> ScriptedLLM:
    """generate -> failing test, diagnose -> code_bug, patch -> fixed source."""
    return ScriptedLLM(
        script=[
            fenced(GOOD_TEST),
            (
                '{"verdict": "code_bug", "reasoning": "add() subtracts, docstring says sum",'
                ' "instructions": "change the return statement to a + b"}'
            ),
            fenced(FIXED_SOURCE),
        ]
    )


def wrong_test_script() -> ScriptedLLM:
    """Correct code, wrong generated test -> diagnose blames the TEST, not the code."""
    return ScriptedLLM(
        script=[
            fenced(WRONG_TEST),
            (
                '{"verdict": "test_bug", "reasoning": "2+3 is 5, test expects -1",'
                ' "instructions": "assert the real sum 5"}'
            ),
            fenced(GOOD_TEST),
        ]
    )


@pytest.fixture
def tmp_source(tmp_path):
    """Write a source file and return its path (content untouched by the agent)."""
    def _write(content: str, name: str = "target.py"):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write
