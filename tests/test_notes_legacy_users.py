"""Regression tests for legacy raw-Telegram-ID note users (SOPHIE-285)."""

from typing import Any

import pytest
from bson import DBRef, ObjectId

from sophie_bot.db.models.notes import NoteModel


# Above 2^31-1, so MongoDB stores it as a BSON `long` rather than an `int`. This is the value from
# the real Sentry event, and the reason the 20260214 `$type: "int"` cleanup never matched it.
LEGACY_INT64_TID = 5126697778
LEGACY_INT32_TID = 12345


def _note_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": ObjectId(),
        "chat": DBRef("chats", ObjectId()),
        "chat_id": -1001,
        "names": ["rules"],
    }
    doc.update(overrides)
    return doc


@pytest.mark.usefixtures("db_init")
@pytest.mark.parametrize("legacy_tid", [LEGACY_INT64_TID, LEGACY_INT32_TID])
def test_legacy_int_user_does_not_break_reads(legacy_tid: int) -> None:
    """Before the validator, this raised `Id must be of type PydanticObjectId` for the whole note."""
    note = NoteModel.model_validate(_note_doc(created_user=legacy_tid, edited_user=legacy_tid))

    # Attribution is not recoverable from the model alone, so it reads as unknown rather than raising.
    assert note.created_user is None
    assert note.edited_user is None


@pytest.mark.usefixtures("db_init")
def test_proper_links_are_preserved() -> None:
    """The coercion must not be over-eager: real links still round-trip."""
    user_oid = ObjectId()

    note = NoteModel.model_validate(_note_doc(created_user=DBRef("chats", user_oid), edited_user=None))

    assert note.created_user.ref.id == user_oid  # type: ignore[union-attr]
    assert note.edited_user is None
