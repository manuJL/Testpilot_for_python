"""Sandbox backends: where untrusted / AI-generated code actually runs."""

from .base import RunResult, Sandbox
from .local import LocalSubprocessSandbox

__all__ = ["LocalSubprocessSandbox", "RunResult", "Sandbox"]
