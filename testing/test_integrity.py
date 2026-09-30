"""Guarantees that make a green run mean something.

Each group here exists because of a real way the agent used to lie: a suite
that passed without ever importing the module under test, a "fix" that deleted
the failing test, a rate limit printed as "budget exhausted", a no-op patch
burning the whole iteration budget, and `--write` saving an unverified edit.
"""

from __future__ import annotations

import json

import pytest
from conftest import BUGGY_SOURCE, GOOD_TEST, WRONG_TEST, ScriptedLLM, fenced
from typer.testing import CliRunner

from testpilot.agent.generate import generate_tests
from testpilot.agent.loop import (
    DIAGNOSE,
    REPORT,
    RUN,
    _route_after_generate,
    _route_after_run,
    node_report,
)
from testpilot.agent.patch import node_patch, patch_test
from testpilot.agent.state import STATUS_ERROR, STATUS_GAVE_UP, STATUS_GREEN
from testpilot.cli import app
from testpilot.integrity import analyse, degradation_reason, usability_reason
from testpilot.prompts import MODULE_NAME
from testpilot.sandbox.base import RunResult

runner = CliRunner()


def green_run() -> RunResult:
    return RunResult(passed=3, failed=0, total=3, raw_output="3 passed in 0.01s")


def red_run() -> RunResult:
    return RunResult(passed=0, failed=1, total=1, raw_output="1 failed in 0.01s")


# ---------------------------------------------------------------- integrity


class TestAnalyse:
    def test_counts_tests_asserts_and_detects_the_import(self):
        stats = analyse(GOOD_TEST)
        assert stats.imports_target is True
        assert stats.test_count == 1
        assert stats.assert_count == 1

    def test_unparseable_file_counts_as_an_empty_suite(self):
        stats = analyse("def test_x(:")
        assert stats.test_count == 0
        assert stats.imports_target is False

    def test_import_via_importlib_string_still_counts(self):
        code = 'm = __import__("testpilot_target")\n\ndef test_x():\n    assert True\n'
        assert analyse(code).imports_target is True


class TestUsability:
    def test_a_real_suite_has_no_objection(self):
        assert usability_reason(GOOD_TEST) is None

    def test_a_suite_that_never_imports_the_target_is_rejected(self):
        reason = usability_reason("def test_x():\n    assert 1 == 1\n")
        assert reason is not None
        assert MODULE_NAME in reason

    def test_a_suite_with_no_tests_is_rejected(self):
        assert usability_reason(f"import {MODULE_NAME}\n") is not None


class TestDegradation:
    def test_deleting_a_test_is_rejected(self):
        before = GOOD_TEST + "\ndef test_more():\n    assert t.add(1, 1) == 2\n"
        after = GOOD_TEST
        assert "deleted tests" in (degradation_reason(before, after) or "")

    def test_dropping_asserts_is_rejected(self):
        before = f"import {MODULE_NAME} as t\n\ndef test_a():\n    assert t.add(1, 1) == 2\n"
        after = f"import {MODULE_NAME} as t\n\ndef test_a():\n    t.add(1, 1)\n"
        assert "deleted asserts" in (degradation_reason(before, after) or "")

    def test_dropping_the_import_is_rejected(self):
        before = GOOD_TEST
        after = "def test_add():\n    assert True\n"
        assert "import" in (degradation_reason(before, after) or "")

    def test_rewriting_an_expectation_is_allowed(self):
        # The legitimate fix: wrong assertion -> right assertion, nothing lost.
        assert degradation_reason(WRONG_TEST, GOOD_TEST) is None

    def test_a_preexisting_problem_does_not_block_the_patch(self):
        # Comparisons are relative to the suite being replaced, never absolute.
        before = "def test_a():\n    assert True\n"
        after = "def test_a():\n    assert 1 == 1\n"
        assert degradation_reason(before, after) is None


# ---------------------------------------------------------------- generate


class TestGenerateRejectsUnusableSuites:
    def test_unusable_twice_returns_no_tests_at_all(self):
        llm = ScriptedLLM(responses={"generate": ["no tests here", "still nothing"]})
        code, note = generate_tests(llm, "def add(a, b):\n    return a + b\n")
        assert code == ""
        assert "unusable" in note
        assert len(llm.calls) == 2, "must use its one corrective retry first"

    def test_recovered_on_retry(self):
        llm = ScriptedLLM(
            responses={"generate": ["def test_x():\n    assert 1", fenced(GOOD_TEST)]}
        )
        code, note = generate_tests(llm, "def add(a, b):\n    return a + b\n")
        assert code.strip()
        assert "recovered" in note


# ---------------------------------------------------------------- patch


class TestPatchCannotGutTheSuite:
    def test_deleting_the_failing_test_is_rejected(self):
        two_tests = GOOD_TEST + "\ndef test_extra():\n    assert t.add(0, 0) == 0\n"
        llm = ScriptedLLM(responses={"patch": [fenced(GOOD_TEST)]})
        new_tests, note = patch_test(llm, "src", two_tests, "reasoning", "instructions")
        assert new_tests == two_tests, "the previous tests must survive"
        assert "rejected" in note
        assert "deleted tests" in note

    def test_a_legitimate_rewrite_is_accepted(self):
        llm = ScriptedLLM(responses={"patch": [fenced(GOOD_TEST)]})
        new_tests, note = patch_test(llm, "src", WRONG_TEST, "reasoning", "instructions")
        assert new_tests == GOOD_TEST
        assert "rewritten" in note

    def test_counts_are_reported_in_the_note(self):
        llm = ScriptedLLM(responses={"patch": [fenced(GOOD_TEST)]})
        _, note = patch_test(llm, "src", WRONG_TEST, "r", "i")
        assert "tests" in note and "asserts" in note

    def test_two_noop_patches_stop_the_run(self):
        llm = ScriptedLLM(responses={"patch": [fenced(BUGGY_SOURCE), fenced(BUGGY_SOURCE)]})
        node = node_patch(llm)
        state = {
            "current_source": BUGGY_SOURCE,  # what the "fix" returns: unchanged
            "test_code": GOOD_TEST,
            "diagnosis": {"verdict": "code_bug", "reasoning": "r", "instructions": "i"},
            "iteration": 0,
        }
        state.update(node(state))  # first patch: unchanged source -> no progress
        assert state["consecutive_no_progress"] == 1
        state.update(node(state))  # second: still nothing
        assert state["consecutive_no_progress"] == 2


# ------------------------------------------------------- report + routing


class TestReportSaysWhatActuallyHappened:
    def test_green_suite_that_imports_the_target_is_green(self):
        state = {
            "last_run": green_run(),
            "test_code": GOOD_TEST,
            "diagnosis": None,
            "iteration": 0,
            "max_iterations": 4,
            "history": [],
        }
        assert node_report()(state)["status"] == STATUS_GREEN

    def test_green_but_never_imported_is_an_error(self):
        # The suite passed while testing nothing at all — call that an error,
        # not "TESTS GREEN".
        state = {
            "last_run": green_run(),
            "test_code": "def test_x():\n    assert True\n",
            "diagnosis": None,
            "iteration": 0,
            "max_iterations": 4,
            "history": [],
        }
        out = node_report()(state)
        assert out["status"] == STATUS_ERROR
        assert MODULE_NAME in out["history"][0]["detail"]

    def test_no_test_code_is_an_error(self):
        state = {
            "last_run": None,
            "test_code": "",
            "diagnosis": None,
            "iteration": 0,
            "max_iterations": 4,
            "history": [],
        }
        out = node_report()(state)
        assert out["status"] == STATUS_ERROR
        assert "test file is empty" in out["history"][0]["detail"]

    def test_a_stalled_run_reports_stalling_not_budget(self):
        state = {
            "last_run": red_run(),
            "test_code": GOOD_TEST,
            "diagnosis": {"verdict": "code_bug", "reasoning": "r"},
            "iteration": 2,
            "max_iterations": 4,
            "consecutive_no_progress": 2,
            "history": [],
        }
        out = node_report()(state)
        assert out["status"] == STATUS_GAVE_UP
        assert "changed nothing" in out["history"][0]["detail"]
        assert "budget" not in out["history"][0]["detail"]


class TestRouters:
    def test_no_test_code_goes_straight_to_report(self):
        assert _route_after_generate({"test_code": ""}) == REPORT
        assert _route_after_generate({"test_code": "   "}) == REPORT
        assert _route_after_generate({"test_code": GOOD_TEST}) == RUN

    def test_stopping_early_waits_for_two_stalled_patches(self):
        base = {"last_run": red_run(), "iteration": 0, "max_iterations": 4}
        assert _route_after_run({**base, "consecutive_no_progress": 1}) == DIAGNOSE
        assert _route_after_run({**base, "consecutive_no_progress": 2}) == REPORT


# ------------------------------------------------------------------- CLI


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")


class TestExitCodesAreHonest:
    def test_an_llm_failure_exits_2_and_never_says_budget(self, tmp_source, key, monkeypatch):
        from testpilot.llm import LLMError

        class AlwaysRateLimited:
            def chat(self, step: str, messages) -> str:
                raise LLMError("Rate limited (HTTP 429) by api.groq.com")

        monkeypatch.setattr(
            "testpilot.agent.loop.make_llm", lambda config=None: AlwaysRateLimited()
        )
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--no-diff"])
        assert result.exit_code == 2, result.output
        assert "ERROR" in result.output
        assert "budget exhausted" not in result.output

    def test_the_json_status_carries_the_error_too(self, tmp_source, key, monkeypatch):
        from testpilot.llm import LLMError

        class AlwaysRateLimited:
            def chat(self, step: str, messages) -> str:
                raise LLMError("Rate limited (HTTP 429)")

        monkeypatch.setattr(
            "testpilot.agent.loop.make_llm", lambda config=None: AlwaysRateLimited()
        )
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--json"])
        assert result.exit_code == 2
        assert json.loads(result.output)["status"] == "error"

    def test_write_refuses_to_save_an_unverified_patch(self, tmp_source, key, monkeypatch):
        # generate -> fail -> diagnose code_bug -> patch (source changes) ->
        # re-run -> still failing -> budget spent. code_patched is True, but
        # nothing was ever proven, so no .fixed.py may appear on disk.
        llm = ScriptedLLM(
            script=[
                fenced(GOOD_TEST),
                '{"verdict": "code_bug", "reasoning": "r", "instructions": "i"}',
                fenced("def add(a, b):\n    return a + b + 1\n"),
            ]
        )
        monkeypatch.setattr("testpilot.agent.loop.make_llm", lambda config=None: llm)
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "-n", "1", "--write", "--no-diff"])

        assert result.exit_code == 1, result.output
        assert not path.with_name("target.fixed.py").exists()
        assert "--write refused" in result.output
        assert path.read_text(encoding="utf-8") == BUGGY_SOURCE
