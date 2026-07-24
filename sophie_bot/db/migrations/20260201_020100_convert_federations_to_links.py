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

from beanie import free_fall_migration
from bson import DBRef

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import (
    Federation,
    FederationBan,
    FederationExportTask,
    FederationImportTask,
)
from sophie_bot.utils.logger import log

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _apply_update(col, doc_id, updates: dict, unsets: dict, session) -> None:
    """Build and apply a $set/$unset mongo update."""
    u: dict = {}
    if updates:
        u["$set"] = updates
    if unsets:
        u["$unset"] = unsets
    await col.update_one({"_id": doc_id}, u, session=session)


async def _tid_list_to_dbref_list(tids: list[int]) -> list[DBRef]:
    """Convert a list of Telegram IDs to a list of DBRef("chats", ...)."""
    refs: list[DBRef] = []
    for tid in tids:
        c = await ChatModel.find_one(ChatModel.tid == tid)
        if c:
            refs.append(DBRef("chats", c.id))
    return refs


async def _dbref_to_tid(dbref_or_value) -> int | None:
    """Extract ObjectID from a DBRef (or plain value), look up ChatModel.tid."""
    oid = dbref_or_value.id if isinstance(dbref_or_value, DBRef) else dbref_or_value
    c = await ChatModel.find_one(ChatModel.id == oid)
    if c:
        return c.tid
    return None


async def _dbref_list_to_tid_list(refs: list) -> list[int]:
    """Convert a list of DBRef values to a list of TIDs via ChatModel lookup."""
    tids: list[int] = []
    for ref in refs:
        tid = await _dbref_to_tid(ref)
        if tid is not None:
            tids.append(tid)
    return tids


# ---------------------------------------------------------------------------
# Per-entity migrate helpers
# ---------------------------------------------------------------------------


async def _convert_federation_doc(doc: dict, session) -> None:
    """Convert a single Federation document from int IDs to DBRef links."""
    col = Federation.get_pymongo_collection()
    updates: dict = {}
    unsets: dict = {}

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
            return

    if doc.get("chats"):
        new_chats = await _tid_list_to_dbref_list(doc["chats"])
        updates["chats"] = new_chats

    if doc.get("admins"):
        new_admins = await _tid_list_to_dbref_list(doc["admins"])
        updates["admins"] = new_admins

    if doc.get("log_chat_id"):
        c = await ChatModel.find_one(ChatModel.tid == doc["log_chat_id"])
        if c:
            updates["log_chat"] = DBRef("chats", c.id)
        unsets["log_chat_id"] = ""
    elif "log_chat_id" in doc:
        unsets["log_chat_id"] = ""

    if updates or unsets:
        await _apply_update(col, doc["_id"], updates, unsets, session)


async def _convert_federation_ban_doc(doc: dict, session) -> None:
    """Convert a single FederationBan document from int IDs to DBRef links."""
    col = FederationBan.get_pymongo_collection()
    updates: dict = {}
    unsets: dict = {}
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
        new_chats = await _tid_list_to_dbref_list([tid for tid in doc["banned_chats"] if isinstance(tid, int)])
        if new_chats:
            updates["banned_chats"] = new_chats
        else:
            unsets["banned_chats"] = ""

    if should_delete:
        await col.delete_one({"_id": doc["_id"]}, session=session)
    elif updates or unsets:
        await _apply_update(col, doc["_id"], updates, unsets, session)


async def _convert_task_doc(col, doc: dict, session) -> None:
    """Convert a single Import/Export task document from int IDs to DBRef links."""
    updates: dict = {}
    unsets: dict = {}
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
        await _apply_update(col, doc["_id"], updates, unsets, session)


# ---------------------------------------------------------------------------
# Per-entity rollback helpers
# ---------------------------------------------------------------------------


async def _rollback_federation_doc(doc: dict, session) -> None:
    """Rollback a single Federation document from DBRef links back to int IDs."""
    col = Federation.get_pymongo_collection()
    updates: dict = {}
    unsets: dict = {}

    if "creator" in doc:
        creator_id = doc["creator"].id if isinstance(doc["creator"], DBRef) else doc["creator"]
        c = await ChatModel.find_one(ChatModel.iid == creator_id)
        if c:
            updates["creator"] = c.tid

    if doc.get("chats"):
        new_chats = []
        for chat_ref in doc["chats"]:
            chat_id = chat_ref.id if isinstance(chat_ref, DBRef) else chat_ref
            c = await ChatModel.find_one(ChatModel.iid == chat_id)
            if c:
                new_chats.append(c.tid)
        updates["chats"] = new_chats

    if doc.get("admins"):
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
        await _apply_update(col, doc["_id"], updates, unsets, session)


async def _rollback_federation_ban_doc(doc: dict, session) -> None:
    """Rollback a single FederationBan document from DBRef links back to int IDs."""
    col = FederationBan.get_pymongo_collection()
    updates: dict = {}
    unsets: dict = {}

    # Convert by Link back to int
    if doc.get("by"):
        by_ref = doc["by"]
        if isinstance(by_ref, DBRef):
            by_iid = by_ref.id
        else:
            by_iid = by_ref

        c = await ChatModel.find_one(ChatModel.id == by_iid)
        if c:
            updates["by"] = c.tid

    # Convert banned_chats Links back to list of int
    if doc.get("banned_chats"):
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
        await _apply_update(col, doc["_id"], updates, unsets, session)


async def _rollback_task_doc(col, doc: dict, session) -> None:
    """Rollback a single Import/Export task document from DBRef links back to int IDs."""
    updates: dict = {}
    unsets: dict = {}
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
        await _apply_update(col, doc["_id"], updates, unsets, session)


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
            await _convert_federation_doc(doc, session)

        # FederationBan - only convert banned_chats and by, keep user_id as int
        col = FederationBan.get_pymongo_collection()
        async for doc in col.find():
            await _convert_federation_ban_doc(doc, session)

        # Import/Export Tasks
        for model in [FederationImportTask, FederationExportTask]:
            col = model.get_pymongo_collection()
            async for doc in col.find():
                await _convert_task_doc(col, doc, session)


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
            await _rollback_federation_doc(doc, session)

        # FederationBan - rollback banned_chats and by, keep user_id as int
        col = FederationBan.get_pymongo_collection()
        async for doc in col.find():
            await _rollback_federation_ban_doc(doc, session)

        # Import/Export Tasks
        for model in [FederationImportTask, FederationExportTask]:
            col = model.get_pymongo_collection()
            async for doc in col.find():
                await _rollback_task_doc(col, doc, session)
