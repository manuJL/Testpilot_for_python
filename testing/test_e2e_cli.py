"""End-to-end: the actual CLI a judge would run.

`testpilot run <file>` through typer's CliRunner, with the LLM faked at the
make_llm seam so the real CLI code path (arg parsing, config, report, exit
codes) is exercised offline.
"""

from __future__ import annotations

import json

import pytest
from conftest import BUGGY_SOURCE, FIXED_SOURCE, code_bug_script
from typer.testing import CliRunner

from testpilot.cli import app

runner = CliRunner()


@pytest.fixture
def fake_llm(monkeypatch):
    """Swap the real HTTP LLM for a scripted one at the make_llm seam."""
    llm = code_bug_script()
    monkeypatch.setattr("testpilot.agent.loop.make_llm", lambda config=None: llm)
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")  # so config.resolve_provider passes
    return llm


class TestCliRun:
    def test_run_green_exit_code(self, tmp_source, fake_llm):
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--no-diff"])
        assert result.exit_code == 0, result.output
        assert "GREEN" in result.output

    def test_run_json_output(self, tmp_source, fake_llm):
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "green"
        assert payload["code_patched"] is True
        assert payload["last_run"]["failed"] == 0
        assert [h["step"] for h in payload["history"]][-1] == "report"

    def test_write_flag_creates_fixed_file(self, tmp_source, fake_llm):
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--write", "--no-diff"])
        assert result.exit_code == 0, result.output
        fixed = path.with_name("target.fixed.py")
        assert fixed.exists()
        assert fixed.read_text(encoding="utf-8") == FIXED_SOURCE
        assert path.read_text(encoding="utf-8") == BUGGY_SOURCE  # original untouched

    def test_missing_key_exits_2_with_friendly_message(self, tmp_source, monkeypatch):
        """No API key -> hard stop, exit 2, tells the user exactly what to do."""
        for var in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path)])
        assert result.exit_code == 2
        assert "API key is not found" in result.output
        assert "open .env and add the API key to continue" in result.output
        # The guard fires BEFORE any work happens (no node progress lines).
        assert "→ generate" not in result.output
        assert "Loop history" not in result.output

    def test_missing_key_stops_even_with_valid_file(self, tmp_source, monkeypatch):
        for var in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        path = tmp_source(BUGGY_SOURCE)
        result = runner.invoke(app, ["run", str(path), "--json"])
        assert result.exit_code == 2  # never reaches the JSON report

    def test_nonexistent_file_rejected_by_typer(self):
        result = runner.invoke(app, ["run", "/definitely/not/here.py"])
        assert result.exit_code != 0


class TestCliGraph:
    def test_graph_command_prints_topology(self):
        result = runner.invoke(app, ["graph"])
        assert result.exit_code == 0
        assert "generate" in result.output and "diagnose" in result.output

    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "testpilot" in result.output
