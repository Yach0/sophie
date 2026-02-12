"""Migration: batch1_convert_to_links

Description:
    Converts several models from chat_id (int) to chat (Link[ChatModel]).
    Models: LanguageModel, DisablingModel, GreetingsModel, RulesModel, BetaModeModel, ChatConnectionSettingsModel, WarnSettingsModel.

Affected Collections:
    - lang
    - disabled
    - greetings
    - rules
    - beta_mode
    - chat_connection_settings
    - warn_settings
"""

from bson import DBRef
from pymongo.errors import OperationFailure

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.db.models.beta import BetaModeModel
from sophie_bot.db.models.chat_connection_settings import ChatConnectionSettingsModel
from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.utils.logger import log


async def migrate_model(collection, session, field_name="chat_id", index_to_drop=None):
    if index_to_drop:
        try:
            await collection.drop_index(index_to_drop)
        except OperationFailure:
            pass

    async for doc in collection.find():
        if field_name in doc:
            chat_id = doc[field_name]
            chat = await ChatModel.find_one(ChatModel.tid == chat_id)
            if chat:
                await collection.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"chat": DBRef("chats", chat.id)}, "$unset": {field_name: ""}},
                    session=session,
                )
            else:
                log.warning(
                    f"Deleting orphaned {collection.name} record without corresponding chat",
                    chat_id=chat_id,
                    doc_id=doc["_id"],
                )
                await collection.delete_one({"_id": doc["_id"]}, session=session)


class Forward:
    @free_fall_migration(
        document_models=[
            LanguageModel,
            DisablingModel,
            GreetingsModel,
            RulesModel,
            BetaModeModel,
            ChatConnectionSettingsModel,
            WarnSettingsModel,
        ]
    )
    async def migrate(self, session):
        await migrate_model(LanguageModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")
        await migrate_model(DisablingModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")
        await migrate_model(GreetingsModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")
        await migrate_model(RulesModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")
        await migrate_model(BetaModeModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")
        await migrate_model(ChatConnectionSettingsModel.get_pymongo_collection(), session, index_to_drop="chat_id")
        await migrate_model(WarnSettingsModel.get_pymongo_collection(), session, index_to_drop="chat_id_1")


class Backward:
    @free_fall_migration(
        document_models=[
            LanguageModel,
            DisablingModel,
            GreetingsModel,
            RulesModel,
            BetaModeModel,
            ChatConnectionSettingsModel,
            WarnSettingsModel,
        ]
    )
    async def rollback(self, session):
        for model in [
            LanguageModel,
            DisablingModel,
            GreetingsModel,
            RulesModel,
            BetaModeModel,
            ChatConnectionSettingsModel,
            WarnSettingsModel,
        ]:
            collection = model.get_pymongo_collection()
            async for doc in collection.find():
                if "chat" in doc:
                    chat_iid = doc["chat"].id if isinstance(doc["chat"], DBRef) else doc["chat"]
                    chat = await ChatModel.find_one(ChatModel.iid == chat_iid)
                    if chat:
                        await collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"chat_id": chat.tid}, "$unset": {"chat": ""}},
                            session=session,
                        )
