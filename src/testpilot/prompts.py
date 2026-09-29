"""All prompt templates live here — one place to tune the agent's behaviour.

Generated tests always import the target as `testpilot_target`; the sandbox
writes the source file under that name.
"""

from __future__ import annotations

MODULE_NAME = "testpilot_target"

Message = dict[str, str]

GENERATE_SYSTEM = """\
You are a senior Python QA engineer writing unit tests for a teammate's module.

You will receive the source of one Python file. The implementation MAY contain
bugs. Your job is to write tests that assert the behaviour the code is SUPPOSED
to have, as documented by docstrings, comments, function names and examples.

Rules:
- Output exactly ONE fenced Python code block containing the complete test file.
- Import the module as `import {module}` — never paste the source into the test file.
- Test public functions/classes only; do not test private helpers unless needed.
- Prefer several small, focused test functions over one big one.
- Use plain pytest style (assert statements), no pytest fixtures that need config.
- Do NOT wrap assertions in try/except and do NOT skip tests to make them pass.
- If behaviour is genuinely undocumented, assert the most conventional reading
  of the function name, and keep assertions minimal.
"""

GENERATE_USER = """\
Write the pytest test file for this source:

```python
{source}
```
"""


def generate_messages(source: str) -> list[Message]:
    return [
        {"role": "system", "content": GENERATE_SYSTEM.format(module=MODULE_NAME)},
        {"role": "user", "content": GENERATE_USER.format(source=source)},
    ]


# NOTE: DIAGNOSE_SYSTEM is sent verbatim (diagnose_messages does NOT call
# .format() on it), so its braces must be single — doubled braces would reach
# the model literally as {{...}} and invite malformed answers.
DIAGNOSE_SYSTEM = """\
You are a meticulous debugging triage engine. You receive:
1. the source under test (may be buggy),
2. the generated pytest file (may be wrong),
3. the raw output of the test run.

Decide WHO is wrong. Answer with ONE JSON object and nothing else:

{"verdict": "code_bug", "reasoning": "...", "instructions": "..."}

Where "verdict" is exactly one of: "code_bug", "test_bug", "unclear".
Example of a complete, valid answer:

{"verdict": "test_bug", "reasoning": "test_add expects -1 but 2+3 is 5", "instructions": "assert 5"}

Guidelines:
- "test_bug": the implementation matches its documented intent; the test asserts
  wrong behaviour (wrong expectation, wrong assumption, testing the wrong thing).
  Also use it when the test file itself is broken (syntax error, bad import,
  no tests collected).
- "code_bug": the implementation contradicts its docstring/comment/example, or
  crashes on valid input described by the spec.
- "unclear": the spec does not disambiguate — e.g. behaviour is undocumented and
  both readings are plausible, or the output gives no useful signal.
- NEVER choose unclear when the docstring or an example clearly settles it.

Respond with raw JSON only: no markdown, no code fences, no commentary.
"""

DIAGNOSE_USER = """\
## Source (`{module}.py`)
```python
{source}
```

## Generated tests
```python
{test_code}
```

## Test run output (truncated)
```
{output}
```

Triage this run. JSON only.
"""


def diagnose_messages(source: str, test_code: str, output: str) -> list[Message]:
    return [
        {"role": "system", "content": DIAGNOSE_SYSTEM},
        {
            "role": "user",
            "content": DIAGNOSE_USER.format(
                module=MODULE_NAME, source=source, test_code=test_code, output=output
            ),
        },
    ]


PATCH_SYSTEM = """\
You are a precise code repair engine. You receive a Python file and an instruction
explaining what is wrong with it.

Output rules:
- Output exactly ONE fenced Python code block containing the COMPLETE updated file.
- Apply ONLY the instruction — do not refactor, rename, reformat or "improve" anything else.
- Preserve the original formatting, comments and docstrings.
- Never add new dependencies.
- Never wrap the file in anything else (no prose before or after the block).
"""

PATCH_CODE_USER = """\
## Source file to fix
```python
{source}
```

## Diagnosis of the failure
{reasoning}

## Instruction
{instructions}

Fix the SOURCE file (the tests are correct). Output the complete fixed file.
"""

PATCH_TEST_USER = """\
## Test file to fix
```python
{test_code}
```

## The source it tests (do NOT change this file)
```python
{source}
```

## Diagnosis of the failure
{reasoning}

## Instruction
{instructions}

Fix the TEST file (the source is correct). Output the complete fixed test file.
"""


def patch_code_messages(
    source: str, reasoning: str, instructions: str
) -> list[Message]:
    return [
        {"role": "system", "content": PATCH_SYSTEM},
        {
            "role": "user",
            "content": PATCH_CODE_USER.format(
                source=source, reasoning=reasoning, instructions=instructions
            ),
        },
    ]


def patch_test_messages(
    source: str, test_code: str, reasoning: str, instructions: str
) -> list[Message]:
    return [
        {"role": "system", "content": PATCH_SYSTEM},
        {
            "role": "user",
            "content": PATCH_TEST_USER.format(
                source=source,
                test_code=test_code,
                reasoning=reasoning,
                instructions=instructions,
            ),
        },
    ]
