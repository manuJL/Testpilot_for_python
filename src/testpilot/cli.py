"""Typer entrypoint: `testpilot run file.py`."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .agent.loop import run_file
from .agent.state import AgentState
from .config import Config, MissingAPIKeyError
from .report import render

app = typer.Typer(
    name="testpilot",
    help="Generate pytest tests for a buggy Python file, run them, and fix the right thing.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


def _print_missing_key(exc: MissingAPIKeyError) -> None:
    """Loud, actionable stop when no API key is configured."""
    console.print()
    console.print("[bold red]✗ API key is not found. "
                  "Please open .env and add the API key to continue.[/]")
    for line in str(exc).splitlines()[1:]:  # skip the duplicated headline
        console.print(f"  [dim]{line}[/]", highlight=False, soft_wrap=True)
    console.print()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"testpilot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """TestPilot — the honest test-writing agent."""


@app.command("run")
def run(
    path: Path = typer.Argument(..., exists=True, dir_okay=False,  # noqa: B008 — typer's declarative API
                                readable=True, help="Python file to test."),
    max_iterations: int = typer.Option(4, "--max-iterations", "-n", min=1, max=10,
                                       help="Max diagnose→patch cycles."),
    provider: str = typer.Option("", "--provider", "-p",
                                 help="groq | openrouter | gemini (default: auto)."),
    model: str = typer.Option("", "--model", "-m",
                              help="Override the model ID for every step."),
    write: bool = typer.Option(False, "--write", "-w",
                               help="Write a .fixed.py next to the source."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    no_diff: bool = typer.Option(False, "--no-diff", help="Skip the unified diff in output."),
) -> None:
    """Run the full generate → run → diagnose → patch loop on PATH."""
    # Config's field factories parse TESTPILOT_TIMEOUT / TESTPILOT_TEMPERATURE,
    # so a malformed .env value raises here — must be inside the guard to keep
    # the 0/1/2 exit-code contract (2 = config error) instead of a traceback.
    try:
        config = Config(max_iterations=max_iterations, provider=provider)
        if model.strip():
            # Applies to both steps; empty means "use env vars / provider default".
            config.generate_model = model.strip()
            config.diagnose_model = model.strip()
        provider_name, _ = config.resolve_provider()
    except MissingAPIKeyError as exc:
        _print_missing_key(exc)
        raise typer.Exit(code=2)
    except (ValueError, TypeError) as exc:
        console.print(f"[red]error:[/] invalid value in .env: {escape(str(exc))}")
        raise typer.Exit(code=2)

    started = time.monotonic()

    def on_event(node: str, state: AgentState) -> None:
        if as_json:
            return
        last = state.get("last_run")
        detail = last.summary if (node == "run" and last) else ""
        console.print(f"  [dim]→[/] [cyan]{node}[/] {detail}")

    if not as_json:
        console.print(f"[bold]TestPilot[/] on [bold]{path}[/]  (provider: {provider_name})")

    try:
        state = run_file(path, config=config, on_event=on_event)
    except MissingAPIKeyError as exc:
        _print_missing_key(exc)
        raise typer.Exit(code=2)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error:[/] {escape(str(exc))}")
        raise typer.Exit(code=2)
    except Exception as exc:  # noqa: BLE001 — top-level guard: show a clean error, never a traceback
        console.print(f"[red]run failed:[/] {escape(str(exc))}")
        raise typer.Exit(code=2)

    state["duration"] = round(time.monotonic() - started, 2)

    # Persist before reporting, so --write works in both --json and rich modes.
    fixed_path = None
    if write and state.get("code_patched"):
        try:
            from .report import write_fixed_file

            fixed_path = write_fixed_file(state)
        except OSError as exc:
            # A failed write must not masquerade as exit code 1 (unclear/gave_up).
            console.print(f"[red]error:[/] could not write fixed file: {escape(str(exc))}")
            raise typer.Exit(code=2)
    elif write and not as_json:
        # --write was asked for but there is no source change to persist.
        # Say so explicitly: silently writing nothing looks like a broken flag.
        # (In --json mode `written_file: null` already carries this.)
        if state.get("tests_patched"):
            console.print(
                "[dim]Note: only the generated tests changed — the source was "
                "unmodified, so no .fixed.py was written.[/]"
            )
        else:
            console.print("[dim]Note: nothing to write; the source never changed.[/]")

    try:
        if as_json:
            payload = {
                "status": state.get("status"),
                "iterations": state.get("iteration"),
                "max_iterations": state.get("max_iterations"),
                "source_path": state.get("source_path"),
                "code_patched": state.get("code_patched", False),
                "tests_patched": state.get("tests_patched", False),
                "written_file": str(fixed_path) if fixed_path else None,
                "last_run": {
                    "summary": state["last_run"].summary,
                    "passed": state["last_run"].passed,
                    "failed": state["last_run"].failed,
                } if state.get("last_run") else None,
                "diagnosis": state.get("diagnosis"),
                "history": state.get("history"),
                "duration": state.get("duration"),
            }
            print(json.dumps(payload, indent=2))
        else:
            if fixed_path:
                console.print(f"\n[green]Wrote[/] [bold]{fixed_path}[/]")
            render(state, console, show_diff=not no_diff)
    except Exception as exc:  # noqa: BLE001 — reporting must not change the run's exit code
        console.print(f"[red]error while reporting:[/] {escape(str(exc))}")
        raise typer.Exit(code=2)

    raise typer.Exit(code=0 if state.get("status") == "green" else 1)


@app.command("graph")
def graph_cmd() -> None:
    """Print the agent's LangGraph topology (handy for the demo)."""
    nodes = ["START", "generate", "run", "diagnose", "patch", "report", "END"]
    table = Table(title="TestPilot agent graph", show_header=False, expand=False)
    table.add_column("from", style="cyan")
    table.add_column("to", style="green")
    table.add_column("when", style="dim")
    rows = [
        ("START", "generate", ""),
        ("generate", "run", ""),
        ("run", "report", "all tests green or budget spent"),
        ("run", "diagnose", "failures remain"),
        ("diagnose", "report", "verdict: unclear"),
        ("diagnose", "patch", "verdict: code_bug | test_bug"),
        ("patch", "run", "prove the fix"),
        ("report", "END", ""),
    ]
    for a, b, when in rows:
        table.add_row(a, b, when)
    console.print(table)
    console.print(f"[dim]nodes: {', '.join(nodes)}[/]")
    console.print("[dim]Mermaid:[/] graph TD; START-->generate-->run;"
                  "run-->|green| report; run-->|fail| diagnose;"
                  "diagnose-->|unclear| report; diagnose-->|fix| patch; patch-->run; report-->END")


if __name__ == "__main__":
    app()
