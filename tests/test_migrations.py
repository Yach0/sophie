"""Test suite for database migrations."""

import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
from bson import DBRef, ObjectId

from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.notes import NoteModel

from sophie_bot.db.models.ai.ai_provider import AIProviderModel
from sophie_bot.db.models.chat import ChatModel, ChatType


def _legacy_notes_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260522_120000_convert_legacy_notes_to_html")


def test_convert_legacy_notes_to_html_update_converts_markdown_note() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {"_id": "note-id", "text": "**legacy** note", "parse_mode": "md", "version": 1}
    )

    assert update == {
        "$set": {
            "text": "<b>legacy</b> note",
            "parse_mode": "html",
            "version": 2,
            "legacy_markdown_text": "**legacy** note",
            "buttons": [],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_extracts_legacy_buttons() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {
            "_id": "note-id",
            "text": "Menu\n[Open](btnurl:https://example.com)\n[Rules](btnrules)",
            "parse_mode": "md",
            "version": 1,
        }
    )

    assert update == {
        "$set": {
            "text": "Menu",
            "parse_mode": "html",
            "version": 2,
            "legacy_markdown_text": "Menu\n[Open](btnurl:https://example.com)\n[Rules](btnrules)",
            "buttons": [
                [{"text": "Open", "action": "url", "data": "https://example.com", "style": None}],
                [{"text": "Rules", "action": "rules", "data": None, "style": None}],
            ],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_extracts_buttons_from_html_v1_note() -> None:
    migration = _legacy_notes_migration()

    update = migration.convert_legacy_note_to_html_update(
        {"_id": "note-id", "text": "Menu\n[Delete](btndelmsg)", "parse_mode": "html", "version": 1}
    )

    assert update == {
        "$set": {
            "text": "Menu\n",
            "version": 2,
            "buttons": [[{"text": "Delete", "action": "delmsg", "data": None, "style": None}]],
            "legacy_buttons": [],
        }
    }


def test_convert_legacy_notes_to_html_update_skips_html_note() -> None:
    migration = _legacy_notes_migration()

    assert migration.convert_legacy_note_to_html_update({"text": "<b>modern</b>", "parse_mode": "html"}) is None


def test_restore_legacy_note_markdown_update_restores_original_text() -> None:
    migration = _legacy_notes_migration()

    update = migration.restore_legacy_note_markdown_update(
        {"text": "<b>legacy</b> note", "parse_mode": "html", "legacy_markdown_text": "**legacy** note"}
    )

    assert update == {
        "$set": {
            "text": "**legacy** note",
            "parse_mode": "md",
            "version": 1,
        },
        "$unset": {
            "legacy_markdown_text": "",
            "legacy_buttons": "",
        },
    }


def test_restore_legacy_note_markdown_update_restores_original_buttons() -> None:
    migration = _legacy_notes_migration()

    update = migration.restore_legacy_note_markdown_update(
        {
            "text": "Menu\n",
            "parse_mode": "html",
            "buttons": [[{"text": "Delete", "action": "delmsg"}]],
            "legacy_buttons": [[{"text": "Old", "action": "url", "data": "https://example.com"}]],
        }
    )

    assert update == {
        "$set": {
            "text": "Menu\n",
            "version": 1,
            "buttons": [[{"text": "Old", "action": "url", "data": "https://example.com"}]],
        },
        "$unset": {
            "legacy_markdown_text": "",
            "legacy_buttons": "",
        },
    }


def _legacy_greetings_migration() -> ModuleType:
    return importlib.import_module(
        "sophie_bot.db.migrations.20260625_042041_convert_legacy_greetings_saveables_to_html"
    )


def test_convert_legacy_greetings_saveables_converts_markdown_and_buttons() -> None:
    migration = _legacy_greetings_migration()

    update = migration.build_greetings_saveables_migration_update(
        {
            "_id": "greetings-id",
            "note": {"text": "**Welcome**", "parse_mode": "md", "version": 1},
            "security_note": {
                "text": "Prove you are human\n[I am not a robot](btnwelcomesecurity)",
                "parse_mode": "md",
                "version": 1,
            },
        }
    )

    assert update == {
        "$set": {
            "note": {
                "text": "<b>Welcome</b>",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "**Welcome**",
                "buttons": [],
                "legacy_buttons": [],
            },
            "security_note": {
                "text": "Prove you are human",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "Prove you are human\n[I am not a robot](btnwelcomesecurity)",
                # The captcha button is hard-added in code, so it is dropped here to avoid a duplicate.
                "buttons": [],
                "legacy_buttons": [],
            },
        }
    }


def test_convert_legacy_greetings_saveables_skips_modern_and_missing() -> None:
    migration = _legacy_greetings_migration()

    assert (
        migration.build_greetings_saveables_migration_update(
            {
                "_id": "greetings-id",
                "note": {"text": "<b>modern</b>", "parse_mode": "html", "version": 2},
                "security_note": None,
            }
        )
        is None
    )


def test_restore_legacy_greetings_saveables_restores_original() -> None:
    migration = _legacy_greetings_migration()

    update = migration.build_greetings_saveables_rollback_update(
        {
            "_id": "greetings-id",
            "note": {
                "text": "<b>Welcome</b>",
                "parse_mode": "html",
                "version": 2,
                "legacy_markdown_text": "**Welcome**",
                "buttons": [],
                "legacy_buttons": [],
            },
        }
    )

    assert update == {
        "$set": {
            "note": {
                "text": "**Welcome**",
                "parse_mode": "md",
                "version": 1,
                "buttons": [],
            }
        }
    }


@pytest.mark.asyncio
async def test_migration_module_imports() -> None:
    """Test that migration modules can be imported without errors."""
    migrations_dir = Path("sophie_bot/db/migrations")
    migration_files = sorted(migrations_dir.glob("[0-9]*.py"))

    for migration_file in migration_files:
        try:
            importlib.import_module(f"sophie_bot.db.migrations.{migration_file.stem}")
            print(f"✓ {migration_file.name}")
        except Exception as e:
            pytest.fail(f"Failed to import {migration_file.name}: {e}")


@pytest.mark.asyncio
async def test_migration_state_model_structure() -> None:
    """Test that MigrationState model is defined correctly."""
    from sophie_bot.db.models.migrations import MigrationState

    # Check that model is defined
    assert MigrationState is not None
    assert hasattr(MigrationState, "__name__")


def _filters_legacy_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260523_020000_convert_filters_legacy_actions")


def test_convert_filters_legacy_reply_message_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "reply_message",
            "actions": {},
            "reply_text": {"text": "hello", "parse_mode": "html"},
        }
    )

    assert update == {
        "$set": {
            "actions": {"reply": {"text": "hello", "parse_mode": "html"}},
            "action": None,
            "version": 2,
        },
        "$unset": {"reply_text": ""},
    }


def test_convert_filters_legacy_get_note_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "get_note",
            "actions": {},
            "note_name": "rules",
        }
    )

    assert update == {
        "$set": {
            "actions": {"send_note": {"notename": "rules"}},
            "action": None,
            "version": 2,
        },
        "$unset": {"note_name": ""},
    }


def test_convert_filters_legacy_delete_message_action() -> None:
    migration = _filters_legacy_migration()

    update = migration.build_legacy_filter_migration_update(
        {
            "_id": "filter-id",
            "action": "delete_message",
            "actions": {},
        }
    )

    assert update == {
        "$set": {
            "actions": {"delmsg": {}},
            "action": None,
            "version": 2,
        },
    }


def test_convert_filters_legacy_skips_modern_filters() -> None:
    migration = _filters_legacy_migration()

    assert (
        migration.build_legacy_filter_migration_update(
            {
                "action": "reply_message",
                "actions": {"reply": {"text": "already migrated"}},
            }
        )
        is None
    )


def _relink_int64_note_users_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260715_151842_relink_legacy_int64_note_users")


async def _seed_legacy_user_chat(tid: int) -> ObjectId:
    """Insert a user chat the way production has it: through Beanie, so `tid` lands under `chat_id`.

    Seeding via raw pymongo would write a `tid` key that production never has, which is exactly
    what hid the alias bug this test now covers.

    Returns `chat.id`, not `chat.iid`: on a locally built instance `iid` holds its own
    `default_factory` ObjectId while Beanie writes `Document.id` to `_id`. The two only agree once
    the document is read back from the database.
    """
    chat = ChatModel(
        tid=tid,
        type=ChatType.private,
        first_name_or_title="Legacy",
        username=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat.insert()

    return chat.id


@pytest.mark.usefixtures("db_init")
async def test_chat_model_persists_tid_under_chat_id_alias() -> None:
    """`ChatModel.tid` is declared `alias="chat_id"`, and Beanie persists by alias.

    Migrations must therefore resolve chats through Beanie (or the alias), never a raw
    `{"tid": ...}` query -- that matches nothing and silently degrades to the not-found branch.
    """
    chat_iid = await _seed_legacy_user_chat(6001234567)
    stored = await ChatModel.get_pymongo_collection().find_one({"_id": chat_iid})

    assert stored["chat_id"] == 6001234567
    assert "tid" not in stored


@pytest.mark.usefixtures("db_init")
async def test_relink_legacy_note_users_matches_int64_ids() -> None:
    """The 20260214 migration used `$type: "int"`, which never matches BSON `long`.

    Telegram IDs above 2^31-1 (most modern accounts) are stored as `long`, so those rows were
    silently skipped and still break note reads (SOPHIE-285). Drive the real query against the
    database so a regression to `$type: "int"` fails this test.
    """
    migration = _relink_int64_note_users_migration()
    notes = NoteModel.get_pymongo_collection()

    # 5126697778 is the real SOPHIE-285 value: above 2^31-1, so stored as a BSON long.
    known_user_oid = await _seed_legacy_user_chat(5126697778)

    chat_ref = DBRef("chats", ObjectId())
    resolvable = (
        await notes.insert_one({"chat": chat_ref, "chat_id": -1, "names": ["a"], "created_user": 5126697778})
    ).inserted_id
    unknown = (
        await notes.insert_one({"chat": chat_ref, "chat_id": -1, "names": ["b"], "created_user": 9999999999})
    ).inserted_id

    # The Int64 rows must be selected at all -- this is the whole bug. `$type: "int"` matches none.
    assert await notes.count_documents({"created_user": {"$type": "int"}}) == 0
    assert await notes.count_documents(migration.legacy_id_query("created_user")) == 2

    relinked, cleared = await migration.relink_legacy_note_users(notes)

    assert (relinked, cleared) == (1, 1)
    # Attribution preserved where the user is known, dropped (never misattributed) where it is not.
    assert (await notes.find_one({"_id": resolvable}))["created_user"] == DBRef("chats", known_user_oid)
    assert "created_user" not in await notes.find_one({"_id": unknown})
    assert await notes.count_documents(migration.legacy_id_query("created_user")) == 0


def _zai_provider_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260507_143000_migrate_zai_provider_to_auto")


def _summary_model_gpt55_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260507_000000_update_ai_summary_model_to_gpt55")


async def _seed_ai_provider_chat() -> ChatModel:
    """Insert a chat through Beanie so `tid` is stored under its `chat_id` alias."""
    chat = ChatModel(
        tid=-1001,
        type=ChatType.supergroup,
        first_name_or_title="AI provider chat",
        username=None,
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
    )
    await chat.insert()
    return chat


@pytest.mark.usefixtures("db_init")
async def test_zai_provider_backward_leaves_pre_existing_auto_chats_alone() -> None:
    """Backward must not touch chats that were already on "auto" before Forward ran.

    "auto" is AIProviderModel.provider's default, so it is by far the largest population.
    The original Backward reverted every "auto" chat to "zai" -- a provider this migration
    removed outright -- rather than the handful Forward moved. This test fails against that
    version: `already_auto` comes back as "zai".
    """
    migration = _zai_provider_migration()
    chat = await _seed_ai_provider_chat()

    migrated = await AIProviderModel(chat=chat, provider="zai").insert()
    already_auto = await AIProviderModel(chat=chat, provider="auto").insert()

    await migration.Forward.migrate.run(None)

    assert (await AIProviderModel.get(migrated.id)).provider == "auto"

    await migration.Backward.noop.run(None)

    # The whole bug: this chat was never "zai" and must never become "zai".
    assert (await AIProviderModel.get(already_auto.id)).provider == "auto"
    # Forward's own set is not restorable either -- it is now indistinguishable from the above.
    assert (await AIProviderModel.get(migrated.id)).provider == "auto"


@pytest.mark.usefixtures("db_init")
async def test_summary_model_gpt55_backward_leaves_pre_existing_new_model_alone() -> None:
    """Backward must not touch documents that were already on the new default summary model.

    "openai/gpt-5.5" is constants.DEFAULT_AI_SUMMARY_MODEL, so documents carry it by default
    or by deliberate choice. The original Backward downgraded all of them to "openai/gpt-5.4";
    this test fails against that version.
    """
    migration = _summary_model_gpt55_migration()
    chat = await _seed_ai_provider_chat()

    migrated = await AIProviderModel(chat=chat, summary_model="openai/gpt-5.4").insert()
    already_new = await AIProviderModel(chat=chat, summary_model="openai/gpt-5.5").insert()

    await migration.Forward.migrate.run(None)

    assert (await AIProviderModel.get(migrated.id)).summary_model == "openai/gpt-5.5"

    await migration.Backward.noop.run(None)

    assert (await AIProviderModel.get(already_new.id)).summary_model == "openai/gpt-5.5"
    assert (await AIProviderModel.get(migrated.id)).summary_model == "openai/gpt-5.5"


@pytest.mark.usefixtures("db_init")
async def test_relink_legacy_note_users_relinks_edited_user() -> None:
    """`edited_user` is in _USER_FIELDS too, and regressed identically to `created_user`."""
    migration = _relink_int64_note_users_migration()
    notes = NoteModel.get_pymongo_collection()

    known_user_oid = await _seed_legacy_user_chat(7126697778)

    chat_ref = DBRef("chats", ObjectId())
    edited = (
        await notes.insert_one({"chat": chat_ref, "chat_id": -2, "names": ["c"], "edited_user": 7126697778})
    ).inserted_id

    relinked, cleared = await migration.relink_legacy_note_users(notes)

    assert (relinked, cleared) == (1, 0)
    assert (await notes.find_one({"_id": edited}))["edited_user"] == DBRef("chats", known_user_oid)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
