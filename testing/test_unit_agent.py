"""Unit tests: diagnose + patch nodes with a scripted test double (no credits)."""


from conftest import ScriptedLLM

from testpilot.agent.diagnose import diagnose, node_diagnose
from testpilot.agent.patch import node_patch, patch_code, patch_test
from testpilot.sandbox.base import RunResult


def run_result(output: str = "1 failed in 0.1s", failed: int = 1) -> RunResult:
    return RunResult(passed=0, failed=failed, total=failed, raw_output=output)


class TestDiagnose:
    def test_valid_json_verdict(self):
        llm = ScriptedLLM(responses={"diagnose": ['{"verdict": "code_bug", "reasoning": "r", "instructions": "i"}']})
        out = diagnose(llm, "src", "tests", run_result())
        assert out["verdict"] == "code_bug"
        assert out["reasoning"] == "r"
        assert out["instructions"] == "i"

    def test_invalid_verdict_becomes_an_error(self):
        # An invented verdict is a model failure, not an ambiguity in the code.
        llm = ScriptedLLM(responses={"diagnose": ['{"verdict": "banana", "reasoning": "x"}']})
        out = diagnose(llm, "src", "tests", run_result())
        assert out["verdict"] == "unclear"      # still routes to REPORT
        assert "error" in out                   # but is reported as an error
        assert "banana" in out["error"]

    def test_unparseable_output_retries_once_then_succeeds(self):
        llm = ScriptedLLM(
            responses={
                "diagnose": [
                    "I am not sure what to say here.",
                    '{"verdict": "code_bug", "reasoning": "r", "instructions": "i"}',
                ]
            }
        )
        out = diagnose(llm, "src", "tests", run_result())
        assert out["verdict"] == "code_bug"
        assert "error" not in out
        assert len(llm.calls) == 2, "must re-prompt with feedback before giving up"

    def test_unparseable_output_twice_is_an_error_not_unclear(self):
        # Two unparseable replies = the model never answered. Claiming
        # "honest stop, I can't tell who is wrong" would be a lie.
        llm = ScriptedLLM(
            responses={"diagnose": ["I am not sure what to say here.", "nor do I know."]}
        )
        out = diagnose(llm, "src", "tests", run_result())
        assert out["verdict"] == "unclear"      # keeps the router pointed at REPORT
        assert "error" in out                   # the report shows ERROR, not UNCLEAR
        assert "unparseable" in out["reasoning"]

    def test_missing_keys_default_safely(self):
        llm = ScriptedLLM(responses={"diagnose": ['{"verdict": "test_bug"}']})
        out = diagnose(llm, "src", "tests", run_result())
        assert out["verdict"] == "test_bug"
        assert out["reasoning"] == "" and out["instructions"] == ""

    def test_node_records_history(self):
        llm = ScriptedLLM(responses={"diagnose": ['{"verdict": "code_bug", "reasoning": "r"}']})
        node = node_diagnose(llm)
        update = node({"current_source": "s", "test_code": "t", "last_run": run_result(), "iteration": 1})
        assert update["diagnosis"]["verdict"] == "code_bug"
        assert update["history"][0]["step"] == "diagnose"


class TestPatch:
    def test_patch_code_applies_valid_python(self):
        llm = ScriptedLLM(responses={"patch": ["```python\nx = 2\n```"]})
        new_source, note = patch_code(llm, "x = 1", "r", "double it")
        assert new_source == "x = 2\n"
        assert "updated" in note

    def test_patch_code_rejects_invalid_python(self):
        llm = ScriptedLLM(responses={"patch": ["```python\ndef broken(:\n```"]})
        new_source, note = patch_code(llm, "x = 1", "r", "fix")
        assert new_source == "x = 1"
        assert "invalid" in note

    def test_patch_code_keeps_source_when_model_returns_same(self):
        llm = ScriptedLLM(responses={"patch": ["```python\nx = 1\n```"]})
        new_source, note = patch_code(llm, "x = 1", "r", "fix")
        assert new_source == "x = 1"
        assert "unchanged" in note

    def test_patch_test_rewrites_tests(self):
        llm = ScriptedLLM(responses={"patch": ["```python\ndef test_new():\n    assert True\n```"]})
        new_tests, note = patch_test(llm, "src", "old", "r", "i")
        assert "def test_new" in new_tests
        assert "rewritten" in note

    def test_node_test_bug_bumps_tests_flag(self):
        llm = ScriptedLLM(responses={"patch": ["```python\ndef test_fixed():\n    assert True\n```"]})
        node = node_patch(llm)
        update = node(
            {
                "current_source": "s",
                "test_code": "def test_old():\n    pass",
                "diagnosis": {"verdict": "test_bug", "reasoning": "r", "instructions": "i"},
                "iteration": 0,
            }
        )
        assert update["tests_patched"] is True
        assert update["iteration"] == 1

    def test_node_code_bug_bumps_code_flag(self):
        llm = ScriptedLLM(responses={"patch": ["```python\ny = 2\n```"]})
        node = node_patch(llm)
        update = node(
            {
                "current_source": "y = 1",
                "test_code": "t",
                "diagnosis": {"verdict": "code_bug", "reasoning": "r", "instructions": "i"},
                "iteration": 0,
            }
        )
        assert update["code_patched"] is True

    def test_node_unclear_does_not_patch_but_advances(self):
        llm = ScriptedLLM()
        node = node_patch(llm)
        update = node(
            {
                "current_source": "y = 1",
                "test_code": "t",
                "diagnosis": {"verdict": "unclear", "reasoning": "ambiguous", "instructions": ""},
                "iteration": 2,
            }
        )
        # No key -> LangGraph leaves the field untouched (last-write-wins channel).
        assert "current_source" not in update
        assert "test_code" not in update
        assert "unclear" in update["history"][0]["detail"]
