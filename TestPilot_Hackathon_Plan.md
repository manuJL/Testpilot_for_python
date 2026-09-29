# TestPilot — BnB’26 Himachal Pradesh Zonal Round Plan

**Hackathon**: BnB’26 Himachal Pradesh Zonal Round  
**Duration**: 29 Sep 12:00 PM → 30 Sep 12:00 PM (24 hours, online)  
**Theme**: Build something that solves a **real problem** you or people around you face every day.  
**Your Hardware**: HP 240 G8 – i3-1115G4 (4 threads), 8 GB RAM, Intel UHD, CachyOS  

**Important**: You are **NOT** participating in the Nebius x NVIDIA Global AI Hackathon.  
This plan adapts the TestPilot idea so it works without any Nebius requirement.

---

## 1. Token Factory Sandbox Alternatives (Free / Cheap)

Since Nebius Token Factory Sandbox is **not free** for ongoing use, here are the best alternatives for running untrusted / AI-generated code safely:

| Rank | Tool                        | Type              | Free Tier                          | Best For                          | Notes for your laptop                  |
|------|-----------------------------|-------------------|------------------------------------|-----------------------------------|----------------------------------------|
| 1    | **Local subprocess**        | Self-hosted       | Completely free                    | Development + 24h demo            | Use this first. Safest for your RAM    |
| 2    | **E2B**                     | Managed microVM   | Hobby + $100 one-time credit       | Production-like isolation         | Best managed alternative               |
| 3    | **Modal Sandboxes**         | Managed gVisor    | ~$30/month free credits            | Python-heavy workloads            | Excellent free credits                 |
| 4    | **Docker Sandboxes**        | Local Docker      | Free                               | Coding agents                     | Needs Docker, still light              |
| 5    | **Upstash Box**             | Managed container | Free tier available                | Agent-friendly                    | Active-CPU billing                     |
| 6    | **Self-hosted Firecracker / gVisor** | DIY          | Free (your VPS cost)               | Maximum control                   | Too heavy for 24h + your hardware      |

**Recommendation for this 24-hour hackathon**:
- Build everything against the **local subprocess** sandbox first.
- Keep the `Sandbox` interface so you can later swap in E2B or Modal if needed.
- Do **not** depend on any paid cloud sandbox during the coding window.

---

## 2. Recommended Project for BnB’26 (Real Problem Focus)

### Project Name: **TestPilot**

**One-liner**  
Point it at a buggy Python file. It writes pytest tests, runs them safely, diagnoses whether the bug is in the code or the tests, and either fixes the tests or proposes a patch for the code — until the tests are green or it honestly says “unclear”.

**Why this fits the zonal theme**  
Students and junior developers in colleges across Himachal (and India) constantly struggle with:
- Writing good unit tests
- Understanding why tests fail
- Debugging their own code under time pressure

TestPilot directly solves a daily pain point for CS students.

**Minimum Viable Feature (must ship in 24h)**  
One complete loop:
1. Read a Python file
2. Generate pytest tests (via free LLM)
3. Run the tests in a local sandbox
4. If they fail → diagnose (test wrong vs code wrong)
5. Apply a simple fix or report the issue
6. Print a clean report + diff

That single working loop is already a strong demo.

---

## 3. Full Architecture (Adapted from your plan)

### Repo Layout
```
testpilot/
├── LICENSE
├── README.md
├── pyproject.toml
├── .env.example
├── src/testpilot/
│   ├── cli.py              # typer entrypoint: `testpilot run file.py`
│   ├── config.py           # provider + model IDs + limits
│   ├── llm.py              # one function: chat(role, messages) -> str
│   ├── sandbox/
│   │   ├── base.py         # Sandbox interface
│   │   ├── local.py        # subprocess version (primary for this hackathon)
│   │   └── e2b.py          # optional future (not required)
│   ├── agent/
│   │   ├── state.py        # dataclass holding the run's state
│   │   ├── loop.py         # the orchestrator
│   │   ├── generate.py     # step: write tests
│   │   ├── diagnose.py     # step: test bug or code bug?
│   │   └── patch.py        # step: apply the fix
│   ├── prompts.py          # all prompt templates
│   ├── parsing.py          # extract code blocks / JSON
│   └── report.py           # final report + diff
├── examples/               # 3 buggy demo files
└── tests/                  # unit tests for parsing + loop (fake LLM)
```

### High-level Flow
```
file.py → CLI → loop.py
                ├── llm.py  → Groq / OpenRouter / Gemini Flash (free)
                └── Sandbox → local subprocess (primary)
                └── report.py → terminal output + diff
```

Two clean seams:
1. `llm.py` hides the provider
2. `Sandbox` interface hides where code runs

### State Object (`state.py`)
```python
@dataclass
class State:
    source_path: Path
    original_source: str
    current_source: str
    test_code: str = ""
    iteration: int = 0
    max_iterations: int = 4
    last_run: RunResult | None = None
    history: list[dict] = field(default_factory=list)
    status: str = "running"   # running | green | code_bug_reported | gave_up
```

### Core Loop (`loop.py`)
```
1. Read file → State
2. GENERATE (fast model) → test_code
3. while iteration < max:
     a. Sandbox.run(source, test_code) → last_run
     b. if all tests pass → status = "green"; break
     c. DIAGNOSE (stronger model) → verdict + fix
     d. PATCH the correct target
     e. record history; iteration += 1
4. REPORT (status, diff, reasoning)
```

**Important design rule**  
Never silently overwrite the user’s original file. Always write a `.fixed.py` or show a clean unified diff.

---

## 4. Best Tools & Frameworks for Your Laptop

### Core Stack (keep it extremely light)

| Purpose              | Tool                          | Why                                      |
|----------------------|-------------------------------|------------------------------------------|
| Language             | **Python 3.12+**              | Native, low overhead                     |
| CLI                  | **Typer**                     | Modern, type-safe, beautiful help        |
| LLM calls            | **Groq** (primary) + OpenRouter fallback | Free, very fast, no GPU needed          |
| Sandbox              | **subprocess** (local)        | Zero extra memory                        |
| Parsing              | `ast` + simple regex          | Built-in                                 |
| Diff                 | `difflib`                     | Built-in                                 |
| Packaging            | `pyproject.toml` + hatch/uv   | Modern, fast                             |
| Testing the agent    | `pytest` + fake LLM           | No credits wasted                        |

### Free LLM Providers (use these)

1. **Groq** – best free tier for coding (Llama 3.3 / Mixtral / Gemma)
2. **OpenRouter** – free models available (`:free` suffix)
3. **Google Gemini Flash** (via AI Studio) – also free and good

### Do **NOT** use on this machine
- Docker Desktop (too heavy)
- Local LLMs (Ollama, LM Studio) – will thrash your 8 GB RAM
- Electron apps
- Heavy web frameworks for the core agent

---

## 5. 24-Hour Build Order (Realistic)

| Time Block | Goal                                      | Deliverable                          |
|------------|-------------------------------------------|--------------------------------------|
| 0–2 h      | Project setup + config + llm.py           | One successful model call            |
| 2–4 h      | Sandbox interface + local.py              | Can run any Python code safely       |
| 4–7 h      | Generate step + run + print output        | First end-to-end generate → run      |
| 7–11 h     | Diagnose (JSON) + Patch                   | Can fix either tests or code         |
| 11–15 h    | Full loop + State + report.py             | Complete agent loop works            |
| 15–18 h    | CLI polish + 3 example files              | `testpilot run examples/bug1.py`     |
| 18–21 h    | README + demo video (even 90 sec)         | Submission ready                     |
| 21–24 h    | Buffer + small improvements + rest        | —                                    |

**Must-have by hour 15**: One complete working loop on a simple off-by-one bug.

---

## 6. Example Demo Files (prepare these early)

1. `examples/off_by_one.py` – classic indexing bug
2. `examples/correct_code_wrong_test.py` – proves the diagnose step works
3. `examples/ambiguous.py` – should end with status `unclear`

---

## 7. Final Submission Checklist (Zonal Round)

- [ ] Working CLI: `testpilot run some_file.py`
- [ ] At least one full successful loop shown
- [ ] Clear README with problem statement + how it solves a real student pain
- [ ] Short demo video (screen recording + voice)
- [ ] Public GitHub repo with open-source license
- [ ] Google Form submitted on time

---

## 8. Quick Start Commands (copy-paste)

```bash
# Create project
mkdir testpilot && cd testpilot
python -m venv .venv
source .venv/bin/activate
pip install typer rich httpx pydantic python-dotenv

# Later
pip install pytest
```

---

**You now have everything in one place.**

Focus on the **local sandbox + Groq** path.  
Ship one solid working loop.  
That is more than enough to impress judges in a 24-hour zonal round focused on real problems.

Good luck. Go build it.
