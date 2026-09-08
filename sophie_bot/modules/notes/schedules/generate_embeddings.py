from __future__ import annotations

from sophie_bot.db.models import ChatModel, NoteModel
from sophie_bot.modules.ai.utils.ai_mode import resolve_chat_capabilities
from sophie_bot.modules.notes.utils.semantic_search import update_note_embedding
from sophie_bot.modules.utils_.scheduler.chat_language import UseChatLanguage
from sophie_bot.modules.utils_.scheduler.for_chats import ForChats
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


class GenerateNoteEmbeddings:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def process_chat(self, chat: ChatModel) -> None:
        chat_notes = NoteModel.find(NoteModel.chat.id == chat.iid)
        async for note in chat_notes:  # skipcq: PYL-E1133
            updated = await update_note_embedding(note)
            if updated:
                log.debug("notes_rag: updated note embedding", chat=chat.tid, note=note.names)

    async def handle(self) -> None:
        if not await is_enabled("notes_rag_embeddings", redis=self.services.redis):
            return

        async for chat in ForChats():
            if not await is_enabled(
                "notes_rag_embeddings",
                chat_tid=chat.tid,
                redis=self.services.redis,
            ):
                continue
            if not (await resolve_chat_capabilities(chat)).ai_enabled:
                log.debug("notes_rag: AI features are not enabled, skipping", chat=chat.tid)
                continue
            async with UseChatLanguage(chat.id, locales=self.services.locales):
                await self.process_chat(chat)
