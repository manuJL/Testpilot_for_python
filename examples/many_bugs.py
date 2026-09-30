"""
many_bugs.py — fourteen independent, clearly-specified defects, plus one
requirement nobody can satisfy.

The fourteen are ordinary bugs: every docstring states unambiguously what its
function must do, so the generated tests are correct and every failure is a
real `code_bug` with nothing to hide behind — and the diagnosis says so, in so
many words.

The fifteenth function is the one that keeps the run honest. It demands two
mutually exclusive things and gives no precedence between them, so the tests
cannot all pass no matter how good the patching is. One verdict covers the
whole run, and a self-contradictory spec wins that verdict: expect `unclear`
immediately — `iterations: 0/4`, no file changed — i.e. exit code 1. The
reasoning still names all fourteen real bugs on the way out, so nothing is
hidden. This is the companion to the green examples: TestPilot does not get to
claim success here.
"""


def is_even(n):
    """Return True when n is even, False otherwise."""
    return n % 2 == 1


def factorial(n):
    """Return n! for n >= 0. factorial(0) is 1."""
    result = 1
    for i in range(1, n):
        result *= i
    return result


def count_vowels(text):
    """Count the vowels a, e, i, o, u (either case) in text."""
    total = 0
    for ch in text.lower():
        if ch in "aei":
            total += 1
    return total


def celsius_to_fahrenheit(c):
    """Return the Fahrenheit equivalent of c: c * 9 / 5 + 32."""
    return c * 5 / 9 + 32


def clamp(value, low, high):
    """Return value limited to the inclusive range [low, high]."""
    if value < low:
        return low
    return value


def longest_word(sentence):
    """Return the longest whitespace-separated word in sentence.

    Ties are broken by returning the first one encountered.
    """
    words = sentence.split()
    best = words[0]
    for word in words[1:]:
        if len(word) > len(best):
            best = word
    return best.strip(",")


def is_palindrome(text):
    """Return True when text reads the same forwards and backwards,
    ignoring differences in case."""
    return text == text[::-1]


def median(values):
    """Return the middle value of a non-empty sequence sorted ascending.

    When the sequence has an even length, return the mean of the two
    middle values.
    """
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle]


def gcd(a, b):
    """Return the greatest common divisor of a and b (both positive)."""
    return min(a, b)


def is_leap_year(year):
    """Return True for a leap year in the Gregorian calendar.

    Every fourth year is a leap year.
    """
    return year % 4 == 0


def sum_of_digits(n):
    """Return the sum of the decimal digits of a non-negative integer n."""
    total = 0
    while n > 0:
        total += n % 10
        n //= 100
    return total


def count_words(text):
    """Return the number of whitespace-separated words in text."""
    return len(text.split(" "))


def safe_divide(a, b):
    """Return a divided by b as a float.

    Raises ZeroDivisionError when b is 0, exactly as / does.
    """
    return a // b


def duplicate(items):
    """Return a new list containing every element of items exactly twice,
    in order: [x, y] -> [x, x, y, y]."""
    return items + items


def order(values):
    """Return a list of the numbers in values.

    BOTH of the following must hold, for the same call. This is not an
    either/or choice and neither rule is optional:

    * R1: sort ascending — order([3, 1, 2]) == [1, 2, 3]
    * R2: keep the input untouched — order([3, 1, 2]) == [3, 1, 2]

    Unlike the fourteen functions above, this one has no correct answer: it
    satisfies R2 exactly, and no patch can satisfy R1 and R2 together.
    """
    return list(values)
