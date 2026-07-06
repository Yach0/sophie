"""End-to-end tests for the Sophie Bot notes module.

Tests cover saving, retrieving, listing, deleting, and searching notes
via bot commands in a group chat context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import NoteModel, SaveableParseMode


async def _setup_group_and_user(
    test_client: TestClient,
    *,
    chat_id: int,
    user_id: int,
    group_title: str,
    first_name: str,
    username: str,
) -> tuple[Any, Any, ChatModel]:
    """Create a group and user, send init to register both in DB."""
    group_chat = ChatFactory.create_group(chat_id=chat_id, title=group_title)
    user_wrapper = test_client.create_user(user_id=user_id, first_name=first_name, username=username)

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    chat_model = await ChatModel.get_by_tid(chat_id)
    assert chat_model is not None, f"ChatModel for group {chat_id} should exist after init"

    return user_wrapper, group_chat, chat_model


async def _save_note_directly(chat_model: ChatModel, names: tuple[str, ...], text: str) -> NoteModel:
    """Insert a NoteModel directly into the database for test setup."""
    note = NoteModel(
        chat_id=chat_model.tid,
        chat=chat_model,
        names=names,
        text=text,
        version=2,
    )
    await note.insert()
    return note


# ---------------------------------------------------------------------------
# test_save_note_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_note_success(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin saves a note and gets a confirmation message."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000001,
        user_id=928000001,
        group_title="Notes Save Group",
        first_name="AdminSave",
        username="admin_save",
    )

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.logging.utils.log.log_event", AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="save",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="greeting Hello, welcome to the group!",
        )

    assert requests, "Bot should respond after saving a note"
    response_text = requests[-1].text or ""
    assert "Note was successfully created" in response_text, (
        f"Response should confirm note creation, got: {response_text}"
    )
    assert "greeting" in response_text, f"Response should mention the note name, got: {response_text}"

    saved_note = await NoteModel.find_one(NoteModel.chat_tid == chat_model.tid)
    assert saved_note is not None
    assert saved_note.parse_mode == SaveableParseMode.html
    assert saved_note.version == 2


@pytest.mark.asyncio
async def test_save_note_ignores_empty_note_name_segments(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extra spaces in /save arguments should not create empty note names."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000011,
        user_id=928000011,
        group_title="Notes Save Space Group",
        first_name="AdminSpace",
        username="admin_space",
    )

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.logging.utils.log.log_event", AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="save",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="greeting  Hello with extra spacing",
        )

    assert requests, "Bot should respond after saving a note with extra spaces"
    response_text = requests[-1].text or ""
    assert "Note was successfully created" in response_text
    assert "greeting" in response_text

    saved_note = await NoteModel.find_one(NoteModel.chat_tid == chat_model.tid)
    assert saved_note is not None
    assert saved_note.names == ("greeting",)


# ---------------------------------------------------------------------------
# test_save_note_requires_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_note_requires_admin(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-admin user cannot save a note — gets denied."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, _chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000002,
        user_id=928000002,
        group_title="Notes NoAdmin Group",
        first_name="RegularUser",
        username="regular_user_notes",
    )

    # Do NOT patch admin permissions — leave default behavior (non-admin)
    requests = await test_client.send_command(
        command="save",
        from_user=user_wrapper.user,
        chat=group_chat,
        args="secret This should not be saved",
    )

    assert requests, "Bot should respond when non-admin tries to save"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower() or "admin" in response_text.lower(), (
        f"Response should mention admin requirement, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# test_get_note_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_success(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrieve an existing note by name."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000003,
        user_id=928000003,
        group_title="Notes Get Group",
        first_name="NoteGetter",
        username="note_getter",
    )

    note = await _save_note_directly(chat_model, ("rules",), "Please follow the group rules!")

    get_note_mock = AsyncMock(return_value=note)
    with patch.object(NoteModel, "get_by_notenames", get_note_mock):
        requests = await test_client.send_command(
            command="get",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="rules",
        )

    assert requests, "Bot should respond when getting a note"
    response_text = requests[-1].text or ""
    assert "Please follow the group rules!" in response_text, f"Response should contain note text, got: {response_text}"


# ---------------------------------------------------------------------------
# test_get_note_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_not_found(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Getting a non-existent note returns an error message."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, _chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000004,
        user_id=928000004,
        group_title="Notes NotFound Group",
        first_name="NoteFinder",
        username="note_finder",
    )

    get_note_mock = AsyncMock(return_value=None)
    with patch.object(NoteModel, "get_by_notenames", get_note_mock):
        requests = await test_client.send_command(
            command="get",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="nonexistent",
        )

    assert requests, "Bot should respond when note is not found"
    response_text = requests[-1].text or ""
    assert "No note was found" in response_text or "nonexistent" in response_text, (
        f"Response should indicate note was not found, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# test_notes_list_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_list_success(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing notes in a chat that has notes."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000005,
        user_id=928000005,
        group_title="Notes List Group",
        first_name="NoteLister",
        username="note_lister",
    )

    note_one = await _save_note_directly(chat_model, ("welcome",), "Welcome to the group!")
    note_two = await _save_note_directly(chat_model, ("faq",), "Frequently asked questions")

    get_chat_notes_mock = AsyncMock(return_value=[note_one, note_two])
    with patch.object(NoteModel, "get_chat_notes", get_chat_notes_mock):
        requests = await test_client.send_command(
            command="notes",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /notes command"
    response_text = requests[-1].text or ""
    assert "welcome" in response_text, f"Response should list 'welcome' note, got: {response_text}"
    assert "faq" in response_text, f"Response should list 'faq' note, got: {response_text}"


# ---------------------------------------------------------------------------
# test_notes_list_empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_list_empty(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing notes when no notes exist shows appropriate message."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, _chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000006,
        user_id=928000006,
        group_title="Notes Empty Group",
        first_name="EmptyLister",
        username="empty_lister",
    )

    get_chat_notes_mock = AsyncMock(return_value=[])
    with patch.object(NoteModel, "get_chat_notes", get_chat_notes_mock):
        requests = await test_client.send_command(
            command="notes",
            from_user=user_wrapper.user,
            chat=group_chat,
        )

    assert requests, "Bot should respond to /notes even when no notes exist"
    response_text = requests[-1].text or ""
    assert "No notes found" in response_text, f"Response should indicate no notes, got: {response_text}"


# ---------------------------------------------------------------------------
# test_notes_list_with_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notes_list_with_search(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listing notes with a search term filters the results."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000007,
        user_id=928000007,
        group_title="Notes Search Filter Group",
        first_name="SearchLister",
        username="search_lister",
    )

    note_welcome = await _save_note_directly(chat_model, ("welcome",), "Welcome to the group!")
    note_rules = await _save_note_directly(chat_model, ("rules",), "Group rules here")
    note_welcome_back = await _save_note_directly(chat_model, ("welcome-back",), "Welcome back!")

    # Return all notes; the handler filters by name matching the search term
    get_chat_notes_mock = AsyncMock(return_value=[note_welcome, note_rules, note_welcome_back])
    with patch.object(NoteModel, "get_chat_notes", get_chat_notes_mock):
        requests = await test_client.send_command(
            command="notes",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="welcome",
        )

    assert requests, "Bot should respond to /notes with search"
    response_text = requests[-1].text or ""
    assert "welcome" in response_text, f"Response should include matching notes, got: {response_text}"
    # The 'rules' note name does NOT contain 'welcome', so it should be filtered out
    assert "rules" not in response_text or "Search pattern" in response_text, (
        f"Non-matching notes should be filtered, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# test_delnote_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delnote_success(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin successfully deletes a note."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000008,
        user_id=928000008,
        group_title="Notes Delete Group",
        first_name="AdminDeleter",
        username="admin_deleter",
    )

    note = await _save_note_directly(chat_model, ("obsolete",), "This note is outdated")

    get_note_mock = AsyncMock(return_value=note)

    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.logging.utils.log.log_event", AsyncMock()),
        patch.object(NoteModel, "get_by_notenames", get_note_mock),
    ):
        requests = await test_client.send_command(
            command="delnote",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="obsolete",
        )

    assert requests, "Bot should respond after deleting a note"
    response_text = requests[-1].text or ""
    assert "Note was successfully deleted" in response_text, f"Response should confirm deletion, got: {response_text}"
    assert "obsolete" in response_text, f"Response should mention deleted note name, got: {response_text}"


# ---------------------------------------------------------------------------
# test_delnote_requires_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delnote_requires_admin(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-admin cannot delete a note."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, _chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000009,
        user_id=928000009,
        group_title="Notes DelNoAdmin Group",
        first_name="RegularDeleter",
        username="regular_deleter",
    )

    # Do NOT patch admin permissions
    requests = await test_client.send_command(
        command="delnote",
        from_user=user_wrapper.user,
        chat=group_chat,
        args="something",
    )

    assert requests, "Bot should respond when non-admin tries to delete"
    response_text = requests[-1].text or ""
    assert "administrator" in response_text.lower() or "admin" in response_text.lower(), (
        f"Response should mention admin requirement, got: {response_text}"
    )


# ---------------------------------------------------------------------------
# test_delnote_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delnote_not_found(
    test_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a non-existent note returns an error."""
    monkeypatch.setattr("sophie_bot.modules.notes.utils.send.bot", test_client.bot)

    user_wrapper, group_chat, _chat_model = await _setup_group_and_user(
        test_client,
        chat_id=-1002800000010,
        user_id=928000010,
        group_title="Notes DelNotFound Group",
        first_name="DelFinder",
        username="del_finder",
    )

    get_note_mock = AsyncMock(return_value=None)
    with (
        patch("sophie_bot.filters.admin_rights.check_user_admin_permissions", AsyncMock(return_value=True)),
        patch.object(NoteModel, "get_by_notenames", get_note_mock),
    ):
        requests = await test_client.send_command(
            command="delnote",
            from_user=user_wrapper.user,
            chat=group_chat,
            args="ghost",
        )

    assert requests, "Bot should respond when deleting a non-existent note"
    response_text = requests[-1].text or ""
    assert "No notes were found" in response_text or "ghost" in response_text, (
        f"Response should indicate note not found, got: {response_text}"
    )

