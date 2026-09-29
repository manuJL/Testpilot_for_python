"""
python6.py — correct function, slightly more involved logic.
is_palindrome(s) returns True if s reads the same forwards and backwards,
ignoring case and spaces.
"""


def is_palindrome(s):
    """Return True if s is a palindrome, ignoring case and spaces."""
    cleaned = s.replace(" ", "").lower()
    return cleaned == cleaned[::-1]
