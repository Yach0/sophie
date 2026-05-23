from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, Section, Template, Title, VList

from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_model_pricing import get_model_pricing
from sophie_bot.modules.ai.utils.ai_model_registry import (
    AI_PROVIDER_TO_NAME,
    AVAILABLE_PROVIDER_NAMES,
    get_provider_models,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


def _format_price(price: float | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:.2f}/1M"


def _format_capabilities(model) -> str:
    capabilities = []
    if model.supports_tools:
        capabilities.append("tools")
    if model.supports_vision:
        capabilities.append("vision")
    if model.supports_translation:
        capabilities.append("translate")
    if model.supports_reasoning:
        capabilities.append("reasoning")
    return ", ".join(capabilities) if capabilities else "basic"


async def op_ai_prices_handler(message: Message) -> None:
    provider_sections = []

    for provider_name in AVAILABLE_PROVIDER_NAMES:
        provider_models = get_provider_models(provider_name)
        if not provider_models:
            continue

        model_lines = []
        for model in provider_models:
            input_price, output_price = await get_model_pricing(model.name)
            markers = []
            if model.default_for_chatbot:
                markers.append(_("default chat"))
            if model.default_for_translation:
                markers.append(_("default translate"))

            model_lines.append(
                Template(
                    "{title}: in {input_price}, out {output_price} [{capabilities}]{markers}",
                    title=Bold(model.title),
                    input_price=Code(_format_price(input_price)),
                    output_price=Code(_format_price(output_price)),
                    capabilities=Code(_format_capabilities(model)),
                    markers=Code(f" ({', '.join(markers)})") if markers else "",
                )
            )

        provider_sections.append(
            Section(
                VList(*model_lines),
                title=AI_PROVIDER_TO_NAME[provider_name],
            )
        )

    doc = Doc(
        Title(f"{AI_EMOJI} {_('AI Prices')}"),
        Template(_("Prices are shown as approximate USD per 1M input/output tokens.")),
        *provider_sections,
    )
    await message.reply(str(doc))


class OpAIPricesHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aiprices"), IsOP(True)

    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, *cls.filters())

    async def handle(self) -> None:
        await op_ai_prices_handler(self.event)
