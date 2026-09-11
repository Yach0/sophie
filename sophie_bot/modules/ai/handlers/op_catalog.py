from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import BooleanArg, IntArg, KeyValueArg, KeyValuesArg, OptionalArg, WordArg
from ass_tg.types.base_abc import ArgFabric, ParsedArg
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
from sophie_bot.modules.ai.utils.ai_catalog import bump_version, get_catalog, mask_api_key
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
_IMAGES_OPTION = "images"
_ROLE_OPTION = "role"
_UNROLE_OPTION = "unrole"
_PRIORITY_OPTION = "priority"


def _option[OptionT](options: object, name: str, expected_type: type[OptionT]) -> OptionT | None:
    if not isinstance(options, Mapping):
        return None
    value = options.get(name)
    resolved = value.get_value() if isinstance(value, ParsedArg) else value
    if resolved is None:
        return None
    if not isinstance(resolved, expected_type):
        raise TypeError(f"Option {name!r} must be {expected_type.__name__}, got {type(resolved).__name__}")
    return resolved


def _parse_role(raw_role: str, priority: int = 0) -> AIModelRole:
    """``<mode>:<purpose>`` or ``<purpose>`` for purposes that are not per-chat."""
    mode_name, _, purpose_name = raw_role.rpartition(":")
    purpose = AIModelPurpose(purpose_name)
    return AIModelRole(mode=AIMode(mode_name) if mode_name else None, purpose=purpose, priority=priority)


def _format_role(role: AIModelRole) -> str:
    name = f"{role.mode.value if role.mode else 'any'}:{role.purpose.value}"
    # Priority is only worth the noise once it is not the default, which is the single-model case.
    return f"{name}#{role.priority}" if role.priority else name


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
            Code("/op_aimodel <name> ^unrole=<role> ^reasoning=<yes/no> ^images=<yes/no>"),
            Code("/op_aimodel <name> ^role=<role> ^priority=<number>"),
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
            _(
                "Several models can share one role: they are tried best-first, ordered by "
                "^priority (lower runs earlier), and the next one takes over when the current "
                "one fails or answers with nothing."
            ),
            Template(
                _("A model that cannot be shown an image is skipped for image messages: set {option}."),
                option=Code("^images=no"),
            ),
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


@flags.handler_help(description=l_("Create or update an AI provider (private chat only)"))
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

        if _option(options, _DELETE_OPTION, bool):
            if not provider:
                return await self.event.answer(str(Template(_("No provider named {name}."), name=Code(name))))
            await provider.delete()
            await bump_version(redis=self.services.redis)
            return await self.event.answer(str(Template(_("Provider {name} deleted."), name=Code(name))))

        if not provider:
            provider = AICatalogProviderModel(name=name)

        if (kind := _option(options, _KIND_OPTION, str)) is not None:
            provider.kind = AIProviderKind(kind)
        if (base_url := _option(options, _BASE_URL_OPTION, str)) is not None:
            provider.base_url = base_url
        if (api_key := _option(options, _KEY_OPTION, str)) is not None:
            provider.api_key = api_key
        if (enabled := _option(options, _ENABLED_OPTION, bool)) is not None:
            provider.enabled = enabled

        await provider.save()
        await bump_version(redis=self.services.redis)

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
        catalog = await get_catalog(redis=self.services.redis)
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


@flags.handler_help(description=l_("Create or update an AI model"))
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
                    KeyValueArg(_IMAGES_OPTION, BooleanArg()),
                    KeyValueArg(_ENABLED_OPTION, BooleanArg()),
                    KeyValueArg(_ROLE_OPTION, WordArg()),
                    KeyValueArg(_UNROLE_OPTION, WordArg()),
                    KeyValueArg(_PRIORITY_OPTION, IntArg()),
                )
            ),
            "name": WordArg(l_("Model name")),
        }

    async def handle(self) -> Any:
        name: str = self.data["name"]
        options = self.data.get("options")
        stored_model = await AICatalogModelModel.find_one(AICatalogModelModel.name == name)

        if _option(options, _DELETE_OPTION, bool):
            if not stored_model:
                return await self.event.reply(str(Template(_("No model named {name}."), name=Code(name))))
            await stored_model.delete()
            await bump_version(redis=self.services.redis)
            return await self.event.reply(str(Template(_("Model {name} deleted."), name=Code(name))))

        raw_role = _option(options, _ROLE_OPTION, str)
        priority = _option(options, _PRIORITY_OPTION, int)
        # Priority orders the models inside one role, so on its own it has nothing to apply to.
        # Silently dropping it would leave an operator believing they had reordered a chain.
        if priority is not None and raw_role is None:
            return await self.event.reply(str(_("^priority only applies together with ^role.")))

        provider_name = _option(options, _PROVIDER_OPTION, str)
        if not stored_model:
            if provider_name is None:
                return await self.event.reply(str(_("A new model needs ^provider=<name>.")))
            stored_model = AICatalogModelModel(name=name, provider=provider_name)
        elif provider_name is not None:
            stored_model.provider = provider_name

        if (api_name := _option(options, _API_NAME_OPTION, str)) is not None:
            stored_model.api_name = api_name
        if (reasoning := _option(options, _REASONING_OPTION, bool)) is not None:
            stored_model.supports_reasoning = reasoning
        if (images := _option(options, _IMAGES_OPTION, bool)) is not None:
            stored_model.supports_images = images
        if (enabled := _option(options, _ENABLED_OPTION, bool)) is not None:
            stored_model.enabled = enabled

        if raw_role is not None:
            role = _parse_role(raw_role, priority if priority is not None else 0)
            # Matched on (mode, purpose) rather than on the whole role, so re-assigning a role this
            # model already has updates its priority instead of listing it twice.
            stored_model.roles = [
                existing
                for existing in stored_model.roles
                if (existing.mode, existing.purpose) != (role.mode, role.purpose)
            ] + [role]
        if (raw_unrole := _option(options, _UNROLE_OPTION, str)) is not None:
            unrole = _parse_role(raw_unrole)
            stored_model.roles = [
                existing
                for existing in stored_model.roles
                if (existing.mode, existing.purpose) != (unrole.mode, unrole.purpose)
            ]

        await stored_model.save()
        await bump_version(redis=self.services.redis)

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
