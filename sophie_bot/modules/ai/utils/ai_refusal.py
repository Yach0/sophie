from __future__ import annotations


class AIModelRefused(Exception):
    """A candidate finished its run without producing a usable answer.

    Not a provider failure — the request succeeded — so it never reaches Sentry as one. It exists so
    the failover loop can treat "answered with nothing" the same way it treats "could not answer",
    and a caller that can recognise a useless answer of its own opts into that handling by raising
    it from an output validator: the loop catches it and moves to the next candidate.

    The last candidate is the exception. There the refusal is re-raised, because a raised refusal
    carries no answer to hand back the way an empty output does, so the caller that chose to raise
    it is the one that decides what an exhausted chain means.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        super().__init__(f"{model_name} produced no usable output")


def is_refusal_output(output: object) -> bool:
    """Sophie's definition of a refusal: a text answer with nothing in it.

    Deliberately structural rather than a phrase list. A model that cannot handle what it was sent —
    an image it cannot see, a tool loop it will not run — either errors, which the failover loop
    already catches, or returns empty; anything else is a real answer, and second-guessing its
    wording would mean re-running perfectly good replies in every language Sophie speaks.

    Structured output is never a refusal: a validated schema is by construction an answer.
    """
    return isinstance(output, str) and not output.strip()
