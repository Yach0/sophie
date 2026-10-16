from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

import pytest
from aiogram.types import Chat, User

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.db.models.privatenotes import PrivateNotesModel


@pytest.fixture
async def identity_chat(db_init: object) -> AsyncIterator[ChatModel]:
    chat = ChatModel.get_user_model(User(id=99100111, is_bot=False, first_name="Identity"))
    try:
        yield chat
    finally:
        await GreetingsModel.find(GreetingsModel.chat.id == chat.id).delete()
        await PrivateNotesModel.find(PrivateNotesModel.chat.id == chat.id).delete()
        await ChatModel.find(ChatModel.id == chat.id).delete()


@pytest.mark.parametrize("operation", ["insert", "save", "upsert"])
async def test_new_chat_links_resolve_without_reloading_identity(
    identity_chat: ChatModel, operation: Literal["insert", "save", "upsert"]
) -> None:
    if operation == "upsert":
        chat = await ChatModel.upsert_user(User(id=identity_chat.tid, is_bot=False, first_name="Identity"))
        identity_chat.id = chat.id
    else:
        chat = identity_chat
        if operation == "insert":
            await chat.insert()
        else:
            await chat.save()

    await GreetingsModel(chat=chat).insert()
    greeting = await GreetingsModel.find_one(GreetingsModel.chat.id == chat.id)
    assert greeting is not None
    linked_chat = await greeting.chat.fetch()
    assert isinstance(linked_chat, ChatModel)
    assert linked_chat.id == chat.id
    assert linked_chat.tid == chat.tid
    loaded = await ChatModel.get(chat.id)
    assert loaded is not None
    assert loaded.id == chat.id


async def test_group_upsert_preserves_identity_on_update(db_init: object) -> None:
    group = await ChatModel.upsert_group(Chat(id=-99100111, type="supergroup", title="Before"))
    try:
        updated_group = await ChatModel.upsert_group(Chat(id=group.tid, type="supergroup", title="After"))
        assert updated_group.id == group.id
        assert updated_group.first_name_or_title == "After"
    finally:
        await group.delete()


async def test_private_notes_state_changes_are_idempotent(identity_chat: ChatModel) -> None:
    await identity_chat.insert()
    for enabled in (False, False, True, True, False, False):
        await PrivateNotesModel.set_state(identity_chat.id, enabled)
        assert await PrivateNotesModel.get_state(identity_chat.id) is enabled
        assert await PrivateNotesModel.find(PrivateNotesModel.chat.id == identity_chat.id).count() == int(enabled)
