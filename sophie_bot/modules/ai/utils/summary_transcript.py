from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.utils.i18n import gettext as _

UNKNOWN_TIME = "??:??"
UNKNOWN_AUTHOR = "unknown"


@dataclass(frozen=True, slots=True)
class SummaryTranscript:
    """A rendered transcript plus the local-only mapping from model-visible references to messages.

    ``format_instructions`` describes the reference scheme to the model and must be sent with the
    transcript, since the two renderings address messages differently.
    """

    text: str
    format_instructions: str
    messages_by_reference: dict[int, MessageType]


def _render_time(message: MessageType, *, anonymize: bool) -> str:
    if not message.created_at:
        return UNKNOWN_TIME
    if anonymize:
        return message.created_at.astimezone(UTC).strftime("%H:%M")
    return message.created_at.isoformat()


def _build_speaker_labels(messages: tuple[MessageType, ...]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for message in messages:
        if message.user_id not in labels:
            labels[message.user_id] = f"speaker{len(labels) + 1}"
    return labels


def _build_anonymized_transcript(messages: tuple[MessageType, ...]) -> SummaryTranscript:
    speakers = _build_speaker_labels(messages)
    messages_by_reference = dict(enumerate(messages, start=1))
    text = "\n".join(
        f"[{number}] [{_render_time(message, anonymize=True)}] [{speakers[message.user_id]}] "
        f"{' '.join(message.text.split())}"
        for number, message in messages_by_reference.items()
    )
    return SummaryTranscript(
        text=text,
        format_instructions=_(
            "Every transcript line starts with its own number in square brackets, followed by the time and an "
            "anonymous speaker label. Reference source messages by those line numbers."
        ),
        messages_by_reference=messages_by_reference,
    )


def _build_plain_transcript(messages: tuple[MessageType, ...]) -> SummaryTranscript:
    messages_by_reference = {message.message_id: message for message in messages}
    text = "\n".join(
        f"[id={message.message_id}] [{_render_time(message, anonymize=False)}] "
        f"[{message.username or UNKNOWN_AUTHOR}] {' '.join(message.text.split())}"
        for message in messages
    )
    return SummaryTranscript(
        text=text,
        format_instructions=_(
            "Every transcript line starts with its message ID as [id=<id>], followed by the timestamp and the "
            "author. Reference source messages by those IDs."
        ),
        messages_by_reference=messages_by_reference,
    )


def build_summary_transcript(messages: tuple[MessageType, ...], *, anonymize: bool) -> SummaryTranscript:
    """Renders the transcript for the AI provider.

    With ``anonymize`` the provider never sees a real Telegram message ID, username, or absolute
    timestamp, so it cannot correlate the transcript back to the chat. Gated by the
    ``ai_summary_improved_privacy`` feature flag.
    """
    if anonymize:
        return _build_anonymized_transcript(messages)
    return _build_plain_transcript(messages)
