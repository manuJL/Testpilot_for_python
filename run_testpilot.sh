#!/usr/bin/env bash
#
# run_testpilot.sh — thin launcher for run_testpilot.py
#
# Run it with no arguments and it will ask where your Python code is:
#
#     ./run_testpilot.sh
#
# Or hand it a folder and/or individual files:
#
#     ./run_testpilot.sh examples/
#     ./run_testpilot.sh foo.py 2.py myscript
#
# All extra flags are passed straight through (--write, --provider, --model,
# --dry-run, --list-skipped, --timeout, --max-iterations, --no-colour).
#
# Exit codes: 0 all green · 1 some not green · 2 could not run.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# uv is commonly installed to ~/.local/bin, which non-login shells may not have.
export PATH="${HOME}/.local/bin:${PATH}"

PYTHON=""
for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        PYTHON="${candidate}"
        break
    fi
done

if [ -z "${PYTHON}" ]; then
    echo "error: python3 not found on PATH" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    if [ -x "${HOME}/.local/bin/uv" ]; then
        export PATH="${HOME}/.local/bin:${PATH}"
    else
        echo "error: \`uv\` not found on PATH." >&2
        echo "       Install it: https://docs.astral.sh/uv/getting-started/installation/" >&2
        exit 2
    fi
fi

exec "${PYTHON}" "${SCRIPT_DIR}/run_testpilot.py" "$@"
