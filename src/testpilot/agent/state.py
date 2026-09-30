"""Agent state — the single source of truth passed between LangGraph nodes.

LangGraph nodes are pure-ish functions: `state in -> partial update out`.
`history` uses an append reducer so nodes can add entries without clobbering
each other; every other key is plain last-write-wins.
"""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

from ..config import Config
from ..sandbox.base import RunResult

# running | green | unclear | gave_up | error
STATUS_RUNNING = "running"
STATUS_GREEN = "green"
STATUS_UNCLEAR = "unclear"
# Budget genuinely spent (or graph step limit hit).
STATUS_GAVE_UP = "gave_up"
# The run was cut short by something OTHER than the budget: an LLM failure
# (rate limit, timeout, auth), an unparseable diagnosis, or no test code at
# all. Conflating this with gave_up would print "budget exhausted" for a 429,
# so it gets its own status and its own exit code (2).
STATUS_ERROR = "error"


class AgentState(TypedDict, total=False):
    # Inputs (set once at start)
    source_path: str
    original_source: str
    max_iterations: int

    # Mutable working set
    current_source: str
    test_code: str
    iteration: int

    # Latest observations
    last_run: RunResult | None
    diagnosis: dict[str, Any] | None
    code_patched: bool
    tests_patched: bool
    # How many patches in a row changed nothing (invalid output, an unchanged
    # file, or a patch rejected by integrity). Two in a row means the model is
    # looping, so the run stops instead of burning the whole budget.
    consecutive_no_progress: int

    # Bookkeeping
    status: str
    notes: Annotated[list[str], operator.add]
    history: Annotated[list[dict[str, Any]], operator.add]
    # Wall-clock seconds for the whole run. Set by the CLI after run_file()
    # returns (the graph itself never writes it), so it is absent from
    # new_state() — declaring it here keeps the contract honest.
    duration: float


def new_state(source_path: Path, source: str, config: Config) -> AgentState:
    """Build the initial state for one TestPilot run."""
    return {
        "source_path": str(source_path),
        "original_source": source,
        "current_source": source,
        "test_code": "",
        "iteration": 0,
        "max_iterations": config.max_iterations,
        "last_run": None,
        "diagnosis": None,
        "code_patched": False,
        "tests_patched": False,
        "consecutive_no_progress": 0,
        "status": STATUS_RUNNING,
        "notes": [],
        "history": [],
    }
