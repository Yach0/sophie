from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery
from beanie import PydanticObjectId
from bson import ObjectId
from bson.errors import InvalidId
from stfu_tg import Button, ButtonRow, Buttons, KeyValue, Section

from sophie_bot.constants import FILTER_MAX_ACTIONS
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.utils_.filter_handler_rules import InvalidFilterHandler, validate_filter_handler
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionDraft,
    ActionWizard,
    ActionWizardCallbackHandler,
    ActionWizardInputHandler,
)
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.views import render_home_view
from sophie_bot.modules.utils_.wizard import WizardCallback, WizardFSM, WizardScopeFilter, WizardSession, WizardView
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class FilterDraft(ActionDraft):
    filter_id: str | None = None
    handler: str
    silent: bool = False

    @classmethod
    def from_model(cls, model: FiltersModel) -> FilterDraft:
        return cls(filter_id=str(model.id), handler=model.handler, actions=model.actions, silent=model.silent)


async def _save_filter(
    chat_iid: PydanticObjectId,
    draft: FilterDraft,
    callback_query: CallbackQuery,
    connection: Any,
) -> None:
    filter_id: ObjectId | None = None
    if draft.filter_id is not None:
        try:
            filter_id = ObjectId(draft.filter_id)
        except (InvalidId, TypeError):
            raise ValueError(_("The filter could not be found."))
    await validate_filter_handler(chat_iid, draft.handler, draft.filter_id)
    filter_model: FiltersModel
    if filter_id is not None:
        filter_model = await FiltersModel.find_one(FiltersModel.id == filter_id, FiltersModel.chat.id == chat_iid)
        if filter_model is None:
            raise ValueError(_("The filter could not be found."))
        filter_model.handler = draft.handler
        filter_model.version = 2
        filter_model.action = None
        filter_model.actions = draft.actions
        filter_model.silent = draft.silent
    else:
        filter_model = FiltersModel(
            chat=chat_iid,
            handler=draft.handler,
            version=2,
            action=None,
            actions=draft.actions,
            silent=draft.silent,
        )
    await filter_model.save()
    if callback_query.from_user:
        await log_event(connection.tid, callback_query.from_user.id, LogEvent.FILTER_SAVED, {"keyword": draft.handler})


FILTER_WIZARD_CONFIG = ActionWizardConfig[FilterDraft](
    scope="filter_action",
    title=l_("Filter action configuration"),
    done_message=l_("Filter on {keyword} was saved."),
    max_actions=FILTER_MAX_ACTIONS,
    draft_model=FilterDraft,
    load_draft=None,
    save_draft=_save_filter,
)


class FilterWizard(ActionWizard[FilterDraft]):
    def render_home(self, draft: FilterDraft) -> WizardView:
        header = Section(KeyValue(_("Handler"), draft.handler), title=_("Handler"))
        toggle_text = _("🔇 Silent mode: On") if draft.silent else _("🔊 Silent mode: Off")
        footer = Section(
            Buttons(
                ButtonRow(
                    Button(
                        toggle_text,
                        callback_data=WizardCallback(scope="filter_action", op="toggle", arg="silent").pack(),
                    )
                )
            ),
            title=_("Filter settings"),
        )
        return render_home_view(self.config, draft, header=header, footer=footer)

    async def toggle(self, handler: SophieCallbackQueryHandler, callback: WizardCallback) -> None:
        session = WizardSession(handler.state, self.config.scope)
        if not await session.is_active(handler.connection.db_model.iid):
            await session.clear()
            await handler.event.answer(_("This session has expired. Please run the command again."), show_alert=True)
            return
        if callback.arg != "silent":
            await handler.event.answer(_("Invalid callback data."), show_alert=True)
            return
        draft = self.config.draft_model.model_validate(await session.get_draft() or {})
        draft.silent = not draft.silent
        await session.set_draft(draft.model_dump(mode="json"))
        view = self.render_home(draft)
        await handler.answer_rich(view.doc, reply_markup=view.markup)
        await handler.event.answer()


FILTER_WIZARD = FilterWizard(FILTER_WIZARD_CONFIG)


def _wizard_filters() -> tuple[CallbackType, ...]:
    return (
        FeatureFlagFilter("action_config_wizard"),
        FeatureFlagFilter("filters"),
        UserRestricting(admin=True),
        GroupOrConnectedFilter(),
    )


class FilterWizardCallbackHandler(ActionWizardCallbackHandler):
    wizard = FILTER_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (*_wizard_filters(), WizardCallback.filter((F.scope == "filter_action") & (F.op != "toggle")))


class FilterWizardToggleHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            *_wizard_filters(),
            FeatureFlagFilter("filters_silent_mode"),
            WizardCallback.filter((F.scope == "filter_action") & (F.op == "toggle")),
        )

    async def handle(self) -> None:
        await FILTER_WIZARD.toggle(self, self.data["callback_data"])


class FilterWizardInputHandler(ActionWizardInputHandler):
    wizard = FILTER_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardFSM.interactive_input,
            WizardScopeFilter("filter_action"),
            *_wizard_filters(),
        )


__all__ = [
    "FILTER_WIZARD",
    "FilterDraft",
    "FilterWizard",
    "FilterWizardCallbackHandler",
    "FilterWizardInputHandler",
    "FilterWizardToggleHandler",
    "InvalidFilterHandler",
]
