from __future__ import annotations

from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.modules.ai.utils.feature_settings import ProactiveReplySettings
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory


def render_messages_for_prompt(messages: tuple[MessageType, ...]) -> str:
    rendered_messages: list[str] = []
    for message in messages:
        username = message.username or str(message.user_id)
        reply_part = ""
        if message.reply_to_message_id:
            reply_username = message.reply_to_username or str(message.reply_to_user_id or "unknown")
            reply_part = f" | replies_to={message.reply_to_message_id} ({reply_username})"
        rendered_messages.append(
            " | ".join(
                (
                    f"message_id={message.message_id}",
                    f"time={message.created_at.isoformat() if message.created_at else 'unknown'}",
                    f"user={username}{reply_part}",
                    f"text={message.text}",
                )
            )
        )
    return "\n".join(rendered_messages)


def build_decision_prompt(messages: tuple[MessageType, ...], settings: ProactiveReplySettings) -> str:
    rendered_messages = render_messages_for_prompt(messages)
    return "\n".join(
        (
            "Decide how Sophie should naturally join this Telegram chat: none, react, or answer.",
            f"Limits: max {settings.max_answers} answers, max {settings.max_reactions} reactions.",
            "Use answer when Sophie should reply to a specific message; the answer action will be sent as a Telegram reply to that message.",
            settings.prompt,
            "React only when it clearly fits the moment; do not react just to do something. Choose only Telegram reaction emoji; avoid 😊 🙂 😅 😆 😜 😉.",
            "Choose none when Sophie would not add anything, or the batch is spam, pure transactions, moderation chatter, or an obvious interruption.",
            "Pick only provided message_id values.",
            "Recent messages:",
            rendered_messages,
        )
    )


def build_decision_history(messages: tuple[MessageType, ...], settings: ProactiveReplySettings) -> AIMessageHistory:
    history = AIMessageHistory()
    history.add_system(
        "Return structured JSON only. Sophie is usually silent and only joins when her contribution is clearly "
        "timely, useful, or funny. Prefer none unless there is a strong natural opening; use reactions for "
        "lightweight moments."
    )
    history.prompt = [build_decision_prompt(messages, settings)]
    return history
