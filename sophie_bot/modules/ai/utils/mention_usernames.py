"""Rewrite the display-name mentions a model wrote into real Telegram usernames.

The chatbot's prompt deliberately identifies people by display name only (see
``AIUserMessageFormatter`` in ``message_history``): a real ``@username`` is never put in front of
the model. The cost is that when the model writes ``@John Smith`` Telegram renders it as inert
text instead of a mention.

This module closes that gap strictly on the way out. It reads the recent-message cache to learn
which users are actually part of the conversation, resolves their usernames from the database, and
rewrites only the mentions it can attribute to exactly one of them. Nothing here ever feeds back
into a prompt, so the model still cannot see a username.
"""

from __future__ import annotations

import re
from asyncio import gather
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from re import Match

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.message_history import CHATBOT_CACHE_MESSAGE_LIMIT
from sophie_bot.utils.feature_flags import is_enabled

# A one-character display name matches far too much prose to be worth resolving.
MIN_MENTION_NAME_LENGTH = 2

# Regions of the Markdown output where an ``@`` is never a mention: code (the model quotes command
# lines and JSON), link destinations, and bare URLs (``.../@handle`` paths, e-mail addresses).
# Order matters: fenced blocks are consumed whole before an inner backtick can start a span.
_PROTECTED_PATTERN = r"```.*?```|~~~.*?~~~|`[^`\n]*`|\]\([^)\n]*\)|https?://\S+"


@dataclass(frozen=True)
class MentionCandidate:
    """One conversation participant: the names the model may have seen, and the real username."""

    display_names: tuple[str, ...]
    username: str


@dataclass(frozen=True)
class MentionIndex:
    """Resolved lookup table for one chat.

    ``usernames_by_name`` maps a normalised display name to the single username it belongs to;
    names shared by several users are absent, so an ambiguous mention is left untouched.
    """

    usernames_by_name: Mapping[str, str]
    known_usernames: frozenset[str]
    pattern: re.Pattern[str] | None


def _normalize_name(name: str) -> str:
    """Fold a display name to its lookup key: case-insensitive, whitespace-insensitive."""
    return " ".join(name.split()).casefold()


def _is_resolvable_name(name: str) -> bool:
    return len(name) >= MIN_MENTION_NAME_LENGTH and any(char.isalnum() for char in name)


def _name_pattern(name: str) -> str:
    """Match a display name tolerantly: the model rarely reproduces spacing exactly."""
    return r"\s+".join(re.escape(token) for token in name.split())


def build_mention_index(candidates: Iterable[MentionCandidate]) -> MentionIndex:
    """Turn participants into a lookup table, dropping every name that is not unambiguous."""
    usernames_by_name: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    known_usernames: set[str] = set()

    for candidate in candidates:
        username = candidate.username.lstrip("@")
        if not username:
            continue
        known_usernames.add(username.casefold())
        for display_name in candidate.display_names:
            normalized_name = _normalize_name(display_name)
            if not _is_resolvable_name(normalized_name):
                continue
            existing_username = usernames_by_name.get(normalized_name)
            if existing_username is not None and existing_username.casefold() != username.casefold():
                ambiguous_names.add(normalized_name)
                continue
            usernames_by_name[normalized_name] = username

    resolved_names = {name: username for name, username in usernames_by_name.items() if name not in ambiguous_names}
    return MentionIndex(
        usernames_by_name=resolved_names,
        known_usernames=frozenset(known_usernames),
        pattern=_build_pattern(resolved_names),
    )


def _build_pattern(resolved_names: Mapping[str, str]) -> re.Pattern[str] | None:
    if not resolved_names:
        return None
    # Longest first so "@John Smith" wins over the "@John" prefix of the same alternation.
    ordered_names = sorted(resolved_names, key=len, reverse=True)
    names_pattern = "|".join(_name_pattern(name) for name in ordered_names)
    return re.compile(
        rf"(?P<protected>{_PROTECTED_PATTERN})|(?<![\w@/])@(?P<name>{names_pattern})(?!\w)",
        re.DOTALL | re.IGNORECASE,
    )


def resolve_mentions(text: str, index: MentionIndex) -> str:
    """Replace ``@DisplayName`` with ``@username`` wherever exactly one user matches.

    Pure and total: anything that is not a confident match — an unknown name, a name shared by two
    users, a mention that is already someone's real username, an ``@`` inside code or a URL — comes
    back byte-for-byte unchanged.
    """
    if index.pattern is None or "@" not in text:
        return text

    def replace(match: Match[str]) -> str:
        if match.group("protected") is not None:
            return match.group(0)

        matched_name = match.group("name")
        normalized_name = _normalize_name(matched_name)
        # A single-token mention that already is a real username is left alone: it resolves in
        # Telegram as-is, and rewriting it could point at a different person entirely.
        if " " not in normalized_name and normalized_name in index.known_usernames:
            return match.group(0)

        username = index.usernames_by_name.get(normalized_name)
        if username is None:
            return match.group(0)
        return f"@{username}"

    return index.pattern.sub(replace, text)


def _display_names(user: ChatModel) -> tuple[str, ...]:
    """The names the model could have been shown for this user, longest form first."""
    first_name = user.first_name_or_title
    if not user.last_name:
        return (first_name,)
    return (f"{first_name} {user.last_name}", first_name)


def _candidate_from_user(user: ChatModel | None) -> MentionCandidate | None:
    if user is None or not user.username:
        return None
    return MentionCandidate(display_names=_display_names(user), username=user.username)


def _recent_user_tids(user_tids: Sequence[int]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(user_tid for user_tid in user_tids if user_tid != CONFIG.bot_id))


async def collect_mention_candidates(chat_tid: int) -> tuple[MentionCandidate, ...]:
    """Participants of the window the model was given context for.

    The recent-message cache is the right source here: it is exactly the set of people the model
    could plausibly be talking about, and it keeps the lookup bounded regardless of chat size.
    """
    messages = await get_cached_messages(chat_tid, limit=CHATBOT_CACHE_MESSAGE_LIMIT)
    user_tids = _recent_user_tids([message.user_id for message in messages])
    if not user_tids:
        return ()

    users = await gather(*(ChatModel.get_by_tid(user_tid) for user_tid in user_tids))
    return tuple(candidate for user in users if (candidate := _candidate_from_user(user)))


async def apply_mention_usernames(text: str, chat_tid: int | None) -> str:
    """Flag-gated entry point used by the reply renderer, for both streamed and final output."""
    if not text or "@" not in text or chat_tid is None:
        return text
    if not await is_enabled("ai_chatbot_mention_usernames", chat_tid=chat_tid):
        return text

    candidates = await collect_mention_candidates(chat_tid)
    if not candidates:
        return text
    return resolve_mentions(text, build_mention_index(candidates))
