# TestPilot — Project Context

Everything a newcomer (or a judge, or a future us) needs to know *why* the
code looks the way it does. Companion to [ARCHITECTURE.md](ARCHITECTURE.md)
(how it works) and [QUICKSTART.md](QUICKSTART.md) (how to run it).

---

## What this is

A 24-hour build for **BnB'26, Himachal Pradesh Zonal Round**, per
`TestPilot_Hackathon_Plan.md`. TestPilot reads a buggy Python file, writes
pytest tests for it, runs them in a sandbox, decides whether the *code* or the
*tests* are wrong, patches the guilty side, and reports honestly — including
reporting when it does not know.

## Constraints that shaped it

| Constraint | Consequence |
|---|---|
| 8 GB laptop, no Docker | Local subprocess sandbox only; `Sandbox` protocol kept swappable so a microVM backend can drop in later |
| Must use **uv** | `uv run`, `uv add`; no `pip`/`venv` anywhere |
| Must use **langgraph** | The loop is a real `StateGraph` with conditional edges, not a `while` loop — and it's drawable for judges via `testpilot graph` |
| No offline mode | Refuses to run without an API key (exit 2) rather than faking answers |
| Temporary `testing/` folder | The whole suite lives in `testing/`, to be deleted later; nothing in `src/` imports from it |

## The two seams

- **`llm.py`** hides *which provider* we talk to.
- **`Sandbox`** hides *where code runs*.

Both are `Protocol`s. Nodes receive them as arguments, which is what makes the
graph testable with a `ScriptedLLM` and a stub `RunResult` in milliseconds.

## Decisions worth defending

**No `FakeLLM` in the shipped package.** It existed early on, then was removed.
A stub that returns canned diagnoses lets the whole pipeline appear to work
while proving nothing, and it would ship in the wheel. The `ScriptedLLM` test
double lives *only* in `testing/conftest.py` — tests get their double, users get
a hard refusal with a clear message.

**Never touch the user's original file.** Diffs are suggestions. `<name>.fixed.py`
is written only with `--write`, as a sibling.

**Counts come from the final summary line only.** Naively grepping the whole
buffer for `N passed` lets a test *print* `"2 passed"` and inflate the totals.
pytest writes its summary last, so the parser walks lines in reverse and stops
at the first one carrying counts.

**Output is streamed to disk, not pipes.** `capture_output=True` would buffer
whatever a runaway test emits into parent memory. Files on disk + a bounded
tail read keeps parent RSS flat (measured: **0 MB delta** on a test that emits
~1 GB).

**Patch flags are sticky.** `code_patched`/`tests_patched` are
`bool(previous) or (changed_this_round)`. A plain assignment meant a later
no-op patch cleared an earlier real fix — and `--write` then silently saved
nothing.

## Model IDs: verified, not trusted

An external audit handed us a table of "current" model IDs. Several were wrong
in ways that would have broken the demo:

| Claim | Reality |
|---|---|
| Use `llama-3.3-70b-versatile` on Groq | **Retired 2026-08-16** — this is the exact model that caused the original `404` |
| `llama-3.1-8b-instant`, `qwen/qwen3-32b`, `kimi-k2-instruct`, `gemma2-9b-it` on Groq | Not in the live catalogue for this key |
| `cohere/north-mini-code:free` "isn't a real slug" | It **is** — confirmed in OpenRouter's live `/models` |
| `meta-llama/llama-3.3-70b-instruct:free` on OpenRouter | **Absent** from the live list |

**Rule adopted:** model IDs come from each provider's live `/models` endpoint,
read the same day, never from a pasted table. Providers deprecate on their own
schedule, so treat every ID in `config.py` as *verified-as-of-writing*. If
`llm.py` ever reports `model_not_found`, re-query that provider's list — do not
"fix" it by editing routing logic.

Live catalogue as verified this session:

- **Groq** — `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
  `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.8-27b`, `allam-2-7b`,
  `canopylabs/*`, `meta-llama/llama-prompt-guard*`, `whisper-*`
- **OpenRouter** — exactly **16** `:free` models, including
  `cohere/north-mini-code:free`, `nvidia/nemotron-3-super-120b-a12b:free`,
  `nvidia/nemotron-3-ultra-550b-a55b:free`, `qwen/qwen3.8-27b:free`,
  `google/gemma-4-31b-it:free`
- **Gemini** — `generativelanguage.googleapis.com` returns **403 without a
  key**, so these IDs could not be independently verified this session

## Verification loop used during the build

1. Run the suite: `uv run pytest testing/ -q`
2. Lint: `uv run ruff check src/ testing/`
3. Run the real thing against a live LLM on all three examples
4. Send independent agents to attack the code from separate angles
5. Fix confirmed majors, re-run from step 1

The agents cross-check each other: a finding reported by two independent
audits gets prioritised over one reported once, because single-pass audits
hallucinate. (One pasted audit claimed `diagnose.py` raised `AttributeError` on
`RunResult.stderr` — the field exists at `sandbox/base.py`, and 60 tests
demonstrate it.)

## Known gaps / honest limitations

- **Gemini model IDs unverified** (403 without a key).
- **`.env.example` contained real API keys.** Fixed — it is now a blank
  template (`.env`, which is gitignored, keeps the working keys). **Both keys
  should still be rotated** before this repo is shared or submitted, since they
  were in plaintext earlier in this session.
- **`testing/` is temporary** by design; the shipped package does not depend on it.
- **Free-tier rate limits are real.** Groq's 8000 TPM limit surfaces as a
  transient `429`. `llm.py` retries up to 4 attempts, honouring the provider's
  `Retry-After` header — or the `try again in Ns` hint in the JSON body when
  only that is present — capped at 45s, with exponential fallback capped at 8s.
  408/429/5xx retry; **401/404 fail fast** so a bad model ID never sleeps
  through the retries. *Sustained* throttling (several files back-to-back on
  one key) still degrades to an honest `gave_up` with the reason in the history
  rather than crashing — which is the intended behaviour, not a silent pass.
- **Gemini/OpenRouter code paths are exercised only through the shared
  `HTTPChatLLM`**; only Groq was validated end-to-end this session.

## Layout

```
├── ARCHITECTURE.md      how it works (long paragraph + module map)
├── CONTEXT.md           this file
├── QUICKSTART.md        copy-paste commands, all verified
├── README.md            project front door
├── TestPilot_Hackathon_Plan.md   the original brief
├── pyproject.toml       uv-managed; entry point `testpilot = testpilot.cli:app`
│                        dev group = pytest + ruff (ruff must be declared, or
│                        `uv run ruff` fails with "Failed to spawn")
├── run_testpilot.sh     batch launcher (asks for a path, wraps the .py)
├── run_testpilot.py     batch runner: file-or-folder, stdlib only
├── .env / .env.example  provider keys + model overrides
├── .github/workflows/ci.yml  offline CI: ruff + the full suite, no API keys
├── docs/               README screenshots (committed so no external image host)
├── examples/            off_by_one.py, correct_code_wrong_test.py, ambiguous.py
├── src/testpilot/       the package (cli, config, llm, parsing, prompts,
│                        report, sandbox/, agent/)
└── testing/             128 tests + ScriptedLLM  (temporary, deletable)
```
