#!/usr/bin/env python3
"""run_testpilot.py — drive the TestPilot agent over a file or a whole folder.

Interactive by default: run it with no arguments and it asks where your Python
code lives. Pass one or more paths (files and/or directories) to skip the prompt.

    ./run_testpilot.py                  # asks "Where is the Python code?"
    ./run_testpilot.py examples/        # every eligible .py in the folder
    ./run_testpilot.py foo.py 2.py      # individual files
    ./run_testpilot.py myscript         # finds myscript.py automatically

Only ever reads your source. A fix is written to `<name>.fixed.py` only when you
pass --write; the original file is never modified.

Exit codes follow TestPilot's contract:
    0  every target finished green
    1  at least one target was unclear / gave up / had failures
    2  could not run at all (no API key, no uv, bad path, ...)

Standard library only — deliberately runnable under a bare `python3`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# This file lives at the project root, next to pyproject.toml.
PROJECT_ROOT = Path(__file__).resolve().parent

# Never descend into these: they are the package, the temp suite, or junk.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "src",      # TestPilot does not lint itself
    "testing",  # the temporary suite (deleted later anyway)
}

# Files that are never a sensible *target* for the agent.
SKIP_FILES = {"conftest.py", "setup.py", "setup.cfg", "__init__.py"}

EXIT_OK = 0
EXIT_NOT_GREEN = 1
EXIT_CANNOT_RUN = 2

# Per-target guard so one hanging file cannot stall the whole batch.
DEFAULT_TARGET_TIMEOUT = 900


def _tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def colour(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def find_uv() -> str | None:
    """Locate uv on PATH, then in the usual install spots."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".cargo" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def has_api_key() -> bool:
    """Mirror the check TestPilot itself does: env vars, then .env."""
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name, "").strip():
            return True

    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key.endswith("_API_KEY") and value.strip():
                    return True
        except OSError:
            pass
    return False


def missing_key_message() -> str:
    return (
        "API key is not found. Please open .env and add the API key to continue.\n"
        "\n"
        "    cp .env.example .env    # then edit it\n"
        "\n"
        "    Groq (free + fast):       https://console.groq.com/keys       -> GROQ_API_KEY=\n"
        "    OpenRouter (free models): https://openrouter.ai/keys          -> OPENROUTER_API_KEY=\n"
        "    Gemini (free tier):       https://aistudio.google.com/apikey  -> GEMINI_API_KEY=\n"
        "\n"
        "Adding one key is enough."
    )


# ---------------------------------------------------------------------------
# Target discovery
# ---------------------------------------------------------------------------


def _looks_like_test(name: str) -> bool:
    """True only for the unambiguous pytest convention `test_*.py`.

    Deliberately NOT matching the `_test.py` suffix: real application files are
    named things like `correct_code_wrong_test.py` (one of our own demo
    examples), and silently skipping them would make the batch quietly incomplete.
    """
    return name.startswith("test_")


def _eligible(path: Path, include_tests: bool = False) -> tuple[bool, str]:
    """Is this .py file a legitimate target? Returns (ok, reason_if_not)."""
    name = path.name
    if name.endswith(".fixed.py"):
        return False, "already-fixed output from a previous run"
    if name in SKIP_FILES:
        return False, "support file, not application code"
    if not include_tests and _looks_like_test(name):
        return False, "a test file, not code under test (use --include-tests)"
    if any(part in SKIP_DIRS for part in path.parts):
        return False, "inside a skipped directory"
    return True, ""


def resolve_one(raw: str, include_tests: bool = False) -> tuple[list[Path], str | None]:
    """Turn one user-supplied argument into target files.

    Returns (files, fatal_error). Accepts a directory, a file, or a bare stem
    like `python` / `2` / `python1` that we complete to `<name>.py`.
    """
    path = Path(raw).expanduser()

    # Bare name -> name.py (so `python`, `2`, `python1` just work).
    if not path.exists() and not path.suffix:
        candidate = Path(str(path) + ".py")
        if candidate.exists():
            path = candidate

    if not path.exists():
        return [], f"'{raw}' does not exist"

    if path.is_dir():
        found: list[Path] = []
        for py in sorted(path.rglob("*.py")):
            ok, _ = _eligible(py, include_tests)
            if ok:
                found.append(py)
        if not found:
            return [], f"'{raw}' contains no eligible .py files"
        return found, None

    if path.suffix != ".py":
        return [], f"'{raw}' is not a .py file"

    ok, reason = _eligible(path, include_tests)
    if not ok:
        return [], f"'{raw}' was skipped: {reason}"
    return [path.resolve()], None


def discover(raw_paths: list[str], include_tests: bool = False) -> tuple[list[Path], list[str]]:
    """Expand all arguments into a de-duplicated, ordered target list."""
    targets: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()

    for raw in raw_paths:
        files, error = resolve_one(raw, include_tests)
        if error:
            errors.append(error)
            continue
        for f in files:
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                targets.append(resolved)
    return targets, errors


def prompt_for_path() -> str | None:
    print("TestPilot — automatic test generation + repair\n")
    try:
        answer = input("Where is the Python code (file or folder)? ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return answer or None


# ---------------------------------------------------------------------------
# Running the agent
# ---------------------------------------------------------------------------


@dataclass
class Result:
    path: Path
    status: str = "error"
    detail: str = ""
    duration: float = 0.0
    iterations: int = 0
    written: str | None = None
    note: str = ""

    def as_dict(self) -> dict:
        """JSON shape for --json; path is stringified because Path isn't."""
        return {
            "file": str(self.path),
            "status": self.status,
            "detail": self.detail,
            "duration": self.duration,
            "iterations": self.iterations,
            "written": self.written,
            "note": self.note,
        }


def _extract_json(stdout: str) -> dict | None:
    """TestPilot's --json output is pure JSON on stdout; be forgiving anyway."""
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = stdout.find("{"), stdout.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(stdout[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def run_one(
    uv: str,
    target: Path,
    *,
    write: bool,
    provider: str | None,
    model: str | None,
    max_iterations: int | None,
    timeout: int,
    as_json: bool = False,
) -> Result:
    """Run TestPilot on one file.

    Default mode hands the child the terminal: stdout/stderr are inherited, so
    you get the exact rich report you'd see from a standalone run — live node
    progress, the status panel, the loop-history table, the diagnosis and the
    unified diff — and the exit code carries the verdict.

    `as_json=True` instead captures `--json` for machine consumption.
    """
    result = Result(path=target)

    cmd = [uv, "run", "testpilot", "run", str(target)]
    if as_json:
        cmd.append("--json")
    if write:
        cmd.append("--write")
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    if max_iterations is not None:
        cmd += ["--max-iterations", str(max_iterations)]

    env = dict(os.environ)
    # uv lives in ~/.local/bin on this machine; make sure the child sees it.
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = f"{local_bin}{os.pathsep}{env.get('PATH', '')}"

    started = time.monotonic()

    # ---- machine mode: capture -------------------------------------------
    if as_json:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result.status = "timeout"
            result.detail = f"exceeded {timeout}s"
            return result
        except OSError as exc:
            result.status = "error"
            result.detail = f"could not launch uv: {exc}"
            return result

        payload = _extract_json(proc.stdout)
        if payload is None:
            result.status = {0: "green", 1: "not green"}.get(proc.returncode, "error")
            tail = (proc.stderr or proc.stdout).strip().splitlines()
            result.detail = tail[-1] if tail else f"exit {proc.returncode} (no output)"
            if proc.returncode == EXIT_CANNOT_RUN:
                result.status = "error"
            return result

        result.status = str(payload.get("status") or "unknown")
        result.iterations = int(payload.get("iterations") or 0)
        result.duration = float(payload.get("duration") or 0.0)
        result.written = payload.get("written_file")
        last_run = payload.get("last_run")
        if isinstance(last_run, dict) and last_run.get("summary"):
            result.detail = str(last_run["summary"])
        diag = payload.get("diagnosis")
        if isinstance(diag, dict) and diag.get("verdict") == "unclear" and not result.detail:
            result.detail = "diagnosis: unclear"
        if proc.returncode == EXIT_CANNOT_RUN and result.status == "error":
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            result.detail = msg[0] if msg else "configuration error"
        return result

    # ---- human mode: stream the real report ------------------------------
    # Inherited stdio keeps rich's colours and the user's terminal width, and
    # lets the CLI print "Wrote <file>" itself, so we never duplicate it.
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result.status = "timeout"
        result.detail = f"exceeded {timeout}s"
        return result
    except OSError as exc:
        result.status = "error"
        result.detail = f"could not launch uv: {exc}"
        return result

    result.duration = round(time.monotonic() - started, 1)
    if proc.returncode == EXIT_OK:
        result.status = "green"
    elif proc.returncode == EXIT_NOT_GREEN:
        # The child already printed WHY (TESTS GREEN / GAVE UP / unclear panel);
        # re-parsing it here would only duplicate what's on screen above.
        result.status = "not green"
    else:
        result.status = "error"
        result.detail = "configuration/runtime error — see output above"
    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_STATUS_STYLE = {
    "green": ("PASS", "32"),
    "gave_up": ("GAVE UP", "33"),
    "unclear": ("UNCLEAR", "33"),
    "error": ("ERROR", "31"),
    "timeout": ("TIMEOUT", "31"),
    "not green": ("NOT GREEN", "33"),
}


def _stats(r: Result) -> str:
    """`1 iter, 6.8s` / `6.8s` — iterations is 0 for an immediately-green run,
    but duration is always worth showing, so don't gate on it."""
    bits = []
    if r.iterations:
        bits.append(f"{r.iterations} iter")
    if r.duration:
        bits.append(f"{r.duration:.1f}s")
    return f"  ({', '.join(bits)})" if bits else ""


def print_summary(results: list[Result], colour_on: bool) -> int:
    print()
    print("=" * 72)
    print("TESTPILOT RESULTS")
    print("=" * 72)

    counts: dict[str, int] = {}
    for r in results:
        label, code = _STATUS_STYLE.get(r.status, (r.status.upper(), "36"))
        counts[r.status] = counts.get(r.status, 0) + 1

        line = f"  [{colour(label, code, colour_on)}]  {r.path.name}"
        if r.detail:
            line += f"  — {r.detail}"
        line += _stats(r)
        print(line)
        if r.note:
            print(f"           note: {r.note}")
        if r.written:
            print(f"           wrote: {r.written}")

    print("-" * 72)
    summary = ", ".join(
        f"{n} {status}" for status, n in sorted(counts.items())
    )
    print(f"  {len(results)} file(s): {summary}")
    print("=" * 72)

    bad = {"error", "timeout"}
    if any(r.status in bad for r in results):
        return EXIT_CANNOT_RUN
    if any(r.status != "green" for r in results):
        return EXIT_NOT_GREEN
    return EXIT_OK


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_testpilot.py",
        description="Run the TestPilot agent over a file or a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ./run_testpilot.py                     prompt for a path\n"
            "  ./run_testpilot.py examples/           whole folder\n"
            "  ./run_testpilot.py a.py b.py           individual files\n"
            "  ./run_testpilot.py myscript            auto-completes to myscript.py\n"
        ),
    )
    parser.add_argument("paths", nargs="*", help="file(s) and/or folder(s)")
    parser.add_argument("--write", action="store_true",
                        help="save fixes to <name>.fixed.py (originals never touched)")
    parser.add_argument("--provider", help="groq | openrouter | gemini")
    parser.add_argument("--model", help="override the model ID for every step")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="loop budget per file (default: TestPilot's own default)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TARGET_TIMEOUT,
                        help=f"per-file wall-clock limit in seconds (default {DEFAULT_TARGET_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would run, then exit")
    parser.add_argument("--include-tests", action="store_true",
                        help="also target test_*.py files (skipped by default)")
    parser.add_argument("--json", action="store_true",
                        help="machine mode: per-file reports are captured instead "
                             "of printed, and the batch result is one JSON array")
    parser.add_argument("--list-skipped", action="store_true",
                        help="show files discovered but skipped, and why")
    parser.add_argument("--no-colour", action="store_true", help="disable ANSI colour")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    colour_on = _tty() and not args.no_colour and os.environ.get("NO_COLOR") is None

    # --- where is the code? -------------------------------------------------
    paths = list(args.paths)
    if not paths:
        if not _tty():
            _err("error: no path given (interactive prompt unavailable — pass a file or folder)")
            return EXIT_CANNOT_RUN
        entered = prompt_for_path()
        if not entered:
            _err("error: no path given")
            return EXIT_CANNOT_RUN
        paths = [entered]

    targets, errors = discover(paths, include_tests=args.include_tests)

    if errors:
        for e in errors:
            _err(f"error: {e}")
        if not targets:
            _err("       nothing to run")
            return EXIT_CANNOT_RUN

    if not targets:
        _err("error: no eligible .py files found")
        return EXIT_CANNOT_RUN

    # --- what got skipped? --------------------------------------------------
    if args.list_skipped:
        for raw in paths:
            p = Path(raw).expanduser()
            if p.is_dir():
                for py in sorted(p.rglob("*.py")):
                    ok, reason = _eligible(py, args.include_tests)
                    if not ok:
                        print(f"  skipped {py}: {reason}")

    # --- dry run ------------------------------------------------------------
    if args.dry_run:
        print(f"would run TestPilot on {len(targets)} file(s):")
        for t in targets:
            print(f"  {t.relative_to(PROJECT_ROOT) if t.is_relative_to(PROJECT_ROOT) else t}")
        return EXIT_OK

    # --- preflight: refuse early, with the real reason ----------------------
    uv = find_uv()
    if uv is None:
        _err("error: `uv` not found on PATH.")
        _err("       Install it: https://docs.astral.sh/uv/getting-started/installation/")
        return EXIT_CANNOT_RUN

    if not has_api_key():
        _err("✗ " + missing_key_message())
        return EXIT_CANNOT_RUN

    json_mode = args.json
    if json_mode:
        # Chatter would break the JSON document on stdout; the CLI's own
        # warnings belong on stderr, where consumers already expect them.
        colour_on = False
    else:
        print(
            colour(f"Running TestPilot on {len(targets)} file(s)", "1", colour_on)
            + (colour("  [writing fixes]", "33", colour_on) if args.write else "")
        )
        print()

    # --- the actual work ----------------------------------------------------
    results: list[Result] = []
    try:
        for index, target in enumerate(targets, start=1):
            label = target.name
            if not json_mode:
                print(f"[{index}/{len(targets)}] {label} ...", flush=True)
            results.append(
                run_one(
                    uv,
                    target,
                    write=args.write,
                    provider=args.provider,
                    model=args.model,
                    max_iterations=args.max_iterations,
                    timeout=args.timeout,
                    as_json=json_mode,
                )
            )
            r = results[-1]
            if not json_mode:
                tag, code = _STATUS_STYLE.get(r.status, (r.status.upper(), "36"))
                print(f"        {colour(tag, code, colour_on)}"
                      + (f" — {r.detail}" if r.detail else "")
                      + _stats(r), flush=True)

            # A configuration error is global: every later file will fail the
            # same way, so stop instead of repeating it N times.
            if r.status == "error" and r.detail:
                lowered = r.detail.lower()
                if "api key" in lowered or "api key is not found" in lowered:
                    _err("\nAborting: no API key — remaining files would fail identically.")
                    break
    except KeyboardInterrupt:
        print("\n\ninterrupted — reporting what finished:", file=sys.stderr)
        if not results:
            return EXIT_CANNOT_RUN

    if json_mode:
        print(json.dumps([r.as_dict() for r in results], indent=2))
        bad = {"error", "timeout"}
        if any(r.status in bad for r in results):
            return EXIT_CANNOT_RUN
        if any(r.status != "green" for r in results):
            return EXIT_NOT_GREEN
        return EXIT_OK

    return print_summary(results, colour_on)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(EXIT_CANNOT_RUN)
