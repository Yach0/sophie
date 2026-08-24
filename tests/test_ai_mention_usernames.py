"""The model only ever sees display names, so its ``@Name`` mentions are inert text.

These tests pin the one-way repair: a mention is rewritten to a real ``@username`` only when it
maps to exactly one known participant, and everything else — unknown names, shared names, code,
URLs, mentions that already are usernames — must survive byte-for-byte.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils import mention_usernames
from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.modules.ai.utils.chatbot_response import build_reply_doc
from sophie_bot.modules.ai.utils.mention_usernames import (
    MentionCandidate,
    apply_mention_usernames,
    build_mention_index,
    collect_mention_candidates,
    resolve_mention_index,
    resolve_mentions,
)

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
    assert index.usernames_by_name == {}
    assert index.pattern is None


def test_regex_special_characters_in_display_names_are_literal() -> None:
    index = _index(MentionCandidate(display_names=("A.B (Ops)",), username="ab_ops"))
    assert resolve_mentions("@A.B (Ops)", index) == "@ab_ops"
    assert resolve_mentions("@AxB (Ops)", index) == "@AxB (Ops)"


# ── Escaping and rendering ─────────────────────────────────────────────────────


@pytest.fixture
def _enabled_with(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(*candidates: MentionCandidate, enabled: bool = True) -> None:
        async def fake_is_enabled(feature: str, chat_tid: int | None = None) -> bool:
            assert feature == "ai_chatbot_mention_usernames"
            assert chat_tid == CHAT_TID
            return enabled

        async def fake_collect(chat_tid: int) -> tuple[MentionCandidate, ...]:
            assert chat_tid == CHAT_TID
            return candidates

        monkeypatch.setattr(mention_usernames, "is_enabled", fake_is_enabled)
        monkeypatch.setattr(mention_usernames, "collect_mention_candidates", fake_collect)

    return _install


async def _render(text: str) -> str:
    doc = await build_reply_doc(
        HEADER,
        text,
        model=None,
        result=None,
        explicit_debug_mode=False,
        chat_tid=CHAT_TID,
    )
    return doc.to_html()


@pytest.mark.asyncio
async def test_rendered_reply_contains_the_resolved_username(_enabled_with: Any) -> None:
    _enabled_with(MentionCandidate(display_names=("John Smith", "John"), username="john_s"))
    html = await _render("Hey @John Smith, done!")
    assert "@john_s" in html
    assert "@John Smith" not in html


@pytest.mark.asyncio
async def test_display_name_with_markup_characters_is_replaced_and_escaped(_enabled_with: Any) -> None:
    # A display name is attacker-controlled text; replacing it must not smuggle raw HTML through,
    # and the surrounding text must still be escaped by the renderer.
    _enabled_with(MentionCandidate(display_names=("<b>Bold</b> & Co",), username="bold_co"))
    html = await _render("Hi @<b>Bold</b> & Co, see <i>this</i>")
    assert "@bold_co" in html
    assert "<b>Bold</b>" not in html
    assert "&lt;i&gt;this&lt;/i&gt;" in html


@pytest.mark.asyncio
async def test_markdown_formatting_around_a_mention_survives(_enabled_with: Any) -> None:
    _enabled_with(MentionCandidate(display_names=("Maria",), username="maria99"))
    html = await _render("**bold** and @Maria and `@Maria`")
    assert "@maria99" in html
    assert "<b>bold</b>" in html
    # The mention inside the code span keeps the display name.
    assert "@Maria</code>" in html


# ── Feature flag ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_flag_leaves_the_reply_untouched(_enabled_with: Any) -> None:
    _enabled_with(MentionCandidate(display_names=("John",), username="john_s"), enabled=False)
    assert await apply_mention_usernames("Hey @John", CHAT_TID) == "Hey @John"


@pytest.mark.asyncio
async def test_enabled_flag_resolves_the_mention(_enabled_with: Any) -> None:
    _enabled_with(MentionCandidate(display_names=("John",), username="john_s"))
    assert await apply_mention_usernames("Hey @John", CHAT_TID) == "Hey @john_s"


@pytest.mark.asyncio
async def test_resolve_mention_index_collects_candidates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_collect(chat_tid: int) -> tuple[MentionCandidate, ...]:
        nonlocal calls
        calls += 1
        return (MentionCandidate(display_names=("John",), username="john_s"),)

    async def fake_is_enabled(feature: str, chat_tid: int | None = None) -> bool:
        return True

    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", fake_collect)
    monkeypatch.setattr(mention_usernames, "is_enabled", fake_is_enabled)

    index = await resolve_mention_index(CHAT_TID)
    assert index is not None
    assert resolve_mentions("@John", index) == "@john_s"
    assert resolve_mentions("Again @John", index) == "Again @john_s"
    assert calls == 1


@pytest.mark.asyncio
async def test_text_without_an_at_sign_never_touches_the_flag_or_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("must not be reached")

    monkeypatch.setattr(mention_usernames, "is_enabled", explode)
    monkeypatch.setattr(mention_usernames, "collect_mention_candidates", explode)

    assert await apply_mention_usernames("no mentions here", CHAT_TID) == "no mentions here"
    assert await apply_mention_usernames("@John", None) == "@John"
    assert await apply_mention_usernames("", CHAT_TID) == ""


@pytest.mark.asyncio
async def test_flag_defaults_to_off() -> None:
    from sophie_bot.utils import feature_flags

    assert feature_flags.get_default_value("ai_chatbot_mention_usernames") is False


# ── Candidate collection ───────────────────────────────────────────────────────


def _cached(user_id: int) -> MessageType:
    return MessageType(user_id=user_id, message_id=user_id, text="hi", created_at=datetime.now(UTC))


class _FakeUser:
    def __init__(self, first_name: str, last_name: str | None, username: str | None) -> None:
        self.first_name_or_title = first_name
        self.last_name = last_name
        self.username = username


@pytest.mark.asyncio
async def test_collect_candidates_uses_the_message_cache(monkeypatch: pytest.MonkeyPatch) -> None:
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

    candidates = await collect_mention_candidates(CHAT_TID)

    assert candidates == (
        MentionCandidate(display_names=("John Smith", "John"), username="john_s"),
        MentionCandidate(display_names=("Maria",), username="maria99"),
    )


@pytest.mark.asyncio
async def test_collect_candidates_without_cached_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cached_messages(chat_tid: int, **kwargs: Any) -> tuple[MessageType, ...]:
        return ()

    monkeypatch.setattr(mention_usernames, "get_cached_messages", fake_cached_messages)
    assert await collect_mention_candidates(CHAT_TID) == ()
