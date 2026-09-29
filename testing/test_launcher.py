"""Offline tests for the batch launcher (`run_testpilot.py` at the project root).

The launcher is stdlib-only and lives outside the package, so it is loaded by
path rather than imported. Nothing here touches the network or an API key: the
whole point is that discovery, filtering and reporting can be trusted without
spending a request.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_launcher():
    spec = importlib.util.spec_from_file_location("run_testpilot", ROOT / "run_testpilot.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_testpilot"] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


# --- test-file detection ----------------------------------------------------


class TestTestFileDetection:
    def test_test_prefix_is_skipped(self):
        assert launcher._looks_like_test("test_helpers.py") is True

    @pytest.mark.parametrize("name", ["correct_code_wrong_test.py", "my_test.py", "pytest.py"])
    def test_suffix_form_is_NOT_skipped(self, name):
        # Regression: an earlier rule matched `_test.py` and silently dropped
        # one of our own demo files from the batch.
        assert launcher._looks_like_test(name) is False


class TestEligibility:
    def test_previous_fix_output_is_never_retargeted(self, tmp_path):
        ok, reason = launcher._eligible(tmp_path / "foo.fixed.py")
        assert ok is False
        assert "already-fixed" in reason

    def test_support_files_are_skipped(self, tmp_path):
        ok, reason = launcher._eligible(tmp_path / "conftest.py")
        assert ok is False
        assert "support file" in reason

    def test_files_inside_the_temp_suite_are_skipped(self, tmp_path):
        inside = tmp_path / "testing" / "whatever.py"
        ok, reason = launcher._eligible(inside)
        assert ok is False
        assert "skipped directory" in reason

    def test_a_real_source_file_is_eligible(self, tmp_path):
        ok, reason = launcher._eligible(tmp_path / "app.py")
        assert (ok, reason) == (True, "")

    def test_include_tests_opts_a_test_file_back_in(self, tmp_path):
        path = tmp_path / "test_something.py"
        assert launcher._eligible(path, include_tests=False)[0] is False
        assert launcher._eligible(path, include_tests=True)[0] is True


# --- turning arguments into targets ----------------------------------------


class TestResolveOne:
    def test_directory_is_walked_recursively(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "pkg" / "b.py").write_text("x = 2\n")
        files, error = launcher.resolve_one(str(tmp_path))
        assert error is None
        assert {f.name for f in files} == {"a.py", "b.py"}

    def test_bare_stem_completes_to_a_py_file(self, tmp_path):
        # `python`, `2`, `python1` should all just work without the extension.
        (tmp_path / "python1.py").write_text("x = 1\n")
        files, error = launcher.resolve_one(str(tmp_path / "python1"))
        assert error is None
        assert [f.name for f in files] == ["python1.py"]

    def test_missing_path_reports_an_error_instead_of_raising(self, tmp_path):
        files, error = launcher.resolve_one(str(tmp_path / "nope"))
        assert files == []
        assert "does not exist" in (error or "")

    def test_non_python_file_is_rejected(self, tmp_path):
        note = tmp_path / "notes.txt"
        note.write_text("hi\n")
        files, error = launcher.resolve_one(str(note))
        assert files == []
        assert "not a .py file" in (error or "")

    def test_explicit_test_file_is_rejected_unless_opted_in(self, tmp_path):
        test_file = tmp_path / "test_x.py"
        test_file.write_text("def test_a(): pass\n")
        assert launcher.resolve_one(str(test_file))[0] == []
        files, error = launcher.resolve_one(str(test_file), include_tests=True)
        assert error is None
        assert len(files) == 1

    def test_directory_with_no_eligible_files_says_so(self, tmp_path):
        (tmp_path / "conftest.py").write_text("")
        files, error = launcher.resolve_one(str(tmp_path))
        assert files == []
        assert "no eligible" in (error or "")


class TestDiscover:
    def test_duplicate_targets_appear_once(self, tmp_path):
        target = tmp_path / "app.py"
        target.write_text("x = 1\n")
        targets, errors = launcher.discover([str(target), str(target), str(tmp_path)])
        assert errors == []
        assert targets == [target.resolve()]

    def test_one_bad_path_does_not_discard_the_good_ones(self, tmp_path):
        good = tmp_path / "app.py"
        good.write_text("x = 1\n")
        targets, errors = launcher.discover([str(tmp_path / "missing"), str(good)])
        assert len(targets) == 1
        assert len(errors) == 1


# --- reporting --------------------------------------------------------------


class TestResultSerialisation:
    def test_as_dict_is_json_serialisable(self, tmp_path):
        result = launcher.Result(path=tmp_path / "a.py", status="green", detail="3 passed")
        payload = json.loads(json.dumps(result.as_dict()))
        assert payload["status"] == "green"
        assert payload["file"].endswith("a.py")

    def test_stats_show_duration_even_for_a_zero_iteration_run(self):
        # A file that is green on the first run has iterations == 0; hiding the
        # timing entirely made fast runs look like they had reported nothing.
        stats = launcher._stats(launcher.Result(path=Path("a.py"), duration=3.4))
        assert "3.4s" in stats

    def test_stats_are_empty_when_there_is_nothing_to_show(self):
        assert launcher._stats(launcher.Result(path=Path("a.py"))) == ""

    @pytest.mark.parametrize(
        ("results", "expected"),
        [
            (["green"], launcher.EXIT_OK),
            (["green", "not green"], launcher.EXIT_NOT_GREEN),
            (["green", "error"], launcher.EXIT_CANNOT_RUN),
            (["timeout"], launcher.EXIT_CANNOT_RUN),
        ],
    )
    def test_summary_exit_codes(self, results, expected, capsys):
        batch = [launcher.Result(path=Path(f"{i}.py"), status=s) for i, s in enumerate(results)]
        assert launcher.print_summary(batch, colour_on=False) == expected
        capsys.readouterr()


class TestColour:
    def test_disabled_colour_emits_no_escape_sequences(self):
        assert launcher.colour("PASS", "32", False) == "PASS"

    def test_enabled_colour_wraps_the_text(self):
        assert launcher.colour("PASS", "32", True) == "\x1b[32mPASS\x1b[0m"


class TestJsonExtraction:
    def test_pure_json_object(self):
        assert launcher._extract_json('{"status": "green"}') == {"status": "green"}

    def test_json_surrounded_by_prose(self):
        text = 'noise before {"status": "green"} noise after'
        assert launcher._extract_json(text) == {"status": "green"}

    @pytest.mark.parametrize("output", ["", "not json at all"])
    def test_unusable_output_returns_none(self, output):
        assert launcher._extract_json(output) is None


# --- offline end-to-end -----------------------------------------------------


class TestLauncherEndToEnd:
    def test_help_exits_cleanly(self):
        with pytest.raises(SystemExit) as exc:
            launcher.build_parser().parse_args(["--help"])
        assert exc.value.code == 0

    def test_json_flag_is_exposed(self):
        assert launcher.build_parser().parse_args(["--json", "a.py"]).json is True

    def test_dry_run_needs_no_api_key(self, tmp_path, capsys, monkeypatch):
        # --dry-run must answer before any preflight, so it works with no
        # credentials configured at all.
        monkeypatch.setattr(launcher, "has_api_key", lambda: False)
        (tmp_path / "app.py").write_text("x = 1\n")
        code = launcher.main(["--dry-run", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == launcher.EXIT_OK
        assert "app.py" in out

    def test_missing_api_key_is_refused_with_the_exact_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launcher, "has_api_key", lambda: False)
        monkeypatch.setattr(launcher, "find_uv", lambda: "uv")
        (tmp_path / "app.py").write_text("x = 1\n")
        code = launcher.main([str(tmp_path)])
        # Returned before printing the banner: refusal happens during preflight.
        assert code == launcher.EXIT_CANNOT_RUN
        assert launcher.missing_key_message().startswith(
            "API key is not found. Please open .env and add the API key to continue."
        )

    def test_missing_uv_is_reported_as_a_runtime_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(launcher, "has_api_key", lambda: True)
        monkeypatch.setattr(launcher, "find_uv", lambda: None)
        (tmp_path / "app.py").write_text("x = 1\n")
        code = launcher.main([str(tmp_path)])
        assert code == launcher.EXIT_CANNOT_RUN
        assert "uv" in capsys.readouterr().err
