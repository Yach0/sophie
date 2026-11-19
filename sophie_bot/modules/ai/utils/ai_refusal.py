from __future__ import annotations


def is_refusal_output(output: object) -> bool:
    """Reject blank text without interpreting wording or structured output."""
    return isinstance(output, str) and not output.strip()
