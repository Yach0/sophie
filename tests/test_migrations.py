"""Test suite for database migrations."""

import importlib
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest
from bson import DBRef, ObjectId

from sophie_bot.services.db import get_collection
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, _serialize_value


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


def _rename_disabled_keys_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260715_225616_rename_legacy_disabled_cmd_keys")


@pytest.mark.usefixtures("db_init")
async def test_rename_legacy_disabled_cmd_keys_round_trips() -> None:
    """Legacy rows hold a first-command key the middleware never enforces; canonical names replace it."""
    migration = _rename_disabled_keys_migration()
    disabled = DisablingModel.get_pymongo_collection()

    legacy_id = (
        await disabled.insert_one(
            {"chat": DBRef("chats", ObjectId()), "cmds": ["aitranslate", "rules", "enableantiflood"]}
        )
    ).inserted_id
    untouched_id = (await disabled.insert_one({"chat": DBRef("chats", ObjectId()), "cmds": ["rules"]})).inserted_id

    assert await migration.rename_disabled_cmd_keys(None, migration.LEGACY_KEYS_TO_CANONICAL) == 1

    assert (await disabled.find_one({"_id": legacy_id}))["cmds"] == ["translate", "rules", "antiflood"]
    assert (await disabled.find_one({"_id": untouched_id}))["cmds"] == ["rules"]

    await migration.rename_disabled_cmd_keys(None, migration.CANONICAL_TO_LEGACY_KEYS)

    assert (await disabled.find_one({"_id": legacy_id}))["cmds"] == ["aitranslate", "rules", "enableantiflood"]
    assert (await disabled.find_one({"_id": untouched_id}))["cmds"] == ["rules"]


def _zai_provider_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260507_143000_migrate_zai_provider_to_auto")


def _summary_model_gpt55_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260507_000000_update_ai_summary_model_to_gpt55")


async def _seed_chat(chat_tid: int) -> ChatModel:
    """Insert a chat through Beanie so `tid` is stored under its `chat_id` alias."""
    chat = ChatModel(
        tid=chat_tid,
        type=ChatType.supergroup,
        first_name_or_title="Rollback chat",
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
    providers = get_collection("ai_provider")

    migrated_id = (await providers.insert_one({"provider": "zai"})).inserted_id
    already_auto_id = (await providers.insert_one({"provider": "auto"})).inserted_id

    await migration.Forward.migrate.run(None)

    assert (await providers.find_one({"_id": migrated_id}))["provider"] == "auto"

    await migration.Backward.noop.run(None)

    # The whole bug: this chat was never "zai" and must never become "zai".
    assert (await providers.find_one({"_id": already_auto_id}))["provider"] == "auto"
    # Forward's own set is not restorable either -- it is now indistinguishable from the above.
    assert (await providers.find_one({"_id": migrated_id}))["provider"] == "auto"


@pytest.mark.usefixtures("db_init")
async def test_summary_model_gpt55_backward_leaves_pre_existing_new_model_alone() -> None:
    """Backward must not touch documents that were already on the new default summary model.

    "openai/gpt-5.5" is constants.DEFAULT_AI_SUMMARY_MODEL, so documents carry it by default
    or by deliberate choice. The original Backward downgraded all of them to "openai/gpt-5.4";
    this test fails against that version.
    """
    migration = _summary_model_gpt55_migration()
    providers = get_collection("ai_provider")

    migrated_id = (await providers.insert_one({"summary_model": "openai/gpt-5.4"})).inserted_id
    already_new_id = (await providers.insert_one({"summary_model": "openai/gpt-5.5"})).inserted_id

    await migration.Forward.migrate.run(None)

    assert (await providers.find_one({"_id": migrated_id}))["summary_model"] == "openai/gpt-5.5"

    await migration.Backward.noop.run(None)

    assert (await providers.find_one({"_id": already_new_id}))["summary_model"] == "openai/gpt-5.5"
    assert (await providers.find_one({"_id": migrated_id}))["summary_model"] == "openai/gpt-5.5"


@pytest.mark.usefixtures("db_init")
async def test_populate_note_links_backward_keeps_links_it_did_not_create() -> None:
    """Backward must not strip `chat` from notes created after the migration.

    `NoteModel.chat` is required, and the previous Backward was an unfiltered
    `update_many({}, {"$unset": {"chat": ""}})` -- it emptied the whole collection's links,
    not the ones Forward populated. This test fails against that version: `chat` is gone.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260125_210014_populate_note_links")
    chat = await _seed_chat(-1101)
    notes = NoteModel.get_pymongo_collection()

    note = NoteModel(chat_tid=chat.tid, chat=chat, names=("modern",), text="created after the migration")
    await note.insert()

    await migration.Backward.noop.run(None)

    stored = await notes.find_one({"_id": note.id})
    assert stored is not None
    assert stored.get("chat") is not None


@pytest.mark.usefixtures("db_init")
async def test_split_warn_actions_backward_only_unsets_what_forward_added() -> None:
    """Backward must remove the scoped fields only where Forward copied legacy actions in.

    The previous Backward iterated every document and unconditionally wrote
    `actions = on_max_warn_actions or []` -- a field Forward never touches. Against that
    version `native` gains a fabricated legacy `actions` and this test fails.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260304_120000_split_warn_actions_scopes")
    chat = await _seed_chat(-1102)
    collection = WarnSettingsModel.get_pymongo_collection()

    legacy_actions = [{"name": "ban_user", "data": {}}]
    legacy = await WarnSettingsModel(chat=chat).insert()
    # `actions` is the pre-migration field; it no longer exists on WarnSettingsModel.
    await collection.update_one({"_id": legacy.id}, {"$set": {"actions": legacy_actions}})

    native = await WarnSettingsModel(chat=chat, on_max_warn_actions=[{"name": "kick_user", "data": {}}]).insert()

    await migration.Forward.migrate.run(None)

    migrated = await collection.find_one({"_id": legacy.id})
    assert migrated["on_max_warn_actions"] == legacy_actions

    await migration.Backward.rollback.run(None)

    # Configured through the current API, never had a legacy `actions` -- must be untouched.
    stored_native = await collection.find_one({"_id": native.id})
    assert "actions" not in stored_native
    assert stored_native["on_max_warn_actions"] == [{"name": "kick_user", "data": {}}]

    # Forward's own document: the fields it added are gone, the legacy field it never wrote survives.
    stored_legacy = await collection.find_one({"_id": legacy.id})
    assert stored_legacy["actions"] == legacy_actions
    assert "on_max_warn_actions" not in stored_legacy
    assert "on_each_warn_actions" not in stored_legacy


@pytest.mark.usefixtures("db_init")
async def test_convert_filters_backward_leaves_native_v2_filters_alone() -> None:
    """Backward must not rewrite filters that were authored in the v2 format.

    A converted filter and a native v2 filter are both `action: None, version: 2`, so the
    previous Backward matched natives too and shredded them to `actions: {}`. This test fails
    against that version.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260523_020000_convert_filters_legacy_actions")
    chat = await _seed_chat(-1103)
    collection = FiltersModel.get_pymongo_collection()

    native_actions = {"reply": {"text": "authored in v2"}}
    native = await FiltersModel(chat=chat, handler="hi", action=None, actions=native_actions, version=2).insert()

    await migration.Backward.noop.run(None)

    stored = await collection.find_one({"_id": native.id})
    assert stored["actions"] == native_actions
    assert stored["action"] is None
    assert stored["version"] == 2


@pytest.mark.usefixtures("db_init")
async def test_convert_antiflood_backward_keeps_actions_carrying_data() -> None:
    """Backward must not truncate an action whose `data` the legacy field cannot express.

    Forward only ever emitted `data: {}`, so an action with a payload was configured later and
    is not Forward's. The previous Backward converted it anyway, dropping the payload -- against
    that version `with_data` comes back as `action: "mute", actions: []` and this test fails.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260125_210014_convert_antiflood_legacy_actions")
    chat = await _seed_chat(-1104)
    collection = AntifloodModel.get_pymongo_collection()

    with_data = await AntifloodModel(chat=chat, actions=[{"name": "mute_user", "data": {"time": "1h"}}]).insert()
    forward_shaped = await AntifloodModel(chat=chat, actions=[{"name": "ban_user", "data": {}}]).insert()

    await migration.Backward.rollback.run(None)

    stored_with_data = await collection.find_one({"_id": with_data.id})
    assert stored_with_data["actions"] == [{"name": "mute_user", "data": {"time": "1h"}}]
    assert stored_with_data.get("action") is None

    # Forward's own output shape is lossless in the legacy form, so it still converts.
    stored_forward_shaped = await collection.find_one({"_id": forward_shaped.id})
    assert stored_forward_shaped["action"] == "ban"
    assert stored_forward_shaped["actions"] == []


@pytest.mark.usefixtures("db_init")
async def test_link_orphaned_notes_backward_keeps_genuine_sophie_attribution() -> None:
    """Backward must not fabricate user ID 0 over notes genuinely authored by Sophie.

    Forward destroyed the original IDs, so nothing can be restored. The previous Backward wrote
    the literal SOPHIE_SYSTEM_TID onto every note pointing at the Sophie chat, including notes
    Forward never touched -- against that version `created_user` becomes 0 and this test fails.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260214_082800_link_orphaned_notes_to_sophie")
    sophie = await _seed_chat(migration.SOPHIE_SYSTEM_TID)
    chat = await _seed_chat(-1105)
    notes = NoteModel.get_pymongo_collection()

    sophie_ref = DBRef("chats", sophie.id)
    note = NoteModel(chat_tid=chat.tid, chat=chat, names=("authored-by-sophie",), text="note")
    await note.insert()
    await notes.update_one({"_id": note.id}, {"$set": {"created_user": sophie_ref}})

    await migration.Backward.noop.run(None)

    stored = await notes.find_one({"_id": note.id})
    assert stored["created_user"] == sophie_ref


@pytest.mark.usefixtures("db_init")
async def test_add_ai_summary_model_backward_keeps_deliberate_gpt54_choice() -> None:
    """Backward must not unset a summary model the owner chose explicitly.

    Forward only backfilled documents missing the field, but the previous Backward `$unset`
    every document equal to "openai/gpt-5.4" -- against that version the field is gone and this
    test fails.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260504_210000_add_ai_summary_model")
    collection = get_collection("ai_provider")

    chosen_id = (await collection.insert_one({"summary_model": "openai/gpt-5.4"})).inserted_id

    await migration.Backward.noop.run(None)

    stored = await collection.find_one({"_id": chosen_id})
    assert stored["summary_model"] == "openai/gpt-5.4"


@pytest.mark.usefixtures("db_init")
async def test_feature_flags_backward_never_drops_an_override_it_did_not_restore() -> None:
    """Backward must not delete overrides it declined to write back to Redis.

    The previous Backward skipped any feature absent from FEATURE_FLAGS and then dropped the
    whole collection, so an override for a retired flag was destroyed without ever reaching
    Redis. Against that version `retired` is gone and this test fails.
    """
    migration = importlib.import_module("sophie_bot.db.migrations.20260507_000000_migrate_feature_flags_to_db")
    collection = FeatureFlagOverride.get_pymongo_collection()

    live_feature = next(iter(FEATURE_FLAGS))
    retired = await FeatureFlagOverride(feature="retired_flag_no_longer_declared", chat_tid=None, value=True).insert()
    live = await FeatureFlagOverride(feature=live_feature, chat_tid=None, value=True).insert()
    unrestorable = await FeatureFlagOverride(feature="flag_with_null_value", chat_tid=None, value=None).insert()

    await migration.Backward.rollback.run(None)

    # Restored to Redis, so removing the row is safe.
    assert await collection.find_one({"_id": live.id}) is None
    assert await aredis.hget(migration._REDIS_KEY, live_feature) == _serialize_value(True).encode()

    # A retired flag's override is still restored, and its row is only removed once it is.
    assert await aredis.hget(migration._REDIS_KEY, "retired_flag_no_longer_declared") == _serialize_value(True).encode()
    assert await collection.find_one({"_id": retired.id}) is None

    # Nothing to write back, so the row is kept rather than destroyed.
    assert await collection.find_one({"_id": unrestorable.id}) is not None


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


async def _reset_collections(*names: str) -> None:
    """db_init is session-scoped, so collections carry over between tests."""
    for name in names:
        await get_collection(name).delete_many({})


def _ai_mode_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_140000_migrate_ai_settings_to_mode")


def _ai_catalog_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_150000_seed_ai_catalog")


@pytest.mark.usefixtures("db_init")
async def test_ai_settings_to_mode_derives_one_mode_per_chat() -> None:
    """A chat's old enabled/moderator pair decides its mode; chats with neither stay disabled."""
    migration = _ai_mode_migration()
    enabled, moderator, modes = (
        get_collection("ai_enabled"),
        get_collection("ai_moderator"),
        get_collection("ai_mode"),
    )
    await _reset_collections("ai_enabled", "ai_moderator", "ai_mode")
    plain_chat, moderated_chat, off_chat = ObjectId(), ObjectId(), ObjectId()

    await enabled.insert_many([{"chat": plain_chat}, {"chat": moderated_chat}])
    await moderator.insert_many([{"chat": moderated_chat, "enabled": True}, {"chat": off_chat, "enabled": True}])

    await migration.Forward.migrate.run(None)

    stored = {document["chat"]: document["mode"] async for document in modes.find({})}
    assert stored == {plain_chat: "support", moderated_chat: "moderation"}
    # A chat that had the moderator configured but AI switched off must not gain AI features.
    assert off_chat not in stored


@pytest.mark.usefixtures("db_init")
async def test_ai_settings_to_mode_backward_restores_only_enabled_chats() -> None:
    migration = _ai_mode_migration()
    modes, enabled = get_collection("ai_mode"), get_collection("ai_enabled")
    await _reset_collections("ai_enabled", "ai_mode")
    support_chat, disabled_chat = ObjectId(), ObjectId()

    await modes.insert_many([{"chat": support_chat, "mode": "support"}, {"chat": disabled_chat, "mode": "disabled"}])

    await migration.Backward.migrate.run(None)

    restored = [document["chat"] async for document in enabled.find({})]
    assert restored == [support_chat]
    assert await modes.count_documents({}) == 0


@pytest.mark.usefixtures("db_init")
async def test_seed_ai_catalog_is_idempotent_and_keeps_operator_edits() -> None:
    """Re-running the seed must not duplicate entries nor overwrite a rotated key."""
    migration = _ai_catalog_migration()
    providers, models = get_collection("ai_catalog_provider"), get_collection("ai_catalog_model")
    await _reset_collections("ai_catalog_provider", "ai_catalog_model")

    await migration.Forward.migrate.run(None)

    seeded_models = await models.count_documents({})
    assert seeded_models == len(migration._MODELS)
    assert await providers.count_documents({"name": "openrouter"}) == 1

    await providers.update_one({"name": "openrouter"}, {"$set": {"api_key": "rotated-by-operator"}})
    await migration.Forward.migrate.run(None)

    assert await models.count_documents({}) == seeded_models
    assert (await providers.find_one({"name": "openrouter"}))["api_key"] == "rotated-by-operator"


@pytest.mark.usefixtures("db_init")
async def test_seeded_catalog_covers_every_purpose_every_mode_falls_back_to() -> None:
    """The support tier answers for any mode with no model of its own, so it must be complete."""
    migration = _ai_catalog_migration()

    roles = {(role["mode"], role["purpose"]) for model in migration._MODELS for role in model["roles"]}

    assert {"chatbot", "translation", "filters"} <= {purpose for mode, purpose in roles if mode == "support"}
    assert {"summary", "moderation_reason"} <= {purpose for mode, purpose in roles if mode is None}


def _sophie_inspect_model_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_160000_add_deep_help_model")


@pytest.mark.usefixtures("db_init")
async def test_sophie_inspect_model_role_is_added_without_disturbing_an_existing_entry() -> None:
    migration = _sophie_inspect_model_migration()
    models = get_collection("ai_catalog_model")
    await _reset_collections("ai_catalog_model")

    await models.insert_one(
        {"name": migration._MODEL_NAME, "provider": "openrouter", "roles": [{"mode": "support", "purpose": "chatbot"}]}
    )

    await migration.Forward.migrate.run(None)

    stored = await models.find_one({"name": migration._MODEL_NAME})
    assert {"mode": "support", "purpose": "chatbot"} in stored["roles"]
    assert migration._ROLE in stored["roles"]

    await migration.Backward.migrate.run(None)

    stored = await models.find_one({"name": migration._MODEL_NAME})
    # Backward drops only its own role: the model may serve other purposes by now.
    assert stored["roles"] == [{"mode": "support", "purpose": "chatbot"}]


@pytest.mark.usefixtures("db_init")
async def test_sophie_inspect_migration_replaces_the_role_it_used_to_write() -> None:
    """It shipped once under the tool's old name; a database that ran it then must converge."""
    migration = _sophie_inspect_model_migration()
    models = get_collection("ai_catalog_model")
    await _reset_collections("ai_catalog_model")

    await models.insert_one(
        {"name": migration._MODEL_NAME, "provider": "openrouter", "roles": [migration._LEGACY_ROLE]}
    )

    await migration.Forward.migrate.run(None)

    stored = await models.find_one({"name": migration._MODEL_NAME})
    assert stored["roles"] == [migration._ROLE]


def _rename_deep_help_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_170000_rename_deep_help_role")


@pytest.mark.usefixtures("db_init")
async def test_rename_deep_help_role_converges_a_stale_catalog() -> None:
    """A database that ran the seed before the tool was renamed holds a role the enum now rejects."""
    migration = _rename_deep_help_migration()
    models = get_collection("ai_catalog_model")
    await _reset_collections("ai_catalog_model")

    await models.insert_many(
        [
            {"name": "a/model", "provider": "openrouter", "roles": [{"mode": None, "purpose": "deep_help"}]},
            {
                "name": "b/model",
                "provider": "openrouter",
                "roles": [{"mode": "support", "purpose": "chatbot"}, {"mode": None, "purpose": "deep_help"}],
            },
            {"name": "c/model", "provider": "openrouter", "roles": [{"mode": None, "purpose": "summary"}]},
        ]
    )

    await migration.Forward.migrate.run(None)

    a = await models.find_one({"name": "a/model"})
    b = await models.find_one({"name": "b/model"})
    c = await models.find_one({"name": "c/model"})
    assert a["roles"] == [{"mode": None, "purpose": "sophie_inspect"}]
    # Only the deep_help role is touched; the other role on the same model is left alone.
    assert {"mode": "support", "purpose": "chatbot"} in b["roles"]
    assert {"mode": None, "purpose": "sophie_inspect"} in b["roles"]
    # A model without a deep_help role is not rewritten.
    assert c["roles"] == [{"mode": None, "purpose": "summary"}]


def _seed_research_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_180000_seed_research_role")


@pytest.mark.usefixtures("db_init")
async def test_seed_research_role_adds_an_any_mode_research_role() -> None:
    migration = _seed_research_migration()
    models = get_collection("ai_catalog_model")
    await _reset_collections("ai_catalog_model")
    await models.insert_one(
        {"name": migration._MODEL_NAME, "provider": "openrouter", "roles": [{"mode": None, "purpose": "summary"}]}
    )

    await migration.Forward.migrate.run(None)

    stored = await models.find_one({"name": migration._MODEL_NAME})
    assert migration._ROLE in stored["roles"]
    # The existing role is left alone.
    assert {"mode": None, "purpose": "summary"} in stored["roles"]

    await migration.Backward.migrate.run(None)
    stored = await models.find_one({"name": migration._MODEL_NAME})
    assert migration._ROLE not in stored["roles"]


def _expand_wildcard_migration() -> ModuleType:
    return importlib.import_module("sophie_bot.db.migrations.20260723_190000_expand_wildcard_roles")


def test_expand_wildcard_roles_fans_a_global_role_across_allowed_modes() -> None:
    expand = _expand_wildcard_migration()._expand_roles

    roles = expand([{"mode": None, "purpose": "summary", "service_tier": "flex"}])
    keys = {(role["mode"], role["purpose"]) for role in roles}

    # summary reaches the modes that allow it, carrying its settings, and no others.
    assert ("entertainment", "summary") in keys
    assert ("support", "summary") in keys
    assert ("moderation", "summary") not in keys
    assert all(role["service_tier"] == "flex" for role in roles)


def test_expand_wildcard_roles_mirrors_support_onto_the_private_modes() -> None:
    expand = _expand_wildcard_migration()._expand_roles

    keys = {(role["mode"], role["purpose"]) for role in expand([{"mode": "support", "purpose": "chatbot"}])}

    # The private-chat modes borrow support's chatbot, since they had no role of their own.
    assert ("sophie_pm", "chatbot") in keys
    assert ("sophie_help", "chatbot") in keys


def test_expand_wildcard_roles_is_idempotent() -> None:
    expand = _expand_wildcard_migration()._expand_roles

    once = expand([{"mode": None, "purpose": "sophie_inspect"}])
    twice = expand(once)

    assert {(role["mode"], role["purpose"]) for role in once} == {(role["mode"], role["purpose"]) for role in twice}
    # Only the help mode may inspect, so the single global role lands on exactly one mode.
    assert once == [{"mode": "sophie_help", "purpose": "sophie_inspect"}]
