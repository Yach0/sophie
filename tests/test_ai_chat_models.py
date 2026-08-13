from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import SELECTABLE_MODES, AIMode
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogModel, CatalogProvider, ResolvedRole
from sophie_bot.modules.ai.utils.ai_chat_models import (
    get_chat_default_model_plan,
    get_chat_filters_model_plan,
    get_chat_summary_model_plan,
    get_chat_translations_model_plan,
)
from sophie_bot.modules.ai.utils.ai_help_mode import set_help_mode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities, resolve_chat_mode
from sophie_bot.modules.ai.utils.chatbot_context import build_chatbot_instructions
from sophie_bot.modules.help.utils.wiki_pages import get_wiki_pages, read_wiki_page

ENTERTAINMENT_CHATBOT = "free/chatbot"
ENTERTAINMENT_CHATBOT_BACKUP = "free/chatbot-backup"
MODERATION_TRANSLATION = "standard/translate"
MODERATION_FILTERS = "standard/filter"
SUPPORT_FILTERS = "quality/filter"
SUMMARY = "quality/summary"

# The entertainment chatbot chain is the one with more than one candidate: the primary cannot be
# shown an image, so an image turn has to skip past it to the backup.
TEXT_ONLY_MODELS = frozenset({ENTERTAINMENT_CHATBOT})


def _catalog() -> AICatalog:
    provider = CatalogProvider(name="openrouter", kind="openrouter", base_url=None, api_key="k")
    names = [
        ENTERTAINMENT_CHATBOT,
        ENTERTAINMENT_CHATBOT_BACKUP,
        MODERATION_TRANSLATION,
        MODERATION_FILTERS,
        SUPPORT_FILTERS,
        SUMMARY,
    ]

    def _role(name: str, priority: int = 0) -> ResolvedRole:
        return ResolvedRole(
            model_name=name,
            service_tier=None,
            reasoning_effort=None,
            supports_images=name not in TEXT_ONLY_MODELS,
            priority=priority,
        )

    return AICatalog(
        version="1",
        providers={provider.name: provider},
        models={
            name: CatalogModel(
                name=name,
                provider=provider,
                api_name=name,
                supports_reasoning=True,
                extra_params=None,
                supports_images=name not in TEXT_ONLY_MODELS,
            )
            for name in names
        },
        roles={
            (AIMode.entertainment, AIModelPurpose.chatbot): (
                _role(ENTERTAINMENT_CHATBOT),
                _role(ENTERTAINMENT_CHATBOT_BACKUP, priority=1),
            ),
            (AIMode.moderation, AIModelPurpose.translation): (_role(MODERATION_TRANSLATION),),
            (AIMode.moderation, AIModelPurpose.filters): (_role(MODERATION_FILTERS),),
            (AIMode.support, AIModelPurpose.filters): (_role(SUPPORT_FILTERS),),
            (AIMode.support, AIModelPurpose.summary): (_role(SUMMARY),),
        },
    )


def _patch_model_builder(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Make get_ai_model return a distinct sentinel per model name, without building a real client."""
    built: dict[str, object] = {}

    def fake_get_ai_model(model_name: str, reasoning_effort: str | None = None) -> object:
        return built.setdefault(model_name, object())

    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_model_factory.get_ai_model", fake_get_ai_model)
    # Both the async accessor ``resolve_roles`` goes through and the cached snapshot the factory
    # reads a pinned model's capabilities from.
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_catalog.get_catalog", AsyncMock(return_value=_catalog()))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_model_factory.catalog", _catalog)
    return built


async def test_override_flag_leads_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned model still runs first — what it gains is the mode's own chain behind it."""
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value="custom/model"))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.entertainment)
    )

    plan = await get_chat_default_model_plan(PydanticObjectId(), chat_tid=-100123)

    assert plan.primary is built["custom/model"]
    assert plan.model_names == ("custom/model", ENTERTAINMENT_CHATBOT, ENTERTAINMENT_CHATBOT_BACKUP)


async def test_override_for_a_purpose_the_mode_does_not_serve_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin is a complete answer: only a purpose with neither a pin nor a catalog model may crash."""
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value="custom/model"))

    plan = await get_chat_filters_model_plan(PydanticObjectId(), mode=AIMode.entertainment)

    assert plan.model_names == ("custom/model",)
    assert plan.primary is built["custom/model"]


async def test_an_override_naming_a_catalog_model_is_not_listed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_model_builder(monkeypatch)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=ENTERTAINMENT_CHATBOT_BACKUP)
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.entertainment)
    )

    plan = await get_chat_default_model_plan(PydanticObjectId())

    assert plan.model_names == (ENTERTAINMENT_CHATBOT_BACKUP, ENTERTAINMENT_CHATBOT)


async def test_chatbot_plan_follows_the_chat_mode_in_priority_order(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.entertainment)
    )

    plan = await get_chat_default_model_plan(PydanticObjectId())

    assert plan.primary is built[ENTERTAINMENT_CHATBOT]
    assert plan.model_names == (ENTERTAINMENT_CHATBOT, ENTERTAINMENT_CHATBOT_BACKUP)


async def test_an_image_turn_skips_the_candidate_that_cannot_see_one(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.entertainment)
    )

    plan = await get_chat_default_model_plan(PydanticObjectId())

    assert plan.models(has_images=True) == (built[ENTERTAINMENT_CHATBOT_BACKUP],)
    assert plan.models(has_images=False) == (built[ENTERTAINMENT_CHATBOT], built[ENTERTAINMENT_CHATBOT_BACKUP])


async def test_translations_and_filters_follow_the_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.moderation)
    )
    chat_iid = PydanticObjectId()

    translations = await get_chat_translations_model_plan(chat_iid)
    filters = await get_chat_filters_model_plan(chat_iid)

    assert translations.primary is built[MODERATION_TRANSLATION]
    assert filters.primary is built[MODERATION_FILTERS]


async def test_filters_model_without_a_chat_uses_the_support_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    plan = await get_chat_filters_model_plan(None)

    assert plan.primary is built[SUPPORT_FILTERS]


async def test_summary_model_follows_the_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.support)
    )

    plan = await get_chat_summary_model_plan(PydanticObjectId())

    assert plan.primary is built[SUMMARY]


async def test_a_mode_without_a_role_for_a_purpose_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolution is strict: entertainment has no filters model here, so it must raise, not fall back."""
    _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    with pytest.raises(ValueError, match="entertainment:filters"):
        await get_chat_filters_model_plan(PydanticObjectId(), mode=AIMode.entertainment)


def test_disabled_mode_grants_nothing() -> None:
    capabilities = get_capabilities(AIMode.disabled)

    assert not capabilities.ai_enabled
    assert not any(
        (
            capabilities.chatbot_for_users,
            capabilities.trigger_on_reply,
            capabilities.proactive_replies,
            capabilities.notes_read,
            capabilities.memory,
            capabilities.moderator,
            capabilities.message_cache,
        )
    )


def test_moderation_mode_keeps_no_message_history() -> None:
    capabilities = get_capabilities(AIMode.moderation)

    assert capabilities.moderator
    assert not capabilities.message_cache
    assert not capabilities.chatbot_for_users
    assert not capabilities.trigger_on_reply


def test_no_mode_lets_the_agent_write_notes() -> None:
    """Notes are read-only in every mode; the save/delete tools no longer exist."""
    from sophie_bot.modules.ai.utils.chatbot_agent import CHATBOT_TOOLS

    assert {tool.name for tool in CHATBOT_TOOLS}.isdisjoint({"save_note", "delete_note"})


class _FakeState:
    """Stands in for FSMContext: only get_data/update_data are used for the help-mode flag."""

    def __init__(self, data: dict | None = None) -> None:
        self._data = data or {}

    async def get_data(self) -> dict:
        return self._data

    async def update_data(self, data: dict) -> dict:
        self._data.update(data)
        return self._data


def _private_chat() -> SimpleNamespace:
    return SimpleNamespace(type=ChatType.private, tid=1, iid=PydanticObjectId())


async def test_private_chats_use_the_pm_assistant() -> None:
    assert await resolve_chat_mode(_private_chat(), _FakeState()) == AIMode.sophie_pm


async def test_private_chats_without_a_session_use_the_pm_assistant() -> None:
    """Schedulers and other background work have no FSM state."""
    assert await resolve_chat_mode(_private_chat(), None) == AIMode.sophie_pm


async def test_sophie_help_lives_in_the_fsm_session() -> None:
    state = _FakeState()

    await set_help_mode(state, True)
    assert await resolve_chat_mode(_private_chat(), state) == AIMode.sophie_help

    await set_help_mode(state, False)
    assert await resolve_chat_mode(_private_chat(), state) == AIMode.sophie_pm


async def test_leaving_the_ai_mode_leaves_sophie_help_behind() -> None:
    """AiPmStop clears the whole state, which is where the help flag lives."""
    state = _FakeState()
    await set_help_mode(state, True)

    cleared = _FakeState()

    assert await resolve_chat_mode(_private_chat(), cleared) == AIMode.sophie_pm


def test_hidden_modes_are_not_offered_by_aimode() -> None:
    assert AIMode.sophie_pm not in SELECTABLE_MODES
    assert AIMode.sophie_help not in SELECTABLE_MODES


def test_every_mode_has_capabilities() -> None:
    """A mode with no entry would raise a KeyError on the first message in that chat."""
    for mode in AIMode:
        assert get_capabilities(mode) is not None


async def test_sophie_help_uses_its_own_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = {"ai_chatbot_system_prompt": "general prompt", "ai_help_system_prompt": "sophie help prompt"}

    async def fake_get_value(feature: str, chat_tid: int | None = None) -> object:
        return prompts.get(feature, "")

    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_context.get_value", AsyncMock(side_effect=fake_get_value))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_context.is_enabled", AsyncMock(return_value=False))
    context = SimpleNamespace(
        chat_tid=-100123,
        chat_iid=PydanticObjectId(),
        user_text=None,
        mode=AIMode.sophie_help,
        connection=SimpleNamespace(db_model=SimpleNamespace()),
    )

    instructions = await build_chatbot_instructions(context)

    assert "sophie help prompt" in instructions
    assert "general prompt" not in instructions


def test_help_tool_lists_wiki_pages() -> None:
    """The tool advertises pages by slug, so the model can ask for one by name."""
    pages = get_wiki_pages()

    assert "saveables" in pages
    # Contributor docs are excluded: they cannot help a user asking how to use the bot.
    assert not any("Development" in path.parts for path in pages.values())


def test_reading_an_unknown_wiki_page_returns_nothing() -> None:
    assert read_wiki_page("does-not-exist") is None
