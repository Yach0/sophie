from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message
from beanie import PydanticObjectId
from bson import ObjectId
from stfu_tg import Button

from sophie_bot.db.models.filters import FilterHandlerType, FilterInSetupType, FiltersModel
from sophie_bot.modules.filters.utils_.filter_handler_rules import validate_filter_handler
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.action_config_wizard.callbacks import ACWCoreCallback
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig, ActionWizardDraft
from sophie_bot.modules.utils_.action_config_wizard.context import get_wizard_state
from sophie_bot.modules.utils_.action_config_wizard.renderer import WizardRenderer
from sophie_bot.utils.i18n import gettext as _


async def start_filter_wizard(handler: Any, draft: ActionWizardDraft, cfg: ActionWizardConfig) -> None:
    """Start the shared wizard using a prepared filter draft."""
    wizard_state = get_wizard_state(handler.data)
    if wizard_state is None:
        return
    chat_iid = handler.connection.db_model.iid
    await wizard_state.start_session(cfg.module_name, chat_iid, draft.to_data())
    document, markup = await WizardRenderer.render_home_page(
        cfg,
        chat_iid=chat_iid,
        draft=draft,
        chat_title=handler.connection.title,
        wizard_state=wizard_state,
    )
    await handler.answer_rich(document, reply_markup=markup)


class FilterWizardContext:
    """Persist a filter only after the shared wizard's final save."""

    async def load(self, chat_iid: PydanticObjectId) -> ActionWizardDraft:
        del chat_iid
        return ActionWizardDraft()

    async def validate(
        self,
        chat_iid: PydanticObjectId,
        draft: ActionWizardDraft,
        event: Message | CallbackQuery | None = None,
        connection: Any = None,
    ) -> None:
        del chat_iid
        handler = draft.metadata.get("handler")
        if not isinstance(handler, str) or not handler:
            raise ValueError(_("Filter handler is missing."))
        if (
            event is not None
            and connection is not None
            and not await validate_filter_handler(event, handler, connection, draft.metadata.get("oid"))
        ):
            raise ValueError(_("Filter handler is invalid."))
        if not draft.actions:
            raise ValueError(_("No actions configured"))

    async def commit(
        self,
        chat_iid: PydanticObjectId,
        draft: ActionWizardDraft,
        event: Message | CallbackQuery | None = None,
        connection: Any = None,
    ) -> None:
        handler = draft.metadata.get("handler")
        if not isinstance(handler, str):
            raise TypeError(_("Filter handler is missing."))
        filter_setup = FilterInSetupType(
            oid=draft.metadata.get("oid"),
            handler=FilterHandlerType(keyword=handler),
            actions=draft.actions,
            silent=bool(draft.metadata.get("silent", False)),
        )
        if filter_setup.oid:
            filter_model = await FiltersModel.find_one(
                FiltersModel.id == ObjectId(filter_setup.oid),
                FiltersModel.chat.id == chat_iid,
            )
            if filter_model is None:
                raise ValueError(_("The filter could not be found."))
            await filter_model.update_fields(filter_setup)
            await filter_model.save()
        else:
            filter_model = filter_setup.to_model(chat_iid)
            await filter_model.save()
        if event is not None and connection is not None and event.from_user:
            await log_event(
                connection.tid,
                event.from_user.id,
                LogEvent.FILTER_SAVED,
                {"keyword": handler},
            )

    def render_details(self, draft: ActionWizardDraft) -> list[tuple[str, str]]:
        handler = draft.metadata.get("handler")
        return [(str(_("Handler")), handler)] if isinstance(handler, str) else []

    def render_controls(self, draft: ActionWizardDraft, callback_prefix: str) -> list[list[Button]]:
        label = _("🔇 Silent mode: On") if draft.metadata.get("silent") else _("🔊 Silent mode: Off")
        return [
            [
                Button(
                    label,
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="context", name="silent").pack(),
                )
            ]
        ]

    def update_control(self, draft: ActionWizardDraft, control_name: str) -> bool:
        if control_name != "silent":
            return False
        draft.metadata["silent"] = not bool(draft.metadata.get("silent", False))
        return True


__all__ = ["FilterWizardContext"]
