from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.proactive_replies import (
    ProactiveAction,
    ProactiveDecision,
    ProactiveReplySettings,
    _get_recent_candidates,
    _is_candidate,
    _limit_actions,
    _normalize_reaction_emoji,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            MessageType(user_id=1, message_id=1, text="hello"),
            True,
        ),
        (
            MessageType(user_id=1, message_id=2, text="/ai hello", has_ai_command=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=3, text="hello", handled_by_ai=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=4, text="hello", reply_to_is_sophie_ai=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=5, text="hello", eligible_for_proactive_ai=False),
            False,
        ),
    ),
)
def test_is_candidate_filters_already_handled_ai_messages(message: MessageType, expected: bool) -> None:
    assert _is_candidate(message) is expected


def test_normalize_reaction_emoji_keeps_supported_telegram_reactions() -> None:
    assert _normalize_reaction_emoji("🤣") == "🤣"


def test_normalize_reaction_emoji_falls_back_for_invalid_reactions() -> None:
    assert _normalize_reaction_emoji("😊") == "👍"


def test_limit_actions_respects_answer_and_reaction_caps() -> None:
    decision = ProactiveDecision(
        actions=[
            ProactiveAction(action="answer", message_id=1),
            ProactiveAction(action="react", message_id=2, emoji="👍"),
            ProactiveAction(action="answer", message_id=3),
            ProactiveAction(action="react", message_id=4, emoji="😂"),
            ProactiveAction(action="answer", message_id=5),
            ProactiveAction(action="none"),
        ]
    )
    settings = ProactiveReplySettings(max_answers=2, max_reactions=1)

    actions = _limit_actions(decision, settings)

    assert [(action.action, action.message_id) for action in actions] == [
        ("answer", 1),
        ("react", 2),
        ("answer", 3),
    ]


@pytest.mark.asyncio
async def test_get_recent_candidates_uses_window_batch_and_eligibility() -> None:
    chat_tid = -1001234567890
    now = datetime.now(timezone.utc)
    old_created_at = now - timedelta(minutes=20)
    recent_created_at = now - timedelta(minutes=3)

    await cache_message("too old", chat_tid, 10, 10, old_created_at, "old_user")
    await cache_message(
        "/ai already handled",
        chat_tid,
        11,
        11,
        recent_created_at,
        "ai_user",
        has_ai_command=True,
        eligible_for_proactive_ai=False,
    )
    for message_id in range(12, 18):
        await cache_message(
            f"message {message_id}",
            chat_tid,
            message_id,
            message_id,
            recent_created_at + timedelta(seconds=message_id),
            f"user_{message_id}",
        )

    settings = ProactiveReplySettings(batch_size=3, window_seconds=600, min_messages=3)

    candidates = await _get_recent_candidates(chat_tid, settings)

    assert [message.message_id for message in candidates] == [15, 16, 17]
