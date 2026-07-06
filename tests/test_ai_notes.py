from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sophie_bot.modules.ai.agent_tools import notes as notes_module
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext


class _FakeNote:
    last_get_by_notenames: tuple[object, tuple[str, ...]] | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.names = kwargs["names"]

    @staticmethod
    async def get_by_notenames(chat_iid: object, notenames: tuple[str, ...]) -> None:
        _FakeNote.last_get_by_notenames = (chat_iid, notenames)
        return None

    async def insert(self) -> None:
        return None


@pytest.mark.asyncio
async def test_ai_save_note_splits_space_separated_notenames(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_model = SimpleNamespace(iid="chat-iid", tid=-1003000000001)
    deps = SophieAIToolContext(
        connection=SimpleNamespace(db_model=chat_model),
        chat_tid=chat_model.tid,
        chat_iid=chat_model.iid,
        user_tid=930000001,
    )
    ctx = SimpleNamespace(deps=deps)

    monkeypatch.setattr("sophie_bot.modules.utils_.admin.is_user_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(notes_module, "NoteModel", _FakeNote)
    monkeypatch.setattr(notes_module.AIChatNotesFunc, "from_model", staticmethod(lambda note: note))

    with patch.object(notes_module, "ai_markdown_to_html", side_effect=lambda text: text):
        result = await notes_module.save_note(ctx, "first second", "note body")

    assert result.names == ("first", "second")
    assert _FakeNote.last_get_by_notenames == (chat_model.iid, ("first", "second"))
    assert result.kwargs["names"] == ("first", "second")
