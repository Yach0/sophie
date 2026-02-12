"""Migration: convert_privatenotes_chat_id_to_link

Description:
    Converts PrivateNotesModel chat_id (int) to chat (Link[ChatModel]).

Affected Collections:
    - privatenotes

Impact:
    - Medium risk: Data migration of primary key reference
    - Small collection: Private notes are usually not very large
"""

from beanie import free_fall_migration
from pymongo.errors import OperationFailure

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.privatenotes import PrivateNotesModel
from sophie_bot.utils.logger import log


class Forward:
    """Convert chat_id to chat Link."""

    @free_fall_migration(document_models=[PrivateNotesModel])
    async def migrate(self, session):
        collection = PrivateNotesModel.get_pymongo_collection()

        # Drop unique index on chat_id if it exists, as unsetting it for multiple documents
        # will cause DuplicateKeyError (multiple null values)
        try:
            await collection.drop_index("chat_id_1")
        except OperationFailure:
            pass

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
                        "Deleting orphaned privatenotes record without corresponding chat",
                        chat_id=chat_id,
                        doc_id=doc["_id"],
                    )
                    await collection.delete_one({"_id": doc["_id"]}, session=session)


class Backward:
    """Convert chat Link back to chat_id."""

    @free_fall_migration(document_models=[PrivateNotesModel])
    async def rollback(self, session):
        collection = PrivateNotesModel.get_pymongo_collection()
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
