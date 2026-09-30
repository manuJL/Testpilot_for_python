"""Test-suite integrity: does this suite actually exercise the code under test?

A test file can be *green* while proving nothing. Three ways this happens in
practice:

1. The model pastes the source into the test file — the suite then tests a
   copy, never the module under test.
2. The model deletes the failing test — one fewer failure, still green.
3. The model rewrites `assert t.add(2, 3) == 5` into `assert True`.

None of those is a fix, so before anything is reported as green we check that
the suite imports the target, still contains its tests, and still contains its
assertions — and we reject a patch that would weaken any of them.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .prompts import MODULE_NAME


@dataclass(frozen=True)
class SuiteStats:
    """What a test file is made of, measured on the AST (never on prose)."""

    imports_target: bool
    test_count: int
    assert_count: int

    def describe(self) -> str:
        return f"tests: {self.test_count}, asserts: {self.assert_count}"


def analyse(test_code: str) -> SuiteStats:
    """Measure a test file. Unparseable input counts as an empty suite."""
    source = test_code or ""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return SuiteStats(imports_target=False, test_count=0, assert_count=0)

    imports_target = False
    test_count = 0
    assert_count = 0

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == MODULE_NAME for alias in node.names):
                imports_target = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == MODULE_NAME:
                imports_target = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                test_count += 1
        elif isinstance(node, ast.Assert):
            assert_count += 1

    # `importlib.import_module("testpilot_target")` (or a string the module is
    # named in) is not an AST import, but it is still a real dependency.
    if not imports_target and MODULE_NAME in source:
        imports_target = True

    return SuiteStats(imports_target=imports_target, test_count=test_count, assert_count=assert_count)


def usability_reason(test_code: str) -> str | None:
    """Why this suite proves nothing. None means it is usable."""
    stats = analyse(test_code)
    if stats.test_count == 0:
        return "it contains no test functions"
    if not stats.imports_target:
        return f"it never imports `{MODULE_NAME}` (so it tests nothing real)"
    return None


def degradation_reason(previous: str, current: str) -> str | None:
    """Why a *patched* suite is untrustworthy. None means the patch is acceptable.

    Every check is a comparison against the suite being replaced, never an
    absolute standard: a pre-existing problem must not block the patch that
    might fix it. Counts may stay level or grow — only a drop is disqualifying,
    because a drop is exactly what "delete the failing test" looks like.
    """
    before = analyse(previous)
    after = analyse(current)

    if before.imports_target and not after.imports_target:
        return f"the patch dropped the `{MODULE_NAME}` import"
    if after.test_count < before.test_count:
        return f"the patch deleted tests ({before.test_count} → {after.test_count})"
    if after.assert_count < before.assert_count:
        return f"the patch deleted asserts ({before.assert_count} → {after.assert_count})"
    return None
