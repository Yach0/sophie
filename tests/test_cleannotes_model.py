"""Model metadata regression tests for CleanNotesModel.

CleanNotesModel is a per-chat singleton, so the chat link must carry a unique
index the same way the other per-chat settings documents do.
"""

from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from sophie_bot.db.models.clean_notes import CleanNotesModel


def _chat_index() -> IndexModel:
    indexes: list[IndexModel] = CleanNotesModel.Settings.indexes
    chat_indexes = [index for index in indexes if index.document["key"] == {"chat.$id": ASCENDING}]
    assert len(chat_indexes) == 1
    return chat_indexes[0]


def test_collection_name() -> None:
    assert CleanNotesModel.Settings.name == "clean_notes"


def test_chat_link_index_is_unique() -> None:
    assert _chat_index().document["unique"] is True
