"""
python4.py — correct function with a docstring describing edge-case behaviour.
safe_divide(a, b) returns a / b, or None if b is zero (never raises).
"""


def safe_divide(a, b):
    """Return a / b, or None if b is zero. Never raises ZeroDivisionError."""
    if b == 0:
        return None
    return a / b
