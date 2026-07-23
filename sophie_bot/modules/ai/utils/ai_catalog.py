from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, TypeVar

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

# The tier every mode falls back to when it has no model for a purpose, or the model is unusable.
FALLBACK_MODE = AIMode.support


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


@dataclass(frozen=True, slots=True)
class ResolvedRole:
    model_name: str
    service_tier: str | None
    reasoning_effort: str | None


@dataclass(frozen=True, slots=True)
class AICatalog:
    version: str = ""
    providers: Mapping[str, CatalogProvider] = field(default_factory=dict)
    models: Mapping[str, CatalogModel] = field(default_factory=dict)
    # (mode, purpose) -> the role serving it. ``mode`` is None for purposes that are not per-chat.
    roles: Mapping[tuple[AIMode | None, AIModelPurpose], ResolvedRole] = field(default_factory=dict)

    def role_for(self, mode: AIMode | None, purpose: AIModelPurpose) -> ResolvedRole | None:
        return self.roles.get((mode, purpose))

    def model_name_for(self, mode: AIMode | None, purpose: AIModelPurpose) -> str | None:
        role = self.roles.get((mode, purpose))
        return role.model_name if role else None


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


DocumentT = TypeVar("DocumentT", bound=Document)


async def load_documents(document_type: type[DocumentT], query: dict | None = None) -> list[DocumentT]:
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
    roles: dict[tuple[AIMode | None, AIModelPurpose], ResolvedRole] = {}
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
        )
        for role in stored_model.roles:
            roles[(role.mode, role.purpose)] = ResolvedRole(
                model_name=stored_model.name,
                service_tier=role.service_tier,
                reasoning_effort=role.reasoning_effort,
            )

    _catalog = AICatalog(version=await _current_version(), providers=providers, models=models, roles=roles)

    log.info("AI catalog loaded", providers=len(providers), models=len(models), roles=len(roles))
    return _catalog


async def get_catalog() -> AICatalog:
    if _catalog.version != await _current_version():
        return await load_catalog()
    return _catalog


def resolve_role_from(
    catalog: AICatalog, mode: AIMode | None, purpose: AIModelPurpose
) -> tuple[ResolvedRole | None, bool]:
    """The role a (mode, purpose) resolves to, and whether it came from the support-tier fallback.

    Priority: the mode's own role, then an any-mode role (``mode=None``) set as a default for every
    mode, then the support tier as a last resort. The middle tier is what makes an ``any:chatbot``
    role apply to every mode rather than doing nothing.
    """
    direct = catalog.role_for(mode, purpose)
    if direct is not None:
        return direct, False

    if mode is not None:
        wildcard = catalog.role_for(None, purpose)
        if wildcard is not None:
            return wildcard, False

    fallback = catalog.role_for(FALLBACK_MODE, purpose)
    return fallback, fallback is not None


def resolve_from(catalog: AICatalog, mode: AIMode | None, purpose: AIModelPurpose) -> tuple[str | None, bool]:
    """The model name a (mode, purpose) resolves to, and whether it is a support-tier fallback."""
    role, is_fallback = resolve_role_from(catalog, mode, purpose)
    return (role.model_name if role else None), is_fallback


async def resolve_role(mode: AIMode | None, purpose: AIModelPurpose) -> ResolvedRole:
    """The role serving a purpose, falling back to an any-mode role then the support tier."""
    current = await get_catalog()
    role, is_fallback = resolve_role_from(current, mode, purpose)
    if role is None:
        raise ValueError(f"No AI model in the catalog serves {purpose.value}")

    if is_fallback:
        log.warning(
            "AI model missing from the catalog, falling back to the support tier",
            fallback=role.model_name,
            mode=mode.value if mode else None,
            purpose=purpose.value,
        )
    return role


async def resolve_model_name(mode: AIMode | None, purpose: AIModelPurpose) -> str:
    """The model name serving a purpose (see ``resolve_role`` for the per-role settings)."""
    current = await get_catalog()
    model_name, is_fallback = resolve_from(current, mode, purpose)
    if not model_name:
        raise ValueError(f"No AI model in the catalog serves {purpose.value}")

    if is_fallback:
        log.warning(
            "AI model missing from the catalog, falling back to the support tier",
            fallback=model_name,
            mode=mode.value if mode else None,
            purpose=purpose.value,
        )
    return model_name
