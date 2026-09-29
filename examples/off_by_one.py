"""Find the largest value in a list.

Classic off-by-one: the loop stops one element early, so the last
candidate is never considered.
"""


def find_largest(numbers: list[float]) -> float:
    """Return the largest number in the list.

    Raises ValueError on an empty list.
    """
    if not numbers:
        raise ValueError("find_largest() arg is an empty sequence")
    largest = numbers[0]
    for i in range(len(numbers) - 1):  # BUG: misses the final element
        if numbers[i] > largest:
            largest = numbers[i]
    return largest
