"""End-to-end: the FULL LangGraph loop on real files, real sandbox, scripted LLM.

These prove the whole pipeline works without spending API credits:
read file -> generate -> sandbox run -> diagnose -> patch -> re-run -> report.
"""

from __future__ import annotations

import textwrap

from conftest import (
    BUGGY_SOURCE,
    FIXED_SOURCE,
    GOOD_TEST,
    ScriptedLLM,
    code_bug_script,
    fenced,
    wrong_test_script,
)

from testpilot.agent.loop import run_file
from testpilot.agent.state import STATUS_GAVE_UP, STATUS_GREEN, STATUS_UNCLEAR
from testpilot.config import Config
from testpilot.report import unified_diff, write_fixed_file


class TestCodeBugPath:
    def test_buggy_code_is_patched_until_green(self, tmp_source):
        path = tmp_source(BUGGY_SOURCE)
        state = run_file(path, llm=code_bug_script(), config=Config(max_iterations=4))

        assert state["status"] == STATUS_GREEN
        assert state["code_patched"] is True
        assert state["current_source"] == FIXED_SOURCE
        assert state["last_run"].all_green

        # Correct trajectory: generate -> run(fail) -> diagnose -> patch -> run(green)
        steps = [h["step"] for h in state["history"]]
        assert steps == ["generate", "run", "diagnose", "patch", "run", "report"]

        # Hard rule: the user's original file is never touched
        assert path.read_text(encoding="utf-8") == BUGGY_SOURCE

    def test_diff_is_produced_for_the_patch(self, tmp_source):
        path = tmp_source(BUGGY_SOURCE)
        state = run_file(path, llm=code_bug_script(), config=Config())
        diff = unified_diff(state["original_source"], state["current_source"], path.name)
        assert "-    return a - b" in diff
        assert "+    return a + b" in diff

    def test_fixed_file_written_next_to_original(self, tmp_source):
        path = tmp_source(BUGGY_SOURCE)
        state = run_file(path, llm=code_bug_script(), config=Config())
        fixed = write_fixed_file(state)
        assert fixed is not None and fixed.name == "target.fixed.py"
        assert fixed.read_text(encoding="utf-8") == FIXED_SOURCE
        assert path.read_text(encoding="utf-8") == BUGGY_SOURCE  # original intact


class TestTestBugPath:
    def test_wrong_test_is_fixed_not_code(self, tmp_source):
        # Code here is CORRECT; the generated test asserts -1 (wrong). The agent
        # must fix the test and leave the source alone.
        path = tmp_source(FIXED_SOURCE)
        state = run_file(path, llm=wrong_test_script(), config=Config())

        assert state["status"] == STATUS_GREEN
        assert state["code_patched"] is False, "code must NOT be modified when the test is wrong"
        assert state["tests_patched"] is True
        assert state["test_code"] == GOOD_TEST
        assert state["current_source"] == FIXED_SOURCE
        assert path.read_text(encoding="utf-8") == FIXED_SOURCE


class TestUnclearPath:
    def test_honest_stop_on_ambiguous_verdict(self, tmp_source):
        path = tmp_source(textwrap.dedent(
            """\
            def merge(a, b):
                \"\"\"Combine two lists.\"\"\"
                return a + b
            """
        ))
        llm = ScriptedLLM(
            script=[
                fenced("import testpilot_target as t\n\ndef test_order():\n    assert t.merge([1],[2]) == [2,1]\n"),
                '{"verdict": "unclear", "reasoning": "docstring does not specify order", "instructions": ""}',
            ]
        )
        state = run_file(path, llm=llm, config=Config())

        assert state["status"] == STATUS_UNCLEAR
        assert state["code_patched"] is False
        assert state["current_source"] == state["original_source"]


class TestBudgetExhaustion:
    def test_gives_up_after_max_iterations(self, tmp_source):
        path = tmp_source(BUGGY_SOURCE)
        # Every diagnose says code_bug but the patch is a no-op -> budget burns.
        llm = ScriptedLLM(
            script=[
                fenced(GOOD_TEST),
                '{"verdict": "code_bug", "reasoning": "still wrong", "instructions": "fix"}',
                fenced(BUGGY_SOURCE),
                '{"verdict": "code_bug", "reasoning": "still wrong", "instructions": "fix"}',
                fenced(BUGGY_SOURCE),
            ]
        )
        state = run_file(path, llm=llm, config=Config(max_iterations=2))

        assert state["status"] == STATUS_GAVE_UP
        assert state["iteration"] == 2
        assert state["current_source"] == BUGGY_SOURCE  # no fake success claimed


class TestInputValidation:
    def test_missing_file_raises(self, tmp_path):
        try:
            run_file(tmp_path / "nope.py", llm=ScriptedLLM(script=[]))
        except FileNotFoundError as exc:
            assert "nope.py" in str(exc)
        else:
            raise AssertionError("expected FileNotFoundError")

    def test_empty_file_raises(self, tmp_source):
        path = tmp_source("", name="empty.py")
        try:
            run_file(path, llm=ScriptedLLM(script=[]))
        except ValueError as exc:
            assert "empty" in str(exc)
        else:
            raise AssertionError("expected ValueError")
