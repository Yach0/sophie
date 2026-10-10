"""Repair output-only name mentions for legacy replies and modern entertainment.

The visible rendering boundary resolves a mention only when the selected context permits it and
exactly one session participant matches. Known unresolved modern first names render without ``@``;
unknown authored mentions and protected code/URLs stay literal. Alias-only modes never back-trace names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Message
from stfu_tg import Doc
from stfu_tg.doc import Element

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils import ai_chatbot_reply as chatbot_reply
from sophie_bot.modules.ai.utils import mention_usernames, proactive_replies
from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.modules.ai.utils.chatbot_response import build_reply_doc
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer
from sophie_bot.modules.ai.utils.mention_usernames import (
    MentionCandidate,
    MentionPolicy,
    apply_mention_usernames,
    build_mention_index,
    collect_mention_candidates,
    resolve_mentions,
)
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import OldContext

pytestmark = pytest.mark.usefixtures("db_init")

CHAT_TID = -100123
HEADER = cast(Element, "H")


def _index(*candidates: MentionCandidate) -> mention_usernames.MentionIndex:
    return build_mention_index(candidates)


def _default_index() -> mention_usernames.MentionIndex:
    return _index(
        MentionCandidate(display_names=("John Smith", "John"), username="john_s"),
        MentionCandidate(display_names=("Maria",), username="maria99"),
    )


# ── Matching ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@John Smith", "@john_s"),
        ("@John", "@john_s"),
        ("ping @Maria please", "ping @maria99 please"),
        # Longest name wins: the full name must not be resolved as first-name-plus-stray-text.
        ("@John Smith is here", "@john_s is here"),
        ("@JOHN smith", "@john_s"),
        ("@john   Smith", "@john_s"),
        ("@John,", "@john_s,"),
        ("(@Maria)", "(@maria99)"),
        ("@John and @Maria", "@john_s and @maria99"),
        ("@John\nnext line", "@john_s\nnext line"),
    ],
)
def test_known_display_names_are_rewritten(text: str, expected: str) -> None:
    assert resolve_mentions(text, _default_index()) == expected


@pytest.mark.parametrize(
    "text",
    [
        "@Unknown person",
        # A longer word that merely starts with a known name is not that name.
        "@Johnny",
        "@Johns",
        # No leading boundary: e-mail addresses and handles glued to other text.
        "foo@John",
        "user@Maria.example",
        "@@John",
        # Protected regions.
        "`@John` in code",
        "```\n@John\n```",
        "~~~\n@Maria\n~~~",
        "[link](@John)",
        "see https://example.com/@John",
        # Plain text without any mention at all.
        "John Smith said hello",
        # Bare name without the @ sigil is left alone.
        "Ask John about it",
    ],
)
def test_non_mentions_are_left_untouched(text: str) -> None:
    assert resolve_mentions(text, _default_index()) == text


def test_mention_that_is_already_a_real_username_is_kept() -> None:
    index = _index(MentionCandidate(display_names=("john_s", "John"), username="john_s"))
    # "john_s" is both a display name and a real username here: rewriting it would be a no-op at
    # best and a misattribution at worst, so it stays exactly as written.
    assert resolve_mentions("@john_s", index) == "@john_s"
    assert resolve_mentions("@John", index) == "@john_s"


def test_username_of_another_user_is_not_rewritten() -> None:
    index = _index(
        MentionCandidate(display_names=("Maria",), username="maria99"),
        MentionCandidate(display_names=("maria99",), username="other_user"),
    )
    assert resolve_mentions("@maria99", index) == "@maria99"


def test_empty_index_is_a_no_op() -> None:
    assert resolve_mentions("@John", _index()) == "@John"


# ── Ambiguity and missing usernames ────────────────────────────────────────────


def test_duplicate_display_names_are_ambiguous_and_skipped() -> None:
    index = _index(
        MentionCandidate(display_names=("John",), username="john_one"),
        MentionCandidate(display_names=("John",), username="john_two"),
    )
    assert resolve_mentions("@John", index) == "@John"


def test_ambiguity_does_not_disable_unrelated_names() -> None:
    index = _index(
        MentionCandidate(display_names=("John Smith", "John"), username="john_one"),
        MentionCandidate(display_names=("John Doe", "John"), username="john_two"),
    )
    assert resolve_mentions("@John", index) == "@John"
    assert resolve_mentions("@John Smith", index) == "@john_one"
    assert resolve_mentions("@John Doe", index) == "@john_two"


def test_same_user_seen_twice_stays_resolvable() -> None:
    index = _index(
        MentionCandidate(display_names=("John",), username="john_s"),
        MentionCandidate(display_names=("John",), username="@john_s"),
    )
    assert resolve_mentions("@John", index) == "@john_s"


def test_candidate_without_username_is_dropped() -> None:
    assert resolve_mentions("@John", _index(MentionCandidate(display_names=("John",), username=""))) == "@John"


@pytest.mark.parametrize("display_name", ["A", "", "   ", "!!!"])
def test_unusable_display_names_are_ignored(display_name: str) -> None:
    index = _index(MentionCandidate(display_names=(display_name,), username="someone"))
    assert resolve_mentions(f"@{display_name}", index) == f"@{display_name}"


def test_regex_special_characters_in_display_names_are_literal() -> None:
    index = _index(MentionCandidate(display_names=("A.B (Ops)",), username="ab_ops"))
    assert resolve_mentions("@A.B (Ops)", index) == "@ab_ops"
    assert resolve_mentions("@AxB (Ops)", index) == "@AxB (Ops)"


# ── Escaping and rendering ─────────────────────────────────────────────────────


@pytest.fixture
def _enabled_with(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(*candidates: MentionCandidate) -> None:
        async def fake_collect(
            chat_tid: int,
            **kwargs: Any,
        ) -> tuple[MentionCandidate, ...]:
            assert chat_tid == CHAT_TID
            return candidates

        monkeypatch.setattr(mention_usernames, "collect_mention_candidates", fake_collect)

    return _install


async def _render(text: str, test_redis: object, *, strip_alien_html_tags: bool = True) -> str:
    doc = await build_reply_doc(
        HEADER,
        text,
        model=None,
        result=None,
        explicit_debug_mode=False,
        chat_tid=CHAT_TID,
        redis=test_redis,
        strip_alien_html_tags=strip_alien_html_tags,
    )
    return doc.to_html()


@pytest.mark.asyncio
async def test_rendered_reply_contains_the_resolved_username(
    _enabled_with: Any, test_redis: object, test_services: object
) -> None:
    _enabled_with(MentionCandidate(display_names=("John Smith", "John"), username="john_s"))
    html = await _render("Hey @John Smith, done!", test_redis)
    assert "@john_s" in html
    assert "@John Smith" not in html


@pytest.mark.asyncio
async def test_display_name_with_markup_characters_is_replaced_and_escaped(
    _enabled_with: Any, test_redis: object, test_services: object
) -> None:
    # A display name is attacker-controlled text; replacing it must not smuggle raw HTML through,
    # and the surrounding text must still be escaped by the renderer.
    _enabled_with(MentionCandidate(display_names=("<b>Bold</b> & Co",), username="bold_co"))
    html = await _render(
        "Hi @<b>Bold</b> & Co, see <i>this</i>",
        test_redis,
    )
    assert "@bold_co" in html
    assert "<b>Bold</b>" not in html
    assert "<i>this</i>" in html


@pytest.mark.asyncio
async def test_rendered_reply_removes_only_unsupported_html(test_redis: object, test_services: object) -> None:
    html = await _render("<section><div>Before</div></section><b>Bold</b>", test_redis)

    assert "<section>" not in html
    assert "<div>" not in html
    assert "Before" in html
    assert "<b>Bold</b>" in html


@pytest.mark.asyncio
async def test_disabled_alien_html_filter_escapes_all_raw_tags(test_redis: object, test_services: object) -> None:
    html = await _render("<div>Before</div><b>Bold</b>", test_redis, strip_alien_html_tags=False)

    assert "&lt;div&gt;Before&lt;/div&gt;" in html
    assert "&lt;b&gt;Bold&lt;/b&gt;" in html


@pytest.mark.asyncio
async def test_markdown_formatting_around_a_mention_survives(
    _enabled_with: Any, test_redis: object, test_services: object
) -> None:
    _enabled_with(MentionCandidate(display_names=("Maria",), username="maria99"))
    html = await _render("**bold** and @Maria and `@Maria`", test_redis)
    assert "@maria99" in html
    assert "<b>bold</b>" in html
    # The mention inside the code span keeps the display name.
    assert "@Maria</code>" in html


@pytest.mark.asyncio
async def test_mentions_are_resolved(_enabled_with: Any, test_redis: object, test_services: object) -> None:
    _enabled_with(MentionCandidate(display_names=("John",), username="john_s"))
    assert await apply_mention_usernames("Hey @John", CHAT_TID, redis=test_redis) == "Hey @john_s"


@pytest.mark.asyncio
async def test_text_without_an_at_sign_never_touches_the_cache(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not be reached")

    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", explode)

    assert await apply_mention_usernames("no mentions here", CHAT_TID, redis=test_redis) == "no mentions here"
    assert await apply_mention_usernames("@John", None, redis=test_redis) == "@John"
    assert await apply_mention_usernames("", CHAT_TID, redis=test_redis) == ""


# ── Candidate collection ───────────────────────────────────────────────────────


def _cached(user_id: int) -> MessageType:
    return MessageType(user_id=user_id, message_id=user_id, text="hi", created_at=datetime.now(UTC))


class _FakeUser:
    def __init__(self, first_name: str, last_name: str | None, username: str | None) -> None:
        self.first_name_or_title = first_name
        self.last_name = last_name
        self.username = username


@pytest.mark.asyncio
async def test_collect_candidates_uses_the_message_cache(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    users = {
        11: _FakeUser("John", "Smith", "john_s"),
        12: _FakeUser("Maria", None, "maria99"),
        13: _FakeUser("NoHandle", None, None),
    }

    async def fake_cached_messages(chat_tid: int, **kwargs: Any) -> tuple[MessageType, ...]:
        assert chat_tid == CHAT_TID
        # 11 appears twice and the bot's own messages are in there too.
        return (_cached(11), _cached(12), _cached(11), _cached(13), _cached(mention_usernames.CONFIG.bot_id))

    async def fake_get_by_tid(user_tid: int) -> Any:
        return users.get(user_tid)

    monkeypatch.setattr(mention_usernames, "get_cached_messages", fake_cached_messages)
    monkeypatch.setattr(mention_usernames.ChatModel, "get_by_tid", fake_get_by_tid)

    candidates = await collect_mention_candidates(CHAT_TID, redis=test_redis)

    assert candidates == (
        MentionCandidate(display_names=("John Smith", "John"), username="john_s"),
        MentionCandidate(display_names=("Maria",), username="maria99"),
        MentionCandidate(display_names=("NoHandle",), username=""),
    )


@pytest.mark.asyncio
async def test_collect_candidates_without_cached_messages(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    async def fake_cached_messages(chat_tid: int, **kwargs: Any) -> tuple[MessageType, ...]:
        return ()

    monkeypatch.setattr(mention_usernames, "get_cached_messages", fake_cached_messages)
    assert await collect_mention_candidates(CHAT_TID, redis=test_redis) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("preloaded_index", [False, True])
async def test_alias_only_rendering_never_back_traces_names(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Any,
    preloaded_index: bool,
) -> None:
    collect = AsyncMock(side_effect=AssertionError("Alias-only rendering must not collect participants"))
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    doc = await build_reply_doc(
        None,
        "**Hi** @Alice",
        None,
        None,
        False,
        CHAT_TID,
        mention_index=(
            _index(MentionCandidate(display_names=("Alice",), username="alice_real"))
            if preloaded_index
            else None
        ),
        redis=test_redis,
        mention_policy=MentionPolicy.OPAQUE,
    )

    assert "<b>Hi</b> @Alice" in doc.to_html()
    assert "@alice_real" not in doc.to_html()
    collect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mention_policy", list(MentionPolicy))
@pytest.mark.parametrize("render_limit", [3900, 1])
async def test_fitted_and_last_resort_replies_preserve_mention_policy(
    monkeypatch: pytest.MonkeyPatch,
    test_services: Any,
    render_limit: int,
    mention_policy: MentionPolicy,
) -> None:
    collect = AsyncMock(return_value=(MentionCandidate(display_names=("Alice",), username="alice_real"),))
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    speaker_names, _lookup = _modern_actor_lookup(
        monkeypatch, (MentionCandidate(display_names=("Alice",), username="alice_real"),)
    )
    monkeypatch.setattr(chatbot_reply, "TELEGRAM_MESSAGE_SAFE_LIMIT", render_limit)

    doc = await chatbot_reply._build_fitting_reply_doc(
        None,
        "Hi @Alice",
        None,
        None,
        False,
        CHAT_TID,
        services=test_services,
        mention_policy=mention_policy,
        speaker_names=speaker_names,
    )

    permitted = mention_policy != MentionPolicy.OPAQUE
    assert ("@alice_real" if permitted else "@Alice") in doc.to_html()


_REPLY_MODES = [
    (True, AIMode.entertainment, False),
    (True, AIMode.entertainment, True),
    (True, AIMode.support, False),
    (True, AIMode.moderation, False),
    (True, AIMode.sophie_help, False),
    (True, AIMode.sophie_pm, False),
    (False, AIMode.support, False),
]


def _reply_history(
    modern: bool, services: Any, speaker_names: tuple[tuple[int, str], ...] = ()
) -> OldContext | ModernContext:
    if not modern:
        return OldContext(services=services)
    history = ModernContext(redis=services.redis, chat_tid=CHAT_TID)
    history._mode = AIMode.entertainment
    for user_tid, first_name in speaker_names:
        history._speaker(user_tid)
        history._state.first_names[str(user_tid)] = first_name
    history.abort = AsyncMock()
    history.finish_run = AsyncMock()
    return history


def _reply_candidates(ambiguous: bool, *, username_collision: bool = False) -> tuple[MentionCandidate, ...]:
    candidates = (MentionCandidate(display_names=("Alice Smith", "Alice"), username="alice_real"),)
    if username_collision:
        candidates += (MentionCandidate(display_names=("Bob Brown", "Bob"), username="Alice"),)
    if ambiguous:
        candidates += (MentionCandidate(display_names=("Alice Jones", "Alice"), username="other_alice"),)
    return candidates


def _modern_actor_lookup(
    monkeypatch: pytest.MonkeyPatch, candidates: tuple[MentionCandidate, ...]
) -> tuple[tuple[tuple[int, str], ...], AsyncMock]:
    speaker_names = tuple((11 + index, candidate.display_names[-1]) for index, candidate in enumerate(candidates))
    # Current DB names may differ from what the model saw. Only the supplied labels are permitted.
    users = [
        SimpleNamespace(tid=user_tid, username=candidate.username, first_name_or_title="Changed", last_name="Surname")
        for (user_tid, _name), candidate in zip(speaker_names, candidates, strict=True)
    ]
    lookup = AsyncMock(return_value=users)
    monkeypatch.setattr(mention_usernames.ChatModel, "find", Mock(return_value=SimpleNamespace(to_list=lookup)))
    return speaker_names, lookup


@pytest.mark.asyncio
@pytest.mark.parametrize(("modern", "mode", "ambiguous"), _REPLY_MODES)
@pytest.mark.parametrize("streaming", [False, True])
async def test_chatbot_visible_mentions_follow_selected_history_through_model_fallback(
    monkeypatch: pytest.MonkeyPatch,
    test_services: Any,
    modern: bool,
    mode: AIMode,
    ambiguous: bool,
    streaming: bool,
) -> None:
    permitted = not modern or mode == AIMode.entertainment
    collect = AsyncMock(
        return_value=_reply_candidates(ambiguous, username_collision=modern),
        side_effect=None if permitted else AssertionError("Alias-only mode must not back-trace names"),
    )
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    speaker_names, _lookup = _modern_actor_lookup(
        monkeypatch, _reply_candidates(ambiguous, username_collision=modern)
    )
    source = SimpleNamespace(
        chat=SimpleNamespace(id=CHAT_TID),
        message_id=7,
        message_thread_id=None,
        from_user=None,
        text="Say hello",
    )
    sent = SimpleNamespace(message_id=8, date=datetime.now(UTC), message_thread_id=None, chat=source.chat)
    edit = AsyncMock(return_value=sent)
    sent.bot = source.bot = SimpleNamespace(edit_message_text=edit)
    streamer = (
        ChatbotMessageStreamer(cast(Message, source), "Thinking...", 0, redis=test_services.redis)
        if streaming
        else None
    )
    if streamer:
        streamer.response_message = sent
    history = _reply_history(modern, test_services, speaker_names)
    primary = SimpleNamespace(model_name="primary-model")
    fallback = SimpleNamespace(model_name="fallback-model")
    monkeypatch.setattr(chatbot_reply, "_resolve_model_plan", AsyncMock(return_value=SimpleNamespace(primary=primary)))
    monkeypatch.setattr(chatbot_reply, "prepare_chatbot_history", AsyncMock(return_value=history))
    monkeypatch.setattr(chatbot_reply, "build_message_streamer", AsyncMock(return_value=streamer))
    monkeypatch.setattr(chatbot_reply, "resolve_chat_service_tier", AsyncMock(return_value=None))

    async def enabled(feature: str, **kwargs: Any) -> bool:
        assert feature != "ai_chatbot_modern_context", "Rendering must use the selected history, not another flag read"
        return feature in {"ai_chatbot", "ai_chatbot_show_model_name"}

    monkeypatch.setattr(chatbot_reply, "is_enabled", enabled)

    async def run(request: Any) -> Any:
        if request.callbacks.on_text_stream:
            await request.callbacks.on_text_stream("Hello @Alice; @publichandle")
            await request.callbacks.on_text_stream("Hello @Alice! @publichandle")
        return SimpleNamespace(
            served_model=fallback,
            output="Hello @Alice! @publichandle",
            message_history=[],
            new_messages=[],
        )

    monkeypatch.setattr(chatbot_reply, "run_chatbot", run)
    monkeypatch.setattr(
        chatbot_reply, "_build_chatbot_header", AsyncMock(side_effect=lambda *args, **kwargs: Doc(args[1]))
    )
    monkeypatch.setattr(chatbot_reply, "should_offer_help_mode", AsyncMock(return_value=False))
    monkeypatch.setattr(chatbot_reply, "remember_chatbot_tool_history", AsyncMock())
    send = AsyncMock(return_value=sent)
    monkeypatch.setattr(chatbot_reply, "send_ai_rich_message", send)
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="chat", tid=CHAT_TID), tid=CHAT_TID)

    await chatbot_reply.ai_chatbot_reply(
        cast(Message, source), connection, mode=mode, services=test_services
    )

    expected = "@alice_real" if permitted and not ambiguous else "Alice" if modern and permitted else "@Alice"
    if streaming:
        previews = [call.kwargs["rich_message"].html for call in edit.await_args_list[:-1]]
        assert len(previews) == 2
        assert all(expected in preview for preview in previews)
        if modern and permitted and ambiguous:
            assert all("@Alice" not in preview for preview in previews)
        final_html = edit.await_args.kwargs["rich_message"].html
    else:
        final_html = send.await_args.args[1].to_rich()
    assert expected in final_html
    assert "@publichandle" in final_html
    if streaming:
        assert all("@publichandle" in preview for preview in previews)
    assert "Fallback Model" in final_html
    if not permitted or ambiguous:
        assert "@alice_real" not in final_html
    if modern and permitted and ambiguous:
        assert "@Alice" not in final_html
    if not permitted:
        collect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("modern", "mode", "ambiguous"), _REPLY_MODES)
@pytest.mark.parametrize("modern_flag", [False, True])
async def test_proactive_visible_mentions_follow_selected_history_through_model_fallback(
    monkeypatch: pytest.MonkeyPatch,
    test_services: Any,
    modern: bool,
    mode: AIMode,
    ambiguous: bool,
    modern_flag: bool,
) -> None:
    # The mode is read only when the proactive trigger sees the flag enabled; the history
    # returned afterward is still authoritative for whether legacy mention repair applies.
    permitted = not modern or (modern_flag and mode == AIMode.entertainment)
    collect = AsyncMock(
        return_value=_reply_candidates(ambiguous, username_collision=modern),
        side_effect=None if permitted else AssertionError("Alias-only proactive reply must not back-trace names"),
    )
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    speaker_names, _lookup = _modern_actor_lookup(
        monkeypatch, _reply_candidates(ambiguous, username_collision=modern)
    )
    history = _reply_history(modern, test_services, speaker_names)
    target = SimpleNamespace(message_id=7, message_thread_id=None, text="Say hello", username="user", user_id=1)
    chat = SimpleNamespace(iid="chat", tid=CHAT_TID, type="group", first_name_or_title="Group")
    sent = SimpleNamespace(message_id=8, date=datetime.now(UTC), message_thread_id=None)
    monkeypatch.setattr(proactive_replies, "get_chat_mode", AsyncMock(return_value=mode))
    monkeypatch.setattr(
        proactive_replies,
        "get_chat_default_model_plan",
        AsyncMock(return_value=SimpleNamespace(primary=SimpleNamespace(model_name="primary-model"))),
    )
    monkeypatch.setattr(proactive_replies, "resolve_chat_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(
        proactive_replies,
        "is_enabled",
        AsyncMock(
            side_effect=lambda feature, **kwargs: feature == "ai_chatbot_show_model_name"
            or (modern_flag and feature == "ai_chatbot_modern_context")
        ),
    )
    monkeypatch.setattr(proactive_replies, "_build_answer_history", AsyncMock(return_value=history))
    monkeypatch.setattr(
        proactive_replies,
        "run_chatbot",
        AsyncMock(
            return_value=SimpleNamespace(
                served_model=SimpleNamespace(model_name="fallback-model"),
                output="Hello @Alice! @publichandle",
                message_history=[],
                new_messages=[],
            )
        ),
    )
    monkeypatch.setattr(
        proactive_replies,
        "build_chatbot_header",
        AsyncMock(side_effect=lambda *args, **kwargs: Doc(kwargs["model_label"])),
    )
    send = AsyncMock(return_value=sent)
    monkeypatch.setattr(proactive_replies, "send_ai_rich_message_to_chat", send)

    await proactive_replies._answer_message(CHAT_TID, chat, target, services=test_services)

    final_html = send.await_args.args[1].to_rich()
    expected = "@alice_real" if permitted and not ambiguous else "Alice" if modern and permitted else "@Alice"
    assert expected in final_html
    assert "@publichandle" in final_html
    assert "Fallback Model" in final_html
    if not permitted or ambiguous:
        assert "@alice_real" not in final_html
    if modern and permitted and ambiguous:
        assert "@Alice" not in final_html
    if not permitted:
        collect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mention_policy", "expected"),
    [
        (MentionPolicy.LEGACY_DISPLAY_NAMES, "@Alice"),
        (MentionPolicy.MODERN_FIRST_NAMES, "@alice_secret"),
        (MentionPolicy.OPAQUE, "@Alice"),
    ],
)
async def test_first_name_match_beats_another_users_username_only_in_modern_entertainment(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Any,
    mention_policy: MentionPolicy,
    expected: str,
) -> None:
    collect = AsyncMock(
        return_value=(
            MentionCandidate(display_names=("Alice Smith", "Alice"), username="alice_secret"),
            MentionCandidate(display_names=("Bob Brown", "Bob"), username="Alice"),
        )
    )
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    speaker_names, _lookup = _modern_actor_lookup(monkeypatch, collect.return_value)
    doc = await build_reply_doc(
        None,
        "Hello @Alice",
        None,
        None,
        False,
        CHAT_TID,
        redis=test_redis,
        mention_policy=mention_policy,
        speaker_names=speaker_names,
    )

    assert f"Hello {expected}" in doc.to_html()


@pytest.mark.parametrize("blocker_first", [False, True])
def test_participant_without_username_blocks_same_name_target(blocker_first: bool) -> None:
    target = MentionCandidate(display_names=("Alice",), username="alice_real")
    blocker = MentionCandidate(display_names=("Alice",), username="")
    candidates = (blocker, target) if blocker_first else (target, blocker)

    assert resolve_mentions("@Alice", build_mention_index(candidates)) == "@Alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("older_actor", ["known", "no_username", "missing"])
@pytest.mark.parametrize("streamed", [False, True])
async def test_session_known_speaker_outside_recent_cache_blocks_wrong_first_name_target(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Any,
    older_actor: str,
    streamed: bool,
) -> None:
    # The recent cache contains only the newer Alice; the model still knows both session speakers.
    collect = AsyncMock(return_value=(MentionCandidate(display_names=("Alice",), username="newer_alice"),))
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", collect)
    users = [SimpleNamespace(tid=12, username="newer_alice")]
    if older_actor != "missing":
        users.append(SimpleNamespace(tid=11, username="older_alice" if older_actor == "known" else None))
    lookup = AsyncMock(return_value=users)
    find = Mock(return_value=SimpleNamespace(to_list=lookup))
    monkeypatch.setattr(mention_usernames.ChatModel, "find", find)
    speaker_names = ((11, "Alice"), (12, "Alice"))
    if streamed:
        source = SimpleNamespace(chat=SimpleNamespace(id=CHAT_TID))
        streamer = ChatbotMessageStreamer(
            cast(Message, source),
            "Thinking...",
            0,
            redis=test_redis,
            mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
            speaker_names=speaker_names,
        )
        doc = await streamer._render_doc("Hello @Alice")
        await streamer._render_doc("Again @Alice")
    else:
        doc = await build_reply_doc(
            None,
            "Hello @Alice",
            None,
            None,
            False,
            CHAT_TID,
            redis=test_redis,
            mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
            speaker_names=speaker_names,
        )

    assert "Hello Alice" in doc.to_html()
    assert "@Alice" not in doc.to_html()
    assert "@newer_alice" not in doc.to_html()
    assert "@older_alice" not in doc.to_html()
    collect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["ambiguous", "unmentionable"])
async def test_known_unresolved_modern_first_name_does_not_select_another_actor(
    monkeypatch: pytest.MonkeyPatch, test_redis: Any, reason: str
) -> None:
    candidates = [MentionCandidate(display_names=("Bob",), username="Alice")]
    if reason == "ambiguous":
        candidates.extend((
            MentionCandidate(display_names=("Alice",), username="alice_one"),
            MentionCandidate(display_names=("Alice",), username="alice_two"),
        ))
    elif reason == "unmentionable":
        candidates.append(MentionCandidate(display_names=("Alice",), username=""))
    speaker_names, _lookup = _modern_actor_lookup(monkeypatch, tuple(candidates))

    doc = await build_reply_doc(
        None,
        "Hello @Alice!",
        None,
        None,
        False,
        CHAT_TID,
        redis=test_redis,
        mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
        speaker_names=speaker_names,
    )

    assert "Hello Alice!" in doc.to_html()
    assert "@Alice" not in doc.to_html()
    assert "@alice_one" not in doc.to_html()
    assert "@alice_two" not in doc.to_html()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["ambiguous", "unmentionable"])
async def test_blocked_multiword_first_name_does_not_resolve_a_shorter_first_name(
    monkeypatch: pytest.MonkeyPatch, test_redis: Any, blocker: str
) -> None:
    candidates = [
        MentionCandidate(display_names=("Alice",), username="short_alice"),
        MentionCandidate(display_names=("Alice Snow",), username="long_alice" if blocker == "ambiguous" else ""),
    ]
    if blocker == "ambiguous":
        candidates.append(MentionCandidate(display_names=("Alice Snow",), username="other_long_alice"))
    speaker_names, _lookup = _modern_actor_lookup(monkeypatch, tuple(candidates))

    doc = await build_reply_doc(
        None,
        "Hello @Alice Snow; hello @Alice!",
        None,
        None,
        False,
        CHAT_TID,
        redis=test_redis,
        mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
        speaker_names=speaker_names,
    )

    assert "Hello Alice Snow; hello @short_alice!" in doc.to_html()
    assert "@long_alice" not in doc.to_html()
    assert "@other_long_alice" not in doc.to_html()


@pytest.mark.parametrize(
    "text",
    [
        "`@Unknown` in code",
        "```\n@Unknown\n```",
        "~~~\n@Unknown\n~~~",
        "[link](@Unknown)",
        "see https://example.com/@Unknown",
        "user@Unknown.example",
    ],
)
def test_modern_unresolved_mentions_in_protected_regions_are_unchanged(text: str) -> None:
    index = build_mention_index(
        (MentionCandidate(display_names=("Unknown",), username="known_actor"),),
        policy=MentionPolicy.MODERN_FIRST_NAMES,
    )

    assert resolve_mentions(text, index) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_modern_output_preserves_unrelated_authored_mentions(
    monkeypatch: pytest.MonkeyPatch, test_redis: Any, streamed: bool
) -> None:
    speaker_names, _lookup = _modern_actor_lookup(
        monkeypatch,
        (MentionCandidate(display_names=("Alice",), username="alice_real"),),
    )
    text = "**Hello** @publichandle; @Unknown; @Johnny; `@Alice`; https://example.com/@Alice; user@Alice.example"
    if streamed:
        source = SimpleNamespace(chat=SimpleNamespace(id=CHAT_TID))
        streamer = ChatbotMessageStreamer(
            cast(Message, source),
            "Thinking...",
            0,
            redis=test_redis,
            mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
            speaker_names=speaker_names,
        )
        doc = await streamer._render_doc(text)
    else:
        doc = await build_reply_doc(
            None,
            text,
            None,
            None,
            False,
            CHAT_TID,
            redis=test_redis,
            mention_policy=MentionPolicy.MODERN_FIRST_NAMES,
            speaker_names=speaker_names,
        )

    html = doc.to_html()
    assert "<b>Hello</b> @publichandle; @Unknown; @Johnny;" in html
    assert "<code>@Alice</code>" in html
    assert "https://example.com/@Alice" in html
    assert "user@Alice.example" in html
