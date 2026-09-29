"""Correct implementation whose obvious-looking test would be wrong.

`slugify` is well specified here: lowercase, spaces become single hyphens,
everything else outside [a-z0-9-] is dropped. A careless test asserts that
underscores survive — the *code* is right, the *test* is wrong.
"""


def slugify(text: str) -> str:
    """Turn arbitrary text into a URL slug.

    - lowercase everything
    - each run of whitespace becomes a single '-'
    - characters outside [a-z0-9-] are removed (so '_' is dropped)
    - leading/trailing '-' are stripped

    Example: slugify("Hello,  World!") == "hello-world"
    """
    result = []
    for char in text.lower():
        if char.isspace():
            result.append("-")
        elif char.isalnum() or char == "-":
            result.append(char)
    slug = "".join(result)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")
