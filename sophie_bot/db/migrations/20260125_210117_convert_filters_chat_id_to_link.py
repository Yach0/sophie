"""Migration: convert_filters_chat_id_to_link

Description:
    Converts FiltersModel chat_id (int) to chat (Link[ChatModel]).

Affected Collections:
    - filters

Impact:
    - Medium risk: Data migration of primary key reference
    - Large collection: Filters can be numerous
"""

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.utils.logger import log


class Forward:
    """Convert chat_id to chat Link."""

    @free_fall_migration(document_models=[FiltersModel])
    async def migrate(self, session):
        collection = FiltersModel.get_pymongo_collection()
        async for doc in collection.find():
            if "chat_id" in doc:
                chat_id = doc["chat_id"]
                chat = await ChatModel.find_one(ChatModel.tid == chat_id)
                if chat:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"chat": chat.id}, "$unset": {"chat_id": ""}},
                        session=session,
                    )
                else:
                    log.warning(
                        "Deleting orphaned filters record without corresponding chat",
                        chat_id=chat_id,
                        doc_id=doc["_id"],
                    )
                    await collection.delete_one({"_id": doc["_id"]}, session=session)


class Backward:
    """Convert chat Link back to chat_id."""

    @free_fall_migration(document_models=[FiltersModel])
    async def rollback(self, session):
        collection = FiltersModel.get_pymongo_collection()
        async for doc in collection.find():
            if "chat" in doc:
                chat_iid = doc["chat"]
                chat = await ChatModel.find_one(ChatModel.iid == chat_iid)
                if chat:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"chat_id": chat.tid}, "$unset": {"chat": ""}},
                        session=session,
                    )
