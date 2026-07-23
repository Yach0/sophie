from __future__ import annotations

from typing import Any, Mapping, cast

from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import BooleanArg, KeyValueArg, KeyValuesArg, OptionalArg, WordArg
from ass_tg.types.base_abc import ArgFabric, ParsedArg
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, Section, Template, Title, VList

from sophie_bot.constants import AI_EMOJI
from sophie_bot.db.models.ai.ai_catalog import (
    AICatalogModelModel,
    AICatalogProviderModel,
    AIModelPurpose,
    AIModelRole,
    AIProviderKind,
)
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, get_catalog
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_DELETE_OPTION = "delete"
_KIND_OPTION = "kind"
_BASE_URL_OPTION = "base_url"
_KEY_OPTION = "key"
_ENABLED_OPTION = "enabled"
_PROVIDER_OPTION = "provider"
_API_NAME_OPTION = "api_name"
_REASONING_OPTION = "reasoning"
_ROLE_OPTION = "role"
_UNROLE_OPTION = "unrole"


def mask_api_key(api_key: str) -> str:
    """Never render a key in full: operators only need to tell two keys apart."""
    if not api_key:
        return "unset"
    if len(api_key) <= 8:
        return "…"
    return f"{api_key[:3]}…{api_key[-4:]}"


def _option(options: object, name: str) -> object | None:
    if not isinstance(options, Mapping):
        return None
    value = cast(Mapping[str, object], options).get(name)
    if isinstance(value, ParsedArg):
        return value.get_value()
    return value


def _parse_role(raw_role: str) -> AIModelRole:
    """``<mode>:<purpose>`` or ``<purpose>`` for purposes that are not per-chat."""
    mode_name, _, purpose_name = raw_role.rpartition(":")
    purpose = AIModelPurpose(purpose_name)
    return AIModelRole(mode=AIMode(mode_name) if mode_name else None, purpose=purpose)


def _format_role(role: AIModelRole) -> str:
    return f"{role.mode.value if role.mode else 'any'}:{role.purpose.value}"


def _provider_usage() -> Section:
    return Section(
        VList(
            Code("/op_aiprovider <name> ^kind=<kind> ^base_url=<url> ^key=<api key> ^enabled=<yes/no>"),
            Code("/op_aiprovider <name> ^delete=yes"),
            Template(_("Kinds: {kinds}"), kinds=Code(", ".join(kind.value for kind in AIProviderKind))),
            _("Only the given options change; the rest keep their current values."),
            _("A key can only be set in a private chat, and that message is deleted right away."),
        ),
        title=_("Usage"),
    )


def _model_usage() -> Section:
    return Section(
        VList(
            Code("/op_aimodel <name> ^provider=<name> ^api_name=<upstream name> ^role=<role> ^enabled=<yes/no>"),
            Code("/op_aimodel <name> ^unrole=<role> ^reasoning=<yes/no>"),
            Code("/op_aimodel <name> ^delete=yes"),
            Template(
                _("Roles: {modes} paired with {purposes}, e.g. {example}"),
                modes=Code(", ".join(mode.value for mode in AIMode if mode is not AIMode.disabled)),
                purposes=Code(", ".join(purpose.value for purpose in AIModelPurpose)),
                example=Code("^role=support:chatbot"),
            ),
            Template(
                _("Drop the mode for purposes that are not per-chat, e.g. {example}"),
                example=Code("^role=summary"),
            ),
            _("A mode with no model for a purpose falls back to the support one."),
            Template(
                _("The upstream name defaults to the model name; set {option} when they differ."),
                option=Code("^api_name"),
            ),
        ),
        title=_("Usage"),
    )


class OpAIProviders(SophieMessageHandler):
    """List the AI providers in the catalog, with masked keys."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aiproviders"), IsOP(True)

    async def handle(self) -> Any:
        lines = [
            Template(
                "{name} [{kind}]: {base_url}, key {key}{disabled}",
                name=Bold(provider.name),
                kind=Code(provider.kind.value),
                base_url=Code(provider.base_url or "default"),
                key=Code(mask_api_key(provider.api_key)),
                disabled="" if provider.enabled else Code(_(" (disabled)")),
            )
            async for provider in AICatalogProviderModel.find_all()
        ]
        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Providers')}"),
            Section(VList(*lines) if lines else _("No providers are configured."), title=_("Providers")),
            _provider_usage(),
        )
        await self.event.reply(str(doc))


@flags.help(description=l_("Create or update an AI provider (private chat only)"))
class OpAIProvider(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        # API keys are passed in the command text, so this is refused outside a private chat.
        return CMDFilter("op_aiprovider"), IsOP(True), ChatTypeFilter("private")

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {
            "options": OptionalArg(
                KeyValuesArg(
                    KeyValueArg(_DELETE_OPTION, BooleanArg()),
                    KeyValueArg(_KIND_OPTION, WordArg()),
                    KeyValueArg(_BASE_URL_OPTION, WordArg()),
                    KeyValueArg(_KEY_OPTION, WordArg()),
                    KeyValueArg(_ENABLED_OPTION, BooleanArg()),
                )
            ),
            "name": WordArg(l_("Provider name")),
        }

    async def handle(self) -> Any:
        # The message carries an API key; drop it from history as soon as it is parsed.
        await common_try(self.event.delete())

        name: str = self.data["name"]
        options = self.data.get("options")
        provider = await AICatalogProviderModel.find_one(AICatalogProviderModel.name == name)

        if _option(options, _DELETE_OPTION):
            if not provider:
                return await self.event.answer(str(Template(_("No provider named {name}."), name=Code(name))))
            await provider.delete()
            await bump_version()
            return await self.event.answer(str(Template(_("Provider {name} deleted."), name=Code(name))))

        if not provider:
            provider = AICatalogProviderModel(name=name)

        if (kind := _option(options, _KIND_OPTION)) is not None:
            provider.kind = AIProviderKind(str(kind))
        if (base_url := _option(options, _BASE_URL_OPTION)) is not None:
            provider.base_url = str(base_url)
        if (api_key := _option(options, _KEY_OPTION)) is not None:
            provider.api_key = str(api_key)
        if (enabled := _option(options, _ENABLED_OPTION)) is not None:
            provider.enabled = bool(enabled)

        await provider.save()
        await bump_version()

        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Provider saved')}"),
            Template(
                "{name} [{kind}]: {base_url}, key {key}",
                name=Bold(provider.name),
                kind=Code(provider.kind.value),
                base_url=Code(provider.base_url or "default"),
                key=Code(mask_api_key(provider.api_key)),
            ),
        )
        return await self.event.answer(str(doc))


class OpAIModels(SophieMessageHandler):
    """List the AI models in the catalog and what each one is used for."""

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aimodels"), IsOP(True)

    async def handle(self) -> Any:
        lines = [
            Template(
                "{name} @ {provider}{roles}{disabled}",
                name=Bold(stored_model.name),
                provider=Code(stored_model.provider),
                roles=Code(f" ({', '.join(_format_role(role) for role in stored_model.roles)})")
                if stored_model.roles
                else "",
                disabled="" if stored_model.enabled else Code(_(" (disabled)")),
            )
            async for stored_model in AICatalogModelModel.find_all()
        ]
        catalog = await get_catalog()
        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Models')}"),
            Section(VList(*lines) if lines else _("No models are configured."), title=_("Models")),
            Template(
                _("Loaded: {models} models, {providers} providers"),
                models=len(catalog.models),
                providers=len(catalog.providers),
            ),
            _model_usage(),
        )
        await self.event.reply(str(doc))


@flags.help(description=l_("Create or update an AI model"))
class OpAIModel(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aimodel"), IsOP(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {
            "options": OptionalArg(
                KeyValuesArg(
                    KeyValueArg(_DELETE_OPTION, BooleanArg()),
                    KeyValueArg(_PROVIDER_OPTION, WordArg()),
                    KeyValueArg(_API_NAME_OPTION, WordArg()),
                    KeyValueArg(_REASONING_OPTION, BooleanArg()),
                    KeyValueArg(_ENABLED_OPTION, BooleanArg()),
                    KeyValueArg(_ROLE_OPTION, WordArg()),
                    KeyValueArg(_UNROLE_OPTION, WordArg()),
                )
            ),
            "name": WordArg(l_("Model name")),
        }

    async def handle(self) -> Any:
        name: str = self.data["name"]
        options = self.data.get("options")
        stored_model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)

        if _option(options, _DELETE_OPTION):
            if not stored_model:
                return await self.event.reply(str(Template(_("No model named {name}."), name=Code(name))))
            await stored_model.delete()
            await bump_version()
            return await self.event.reply(str(Template(_("Model {name} deleted."), name=Code(name))))

        provider_name = _option(options, _PROVIDER_OPTION)
        if not stored_model:
            if provider_name is None:
                return await self.event.reply(str(_("A new model needs ^provider=<name>.")))
            stored_model = AICatalogModelModel(name=name, provider=str(provider_name))
        elif provider_name is not None:
            stored_model.provider = str(provider_name)

        if (api_name := _option(options, _API_NAME_OPTION)) is not None:
            stored_model.api_name = str(api_name)
        if (reasoning := _option(options, _REASONING_OPTION)) is not None:
            stored_model.supports_reasoning = bool(reasoning)
        if (enabled := _option(options, _ENABLED_OPTION)) is not None:
            stored_model.enabled = bool(enabled)

        if (raw_role := _option(options, _ROLE_OPTION)) is not None:
            role = _parse_role(str(raw_role))
            stored_model.roles = [existing for existing in stored_model.roles if existing != role] + [role]
        if (raw_unrole := _option(options, _UNROLE_OPTION)) is not None:
            unrole = _parse_role(str(raw_unrole))
            stored_model.roles = [existing for existing in stored_model.roles if existing != unrole]

        await stored_model.save()
        await bump_version()

        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Model saved')}"),
            Template(
                "{name} @ {provider}{roles}",
                name=Bold(stored_model.name),
                provider=Code(stored_model.provider),
                roles=Code(f" ({', '.join(_format_role(role) for role in stored_model.roles)})")
                if stored_model.roles
                else "",
            ),
        )
        return await self.event.reply(str(doc))
