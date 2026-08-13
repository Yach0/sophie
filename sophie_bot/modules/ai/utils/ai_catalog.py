from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from beanie import Document
from beanie.exceptions import CollectionWasNotInitialized
from pydantic import ValidationError

from sophie_bot.db.models.ai.ai_catalog import (
    AICatalogModelModel,
    AICatalogProviderModel,
    AIModelPurpose,
    AIProviderKind,
)
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

# Bumped on every catalog mutation. Processes compare it against the version their snapshot was
# built from, so an operator change reaches the bot, scheduler and API without a restart.
_VERSION_KEY = "sophie:ai:catalog_version"

# Which purposes each mode can actually use — a group mode never needs a private-chat model, a
# private-chat mode has no group to moderate or summarise, and only the help mode inspects Sophie's
# own source. This is the single source of truth for both resolution and the operator panel: a
# (mode, purpose) outside this map is not a real combination, so it is never resolved, never shown
# as a table cell, and never offered as a role to assign.
MODE_PURPOSES: dict[AIMode, frozenset[AIModelPurpose]] = {
    AIMode.entertainment: frozenset(
        {
            AIModelPurpose.chatbot,
            AIModelPurpose.translation,
            AIModelPurpose.filters,
            AIModelPurpose.summary,
            AIModelPurpose.research,
        }
    ),
    AIMode.moderation: frozenset(
        {
            AIModelPurpose.chatbot,
            AIModelPurpose.translation,
            AIModelPurpose.filters,
            AIModelPurpose.moderation_reason,
            AIModelPurpose.research,
        }
    ),
    AIMode.support: frozenset(
        {
            AIModelPurpose.chatbot,
            AIModelPurpose.translation,
            AIModelPurpose.filters,
            AIModelPurpose.summary,
            AIModelPurpose.moderation_reason,
            AIModelPurpose.research,
        }
    ),
    AIMode.sophie_pm: frozenset(
        {
            AIModelPurpose.chatbot,
            AIModelPurpose.translation,
            AIModelPurpose.research,
        }
    ),
    AIMode.sophie_help: frozenset({AIModelPurpose.chatbot, AIModelPurpose.sophie_inspect}),
}

# The modes that resolve catalog models, in display order. Excludes ``disabled`` (runs no AI).
CATALOG_MODES: tuple[AIMode, ...] = tuple(MODE_PURPOSES)


def mode_allows(mode: AIMode, purpose: AIModelPurpose) -> bool:
    return purpose in MODE_PURPOSES.get(mode, frozenset())


@dataclass(frozen=True, slots=True)
class CatalogProvider:
    name: str
    kind: AIProviderKind
    base_url: str | None
    api_key: str


@dataclass(frozen=True, slots=True)
class CatalogModel:
    name: str
    provider: CatalogProvider
    api_name: str
    supports_reasoning: bool
    extra_params: dict[str, object] | None
    supports_images: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedRole:
    model_name: str
    service_tier: str | None
    reasoning_effort: str | None
    # Copied off the model the role belongs to, so a candidate carries everything the runtime needs
    # to decide whether it may serve a given request without reaching back into the catalog.
    supports_images: bool = True
    priority: int = 0


@dataclass(frozen=True, slots=True)
class AICatalog:
    version: str = ""
    providers: Mapping[str, CatalogProvider] = field(default_factory=dict)
    models: Mapping[str, CatalogModel] = field(default_factory=dict)
    # (mode, purpose) -> the ordered candidates serving it, best first. One entry is the common case;
    # more make a failover chain.
    roles: Mapping[tuple[AIMode, AIModelPurpose], tuple[ResolvedRole, ...]] = field(default_factory=dict)

    def roles_for(self, mode: AIMode, purpose: AIModelPurpose) -> tuple[ResolvedRole, ...]:
        return self.roles.get((mode, purpose), ())

    def role_for(self, mode: AIMode, purpose: AIModelPurpose) -> ResolvedRole | None:
        """The highest-priority candidate, for the callers that only need one model."""
        candidates = self.roles_for(mode, purpose)
        return candidates[0] if candidates else None

    def model_name_for(self, mode: AIMode, purpose: AIModelPurpose) -> str | None:
        role = self.role_for(mode, purpose)
        return role.model_name if role else None

    def model_names_for(self, mode: AIMode, purpose: AIModelPurpose) -> tuple[str, ...]:
        return tuple(role.model_name for role in self.roles_for(mode, purpose))


_catalog = AICatalog()


def catalog() -> AICatalog:
    """The last loaded snapshot, for synchronous model building. Await ``get_catalog`` to refresh."""
    return _catalog


def mask_api_key(api_key: str) -> str:
    """Never render a key in full: an operator only needs to tell two keys apart."""
    if not api_key:
        return "unset"
    if len(api_key) <= 8:
        return "…"
    return f"{api_key[:3]}…{api_key[-4:]}"


async def _current_version() -> str:
    version = await aredis.get(_VERSION_KEY)
    if version is None:
        return ""
    return version.decode() if isinstance(version, bytes) else str(version)


async def bump_version() -> None:
    """Invalidate every process's snapshot after a catalog mutation."""
    await aredis.incr(_VERSION_KEY)  # ty: ignore[invalid-await]


async def load_documents[DocumentT: Document](
    document_type: type[DocumentT], query: dict | None = None
) -> list[DocumentT]:
    """Parse rows one by one, dropping any the current code cannot read.

    The catalog is edited at runtime and outlives the code that wrote it, so a row left behind by
    an older version — a purpose that has since been renamed, a provider kind that no longer
    exists — must cost that one row, not the bot's ability to start or the panel's ability to list.
    """
    parsed: list[DocumentT] = []
    try:
        collection = document_type.get_pymongo_collection()
    except CollectionWasNotInitialized:
        # Tools that load the modules without a database, such as the wiki generator, get an empty
        # catalog rather than a crash.
        log.warning("AI catalog is unavailable: the database is not initialised")
        return parsed

    async for raw_document in collection.find(query or {}):
        try:
            parsed.append(document_type.model_validate(raw_document))
        except ValidationError as error:
            log.warning(
                "AI catalog row skipped: it does not match the current schema",
                collection=document_type.get_collection_name(),
                name=raw_document.get("name"),
                error=str(error),
            )
    return parsed


async def load_catalog() -> AICatalog:
    global _catalog

    providers = {
        provider.name: CatalogProvider(
            name=provider.name,
            kind=provider.kind,
            base_url=provider.base_url,
            api_key=provider.api_key,
        )
        for provider in await load_documents(AICatalogProviderModel, {"enabled": True})
    }

    models: dict[str, CatalogModel] = {}
    candidates: dict[tuple[AIMode, AIModelPurpose], list[ResolvedRole]] = {}
    for stored_model in await load_documents(AICatalogModelModel, {"enabled": True}):
        provider = providers.get(stored_model.provider)
        if provider is None:
            log.warning(
                "AI catalog model skipped: its provider is missing or disabled",
                model=stored_model.name,
                provider=stored_model.provider,
            )
            continue

        models[stored_model.name] = CatalogModel(
            name=stored_model.name,
            provider=provider,
            api_name=stored_model.api_name or stored_model.name,
            supports_reasoning=stored_model.supports_reasoning,
            extra_params=stored_model.extra_params,
            supports_images=stored_model.supports_images,
        )
        for role in stored_model.roles:
            key = (role.mode, role.purpose)
            role_candidates = candidates.setdefault(key, [])
            # A model listing the same (mode, purpose) twice would otherwise be tried twice in a row,
            # which is never what the second row meant.
            if any(existing.model_name == stored_model.name for existing in role_candidates):
                continue
            role_candidates.append(
                ResolvedRole(
                    model_name=stored_model.name,
                    service_tier=role.service_tier,
                    reasoning_effort=role.reasoning_effort,
                    supports_images=stored_model.supports_images,
                    priority=role.priority,
                )
            )

    # Sorted here rather than at resolution time: the order is a property of the snapshot, and every
    # request would otherwise re-sort the same list. The model name breaks priority ties so the same
    # catalog always yields the same order, whatever order Mongo returned the rows in.
    roles = {
        key: tuple(sorted(role_candidates, key=lambda role: (role.priority, role.model_name)))
        for key, role_candidates in candidates.items()
    }

    _catalog = AICatalog(version=await _current_version(), providers=providers, models=models, roles=roles)

    log.info("AI catalog loaded", providers=len(providers), models=len(models), roles=len(roles))
    return _catalog


async def get_catalog() -> AICatalog:
    if _catalog.version != await _current_version():
        return await load_catalog()
    return _catalog


async def resolve_roles(mode: AIMode, purpose: AIModelPurpose) -> tuple[ResolvedRole, ...]:
    """Every candidate serving a (mode, purpose), best first, or a crash when there are none.

    Resolution is exact: the mode's own roles or nothing. There is no any-mode wildcard and no
    support-tier fallback — an unconfigured combination is an operator mistake, and failing loudly
    beats silently serving a model the operator never chose for that mode.
    """
    candidates = (await get_catalog()).roles_for(mode, purpose)
    if not candidates:
        raise ValueError(f"No AI model in the catalog serves {mode.value}:{purpose.value}")
    return candidates


async def resolve_role(mode: AIMode, purpose: AIModelPurpose) -> ResolvedRole:
    """The highest-priority role serving a (mode, purpose), or a crash."""
    return (await resolve_roles(mode, purpose))[0]


async def resolve_model_name(mode: AIMode, purpose: AIModelPurpose) -> str:
    """The model name serving a (mode, purpose) (see ``resolve_role`` for the per-role settings)."""
    return (await resolve_role(mode, purpose)).model_name
