# TestPilot — Architecture

How TestPilot works, from the command you type to the file it writes.

---

## One long paragraph

You point TestPilot at a Python file with `testpilot run path/to/file.py`, and
the Typer CLI first loads `.env` through `python-dotenv` and asks
`Config.resolve_provider()` which LLM backend to use — that function reads
`TESTPILOT_PROVIDER` (or the `--provider` flag), warns loudly on stderr if the
name you asked for is unknown or has no API key, and otherwise falls back
through the ordered list `groq → openrouter → gemini`, stopping at the first
provider that actually has a key, and if none has one it raises
`MissingAPIKeyError`, which the CLI turns into the exact message *"API key is
not found. Please open .env and add the API key to continue."* and a **process
exit code of 2** — because there is deliberately no offline mode, so the tool
refuses to pretend it can run. Once a provider is chosen, `run_file()` in
`agent/loop.py` reads your source file (refusing an empty one), builds the
initial `AgentState` TypedDict, and constructs a LangGraph `StateGraph` whose
nodes are thin closures that capture the `LLM` and `Sandbox` objects — so the
graph is `START → generate → run → (diagnose → patch ↺ run) → report → END`,
with two conditional routers deciding the edges. The `generate` node calls the
model with `GENERATE_SYSTEM`/`GENERATE_USER` prompts to write a pytest file,
extracts the fenced code block with `parsing.extract_code()` (taking the
*longest* block, since models often restate the old file before the new one),
validates it with `ast.parse()` — retrying once with syntax feedback, and
catching `ValueError` as well as `SyntaxError` because NUL bytes raise the
former — and stores it in `state["test_code"]`. The `run` node hands the source
and tests to the `Sandbox`, whose `LocalSubprocessSandbox` implementation writes
both files into a `TemporaryDirectory`, scrubs the environment down to eleven
harmless variables so **your API keys never reach generated code**, launches
`python -m pytest -q --no-header --tb=short` with a wall-clock timeout, and
streams the child's stdout/stderr to *files on disk* rather than pipes so that
a runaway `print` loop can never balloon the parent's memory; it then reads back
only a bounded tail and parses the counts from the **final summary line only** —
never from the whole buffer, so a test that prints `"2 passed"` cannot inflate
the totals or fake a green run — while also handling `skipped` counts and the
unittest fallback's `Ran N tests … OK` / `FAILED (failures=1)` shapes. If the
run is green, or the iteration budget is spent, the router sends you straight to
`report`; otherwise `diagnose` runs, feeding the source, the generated tests and
the raw output to the model and demanding exactly one JSON object back,
`{"verdict": "code_bug" | "test_bug" | "unclear", "reasoning", "instructions"}`,
which `parsing.extract_json()` pulls out with a balanced-brace scanner that
survives nested objects and braces inside string values, and an unparseable or
out-of-vocabulary verdict is normalized to `unclear` rather than trusted. The
`patch` node then applies the fix to *whichever side was blamed* — `patch_code`
rewrites the source, `patch_test` rewrites the tests — validating the model's
output as real Python and quietly keeping the previous file if it is not
(so a bad model response can never degrade a working file), while the
`code_patched`/`tests_patched` flags are **sticky ORs** so a later no-op patch
cannot clear an earlier successful one and silently cost you the fix at
`--write` time; control then loops back to `run`, and this repeats until green,
until the budget is exhausted, or until the model admits `unclear`. Throughout,
LangGraph's append reducers on `history` and `notes` mean no node can clobber
another node's log entries, and `run_file()` streams the graph so that even if a
node dies part-way — a transient `HTTP 429` from a free-tier provider, or
hitting the graph step limit — you keep everything completed so far and get an
honest `gave_up` status with the reason in the history instead of a stack trace.
Finally `report` renders a Rich table of the loop history, the diagnosis, and a
unified diff of the suggested change; your original file is **never modified**
unless you pass `--write`, which writes `<name>.fixed.py` as a sibling and is
honoured in both human and `--json` output modes; and the process exits **0** for
green, **1** for `unclear`/`gave_up`, **2** for configuration or runtime errors —
so the exit code alone tells a script what happened.

---

## The seams

Two interfaces carry the whole design. Everything else is an implementation.

```python
class LLM(Protocol):                      # llm.py
    def chat(self, step: str, messages: list[Message]) -> str: ...

class Sandbox(Protocol):                  # sandbox/base.py
    def run(self, source: str, test_code: str, timeout: int) -> RunResult: ...
```

| Seam | Production implementation | Test double |
|---|---|---|
| `LLM` | `HTTPChatLLM` (httpx → Groq / OpenRouter / Gemini) | `ScriptedLLM`, in `testing/conftest.py` **only** |
| `Sandbox` | `LocalSubprocessSandbox` (subprocess + temp dir) | real sandbox, or a stub `RunResult` |

> There is deliberately **no fake LLM in `src/testpilot`**. A stub that
> fabricated plausible-looking diagnoses would let the agent "pass" without ever
> proving it can talk to a model — and it would ship in the wheel. The tool
> refuses to run without a key instead (exit code 2).

## Module map

| File | Responsibility |
|---|---|
| `cli.py` | Typer entry point, exit-code contract, Rich output, `--json`, `--write` |
| `config.py` | Provider/model resolution, `.env`, `MissingAPIKeyError` |
| `llm.py` | `LLM` protocol, `HTTPChatLLM`, `LLMError`, friendly HTTP errors |
| `prompts.py` | All prompt templates and their builders |
| `parsing.py` | `extract_code`, `extract_all_code`, `extract_json`, `strip_fences` |
| `report.py` | `render()`, `unified_diff()`, `write_fixed_file()` |
| `sandbox/base.py` | `Sandbox` protocol + `RunResult` (the only test-run vocabulary) |
| `sandbox/local.py` | Temp-dir subprocess runner, output parsing, env scrub |
| `agent/state.py` | `AgentState` TypedDict + `new_state()` |
| `agent/generate.py` | GENERATE node |
| `agent/diagnose.py` | DIAGNOSE node + verdict normalization |
| `agent/patch.py` | PATCH node, sticky flags |
| `agent/loop.py` | Graph construction, routers, `run_file()` |

## The state machine

```
START
  │
  ▼
generate ──▶ run ──┬─ all green? ──────────────▶ report ──▶ END
                   ├─ budget spent? ───────────▶ report ──▶ END
                   └─ failures remain ──▶ diagnose ──┬─ unclear ──▶ report ──▶ END
                                                    └─ code_bug/test_bug ──▶ patch ──▶ run
```

Routers live in `loop.py` as `_route_after_run` and `_route_after_diagnose`.
Roughly three node executions cost one iteration (`diagnose → patch → run`),
plus the one-time `generate` and the closing `report`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Tests green — the code (or the tests) are now correct |
| `1` | `unclear` or `gave_up` — TestPilot is honestly unsure, or ran out of budget |
| `2` | Configuration/runtime error — no API key, bad `.env` value, unreadable file, I/O failure |

Code 2 is reserved so it never collides with "the agent couldn't decide".

## Safety properties

1. **Your original file is never written to.** Fixes go to `<name>.fixed.py`,
   and only with `--write`.
2. **API keys never reach generated code.** The sandbox env keeps exactly
   `PATH, HOME, LANG, LC_ALL, TMPDIR, PYTHONPATH` plus two Python flags.
3. **Bounded memory and bounded time.** Output goes to disk and comes back
   capped at 512 KB per stream; every run has a wall-clock timeout.
4. **No fake-green.** Counts are read from pytest's final summary line only, so
   a test cannot print its way to a passing verdict, and `RunResult.all_green`
   requires `total > 0`.
5. **A bad model reply cannot degrade a working file** — patch output must
   parse as Python before it replaces anything.

## Configuration surface

Everything is an environment variable so the demo switches providers without a
code change:

| Variable | Effect |
|---|---|
| `GROQ_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY` | Whichever exists first (in `PROVIDER_ORDER`) wins |
| `TESTPILOT_PROVIDER` / `--provider` | Force a provider (warns and falls back if unusable) |
| `TESTPILOT_GENERATE_MODEL` / `TESTPILOT_DIAGNOSE_MODEL` / `--model` | Override model IDs per step |
| `TESTPILOT_MAX_ITERATIONS` / `--max-iterations` | Loop budget (default 4, CLI caps at 10) |
| `TESTPILOT_TIMEOUT` / `TESTPILOT_TEMPERATURE` | Sandbox timeout, sampling temperature |

Model tables (`GROQ_MODELS`, `OPENROUTER_MODELS`, `GEMINI_MODELS`) hold IDs
verified against each provider's live `/models` endpoint. See
[CONTEXT.md](CONTEXT.md) for why this was verified rather than trusted.
