"""
python5.py — contains a real bug (wrong comparison) for TestPilot to catch and patch.
find_max(numbers) should return the largest number in a non-empty list.
"""


def find_max(numbers):
    """Return the largest number in numbers. numbers is a non-empty list."""
    largest = numbers[0]
    for n in numbers:
        # BUG: uses < instead of >, so this actually finds the minimum.
        if n < largest:
            largest = n
    return largest
