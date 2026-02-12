"""Migration: convert_federations_to_links

Description:
    Converts federation-related models to use Link[ChatModel].
    Note: FederationBan.user uses user_id (int), but banned_chats and by use Links.

Affected Collections:
    - feds
    - fed_bans (partial - only banned_chats and by)
    - fed_import_tasks
    - fed_export_tasks
"""

from __future__ import annotations

from bson import DBRef

from beanie import free_fall_migration
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import (
    Federation,
    FederationBan,
    FederationImportTask,
    FederationExportTask,
)
from sophie_bot.utils.logger import log


class Forward:
    @free_fall_migration(
        document_models=[
            Federation,
            FederationBan,
            FederationImportTask,
            FederationExportTask,
        ]
    )
    async def migrate(self, session):
        # Federation
        col = Federation.get_pymongo_collection()
        async for doc in col.find():
            updates = {}
            unsets = {}
            if "creator" in doc and isinstance(doc["creator"], int):
                c = await ChatModel.find_one(ChatModel.tid == doc["creator"])
                if c:
                    updates["creator"] = DBRef("chats", c.id)
                else:
                    log.warning(
                        "Deleting orphaned federation without creator",
                        fed_id=doc.get("fed_id"),
                        doc_id=doc["_id"],
                    )
                    await col.delete_one({"_id": doc["_id"]}, session=session)
                    continue

            if "chats" in doc and doc["chats"]:
                new_chats = []
                for tid in doc["chats"]:
                    c = await ChatModel.find_one(ChatModel.tid == tid)
                    if c:
                        new_chats.append(DBRef("chats", c.id))
                updates["chats"] = new_chats

            if "admins" in doc and doc["admins"]:
                new_admins = []
                for tid in doc["admins"]:
                    c = await ChatModel.find_one(ChatModel.tid == tid)
                    if c:
                        new_admins.append(DBRef("chats", c.id))
                updates["admins"] = new_admins

            if "log_chat_id" in doc and doc["log_chat_id"]:
                c = await ChatModel.find_one(ChatModel.tid == doc["log_chat_id"])
                if c:
                    updates["log_chat"] = DBRef("chats", c.id)
                unsets["log_chat_id"] = ""
            elif "log_chat_id" in doc:
                unsets["log_chat_id"] = ""

            if updates or unsets:
                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await col.update_one({"_id": doc["_id"]}, u, session=session)

        # FederationBan - only convert banned_chats and by, keep user_id as int
        col = FederationBan.get_pymongo_collection()
        async for doc in col.find():
            updates = {}
            unsets = {}
            should_delete = False

            # Check if federation exists
            fed_id = doc.get("fed_id")
            if fed_id:
                federation = await Federation.find_one(Federation.fed_id == fed_id)
                if not federation:
                    log.warning(
                        "Deleting orphaned federation ban - federation not found",
                        fed_ban_id=doc.get("_id"),
                        fed_id=fed_id,
                    )
                    should_delete = True

            # Convert by (int) to by Link
            if not should_delete and "by" in doc and isinstance(doc["by"], int):
                c = await ChatModel.find_one(ChatModel.tid == doc["by"])
                if c:
                    updates["by"] = DBRef("chats", c.id)
                else:
                    # Use Sophie (bot) as fallback for orphaned bans
                    from sophie_bot.config import CONFIG

                    sophie = await ChatModel.find_one(ChatModel.tid == CONFIG.bot_id)
                    if sophie:
                        log.warning(
                            "Using Sophie as fallback for orphaned federation ban - by user not found",
                            fed_ban_id=doc.get("_id"),
                            fed_id=fed_id,
                            by_tid=doc["by"],
                            sophie_tid=CONFIG.bot_id,
                        )
                        updates["by"] = DBRef("chats", sophie.id)
                    else:
                        log.warning(
                            "Deleting orphaned federation ban - by user not found and Sophie not in DB",
                            fed_ban_id=doc.get("_id"),
                            fed_id=fed_id,
                            by_tid=doc["by"],
                        )
                        should_delete = True

            # Convert banned_chats list of int to Links
            if not should_delete and "banned_chats" in doc and doc["banned_chats"]:
                new_chats = []
                for tid in doc["banned_chats"]:
                    if isinstance(tid, int):
                        c = await ChatModel.find_one(ChatModel.tid == tid)
                        if c:
                            new_chats.append(DBRef("chats", c.id))
                if new_chats:
                    updates["banned_chats"] = new_chats
                else:
                    unsets["banned_chats"] = ""

            if should_delete:
                await col.delete_one({"_id": doc["_id"]}, session=session)
            elif updates or unsets:
                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await col.update_one({"_id": doc["_id"]}, u, session=session)

        # Import/Export Tasks
        for model in [FederationImportTask, FederationExportTask]:
            col = model.get_pymongo_collection()
            async for doc in col.find():
                updates = {}
                unsets = {}
                if "chat_id" in doc:
                    c = await ChatModel.find_one(ChatModel.tid == doc["chat_id"])
                    if c:
                        updates["chat"] = DBRef("chats", c.id)
                    unsets["chat_id"] = ""
                if "user_id" in doc:
                    c = await ChatModel.find_one(ChatModel.tid == doc["user_id"])
                    if c:
                        updates["user"] = DBRef("chats", c.id)
                    unsets["user_id"] = ""
                if updates or unsets:
                    u = {}
                    if updates:
                        u["$set"] = updates
                    if unsets:
                        u["$unset"] = unsets
                    await col.update_one({"_id": doc["_id"]}, u, session=session)


class Backward:
    @free_fall_migration(
        document_models=[
            Federation,
            FederationBan,
            FederationImportTask,
            FederationExportTask,
        ]
    )
    async def rollback(self, session):
        # Federation
        col = Federation.get_pymongo_collection()
        async for doc in col.find():
            updates = {}
            unsets = {}
            if "creator" in doc:
                creator_id = doc["creator"].id if isinstance(doc["creator"], DBRef) else doc["creator"]
                c = await ChatModel.find_one(ChatModel.iid == creator_id)
                if c:
                    updates["creator"] = c.tid

            if "chats" in doc and doc["chats"]:
                new_chats = []
                for chat_ref in doc["chats"]:
                    chat_id = chat_ref.id if isinstance(chat_ref, DBRef) else chat_ref
                    c = await ChatModel.find_one(ChatModel.iid == chat_id)
                    if c:
                        new_chats.append(c.tid)
                updates["chats"] = new_chats

            if "admins" in doc and doc["admins"]:
                new_admins = []
                for admin_ref in doc["admins"]:
                    admin_id = admin_ref.id if isinstance(admin_ref, DBRef) else admin_ref
                    c = await ChatModel.find_one(ChatModel.iid == admin_id)
                    if c:
                        new_admins.append(c.tid)
                updates["admins"] = new_admins

            if "log_chat" in doc:
                log_chat_id = doc["log_chat"].id if isinstance(doc["log_chat"], DBRef) else doc["log_chat"]
                c = await ChatModel.find_one(ChatModel.iid == log_chat_id)
                if c:
                    updates["log_chat_id"] = c.tid
                unsets["log_chat"] = ""

            if updates or unsets:
                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await col.update_one({"_id": doc["_id"]}, u, session=session)

        # FederationBan - rollback banned_chats and by, keep user_id as int
        col = FederationBan.get_pymongo_collection()
        async for doc in col.find():
            updates = {}
            unsets = {}

            # Convert by Link back to int
            if "by" in doc and doc["by"]:
                by_ref = doc["by"]
                if isinstance(by_ref, DBRef):
                    by_iid = by_ref.id
                else:
                    by_iid = by_ref

                c = await ChatModel.find_one(ChatModel.id == by_iid)
                if c:
                    updates["by"] = c.tid

            # Convert banned_chats Links back to list of int
            if "banned_chats" in doc and doc["banned_chats"]:
                new_chats = []
                for chat_ref in doc["banned_chats"]:
                    if isinstance(chat_ref, DBRef):
                        chat_iid = chat_ref.id
                    else:
                        chat_iid = chat_ref

                    c = await ChatModel.find_one(ChatModel.id == chat_iid)
                    if c:
                        new_chats.append(c.tid)

                if new_chats:
                    updates["banned_chats"] = new_chats
                else:
                    unsets["banned_chats"] = ""

            if updates or unsets:
                u = {}
                if updates:
                    u["$set"] = updates
                if unsets:
                    u["$unset"] = unsets
                await col.update_one({"_id": doc["_id"]}, u, session=session)

        # Import/Export Tasks
        for model in [FederationImportTask, FederationExportTask]:
            col = model.get_pymongo_collection()
            async for doc in col.find():
                updates = {}
                unsets = {}
                if "chat" in doc:
                    chat_id = doc["chat"].id if isinstance(doc["chat"], DBRef) else doc["chat"]
                    c = await ChatModel.find_one(ChatModel.iid == chat_id)
                    if c:
                        updates["chat_id"] = c.tid
                    unsets["chat"] = ""
                if "user" in doc:
                    user_id = doc["user"].id if isinstance(doc["user"], DBRef) else doc["user"]
                    c = await ChatModel.find_one(ChatModel.iid == user_id)
                    if c:
                        updates["user_id"] = c.tid
                    unsets["user"] = ""
                if updates or unsets:
                    u = {}
                    if updates:
                        u["$set"] = updates
                    if unsets:
                        u["$unset"] = unsets
                    await col.update_one({"_id": doc["_id"]}, u, session=session)
