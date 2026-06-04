from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from ass_tg.types import OneOf
from stfu_tg import Italic, KeyValue, Section, Template

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, GlobalSettings
from sophie_bot.db.models.beta import BetaModeModel, PreferredMode
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

mode_names = {
    "auto": l_("Auto"),
    "stable": l_("Old"),
    "beta": l_("Latest"),
}

preferred_mode_by_user_mode = {
    "auto": PreferredMode.auto,
    "latest": PreferredMode.beta,
    "old": PreferredMode.stable,
    "beta": PreferredMode.beta,
    "stable": PreferredMode.stable,
}


@flags.args(
    new_state=OneOf(("auto", "latest", "old", "beta", "stable"), l_("Preferred strategy mode")),
)
@flags.help(description=l_("Set preferred strategy mode"))
async def set_preferred_mode(message: Message, new_state: str, chat_db: ChatModel) -> None:
    state = preferred_mode_by_user_mode[new_state]

    await BetaModeModel.set_preferred_mode(chat_db.iid, state)

    buttons = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_("Sophie Support"),
                    url=CONFIG.support_link,
                )
            ]
        ]
    )

    await message.reply(
        str(
            Section(
                KeyValue(_("New strategy"), mode_names[state.name]),
                (
                    _("Preferred mode cannot always match the current state due to development and rollout progress.")
                    if state != PreferredMode.auto
                    else None
                ),
                title=_("Preferred mode changed"),
            )
        ),
        reply_markup=buttons,
    )


@flags.help(description=l_("Get current strategy mode / current state"))
async def show_beta_state(message: Message, chat_db: ChatModel) -> None:
    beta_state = await BetaModeModel.get_by_chat_iid(chat_db.iid)

    preferred_mode = PreferredMode(beta_state.preferred_mode) if beta_state else PreferredMode.auto

    gs_beta_db = await GlobalSettings.get_by_key("beta_percentage")
    percentage = int(gs_beta_db.value) if gs_beta_db else 0

    if beta_state and beta_state.mode:
        current_mode_text = mode_names[beta_state.mode.name]
    elif percentage == 0:
        current_mode_text = mode_names[PreferredMode.stable.name]
    else:
        current_mode_text = l_("Unknown")

    await message.reply(
        str(
            Section(
                KeyValue(_("Preferred mode"), mode_names[preferred_mode.name]),
                KeyValue(_("Current mode"), current_mode_text),
                title=_("Mode information"),
            )
            + Template(_("Use '{cmd}' to change it."), cmd=Italic("/setmode (auto / latest / old)")),
        )
    )
