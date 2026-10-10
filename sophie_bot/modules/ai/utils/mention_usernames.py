"""Rewrite the display-name mentions a model wrote into real Telegram usernames.

Legacy replies repair display-name mentions using recent conversation participants. Modern
entertainment replies repair supplied first-name mentions using their session-speaker snapshot.
Known unresolved names render without ``@``; unrelated authored mentions remain untouched.

The repair happens strictly on the way out. No real usernames are ever fed back into a prompt;
code, link destinations, and URLs retain their literal text.
"""

from __future__ import annotations

import re
from asyncio import gather
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from re import Match
from types import MappingProxyType

from redis.asyncio import Redis

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.old_context import CHATBOT_CACHE_MESSAGE_LIMIT

# A one-character display name matches far too much prose to be worth resolving.
MIN_MENTION_NAME_LENGTH = 2

# Regions of the Markdown output where an ``@`` is never a mention: code (the model quotes command
# lines and JSON), link destinations, and bare URLs (``.../@handle`` paths, e-mail addresses).
# Order matters: fenced blocks are consumed whole before an inner backtick can start a span.
_PROTECTED_PATTERN = r"```.*?```|~~~.*?~~~|`[^`\n]*`|\]\([^)\n]*\)|https?://\S+"


class MentionPolicy(Enum):
    LEGACY_DISPLAY_NAMES = "legacy_display_names"
    MODERN_FIRST_NAMES = "modern_first_names"
    OPAQUE = "opaque"


@dataclass(frozen=True)
class MentionCandidate:
    """One conversation participant: the names the model may have seen, and the real username."""

    display_names: tuple[str, ...]
    username: str


@dataclass(frozen=True)
class MentionIndex:
    """Resolved lookup table for one chat.

    ``usernames_by_name`` maps a normalised supplied name to its unambiguous username.
    Only known unresolved first names render as inert plain names in modern entertainment.
    """

    usernames_by_name: Mapping[str, str]
    known_usernames: frozenset[str]
    pattern: re.Pattern[str] | None
    policy: MentionPolicy = MentionPolicy.LEGACY_DISPLAY_NAMES


def _normalize_name(name: str) -> str:
    """Fold a display name to its lookup key: case-insensitive, whitespace-insensitive."""
    return " ".join(name.split()).casefold()


def _is_resolvable_name(name: str) -> bool:
    return len(name) >= MIN_MENTION_NAME_LENGTH and any(char.isalnum() for char in name)


def _name_pattern(name: str) -> str:
    """Match a display name tolerantly: the model rarely reproduces spacing exactly."""
    return r"\s+".join(re.escape(token) for token in name.split())


def build_mention_index(
    candidates: Iterable[MentionCandidate],
    *,
    policy: MentionPolicy = MentionPolicy.LEGACY_DISPLAY_NAMES,
) -> MentionIndex:
    """Turn participants into a lookup table, dropping every name that is not unambiguous."""
    usernames_by_name: dict[str, str] = {}
    ambiguous_names: set[str] = set()
    known_usernames: set[str] = set()
    mention_names: set[str] = set()

    for candidate in candidates:
        username = candidate.username.lstrip("@")
        if username:
            known_usernames.add(username.casefold())
        for display_name in candidate.display_names:
            normalized_name = _normalize_name(display_name)
            if not _is_resolvable_name(normalized_name):
                continue
            mention_names.add(normalized_name)
            if not username:
                # This participant occupies the name but cannot be mentioned. Do not
                # silently redirect their name to somebody else who has a username.
                ambiguous_names.add(normalized_name)
                continue
            existing_username = usernames_by_name.get(normalized_name)
            if existing_username is not None and existing_username.casefold() != username.casefold():
                ambiguous_names.add(normalized_name)
                continue
            usernames_by_name[normalized_name] = username

    resolved_names = {name: username for name, username in usernames_by_name.items() if name not in ambiguous_names}
    return MentionIndex(
        usernames_by_name=MappingProxyType(resolved_names),
        known_usernames=frozenset(known_usernames),
        pattern=_build_pattern(mention_names if policy == MentionPolicy.MODERN_FIRST_NAMES else resolved_names)
        if policy != MentionPolicy.OPAQUE
        else None,
        policy=policy,
    )


def _build_pattern(
    names: Iterable[str],
) -> re.Pattern[str] | None:
    # Include blocked names in modern matching, longest first, so a blocked multiword first
    # name cannot fall through to another participant's shorter, resolvable first name.
    ordered_names = sorted(names, key=len, reverse=True)
    patterns = [_name_pattern(name) for name in ordered_names]
    if not patterns:
        return None
    names_pattern = "|".join(patterns)
    return re.compile(
        rf"(?P<protected>{_PROTECTED_PATTERN})|(?<![\w@/])@(?P<name>{names_pattern})(?!\w)",
        re.DOTALL | re.IGNORECASE,
    )


def resolve_mentions(text: str, index: MentionIndex) -> str:
    """Replace ``@DisplayName`` with ``@username`` wherever exactly one user matches.

    Known unresolved modern first names lose their ``@`` to avoid selecting another actor.
    Unknown authored mentions, legacy unresolved mentions, and protected code/URLs stay literal.
    """
    if index.pattern is None or "@" not in text:
        return text

    def replace(match: Match[str]) -> str:
        if match.group("protected") is not None:
            return match.group(0)

        matched_name = match.group("name")
        normalized_name = _normalize_name(matched_name)
        # Legacy output may already use a real username. Modern first-name labels instead
        # select their session actor, even when another participant owns that username.
        if (
            index.policy == MentionPolicy.LEGACY_DISPLAY_NAMES
            and " " not in normalized_name
            and normalized_name in index.known_usernames
        ):
            return match.group(0)

        username = index.usernames_by_name.get(normalized_name)
        if username is None:
            return matched_name if index.policy == MentionPolicy.MODERN_FIRST_NAMES else match.group(0)
        return f"@{username}"

    return index.pattern.sub(replace, text)


def _display_names(user: ChatModel) -> tuple[str, ...]:
    """The names the model could have been shown for this user, longest form first."""
    first_name = user.first_name_or_title
    if not user.last_name:
        return (first_name,)
    return f"{first_name} {user.last_name}", first_name


def _candidate_from_user(user: ChatModel | None) -> MentionCandidate | None:
    if user is None:
        return None
    return MentionCandidate(display_names=_display_names(user), username=user.username or "")


def _recent_user_tids(user_tids: Sequence[int]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(user_tid for user_tid in user_tids if user_tid != CONFIG.bot_id))


async def collect_mention_candidates(chat_tid: int, *, redis: Redis) -> tuple[MentionCandidate, ...]:
    """Participants of the window the model was given context for.

    The recent-message cache is the right source here: it is exactly the set of people the model
    could plausibly be talking about, and it keeps the lookup bounded regardless of chat size.
    """
    messages = await get_cached_messages(
        chat_tid,
        limit=CHATBOT_CACHE_MESSAGE_LIMIT,
        redis=redis,
    )
    user_tids = _recent_user_tids([message.user_id for message in messages])
    if not user_tids:
        return ()

    users = await gather(*(ChatModel.get_by_tid(user_tid) for user_tid in user_tids))
    return tuple(candidate for user in users if (candidate := _candidate_from_user(user)))


async def apply_mention_usernames(
    text: str,
    chat_tid: int | None,
    *,
    redis: Redis,
) -> str:
    """Rewrite confidently resolved mentions in both streamed and final output."""
    if not text or "@" not in text or chat_tid is None:
        return text
    index = await resolve_mention_index(chat_tid, redis=redis)
    if index is None:
        return text
    return resolve_mentions(text, index)


async def resolve_mention_index(chat_tid: int | None, *, redis: Redis) -> MentionIndex | None:
    """Resolve the mention index once for a reply/run."""
    if chat_tid is None:
        return None
    candidates = await collect_mention_candidates(chat_tid, redis=redis)
    return build_mention_index(candidates) if candidates else None
