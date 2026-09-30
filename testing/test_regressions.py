"""Regression tests: recently-fixed bugs locked down so they stay fixed.

Each test here maps to a bug that shipped at least once: brace-blind JSON
extraction, first-block-wins code picking, summary-line misparsing (skips,
unittest fallback, stdout inflation), non-sticky patch flags, and rich markup
injection in the CLI.
"""

import httpx
import pytest
from conftest import ScriptedLLM, fenced
from rich.markup import escape

from testpilot import cli
from testpilot.agent.patch import node_patch
from testpilot.agent.state import AgentState
from testpilot.parsing import extract_code, extract_json
from testpilot.sandbox.base import RunResult
from testpilot.sandbox.local import LocalSubprocessSandbox


def parse(output: str) -> RunResult:
    return LocalSubprocessSandbox._parse(output)


class TestRetryPolicy:
    """Locking down 429 handling: the first version capped *every* wait at 8s,
    so it retried before Groq's own `retry-after: 15` had elapsed and the run
    gave up twice in a row on files that pass standalone."""

    def test_provider_hint_is_not_truncated_by_the_fallback_cap(self):
        from testpilot.llm import _retry_delay

        response = httpx.Response(429, headers={"retry-after": "15"})
        assert _retry_delay(response, 0) == 15.0

    def test_millisecond_header_is_read_as_milliseconds(self):
        from testpilot.llm import _retry_delay

        response = httpx.Response(429, headers={"retry-after-ms": "9112"})
        assert _retry_delay(response, 0) == pytest.approx(9.112)

    def test_hint_in_the_body_when_the_header_is_absent(self):
        # Groq puts "Please try again in 14.025s." in the JSON body.
        from testpilot.llm import _retry_delay

        response = httpx.Response(
            429, json={"error": {"message": "Please try again in 14.025s."}}
        )
        assert _retry_delay(response, 0) == pytest.approx(14.025)

    def test_falls_back_to_exponential_when_there_is_no_hint(self):
        from testpilot.llm import _retry_delay

        response = httpx.Response(429, json={"error": {"message": "slow down"}})
        assert _retry_delay(response, 0) == 1.0
        assert _retry_delay(response, 3) == 8.0  # fallback cap holds

    def test_an_absurd_hint_cannot_hang_the_process_forever(self):
        from testpilot.llm import _MAX_HINT_WAIT, _retry_delay

        response = httpx.Response(429, headers={"retry-after": "9999"})
        assert _retry_delay(response, 0) == _MAX_HINT_WAIT

    def test_throttling_is_retried_but_a_bad_model_id_is_not(self):
        # Retrying 404 would sleep three times for an error that never heals.
        from testpilot.llm import _RETRYABLE_STATUS

        assert 429 in _RETRYABLE_STATUS
        assert 500 in _RETRYABLE_STATUS
        assert 404 not in _RETRYABLE_STATUS
        assert 401 not in _RETRYABLE_STATUS

    def test_there_are_more_attempts_than_a_single_burst_needs(self):
        from testpilot.llm import _MAX_ATTEMPTS

        assert _MAX_ATTEMPTS >= 4


class TestExtractJson:
    def test_nested_object_surrounded_by_prose(self):
        # Regression: find("{")..rfind("}") / non-greedy regex broke on nesting.
        text = (
            'Reasoning first {"verdict": "code_bug", "meta": {"nested": {"deep": 1}},'
            ' "instructions": "fix"} trailing prose'
        )
        data = extract_json(text)
        assert data["verdict"] == "code_bug"
        assert data["meta"] == {"nested": {"deep": 1}}
        assert data["instructions"] == "fix"

    def test_closing_brace_inside_string_value(self):
        # Regression: a "}" inside a string used to cut the object short.
        data = extract_json('{"reasoning": "use } here", "verdict": "unclear"}')
        assert data["verdict"] == "unclear"
        assert data["reasoning"] == "use } here"

    def test_no_json_at_all_still_raises(self):
        with pytest.raises(ValueError):
            extract_json("The verdict is unclear and I could not format it as JSON.")


class TestExtractCode:
    def test_prefers_longer_replacement_over_stale_snippet(self):
        # Companion to test_returns_longest_block_when_multiple: models often
        # restate a truncated file before the full replacement — keep the full one.
        text = (
            "```python\ndef add(a, b):\n    return a - b\n```\n\n"
            "Corrected version:\n"
            "```python\ndef add(a, b):\n    return a + b\n\n\n"
            "def helper():\n    return 1\n```"
        )
        assert extract_code(text) == (
            "def add(a, b):\n    return a + b\n\n\ndef helper():\n    return 1"
        )


class TestSandboxParse:
    def test_two_passed_is_green(self):
        result = parse("2 passed in 0.12s")
        assert result.passed == 2
        assert result.failed == 0 and result.errors == 0 and result.skipped == 0
        assert result.total == 2
        assert result.all_green and result.collected

    def test_failure_and_pass_counts(self):
        result = parse("1 failed, 2 passed in 0.31s")
        assert result.passed == 2
        assert result.failed == 1
        assert result.total == 3
        assert not result.all_green

    def test_no_tests_ran_is_not_collected(self):
        result = parse("no tests ran in 0.01s")
        assert result.total == 0
        assert not result.collected
        assert not result.all_green

    def test_single_error_counts_as_error(self):
        result = parse("1 error in 0.05s")
        assert result.errors == 1
        assert result.total == 1
        assert not result.all_green

    def test_skipped_run_is_collected_not_errored(self):
        # Regression: an all-skipped run used to land as errors=1 / total=0,
        # which both claimed "collection failed" and hid the real skip count.
        result = parse("1 skipped in 0.01s")
        assert result.skipped == 1
        assert result.passed == 0 and result.failed == 0 and result.errors == 0
        assert result.total == 1
        assert result.collected

    def test_skipped_and_passed_is_green(self):
        result = parse("1 skipped, 3 passed in 0.02s")
        assert result.passed == 3 and result.skipped == 1
        assert result.total == 4
        assert result.all_green

    def test_counts_come_from_summary_not_stdout(self):
        # Regression: counts were read from the whole buffer, so a test's own
        # stdout could flip a red run to green. The summary line comes last.
        result = parse("printing from a test\n1 failed in 0.10s")
        assert result.failed == 1
        assert result.passed == 0
        assert not result.all_green

    def test_noisy_stdout_cannot_inflate_totals(self):
        # Stronger shape of the same bug: stdout contains a fake green summary.
        result = parse("3 passed printed by a noisy test\n1 failed in 0.10s")
        assert result.passed == 0
        assert result.failed == 1
        assert result.total == 1
        assert not result.all_green

    def test_unittest_ok_counts_passed(self):
        # Regression: "Ran 4 tests ... OK" used to report "no tests collected".
        output = (
            "----------------------------------------------------------------------\n"
            "Ran 4 tests in 0.001s\n\nOK"
        )
        result = parse(output)
        assert result.passed == 4
        assert result.total == 4
        assert result.all_green

    def test_unittest_failures_split_passed_and_failed(self):
        result = parse("Ran 2 tests in 0.001s\n\nFAILED (failures=1)")
        assert result.passed == 1
        assert result.failed == 1
        assert result.total == 2
        assert not result.all_green

    def test_unittest_errors_do_not_fabricate_failure(self):
        # Regression: FAILED (errors=1) used to also report 1 fabricated failure.
        result = parse("Ran 2 tests in 0.001s\n\nFAILED (errors=1)")
        assert result.errors == 1
        assert result.failed == 0
        assert result.passed == 1
        assert result.total == 2
        assert not result.all_green


class TestRunResultSummary:
    def test_empty_run_is_never_green(self):
        # total == 0 means nothing ran, however the counters got set.
        result = RunResult()
        assert result.total == 0
        assert not result.all_green
        assert result.summary == "no tests collected"

    def test_zero_total_never_claims_green_even_with_counts(self):
        result = RunResult(passed=2)
        assert result.total == 0
        assert not result.all_green
        assert result.summary == "no tests collected"

    def test_summary_formats_skipped(self):
        result = parse("1 skipped in 0.01s")
        assert "skipped" in result.summary
        assert result.summary == "1 skipped"

    def test_all_skipped_is_collected_but_not_green(self):
        # Collected (diagnose can see it) but nothing was verified, so claiming
        # "TESTS GREEN" / exit 0 would be dishonest.
        result = parse("1 skipped in 0.01s")
        assert result.collected
        assert result.skipped == 1
        assert not result.all_green

    def test_partial_skips_still_green(self):
        # Skips alongside real passes are fine — those tests did verify.
        result = parse("1 skipped, 3 passed in 0.02s")
        assert result.passed == 3
        assert result.all_green


class TestPatchStickyFlags:
    def test_noop_patch_does_not_clear_code_patched(self):
        # First patch changes the source; the second returns it unchanged.
        # The sticky flag must survive the no-op (else --write silently no-ops).
        llm = ScriptedLLM(script=[fenced("x = 2\n"), fenced("x = 2\n")])
        node = node_patch(llm)
        state = {
            "current_source": "x = 1\n",
            "test_code": "def test_x():\n    pass",
            "diagnosis": {"verdict": "code_bug", "reasoning": "r", "instructions": "i"},
            "iteration": 0,
        }

        update = node(state)
        assert update["code_patched"] is True  # new != old on the first patch
        state.update(update)

        update = node(state)
        assert "unchanged" in update["notes"][0]  # second patch was a no-op
        assert update["code_patched"] is True  # bool(prev) or (new != old)
        assert state["current_source"] == "x = 2\n"

    def test_noop_patch_does_not_clear_tests_patched(self):
        llm = ScriptedLLM(script=[fenced("def test_new():\n    assert True\n")] * 2)
        node = node_patch(llm)
        state = {
            "current_source": "x = 1",
            "test_code": "def test_old():\n    pass",
            "diagnosis": {"verdict": "test_bug", "reasoning": "r", "instructions": "i"},
            "iteration": 0,
        }

        update = node(state)
        assert update["tests_patched"] is True
        state.update(update)

        update = node(state)
        assert update["tests_patched"] is True


class TestAgentStateContract:
    def test_bookkeeping_keys_are_declared(self):
        # run_file() never writes duration (the CLI does); declaring it on the
        # TypedDict keeps the contract honest. The rest are run bookkeeping.
        expected = {"duration", "status", "notes", "history", "max_iterations"}
        assert expected <= set(AgentState.__annotations__)


class TestCliMarkupSafety:
    def test_cli_escapes_rich_markup(self):
        # Regression: exception text containing "[red]" was fed raw to
        # console.print and blew up rich's markup parser mid-error-report.
        assert cli.escape is escape


class TestModuleNameContract:
    """HARD CONTRACT: prompts.py and sandbox/local.py both spell the module name.

    The generated tests do `import testpilot_target`, and the sandbox writes the
    source to `<that name>.py`. The string lives in two files with nothing tying
    them together — if either side changes, every single run dies with
    ModuleNotFoundError while the test suite stays green. Lock them together.
    """

    def test_sandbox_writes_the_module_name_prompts_advertise(self):
        from testpilot.prompts import MODULE_NAME
        from testpilot.sandbox.local import _MODULE_NAME

        assert _MODULE_NAME == MODULE_NAME

    def test_generated_import_is_actually_present_in_prompts(self):
        # GENERATE_SYSTEM is .format(module=...)'d — assert on the *rendered*
        # prompt, which is what the model actually receives and what the
        # sandbox's import has to agree with.
        from testpilot.prompts import MODULE_NAME, generate_messages

        rendered = generate_messages("def f(): return 1")[0]["content"]
        assert MODULE_NAME in rendered
        assert f"import {MODULE_NAME}" in rendered


class TestNoFakeGreenRules:
    """Two load-bearing prompt rules, locked down so they stay in.

    Observed live: a spec demanding two mutually exclusive rules was diagnosed
    `code_bug` on round 1 (no patch can satisfy both), then on round 2 the model
    flipped the verdict to `test_bug` — its own reasoning admitting the
    requirement was "impossible given the code and the specification" — and
    rewrote the suite to drop it. The counts came out identical (10 -> 10 tests,
    18 -> 18 asserts), so `integrity.py` could not see the difference and the run
    reported green.

    Counts cannot catch that; only the prompts can. Removing either rule below
    re-opens the hole, so assert on the exact strings that carry them.

    The prompts are hard-wrapped, so compare against whitespace-normalised text
    — asserting on a phrase that happens to sit across a line break would make
    this test fail for the wrong reason.
    """

    @staticmethod
    def _flat(text: str) -> str:
        return " ".join(text.split())

    def test_diagnose_must_not_break_a_spec_contradiction_by_blaming_one_side(self):
        from testpilot.prompts import DIAGNOSE_SYSTEM

        flat = self._flat(DIAGNOSE_SYSTEM)
        assert "mutually exclusive" in flat
        assert "deleting a requirement the docstring states" in flat

    def test_diagnose_keeps_the_counter_rule_that_unclear_is_not_a_default(self):
        # The new rule must not swallow the old one: an under-specified detail
        # still has a right answer and still needs a verdict.
        from testpilot.prompts import DIAGNOSE_SYSTEM

        assert "NEVER choose unclear" in DIAGNOSE_SYSTEM

    def test_a_docstring_restating_test_can_never_be_the_blamed_side(self):
        # Observed live: 14 real code bugs fixed on round 1 left two failures —
        # one genuine test bug plus one self-contradictory spec. The single
        # verdict bundled them as `test_bug`, so rewriting the suite to fix the
        # stray test ALSO dropped the inconvenient requirement, and the run
        # reported green (16 -> 16 tests, 47 -> 47 asserts, invisible to the
        # count-based integrity guard).
        from testpilot.prompts import DIAGNOSE_SYSTEM

        flat = " ".join(DIAGNOSE_SYSTEM.split())
        assert "merely restates what the source's docstring requires is NEVER" in flat
        assert "only when no failing test does that" in flat

    def test_patch_is_forbidden_to_rewrite_a_test_that_quotes_the_docstring(self):
        from testpilot.prompts import PATCH_SYSTEM

        flat = self._flat(PATCH_SYSTEM)
        assert "docstring explicitly requires" in flat
        assert "fakes a pass" in flat
