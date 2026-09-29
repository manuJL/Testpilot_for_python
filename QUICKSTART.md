# ⚡ Quickstart

Get TestPilot running in under 2 minutes.

```bash
uv sync                      # 1. install deps
cp .env.example .env         # 2. add your API key (see below)
uv run testpilot run examples/off_by_one.py   # 3. go
```

> **TestPilot refuses to run without an API key.** There is no offline/fake mode.
> If you skip step 2 you get:
> ```
> ✗ API key is not found. Please open .env and add the API key to continue.
> ```
> (exit code `2`)

---

## Prerequisites

| Need | Version |
|------|---------|
| [uv](https://docs.astral.sh/uv/) | any recent (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| Python | 3.11+ (3.14 used here — uv fetches it automatically) |
| API key | one free key from Groq / OpenRouter / Gemini |

No Docker. No GPU. No local models.

---

## Step 1 — Install

```bash
uv sync
```

This creates `.venv` and installs everything from `pyproject.toml`
(typer, rich, httpx, pydantic, python-dotenv, langgraph, pytest).

---

## Step 2 — Add your API key

```bash
cp .env.example .env
```

Open `.env` and fill in **at least one** key:

| Provider | Get a free key at | Variable |
|----------|-------------------|----------|
| **Groq** (recommended — fastest) | <https://console.groq.com/keys> | `GROQ_API_KEY=` |
| OpenRouter | <https://openrouter.ai/keys> | `OPENROUTER_API_KEY=` |
| Gemini | <https://aistudio.google.com/apikey> | `GEMINI_API_KEY=` |

One key is enough. TestPilot auto-picks whichever is present.

---

## Step 3 — Run it

```bash
uv run testpilot run examples/off_by_one.py
```

You'll see the full loop execute live:

```
TestPilot on examples/off_by_one.py  (provider: groq)
  → generate
  → run 1 passed, 1 failed, 0 errors
  → diagnose
  → patch
  → run 2 passed, 0 failed, 0 errors
  → report
```

…followed by the status panel, loop history, diagnosis and a unified diff.

**Exit codes:** `0` = tests green · `1` = unclear / gave up · `2` = config error (e.g. no API key).

---

## Useful flags

```bash
uv run testpilot run path/to/file.py -n 6        # allow 6 diagnose→patch cycles (default 4)
uv run testpilot run path/to/file.py --write      # save fix to file.fixed.py (original untouched)
uv run testpilot run path/to/file.py --json       # machine-readable report
uv run testpilot run path/to/file.py --no-diff    # hide the unified diff
uv run testpilot run path/to/file.py -p openrouter # force a provider
uv run testpilot graph                            # print the agent's LangGraph topology
uv run testpilot --version
```

---

## Try all three examples

```bash
uv run testpilot run examples/off_by_one.py            # bug in the CODE  -> fixes source
uv run testpilot run examples/correct_code_wrong_test.py  # bug in the TEST -> fixes test
uv run testpilot run examples/ambiguous.py             # undecidable      -> stops, says "unclear"
```

| Example | Expected status |
|---------|-----------------|
| `off_by_one.py` | ✅ green (patches the source → `.fixed.py`) |
| `correct_code_wrong_test.py` | ✅ green (patches the generated test, source untouched) |
| `ambiguous.py` | 🤔 `unclear` — honest stop, no changes made |

Your original file is **never modified**. Fixes are shown as a diff and only
written to `<name>.fixed.py` when you pass `--write`.

---

## Run the tests

```bash
uv run pytest testing/            # full suite (~25s, 57 tests, no API credits used)
uv run pytest testing/ -v         # verbose
uv run pytest testing/test_e2e_loop.py   # just the end-to-end agent loop
```

- `testing/test_unit_*.py` — parsing, sandbox, agent nodes, graph routing
- `testing/test_e2e_*.py` — the full LangGraph loop + the real CLI

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `✗ API key is not found` | `cp .env.example .env`, add a key, re-run. |
| `uv: command not found` | `export PATH="$HOME/.local/bin:$PATH"` |
| `LLMError: ... 401/403` | Key is invalid or revoked — regenerate it in `.env`. |
| `LLMError: ... 429` | Rate limited — wait a few seconds, or switch provider with `-p`. |
| `gave up` / `unclear` | Normal on ambiguous code. Try `-n 6`, or check the docstring of your function. |
| Tests not collected | Generated tests import `testpilot_target`; the sandbox handles this automatically — see the run output. |

---

## How it works

```
file.py → CLI → LangGraph agent
                  ├── llm.py   → Groq / OpenRouter / Gemini (real API, key required)
                  └── Sandbox  → local subprocess (temp dir, env scrubbed, timeout)
                  └── report.py → terminal report + unified diff
```

```
START → generate → run ──(green/budget)──→ report → END
                      └→ diagnose ─(unclear)┘
                              └→ patch ↺ run
```

1. **generate** — LLM writes pytest tests for your file
2. **run** — tests execute in an isolated subprocess (no API keys leak in)
3. **diagnose** — LLM decides: `code_bug`, `test_bug`, or `unclear`
4. **patch** — fixes the guilty side only, then re-runs to prove it
5. **report** — status, history, diagnosis, diff

---

## Next steps

- Read [`README.md`](README.md) for the problem statement
- See `TestPilot_Hackathon_Plan.md` for the full build plan
- Swap in a different provider: edit `.env` only, no code changes
