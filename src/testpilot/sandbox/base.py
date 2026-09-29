"""The Sandbox interface.

Two clean seams in TestPilot: `llm.py` hides the provider, this hides where
code runs. Swapping the local subprocess for E2B/Modal later means implementing
`run()` only — the agent loop never changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RunResult:
    """Outcome of one sandboxed pytest run."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    total: int = 0
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    raw_output: str = ""

    @property
    def all_green(self) -> bool:
        """Green means *verified*: at least one test actually ran and passed.

        A run of `1 skipped` collected fine (so `collected` is True and diagnose
        can still see the output), but nothing was checked — calling that
        "TESTS GREEN" and exiting 0 would be exactly the dishonesty this tool
        exists to avoid.
        """
        return (
            self.total > 0
            and self.passed > 0
            and self.failed == 0
            and self.errors == 0
            and not self.timed_out
        )

    @property
    def collected(self) -> bool:
        """Did pytest find any tests at all? (skips still count as collected)"""
        return self.total > 0

    @property
    def summary(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        if not self.collected:
            return "no tests collected"
        parts = []
        if self.passed:
            parts.append(f"{self.passed} passed")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.errors:
            parts.append(f"{self.errors} error{'s' if self.errors != 1 else ''}")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        return ", ".join(parts) or "no tests collected"


class Sandbox(Protocol):
    def run(self, source: str, test_code: str, timeout: int = 30) -> RunResult: ...
