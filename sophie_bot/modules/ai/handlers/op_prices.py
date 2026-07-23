from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, Section, Template, Title, VList

from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_model_pricing import get_model_pricing
from sophie_bot.modules.ai.utils.ai_model_registry import MODE_MODELS, is_model_available
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


def _format_price(price: float | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:.2f}/1M"


async def op_ai_prices_handler(message: Message) -> None:
    mode_sections = []

    for mode, models_by_purpose in MODE_MODELS.items():
        model_lines = []
        for purpose, model_name in models_by_purpose.items():
            input_price, output_price = await get_model_pricing(model_name)
            model_lines.append(
                Template(
                    "{purpose}: {name} — in {input_price}, out {output_price}{unavailable}",
                    purpose=Bold(purpose),
                    name=Code(model_name),
                    input_price=Code(_format_price(input_price)),
                    output_price=Code(_format_price(output_price)),
                    unavailable="" if is_model_available(model_name) else Code(_(" (provider not configured)")),
                )
            )

        mode_sections.append(Section(VList(*model_lines), title=mode.value))

    doc = Doc(
        Title(f"{AI_EMOJI} {_('AI Prices')}"),
        Template(_("Prices are shown as approximate USD per 1M input/output tokens.")),
        *mode_sections,
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
