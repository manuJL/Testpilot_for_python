"""ambiguous.py — undecidable choices, on purpose. Ends `unclear`, exit 1.

Each function has two parts:

* a plainly documented case with one right answer, which the tests can and do
  pass, and
* a second case where the spec states two requirements **for the same call**,
  with no preference between them and neither one optional.

The tests will check both rules; the source can honour only one of them.

The first part matters as much as the second. A function whose entire spec is
contested gives the generator nothing to hold on to, and it tends to write a
single test that picks whichever reading it likes — which the code can then
simply satisfy, so the run goes green having learned nothing. A clear case
earns the suite at least one test that must pass, and the contested case earns
one that cannot.

Expect `unclear` — no file written — or `gave_up` if it takes a try or two to
see it, i.e. exit code 1. Never a fake green.
"""


def index_of(items, target):
    """Return the zero-based position of target in items.

    When target is present, return its index — this part is settled:
    index_of([7, 8], 8) == 1

    When target is absent, BOTH of the following must hold for the same call.
    This is not an either/or choice and neither rule is optional:

    * R1: index_of([7, 8], 9) == -1
    * R2: index_of([7, 8], 9) == 2   (one past the end)
    """
    if target in items:
        return items.index(target)
    # Satisfies R1 exactly; R2 can never hold at the same time.
    return -1


def take(items, n):
    """Return n elements of items.

    When n is at least len(items), return all of them — this part is settled:
    take([1, 2], 5) == [1, 2]

    When 0 < n < len(items), BOTH of the following must hold for the same call.
    This is not an either/or choice and neither rule is optional:

    * R1: take([1, 2, 3, 4], 2) == [1, 2]   (the opening)
    * R2: take([1, 2, 3, 4], 2) == [3, 4]   (the closing)
    """
    # Satisfies R1 exactly; R2 can never hold at the same time.
    return list(items[:n])


def pad(text, width):
    """Return text padded with spaces to width characters.

    When len(text) >= width, return it unchanged — this part is settled:
    pad("abcdef", 3) == "abcdef"

    When len(text) < width, BOTH of the following must hold for the same call.
    This is not an either/or choice and neither rule is optional:

    * R1: pad("ab", 5) == "ab   "   (extra space on the right)
    * R2: pad("ab", 5) == "   ab"   (extra space on the left)
    """
    # Satisfies R1 exactly; R2 can never hold at the same time.
    return text.ljust(width)
