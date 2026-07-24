from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, Section, Template, Title, VList

from sophie_bot.constants import AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_catalog import get_catalog
from sophie_bot.modules.ai.utils.ai_model_pricing import get_model_pricing
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


def _format_price(price: float | None) -> str:
    if price is None:
        return "N/A"
    return f"${price:.2f}/1M"


async def op_ai_prices_handler(message: Message) -> None:
    catalog = await get_catalog()

    model_lines = []
    for model_name in sorted(catalog.models):
        input_price, output_price = await get_model_pricing(model_name)
        roles = sorted(
            f"{mode.value if mode else 'any'}:{purpose.value}"
            for (mode, purpose), name in catalog.roles.items()
            if name == model_name
        )
        model_lines.append(
            Template(
                "{name}: in {input_price}, out {output_price}{roles}",
                name=Bold(model_name),
                input_price=Code(_format_price(input_price)),
                output_price=Code(_format_price(output_price)),
                roles=Code(f" ({', '.join(roles)})") if roles else "",
            )
        )

    doc = Doc(
        Title(f"{AI_EMOJI} {_('AI Prices')}"),
        Template(_("Prices are shown as approximate USD per 1M input/output tokens.")),
        Section(VList(*model_lines) if model_lines else _("The AI catalog is empty."), title=_("Models")),
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
