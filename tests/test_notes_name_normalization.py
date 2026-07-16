"""Regression tests for case-insensitive note names (finding #12).

Note names were lowercased on save but passed raw to `get_by_notenames`, which does an exact
`In(NoteModel.names, ...)` match. `/save Rules` stored "rules", so `/get Rules`, `#Rules` and
`/delnote Rules` all missed the note: reachable only in all-lowercase, undeletable by the name the
user typed. The REST create path stored names unnormalized, so an uppercase API-created name could
never be hit by `#name` or the AI tool.

Both directions are now closed in `sophie_bot/db/models/notes.py`: a field validator normalizes on
write and `get_by_notenames` normalizes the query on read.

These tests deliberately avoid executing `get_by_notenames` against the DB: `get_by_notenames`
filters on `NoteModel.chat.id`, which renders `{"chat.$id": ...}`, and mongomock does not match
dotted paths into a DBRef -- it returns None regardless of the note names, so a DB-backed test here
cannot tell a normalized query from an unnormalized one. The query the method builds is asserted
instead.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.notes import NoteModel, normalize_notenames


def test_normalize_notenames_lowercases() -> None:
    assert normalize_notenames(("Rules", "FAQ", "already", "MiXeD")) == ("rules", "faq", "already", "mixed")


def test_normalize_notenames_is_a_noop_on_lowercase_input() -> None:
    assert normalize_notenames(("rules", "faq")) == ("rules", "faq")


@pytest.mark.usefixtures("db_init")
@pytest.mark.parametrize(
    ("stored_names", "expected"),
    [
        (("Rules",), ("rules",)),
        (("RULES", "FAQ"), ("rules", "faq")),
        (("rules",), ("rules",)),
    ],
)
def test_note_names_are_normalized_on_construction(stored_names: tuple[str, ...], expected: tuple[str, ...]) -> None:
    """The write path (bot save, REST create, AI tool) can no longer persist an unreachable name."""
    note = NoteModel.model_validate(
        {"chat_id": -1001, "chat": {"id": PydanticObjectId(), "collection": "chats"}, "names": stored_names}
    )

    assert note.names == expected


@pytest.mark.usefixtures("db_init")
@pytest.mark.asyncio
@pytest.mark.parametrize("queried_name", ["Rules", "RULES", "rUlEs", "rules"])
async def test_get_by_notenames_queries_lowercased_names(queried_name: str) -> None:
    """The read path normalizes, so any casing the user types matches the stored note."""
    find_one_mock = AsyncMock(return_value=None)
    with patch.object(NoteModel, "find_one", find_one_mock):
        await NoteModel.get_by_notenames(PydanticObjectId(), (queried_name,))

    names_operator = find_one_mock.call_args.args[1]
    assert names_operator.query == {"names": {"$in": ("rules",)}}, (
        f"A note stored as 'rules' must be queried for when the user types {queried_name!r}"
    )


@pytest.mark.usefixtures("db_init")
@pytest.mark.asyncio
async def test_get_by_notenames_still_scopes_to_the_chat() -> None:
    """Normalizing the names must not disturb the chat scoping."""
    chat_iid = PydanticObjectId()
    find_one_mock = AsyncMock(return_value=None)
    with patch.object(NoteModel, "find_one", find_one_mock):
        await NoteModel.get_by_notenames(chat_iid, ("Rules",))

    # Beanie rewrites the `_id` key to `$id` when it builds the final query; this is the
    # pre-rewrite form that `find_one` receives.
    chat_operator = find_one_mock.call_args.args[0]
    assert chat_operator == {"chat._id": chat_iid}
