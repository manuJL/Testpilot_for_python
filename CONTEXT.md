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

**A suite has to prove something.** `integrity.py` rejects a generated file
that dropped the `testpilot_target` import, deleted the tests that were
failing, or stripped assertions — and the history prints `tests: N, asserts: M`
so a shrinking suite is visible. Green additionally requires at least one
*passing* test against the real module, so `1 skipped` can't be sold as success.
The complementary rule lives at the other end: `--write` only ever writes a
`.fixed.py` from a run that actually ended green.

**An unparseable answer is not a verdict.** A diagnosis the model never gave
must not be defaulted into a genuine `unclear` — `diagnose` retries once with
nudging, and if it still isn't valid JSON the run stops as `error` (exit 2).

**The most tempting fake green is deleting a requirement, not deleting a test.**
Caught live, twice. **First:** a spec demanding two mutually exclusive rules got
`code_bug` on round 1 (patch can't satisfy both), then on round 2 the model
flipped the verdict to `test_bug` — its own reasoning said *"impossible given
the code and the specification"* — and rewrote the tests to drop R2.
`10 → 10 tests, 18 → 18 asserts`, so the count-based integrity guard waved it
through and the run reported green. **Then, harder:** `many_bugs.py` fixed all
14 genuine code bugs on round 1 and was left with two failures — one stray test
bug and one contradictory spec. A single verdict bundled them, so rewriting the
suite to correct the stray test *also* dropped the inconvenient requirement
(`16 → 16 tests, 47 → 47 asserts` → green again). The count guard is
structural: it cannot see a test rewritten to say the opposite.

Three prompt rules now close it: `diagnose` answers `unclear` when the *spec*
is self-contradictory (blaming either side is not a diagnosis); `diagnose` may
only return `test_bug` when no failing test merely restates the docstring
(otherwise one stray test bug becomes a licence to rewrite everything); and
`patch` may never rewrite a test that asserts what the docstring explicitly
requires. Each rule has a regression test in `testing/test_regressions.py`.

The examples taught us a wording rule too. "Both are required, with **no
precedence between them**" was read back as *"the specification does not require
both readings to hold simultaneously"* — i.e. either/or — which made the R1
tests look wrong and the run went green. Stating it as **"both must hold for the
same call; this is not an either/or choice"** closed it. Concrete, same-input,
incompatible outputs (`typical([1,2,3,4]) == 2` *and* `== 2.5`) matter more than
any amount of prose about precedence.

Two more findings, both from watching `ambiguous.py` flip between green and red:

- **`generate` makes one global pick per run.** It chose a single reading for
  all three functions, so adding more contradictory functions does *not* dilute
  the risk — every run was all-green or all-red together. What actually decided
  it was a line in the module docstring: **"The tests will check both rules; the
  source can honour only one of them."** With it, `generate` wrote 3 tests per
  function (clear case + R1 + R2 = 9 total) and the run was red 2/2; without it,
  it wrote 2 (clear + one reading) and the run was green as often as not.
- **A spec that is contested *everywhere* gives the generator nothing to hold
  on to.** Pairing each contested case with a plainly documented case that must
  pass is what makes the suite trustworthy enough to fail.

**Honest limitation:** `integrity.py` compares counts, not meaning — a test
rewritten to assert the opposite is invisible to it, and a prompt rule is only
as good as the model reading it. A semantic check (does the assertion still
reflect a stated requirement?) is genuinely open work, not something a regex
can settle. This is also why the README lists *one verdict per run* as a known
limitation: a file mixing a test bug with a broken spec still has to express
itself in one word.

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

- **Single file only.** The source is copied into a temp dir as one module, so
  relative imports, sibling modules and uninstalled third-party imports fail on
  import — and TestPilot will then try to "fix" imports it has no business
  touching. Self-contained files only.
- **The sandbox is accident-proof, not adversarial-proof.** Temp dir, scrubbed
  env, `HOME` redirected, rlimits, process-group kill on timeout. That stops
  infinite loops and a test reading your real `~/.env`. It is not a container —
  genuinely untrusted input wants `podman run --network none`.
- **Gemini model IDs unverified** (403 without a key).
- **`.env.example` contained real API keys.** Fixed — it is now a blank
  template (`.env`, which is gitignored, keeps the working keys). **Both keys
  should still be rotated** before this repo is shared or submitted, since they
  were in plaintext earlier in this session.
- **`testing/` is temporary** by design; the shipped package does not depend on it.
- **Free-tier rate limits are real, and Groq's are *daily*.** The free tier is
  **200 000 tokens per day**, reported as
  `on tokens per day (TPD): Limit 200000, Used 199188 … Please try again in 35.8s`.
  `llm.py` retries up to 4 attempts honouring `Retry-After` — or the
  `try again in Ns` hint in the JSON body when only that is present — capped at
  45s, with exponential fallback capped at 8s. 408/429/5xx retry; **401/404 fail
  fast** so a bad model ID never sleeps through the retries.
  A body that mentions a spent quota **and** carries no usable wait schedule (or
  asks for a wait longer than this CLI will block for) breaks out immediately
  instead of burning four attempts — and the message quotes the provider's own
  words rather than paraphrasing them into a claim we can't verify. That matters:
  one early version asserted "daily quota exhausted" for a body that actually
  said *tokens per day, try again in 9m50s*, which is a different and much more
  actionable fact.
- **Sustained throttling degrades to an honest `gave_up`** with the reason in
  the history rather than crashing — intended behaviour, not a silent pass. A
  hard `LLMError` is different: that stops the run as `error` (exit 2), because
  an infrastructure failure must never be dressed up as a model verdict.
- **OpenRouter was validated end-to-end this session** (both configured models,
  `cohere/north-mini-code:free` and `nvidia/nemotron-3-super-120b-a12b:free`,
  answered 200 from the live catalogue). Gemini's code paths are exercised only
  through the shared `HTTPChatLLM`.

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
├── examples/            demo targets. Green side: off_by_one.py,
│                        correct_code_wrong_test.py, python1/3/4/5/6.py
│                        (named pythonN.py on purpose — a `test_*.py` name makes the
│                        launcher skip the file as a test, not code under test).
│                        Red side (all verified exit 1): ambiguous.py,
│                        conflicting_spec.py, impossible_spec.py, many_bugs.py —
│                        written so they must end non-green
├── src/testpilot/       the package (cli, config, integrity, llm, parsing,
│                        prompts, report, sandbox/, agent/)
└── testing/             157 tests + ScriptedLLM  (temporary, deletable)
```
