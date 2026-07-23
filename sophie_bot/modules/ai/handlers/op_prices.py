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
    get_provider_models,
)
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


def _format_price(price: float | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:.2f}/1M"


async def op_ai_prices_handler(message: Message) -> None:
    provider_sections = []

    for provider_name in AI_PROVIDER_TO_NAME:
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
                    "{name}: in {input_price}, out {output_price}{markers}",
                    name=Bold(model.name),
                    input_price=Code(_format_price(input_price)),
                    output_price=Code(_format_price(output_price)),
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
