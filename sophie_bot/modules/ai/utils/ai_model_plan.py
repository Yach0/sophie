from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.models import Model


@dataclass(frozen=True, slots=True)
class AIModelCandidate:
    """One model that may serve a purpose, with the capabilities that decide whether it may.

    ``model`` is already built for the role it came from, so its reasoning effort and provider
    settings travel with it — a candidate is runnable as-is, with nothing left to resolve.
    """

    model: Model
    model_name: str
    supports_images: bool = True


@dataclass(frozen=True, slots=True)
class AIModelPlan:
    """The ordered models that may serve one AI purpose in one chat, best first.

    Every purpose gets a plan, whether it has one candidate or five: the runtime always walks the
    same list, so a mode with a single model behaves exactly as it did before failover existed.
    """

    candidates: tuple[AIModelCandidate, ...] = ()

    @property
    def primary(self) -> Model:
        """The model to build the agent with. Empty plans are an operator mistake, so they raise."""
        if not self.candidates:
            raise ValueError("An AI model plan with no candidates cannot serve a request")
        return self.candidates[0].model

    @property
    def model_names(self) -> tuple[str, ...]:
        return tuple(candidate.model_name for candidate in self.candidates)

    def eligible(self, *, has_images: bool) -> tuple[AIModelCandidate, ...]:
        """The candidates allowed to serve a request, in priority order.

        A request carrying an image skips the models that cannot be shown one. If that leaves
        nothing, the full list comes back instead: a chat whose every candidate is text-only is
        better served by trying anyway — which is what happened before image filtering existed —
        than by refusing to answer at all.
        """
        if not has_images:
            return self.candidates
        with_images = tuple(candidate for candidate in self.candidates if candidate.supports_images)
        return with_images or self.candidates

    def models(self, *, has_images: bool) -> tuple[Model, ...]:
        return tuple(candidate.model for candidate in self.eligible(has_images=has_images))


def build_model_plan(candidates: Iterable[AIModelCandidate]) -> AIModelPlan:
    """A plan from candidates in priority order, dropping any model that already appears earlier."""
    seen: set[str] = set()
    ordered: list[AIModelCandidate] = []
    for candidate in candidates:
        if candidate.model_name in seen:
            continue
        seen.add(candidate.model_name)
        ordered.append(candidate)
    return AIModelPlan(candidates=tuple(ordered))


def _contents_have_image(contents: Sequence[UserContent]) -> bool:
    return any(isinstance(content, BinaryContent) and content.is_image for content in contents)


def request_has_images(
    user_prompt: str | Sequence[UserContent] | None,
    message_history: Sequence[ModelRequest | ModelResponse] | None = None,
) -> bool:
    """Whether anything the model will be shown is an image.

    Both halves count: the current turn carries the images pulled off the message and its reply,
    while the history carries any the caller folded in earlier. Non-visual binary content (audio)
    does not — it reaches the model transcribed to text, which every model can read.
    """
    if user_prompt is not None and not isinstance(user_prompt, str) and _contents_have_image(user_prompt):
        return True

    for message in message_history or ():
        for part in message.parts:
            if (
                isinstance(part, UserPromptPart)
                and not isinstance(part.content, str)
                and _contents_have_image(part.content)
            ):
                return True
    return False
