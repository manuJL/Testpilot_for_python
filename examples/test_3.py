"""
python3.py — contains a real bug (off-by-one) for TestPilot to catch and patch.
is_even(n) should return True if n is even, False otherwise.
"""


def is_even(n):
    """Return True if n is even, False otherwise."""
    # BUG: this is backwards — returns True for odd numbers.
    return n % 2 == 1
