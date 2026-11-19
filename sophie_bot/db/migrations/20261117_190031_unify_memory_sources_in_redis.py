"""Move original memory messages into the existing 48-hour Redis history.

Affected collection: ai_evidence_memory. Facts, opt-outs, and generation fences
remain in MongoDB. Copy unexpired sources before unsetting state.sources; no
MongoDB backup retains original text. Stop old bot/scheduler versions during the
migration. Reruns are safe. Rollback reconstructs only still-retained sources:
expired text is intentionally unrecoverable under the new 48-hour policy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from beanie import free_fall_migration
from bson import DBRef
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.db.models.ai.ai_evidence_memory import MemorySource, StoredMemoryState
from sophie_bot.modules.ai.utils.cache_messages import MESSAGE_CACHE_TTL, get_context_epoch_key, store_cached_message
from sophie_bot.modules.ai.utils.evidence_memory import cached_sources, source_message
from sophie_bot.services.migrations import MigrationResources


class Forward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_evidence_memory"]
        cutoff = datetime.now(UTC) - MESSAGE_CACHE_TTL
        async for document in collection.find({"state.sources": {"$exists": True}}, session=session):
            reference = document["chat"]
            if not isinstance(reference, DBRef):
                raise TypeError("Memory chat must be a DBRef")
            chat = await resources.database.database["chats"].find_one({"_id": reference.id}, session=session)
            raw_sources = document["state"]["sources"]
            stored = StoredMemoryState.model_validate(document["state"])
            processed = set(stored.processed_ids)
            attempts = dict(stored.writer_attempts)
            if chat is not None:
                chat_tid = chat["chat_id"]
                epoch = int(await resources.redis.get(get_context_epoch_key(chat_tid)) or 0)
                for raw_source in raw_sources:
                    source = MemorySource.model_validate(raw_source)
                    if source.sent_at < cutoff or source.speaker_tid in stored.excluded_users:
                        continue
                    saved = await store_cached_message(
                        source_message(source),
                        chat_tid,
                        redis=resources.redis,
                        expected_epoch=epoch,
                        preserve_existing=True,
                    )
                    if not saved:
                        raise ValueError("Chat history reset during memory migration; retry with services stopped")
                    if source.processed:
                        processed.add(source.message_id)
                    if source.writer_attempts:
                        attempts[str(source.message_id)] = source.writer_attempts
            await collection.update_one(
                {"_id": document["_id"]},
                {
                    "$unset": {"state.sources": ""},
                    "$set": {
                        "state.processed_ids": sorted(processed),
                        "state.writer_attempts": attempts,
                    },
                },
                session=session,
            )


class Backward:
    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        collection = resources.database.database["ai_evidence_memory"]
        async for document in collection.find({"state.sources": {"$exists": False}}, session=session):
            reference = document["chat"]
            if not isinstance(reference, DBRef):
                raise TypeError("Memory chat must be a DBRef")
            chat = await resources.database.database["chats"].find_one({"_id": reference.id}, session=session)
            state = StoredMemoryState.model_validate(document["state"])
            sources = await cached_sources(chat["chat_id"], state, redis=resources.redis) if chat else []
            await collection.update_one(
                {"_id": document["_id"]},
                {
                    "$set": {"state.sources": [source.model_dump() for source in sources]},
                    "$unset": {"state.processed_ids": "", "state.writer_attempts": ""},
                },
                session=session,
            )
