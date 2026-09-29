# TestPilot

[![CI](https://github.com/manuJL/Pyfixer/actions/workflows/ci.yml/badge.svg)](https://github.com/manuJL/Pyfixer/actions/workflows/ci.yml)

**Point it at a buggy Python file. It writes the tests, runs them safely, and fixes the right thing.**

TestPilot is a small agent loop for students and junior developers: it generates
pytest tests for a Python file, runs them in a sandbox, diagnoses whether a
failure means the *test* is wrong or the *code* is wrong, and then patches the
guilty side — or honestly reports `unclear` when it can't tell.

> Status: working end-to-end — 128 tests, lint-clean, verified against a live
> LLM on all examples in the exasmples  folder and  (see [CONTEXT.md](CONTEXT.md)). CI runs the whole
> suite offline, so it needs no API keys.

## Demo

One `./run_testpilot.sh examples/` pass:

**1. Generate → run → diagnose → patch → re-run, for each file:**

![TestPilot batch run: ambiguous.py is diagnosed as a test bug and patched to green, then correct_code_wrong_test.py also finishes green](docs/demo-batch-run.png)

**2. The diagnosis and the suggested fix, as a unified diff:**

![off_by_one.py: the diagnosis pins the off-by-one loop, and the suggested fix is printed as a unified diff while the original file stays untouched](docs/demo-diagnosis.png)

**3. The batch summary — and it runs on any file, not just the bundled examples:**

![Batch summary showing 3 of 3 files green, followed by a run on an arbitrary file outside the project](docs/demo-summary.png)

## Quick start

```bash
uv sync
cp .env.example .env     # add your Groq key or or create .env  file in the project paste your AI api directly and all the all api keys mentioned in  the project  are free and has great  limits to test the project 
uv run testpilot run examples/off_by_one.py
```

Exit codes: **0** tests green · **1** unclear/out of budget · **2** config error
(no API key — there is no offline mode).

### Batch runner

Point it at a folder, or at individual files — it asks if you give it nothing:

```bash
./run_testpilot.sh                  # prompts: "Where is the Python code?"
./run_testpilot.sh examples/        # every eligible .py in the folder
./run_testpilot.sh a.py b.py 2.py   # individual files
./run_testpilot.sh myscript         # auto-completes to myscript.py
```

`run_testpilot.py` is the real implementation (stdlib only); the `.sh` is a
convenience wrapper that finds `python3`/`uv`.

By default each file streams TestPilot's full report straight to your terminal —
node progress (`→ generate`, `→ run 7 passed`), the status panel, the loop-history
table, the diagnosis and the unified diff — exactly as a standalone run prints it,
followed by a batch summary table. Useful flags: `--write` (save
`<name>.fixed.py`), `--dry-run` (show what would run), `--json` (machine mode:
per-file reports are captured, and stdout is one JSON array), `--list-skipped`,
`--include-tests`, `--provider`, `--model`, `--timeout`.

Auto-detection: folders are walked recursively, skipping `src/`, `testing/`,
venvs, `__pycache__`, `test_*.py`, `conftest.py`, and any `*.fixed.py` left by a
previous run. Duplicate targets are de-duplicated, and a config error aborts the
batch instead of failing the same way on every remaining file.

## Documentation

| File | What's in it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it works, in one long paragraph + module map + state machine |
| [CONTEXT.md](CONTEXT.md) | Why it's built this way: decisions, verified model IDs, known gaps |
| [QUICKSTART.md](QUICKSTART.md) | Copy-paste commands, all verified |
| [TestPilot_Hackathon_Plan.md](TestPilot_Hackathon_Plan.md) | The original brief |

## Layout

```
src/testpilot/
├── cli.py           # typer entrypoint: `testpilot run file.py`
├── config.py        # provider + model IDs + limits
├── llm.py           # LLM protocol + HTTPChatLLM (Groq/OpenRouter/Gemini)
├── sandbox/         # Sandbox interface + local subprocess backend
├── agent/           # state, loop, generate, diagnose, patch
├── prompts.py       # all prompt templates
├── parsing.py       # extract code blocks / JSON
└── report.py        # final report + diff
```
## why  this was made  and idea behind it 

honestly i use llm a lot  very lot and i love vibe  coding  and  you know all these  llms  sometimes consumes your tokens   by generating  the whole rewrite   code instead of telling what to fix and where and don't even the run the codes   which can leads  lots of hallicutions and promblems that everyones hates that why i made Testpilot  a aagent that runs the code in the sandbox enviroment inside your terminal and  tells  weather your python code is great or not  and how to improve the  code only using llm api   and with strict system-prompt and also  i plan to  work further on this and  publish as standalone project or make it plugin for claude code/open code  

## License

MIT
