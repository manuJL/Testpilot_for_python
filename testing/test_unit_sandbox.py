"""Unit tests: local subprocess sandbox (isolation, parsing, timeout)."""

import textwrap

from testpilot.sandbox.local import LocalSubprocessSandbox


def sandbox() -> LocalSubprocessSandbox:
    return LocalSubprocessSandbox()


PASSING = textwrap.dedent(
    """\
    import testpilot_target as t

    def test_ok():
        assert t.double(4) == 8
    """
)

FAILING = textwrap.dedent(
    """\
    import testpilot_target as t

    def test_bad():
        assert t.double(4) == 9
    """
)


class TestRun:
    def test_all_passing(self):
        result = sandbox().run("def double(x):\n    return x * 2\n", PASSING)
        assert result.all_green
        assert result.passed == 1 and result.failed == 0
        assert not result.timed_out

    def test_failing_test_is_reported(self):
        result = sandbox().run("def double(x):\n    return x * 2\n", FAILING)
        assert not result.all_green
        assert result.failed == 1
        assert "AssertionError" in result.raw_output or "assert" in result.raw_output

    def test_no_tests_collected(self):
        result = sandbox().run("x = 1\n", "import testpilot_target\n")
        assert not result.collected
        assert not result.all_green

    def test_broken_test_file_is_an_error(self):
        result = sandbox().run("x = 1\n", "def test_oops(:\n    pass\n")
        assert not result.all_green
        assert result.errors >= 1 or not result.collected

    def test_timeout_is_capped(self):
        slow = "def test_hang():\n    while True:\n        pass\n"
        result = sandbox().run("x = 1\n", slow, timeout=3)
        assert result.timed_out
        assert not result.all_green

    def test_api_keys_do_not_leak_into_sandbox(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_secret_should_not_leak")
        probe = textwrap.dedent(
            """\
            import os

            def test_no_key():
                assert "GROQ_API_KEY" not in os.environ
            """
        )
        result = sandbox().run("x = 1\n", probe)
        assert result.all_green, result.raw_output
        assert "gsk_secret_should_not_leak" not in result.raw_output

    def test_original_source_not_written_to_disk(self):
        import os
        import tempfile

        before = set(os.listdir(tempfile.gettempdir()))
        sandbox().run("def f():\n    return 1\n", PASSING)
        new = set(os.listdir(tempfile.gettempdir())) - before
        assert not any("testpilot_" in name for name in new), new
