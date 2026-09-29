"""Unit tests: routing decisions of the LangGraph (who goes where, when)."""

from testpilot.agent.loop import (
    DIAGNOSE,
    PATCH,
    REPORT,
    _route_after_diagnose,
    _route_after_run,
)
from testpilot.sandbox.base import RunResult


def green_run() -> RunResult:
    return RunResult(passed=3, failed=0, errors=0, total=3)


def red_run() -> RunResult:
    return RunResult(passed=1, failed=2, errors=0, total=3)


class TestRouteAfterRun:
    def test_green_goes_to_report(self):
        assert _route_after_run({"last_run": green_run(), "iteration": 0, "max_iterations": 4}) == REPORT

    def test_red_with_budget_goes_to_diagnose(self):
        assert _route_after_run({"last_run": red_run(), "iteration": 0, "max_iterations": 4}) == DIAGNOSE

    def test_red_without_budget_goes_to_report(self):
        assert _route_after_run({"last_run": red_run(), "iteration": 4, "max_iterations": 4}) == REPORT

    def test_missing_run_goes_to_diagnose(self):
        assert _route_after_run({"last_run": None, "iteration": 0, "max_iterations": 4}) == DIAGNOSE


class TestRouteAfterDiagnose:
    def test_unclear_goes_to_report(self):
        state = {"diagnosis": {"verdict": "unclear"}, "iteration": 0, "max_iterations": 4}
        assert _route_after_diagnose(state) == REPORT

    def test_code_bug_goes_to_patch(self):
        state = {"diagnosis": {"verdict": "code_bug"}, "iteration": 0, "max_iterations": 4}
        assert _route_after_diagnose(state) == PATCH

    def test_test_bug_goes_to_patch(self):
        state = {"diagnosis": {"verdict": "test_bug"}, "iteration": 1, "max_iterations": 4}
        assert _route_after_diagnose(state) == PATCH

    def test_budget_spent_goes_to_report_even_if_fixable(self):
        state = {"diagnosis": {"verdict": "code_bug"}, "iteration": 4, "max_iterations": 4}
        assert _route_after_diagnose(state) == REPORT

    def test_missing_diagnosis_defaults_to_report(self):
        state = {"diagnosis": None, "iteration": 0, "max_iterations": 4}
        assert _route_after_diagnose(state) == REPORT
