"""Shared building blocks for e2e tests: ID allocation, chat registration, admin rights."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING, Final

from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message, Update, User
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.utils import feature_flags

if TYPE_CHECKING:
    from aiogram.types import Chat
    from aiogram_test_framework import TestClient
    from aiogram_test_framework.types import CapturedRequest

# Fixed rather than taken from CONFIG.username: Settings declares no env_prefix, so a bare USERNAME
# in the environment lands on it -- and desktop sessions export USERNAME as the login name. Letting
# that reach the mock bot makes every `/command@bot` test pass or fail per developer.
TEST_BOT_USERNAME: Final = "test_bot"

# Allocated from ranges no hand-written test literal uses, so a test that still pins its own
# IDs cannot collide with a generated one.
_user_ids = count(800_000_001)
_group_ids = count(-1_009_000_000_001, -1)
_message_ids = count(500_000)
_update_ids = count(900_000)


def next_user_id() -> int:
    """Allocate a Telegram user ID that no other test in this process will reuse."""
    return next(_user_ids)


def next_group_id() -> int:
    """Allocate a Telegram supergroup ID that no other test in this process will reuse."""
    return next(_group_ids)


def next_message_id() -> int:
    """Allocate a message id unique within this process (also usable as an update id)."""
    return next(_message_ids)


async def create_test_user_and_group(
    test_client: TestClient,
    *,
    user_id: int | None = None,
    first_name: str = "Tester",
    username: str | None = None,
    chat_id: int | None = None,
    group_title: str = "Test Group",
) -> tuple[User, Chat, ChatModel]:
    """Create a user and a group, and persist both by sending one message through the bot.

    IDs default to freshly allocated ones; pass them explicitly only when the test asserts
    on the literal value.
    """
    user_id = next_user_id() if user_id is None else user_id
    chat_id = next_group_id() if chat_id is None else chat_id

    user_wrapper = test_client.create_user(
        user_id=user_id,
        first_name=first_name,
        username=username if username is not None else f"user_{user_id}",
    )
    group = ChatFactory.create_group(chat_id=chat_id, title=group_title)

    # Registration is a side effect of SaveChatsMiddleware; there is no other way in.
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    user_model = await ChatModel.get_by_tid(user_id)
    assert user_model is not None, f"ChatModel for user {user_id} should exist after init message"

    return user_wrapper.user, group, user_model


_ADMIN_RIGHTS = (
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_send_welcome_messages",
    "can_delete_stories",
    "can_pin_messages",
    "can_manage_topics",
)


async def _ensure_chat_model(tid: int, *, first_name: str, is_bot: bool) -> ChatModel:
    chat = await ChatModel.get_by_tid(tid)
    if chat is not None:
        return chat

    await ChatModel.upsert_user(User(id=tid, is_bot=is_bot, first_name=first_name))
    chat = await ChatModel.get_by_tid(tid)
    assert chat is not None
    return chat


async def grant_admin(
    chat_tid: int,
    user_tid: int,
    *,
    creator: bool = False,
    is_anonymous: bool = False,
    custom_title: str | None = None,
    **rights: bool,
) -> ChatAdminModel:
    """Make a user an admin of a chat, the way Sophie itself records it.

    Admin checks read ChatAdminModel rather than calling Telegram, so tests express admin
    rights as state instead of patching `check_user_admin_permissions`. Every right defaults
    to granted; pass `can_restrict_members=False` to withhold one.
    """
    chat = await ChatModel.get_by_tid(chat_tid)
    assert chat is not None, f"Chat {chat_tid} must be registered before granting admin rights"
    user = await _ensure_chat_model(user_tid, first_name=f"User {user_tid}", is_bot=False)

    member_user = User(id=user_tid, is_bot=False, first_name=user.first_name_or_title)
    if creator:
        member: ChatMemberOwner | ChatMemberAdministrator = ChatMemberOwner(
            status=ChatMemberStatus.CREATOR,
            user=member_user,
            is_anonymous=is_anonymous,
            custom_title=custom_title,
        )
    else:
        unknown = set(rights) - set(_ADMIN_RIGHTS)
        assert not unknown, f"Unknown administrator rights: {sorted(unknown)}"
        member = ChatMemberAdministrator(
            status=ChatMemberStatus.ADMINISTRATOR,
            user=member_user,
            can_be_edited=False,
            is_anonymous=is_anonymous,
            custom_title=custom_title,
            **{right: rights.get(right, True) for right in _ADMIN_RIGHTS},
        )

    return await ChatAdminModel.upsert_admin(chat.iid, user.iid, member)


async def grant_bot_admin(chat_tid: int, **rights: bool) -> ChatAdminModel:
    """Give Sophie herself admin rights in a chat, for handlers behind BotHasPermissions."""
    await _ensure_chat_model(CONFIG.bot_id, first_name="Sophie", is_bot=True)
    return await grant_admin(chat_tid, CONFIG.bot_id, **rights)


async def get_wizard_session_id(test_client: TestClient, chat_tid: int, user_tid: int) -> str:
    state = test_client.dispatcher.fsm.get_context(
        bot=test_client.bot,
        chat_id=chat_tid,
        user_id=user_tid,
    )
    data = await state.get_data()
    wizard = data.get("wizard")
    assert isinstance(wizard, dict)
    session_id = wizard.get("session_id")
    assert isinstance(session_id, str) and session_id
    return session_id


async def _feed(test_client: TestClient, message: Message) -> list[CapturedRequest]:
    """Feed one message update through the dispatcher and return the requests it produced."""
    start = len(test_client.capture)
    await test_client.dispatcher.feed_update(
        bot=test_client.bot,
        update=Update(update_id=next(_update_ids), message=message),
    )
    return test_client.capture.all_requests[start:]


async def join_group(
    test_client: TestClient,
    group: Chat,
    *members: User,
    added_by: User | None = None,
    date: datetime | None = None,
) -> list[CapturedRequest]:
    """Simulate members joining `group`, driving SaveChatsMiddleware and NewUserMiddleware.

    `added_by` is the service-message sender (whoever added them); it defaults to a fresh
    non-admin so the "an admin added the user" branch stays opt-in. Pass an admin (see
    `grant_admin`) to exercise it. `date` lets a test make the join look old for the
    stale-join branch.
    """
    adder = added_by or User(id=next_user_id(), is_bot=False, first_name="Adder")
    message = Message(
        message_id=next(_message_ids),
        date=date or datetime.now(UTC),
        chat=group,
        from_user=adder,
        new_chat_members=list(members),
    )
    return await _feed(test_client, message)


async def send_reply_command(
    test_client: TestClient,
    *,
    command: str,
    from_user: User,
    group: Chat,
    replied: Message,
    args: str | None = None,
) -> list[CapturedRequest]:
    """Feed a `/command` that replies to `replied` (send_command can't attach a reply)."""
    from aiogram_test_framework.factories import MessageFactory

    text = f"/{command} {args}" if args else f"/{command}"
    message = MessageFactory.create(text=text, from_user=from_user, chat=group, reply_to_message=replied)
    return await _feed(test_client, message)


async def leave_group(test_client: TestClient, group: Chat, member: User) -> list[CapturedRequest]:
    """Simulate `member` leaving `group`, driving LeaveUserMiddleware."""
    message = Message(
        message_id=next(_message_ids),
        date=datetime.now(UTC),
        chat=group,
        from_user=member,
        left_chat_member=member,
    )
    return await _feed(test_client, message)


async def set_feature(feature: str, enabled: bool, *, chat_tid: int | None = None) -> None:
    """Persist a real feature-flag override, instead of patching `is_enabled` at call sites.

    A global override when `chat_tid` is None, otherwise a per-chat one. Both go through the
    production setters so the Redis cache stays consistent. Cleared automatically between tests:
    FeatureFlagOverride is a registered Beanie model that the autouse `clean_db` truncates, and
    the cache lives on `aredis`, which `reset_redis` flushes.
    """
    if chat_tid is None:
        await feature_flags.set_value(feature, enabled)
    else:
        await feature_flags.set_chat_override(feature, chat_tid, enabled)
