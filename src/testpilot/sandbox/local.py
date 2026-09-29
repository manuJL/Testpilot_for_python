"""Local subprocess sandbox — primary backend for this hackathon.

Design goals: zero extra memory, no Docker, works on an 8 GB laptop.

Isolation is intentionally modest (temp dir + env scrub + wall-clock timeout):
this protects the demo from *accidents* (infinite loops, runaway output), not
from hostile code. The `Sandbox` interface exists so a real microVM backend
(E2B / Modal / Firecracker) can be dropped in later.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .base import RunResult

_MODULE_NAME = "testpilot_target"


# Per-stream cap read back from disk. pytest's summary line is at the end, so
# the tail is the part that matters; a noisy test can gigabytes of stdout and we
# still only ever hold this much in memory.
_MAX_TAIL_BYTES = 512 * 1024


def _read_tail(path: Path, limit: int = _MAX_TAIL_BYTES) -> str:
    """Read at most `limit` bytes from the end of a file (bounded memory)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                data = fh.read()
                # Drop the possibly-partial first line.
                data = data.split(b"\n", 1)[-1]
            else:
                data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace")


class LocalSubprocessSandbox:
    """Writes source + tests into a temp dir and runs `python -m pytest`."""

    def __init__(self, python: str | None = None, pytest_available: bool | None = None):
        self.python = python or sys.executable
        if pytest_available is None:
            pytest_available = self._has_pytest()
        self.pytest_available = pytest_available

    def _has_pytest(self) -> bool:
        try:
            proc = subprocess.run(
                [self.python, "-m", "pytest", "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,  # returncode is inspected by the caller
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def run(self, source: str, test_code: str, timeout: int = 30) -> RunResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="testpilot_") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / f"{_MODULE_NAME}.py").write_text(source, encoding="utf-8")
            test_file = tmp_path / "test_generated.py"
            test_file.write_text(test_code, encoding="utf-8")

            if self.pytest_available:
                cmd = [
                    self.python, "-m", "pytest", str(test_file),
                    "-q", "--no-header", "-p", "no:cacheprovider", "--tb=short",
                ]
            else:
                # Graceful degradation: run the file directly with unittest.
                cmd = [self.python, "-m", "unittest", "discover", "-s", tmp, "-p", "test_*.py"]

            env = self._clean_env()
            # Stream to files on disk rather than PIPEs: a runaway `while True:
            # print(...)` inside the timeout window would otherwise buffer
            # gigabytes into RAM (OOM on an 8 GB laptop). pytest prints its
            # summary line last, so we read the TAIL back, capped.
            out_path = tmp_path / "stdout.txt"
            err_path = tmp_path / "stderr.txt"
            timed_out = False
            with out_path.open("w", encoding="utf-8") as out_fh, err_path.open(
                "w", encoding="utf-8"
            ) as err_fh:
                try:
                    subprocess.run(
                        cmd,
                        cwd=tmp,
                        env=env,
                        stdout=out_fh,
                        stderr=err_fh,
                        timeout=timeout,
                        check=False,  # non-zero is normal: failing tests ARE the signal
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True

            stdout = _read_tail(out_path)
            stderr = _read_tail(err_path)

        combined = f"{stdout}\n{stderr}"
        result = self._parse(combined)
        result.stdout = stdout
        result.stderr = stderr
        result.timed_out = timed_out
        result.duration = time.monotonic() - started
        # On a timeout pytest is killed before flushing its internal capture, so
        # stdout is often empty — say so explicitly rather than handing diagnose
        # a blank string it can only answer "unclear" to.
        if timed_out and not combined.strip():
            combined = (
                f"Test run exceeded the {timeout}s wall-clock timeout and was killed. "
                "Output was lost (pytest had not flushed). A test is likely looping "
                "forever or blocking on input()."
            )
        result.raw_output = self._truncate(combined, 8000)
        # NOTE: no "fabricate errors" rescue here. _parse reads pytest's real
        # summary line ("1 error in 0.05s"), so a collection failure already
        # lands as errors=1 and an all-skipped run lands as skipped=1.
        return result

    def _clean_env(self) -> dict[str, str]:
        """Minimal environment: no API keys leak into generated code."""
        keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH")
        env = {k: v for k, v in os.environ.items() if k in keep}
        env["PYTHONHASHSEED"] = "0"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Make the package importable from generated tests if needed.
        src = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
        return env

    @staticmethod
    def _parse(output: str) -> RunResult:
        """Parse pytest (primary) or unittest (fallback) output.

        Counts are read ONLY from the final summary line, never from the whole
        buffer — otherwise a test that prints "2 passed" would inflate the
        totals, and a red run could be misread as green.
        """
        result = RunResult()
        lines = [ln for ln in output.splitlines() if ln.strip()]
        if not lines:
            return result

        count_re = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")
        # pytest always suffixes its summary with "in <t>s"
        # ("1 failed, 2 passed in 0.31s"). Requiring it is what separates the
        # real verdict from a test's own stdout: if pytest dies without writing
        # a summary (INTERNALERROR, segfault, OOM-kill), a test that printed
        # "3 passed" must NOT be mistaken for the result — that flips a red run
        # green. unittest's verdict carries no timing, so it is handled below.
        timing_re = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")

        for line in reversed(lines):
            if not timing_re.search(line):
                continue
            match = count_re.findall(line)
            if not match:
                continue
            for count, word in match:
                n = int(count)
                if word == "passed":
                    result.passed = n
                elif word.startswith("fail"):
                    result.failed = n
                elif word.startswith("error"):
                    result.errors = n
                elif word == "skipped":
                    result.skipped = n
                # xfailed/xpassed are not failures.
            result.total = result.passed + result.failed + result.errors + result.skipped
            return result

        # No pytest summary: distinguish "no tests" from the unittest fallback.
        if "no tests ran" in output or "collected 0 items" in output:
            return result

        # unittest fallback: "Ran 4 tests in 0.001s" then "OK" / "FAILED (...)".
        ran = re.search(r"Ran (\d+) tests?", output)
        if "FAILED" in output:
            failures = re.search(r"failures=(\d+)", output)
            err = re.search(r"errors=(\d+)", output)
            result.failed = int(failures.group(1)) if failures else 0
            result.errors = int(err.group(1)) if err else 0
            # "FAILED (errors=1)" means 0 failures — do not fabricate one.
            if not failures and not err:
                result.failed = 1
            total = int(ran.group(1)) if ran else result.failed + result.errors
            result.total = max(total, result.failed + result.errors)
            # unittest never counts the passing tests explicitly; derive them.
            result.passed = max(0, result.total - result.failed - result.errors)
            return result

        if ran and re.search(r"^\s*OK\s*$", output, re.MULTILINE):
            result.passed = int(ran.group(1))
            result.total = result.passed
        return result

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit // 2] + "\n... [truncated] ...\n" + text[-limit // 2 :]
