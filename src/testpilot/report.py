"""Final report: rich terminal output + unified diff.

Hard rule from the plan: never silently overwrite the user's original file.
If code was patched, we write `<name>.fixed.py` and show a unified diff.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .agent.state import (
    STATUS_ERROR,
    STATUS_GAVE_UP,
    STATUS_GREEN,
    STATUS_RUNNING,
    STATUS_UNCLEAR,
    AgentState,
)

_STATUS_STYLES = {
    STATUS_GREEN: ("green", "✅ TESTS GREEN"),
    STATUS_UNCLEAR: ("yellow", "🤔 UNCLEAR — honest stop"),
    STATUS_GAVE_UP: ("red", "🛑 GAVE UP — budget exhausted"),
    # Not the budget's fault: rate limit, timeout, unparseable diagnosis or no
    # test code at all. Kept separate so a 429 never reads as "you ran out of
    # iterations" — the whole point is reporting honestly.
    STATUS_ERROR: ("red", "❌ ERROR — run stopped early"),
    STATUS_RUNNING: ("blue", "RUNNING"),
}


def status_label(status: str) -> str:
    """Human-readable label for a run status (shared by render and the CLI)."""
    return _STATUS_STYLES.get(status, ("white", escape(str(status)).upper()))[1]


def unified_diff(original: str, current: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def write_fixed_file(state: AgentState) -> Path | None:
    """Persist the patched source as `<name>.fixed.py` (original untouched)."""
    if not state.get("code_patched"):
        return None
    original_path = Path(state["source_path"])
    fixed_path = original_path.with_name(original_path.stem + ".fixed.py")
    fixed_path.write_text(state["current_source"], encoding="utf-8")
    return fixed_path


def render(state: AgentState, console: Console | None = None, *, show_diff: bool = True) -> None:
    """Print the report. Read-only: never writes files (see write_fixed_file)."""
    console = console or Console()
    status = state.get("status", "running")
    style, label = _STATUS_STYLES.get(status, ("white", escape(str(status)).upper()))

    run = state.get("last_run")
    summary = escape(run.summary) if run else "no run"
    header = (
        f"[bold {style}]{label}[/]   "
        f"iterations: {state.get('iteration', 0)}/{state.get('max_iterations', 0)}   "
        f"last run: {summary}"
    )
    # source_path comes from the filesystem and the summary/details from pytest
    # and the LLM — all untrusted as Rich markup. A stray `[` in either is
    # swallowed, a stray `[/x]` raises MarkupError and hides the whole report.
    console.print(
        Panel(header, title=f"[bold]TestPilot · {escape(str(state['source_path']))}[/]", expand=False)
    )

    # History timeline
    table = Table(title="Loop history", show_lines=False, expand=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("step", style="cyan", width=10)
    table.add_column("detail")
    for entry in state.get("history", []):
        table.add_row(
            str(entry.get("iteration", "")),
            str(entry.get("step", "")),
            escape(str(entry.get("detail", ""))),
        )
    console.print(table)

    # Diagnosis, when we have one
    diagnosis = state.get("diagnosis")
    if diagnosis:
        verdict_style = {"code_bug": "red", "test_bug": "magenta", "unclear": "yellow"}.get(
            diagnosis.get("verdict"), "white"
        )
        console.print(
            f"[bold]Diagnosis:[/] [{verdict_style}]{escape(str(diagnosis.get('verdict')))}[/] — "
            f"{escape(str(diagnosis.get('reasoning', '')))}"
        )

    # Diff — the original file is never modified here; saving is explicit via --write
    diff = unified_diff(state["original_source"], state["current_source"], state["source_path"])
    if diff and show_diff:
        console.print("\n[bold]Suggested fix for the source file:[/]")
        console.print(Syntax(diff, "diff", theme="monokai", word_wrap=False))
        if status == STATUS_GREEN:
            console.print(
                "[dim](your original file was not touched; re-run with --write to save a .fixed.py)[/]"
            )
        else:
            console.print(
                "[dim](your original file was not touched; --write only saves a "
                ".fixed.py from a run that actually went green)[/]"
            )

    if state.get("test_code") and status == STATUS_UNCLEAR:
        console.print("[dim]Generated tests were kept but not applied to your file.[/]")
