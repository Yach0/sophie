from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from ass_tg.types import IntArg, KeyValueArg, KeyValuesArg, OneOf, OptionalArg
from ass_tg.types.base_abc import ArgFabric, ParsedArg
from stfu_tg import Code, Italic, KeyValue, Section, Template

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, GlobalSettings
from sophie_bot.db.models.beta import BetaModeModel, PreferredMode
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_CURRENT_CHAT_SENTINEL = object()
_CHAT_OPTION = "chat"

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


class _ChatKeyValue(IntArg):
    def __init__(self) -> None:
        super().__init__()
        self.default_no_value_value = _CURRENT_CHAT_SENTINEL


def _extract_option_value(options: object, option: str) -> object | None:
    if not isinstance(options, Mapping):
        return None
    option_values = cast(Mapping[str, object], options)
    parsed_value = option_values.get(option)
    if parsed_value is None:
        return None
    if isinstance(parsed_value, ParsedArg):
        return parsed_value.get_value()
    return parsed_value


def _extract_chat_tid(chat_value: object, current_chat_tid: int) -> int:
    if chat_value is None:
        return current_chat_tid
    parsed_value = getattr(chat_value, "value", chat_value)
    if parsed_value is _CURRENT_CHAT_SENTINEL:
        return current_chat_tid
    return cast(int, parsed_value)


class SetModeHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_setmode"), IsOP(True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {
            "options": OptionalArg(KeyValuesArg(KeyValueArg(_CHAT_OPTION, _ChatKeyValue()))),
            "new_state": OptionalArg(
                OneOf(tuple(preferred_mode_by_user_mode), l_("Preferred strategy mode")),
            ),
        }

    async def handle(self) -> Any:
        options = self.data.get("options")
        chat_arg = _extract_option_value(options, _CHAT_OPTION)
        chat_tid = _extract_chat_tid(chat_arg, self.event.chat.id)
        new_state: str | None = self.data.get("new_state")

        chat_db = await ChatModel.get_by_tid(chat_tid)
        if chat_db is None:
            return await self.event.reply(Template(_("Chat {chat} not found."), chat=Code(chat_tid)).to_html())

        if new_state is None:
            return await self._show_state(chat_db, chat_tid)

        return await self._set_state(chat_db, chat_tid, new_state)

    async def _show_state(self, chat_db: ChatModel, chat_tid: int) -> Any:
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

        return await self.event.reply(
            str(
                Section(
                    KeyValue(_("Chat"), chat_tid),
                    KeyValue(_("Preferred mode"), mode_names[preferred_mode.name]),
                    KeyValue(_("Current mode"), current_mode_text),
                    title=_("Mode information"),
                )
                + Template(
                    _("Use '{cmd}' to change it."),
                    cmd=Italic("/op_setmode [^chat=<chat_id>] (auto / latest / old)"),
                ),
            )
        )

    async def _set_state(self, chat_db: ChatModel, chat_tid: int, new_state: str) -> Any:
        state = preferred_mode_by_user_mode[new_state]

        await BetaModeModel.set_preferred_mode(chat_db.iid, state)

        mismatch_note = (
            _("Preferred mode cannot always match the current state due to development and rollout progress.")
            if state != PreferredMode.auto
            else None
        )

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

        return await self.event.reply(
            str(
                Section(
                    KeyValue(_("Chat"), chat_tid),
                    KeyValue(_("New strategy"), mode_names[state.name]),
                    mismatch_note,
                    title=_("Preferred mode changed"),
                )
            ),
            reply_markup=buttons,
        )
