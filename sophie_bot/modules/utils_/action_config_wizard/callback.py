from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from beanie import PydanticObjectId

from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.modules.utils_.action_config_wizard.helpers import convert_action_data_to_model
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _

from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig, ActionWizardDraft
from .context import get_wizard_state as _get_wizard_state
from .renderer import WizardRenderer
from .state import ActionConfigFSM, WizardState


class _ACWCallbackHandler(SophieCallbackQueryHandler):
    """Dispatch callbacks against the session's one aggregate draft."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> Any:
        callback_query: CallbackQuery = self.event
        data: ACWCoreCallback = self.data["callback_data"]
        dispatch = {
            "add": self._op_add,
            "remove": self._op_remove,
            "configure": self._op_configure,
            "back": self._op_back,
            "show": self._op_show,
            "select": self._op_select,
            "context": self._op_context,
            "done": self._op_done,
            "cancel": self._op_cancel,
        }
        handler = dispatch.get(data.op)
        if handler is None:
            await callback_query.answer(_("Invalid callback data"))
            return
        await handler(callback_query, data)

    async def _get_draft(self, wizard_state: WizardState, chat_iid: PydanticObjectId) -> ActionWizardDraft | None:
        if not await wizard_state.is_active(self.cfg.module_name, chat_iid):
            return None
        raw_draft = await wizard_state.get_draft()
        if raw_draft is None:
            draft = await self.cfg.context.load(chat_iid)
            await wizard_state.set_draft(draft.to_data())
            return draft
        try:
            return ActionWizardDraft.from_data(raw_draft)
        except (TypeError, ValueError):
            return None

    async def _op_add(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        del data
        document, markup = await WizardRenderer.render_add_action_list(
            self.cfg,
            default_action_name=self.cfg.default_action_name,
        )
        await self.answer_rich(document, reply_markup=markup)
        await callback_query.answer()

    async def _op_remove(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name:
            await callback_query.answer(_("Invalid callback data"))
            return
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        draft = await self._get_draft(wizard_state, self.connection.db_model.iid)
        if draft is None or data.name not in draft.actions:
            await callback_query.answer(_("Action not found."))
            return
        draft.remove_action(data.name)
        await wizard_state.set_draft(draft.to_data())
        await callback_query.answer(_("Action removed."))
        await self._show_home(callback_query, draft)

    async def _op_configure(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name or data.name not in ALL_MODERN_ACTIONS:
            await callback_query.answer(_("Invalid action"))
            return
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        draft = await self._get_draft(wizard_state, self.connection.db_model.iid)
        if draft is None or data.name not in draft.actions:
            await callback_query.answer(_("Action not found."))
            return
        await WizardRenderer.send_action_configured(
            callback_query,
            action_name=data.name,
            callback_prefix=self.cfg.callback_prefix,
            success_message=self.cfg.success_message,
            action_data=draft.actions[data.name],
            show_cancel=True,
            show_done=True,
        )

    async def _op_back(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        del data
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return
        if self.cfg.on_back_render is not None:
            await self.cfg.on_back_render(self, callback_query)
            return
        await self._show_home(callback_query)
        await callback_query.answer()

    async def _op_show(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        del data
        await self._show_home(callback_query)

    async def _op_select(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name or data.name not in ALL_MODERN_ACTIONS:
            await callback_query.answer(_("Invalid action"))
            return
        action = ALL_MODERN_ACTIONS[data.name]
        if self.cfg.action_filter is not None and not self.cfg.action_filter(action):
            await callback_query.answer(_("Invalid action"))
            return
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        chat_iid = self.connection.db_model.iid
        draft = await self._get_draft(wizard_state, chat_iid)
        if draft is None:
            await callback_query.answer(_("Setup session expired. Please start again."))
            return
        if not self.cfg.allow_multiple_actions and draft.actions:
            await callback_query.answer(_("Only one action can be configured."))
            return
        if self.cfg.maximum_actions is not None and len(draft.actions) >= self.cfg.maximum_actions:
            await callback_query.answer(_("The maximum number of actions has been reached."))
            return
        if action.interactive_setup and action.interactive_setup.setup_message:
            await self._start_interactive_setup(callback_query, data.name, chat_iid)
            return
        action_data = action.default_data
        if action_data is not None and hasattr(action_data, "model_dump"):
            action_data = action_data.model_dump(mode="json")
        draft.replace_action(data.name, action_data)
        await wizard_state.set_draft(draft.to_data())
        await WizardRenderer.send_action_configured(
            callback_query,
            action_name=data.name,
            callback_prefix=self.cfg.callback_prefix,
            success_message=self.cfg.success_message,
            action_data=action_data,
            show_delete=False,
            show_cancel=True,
            show_done=True,
        )

    async def _op_context(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name:
            await callback_query.answer(_("Invalid callback data"))
            return
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        draft = await self._get_draft(wizard_state, self.connection.db_model.iid)
        if draft is None or not self.cfg.context.update_control(draft, data.name):
            await callback_query.answer(_("Invalid callback data"))
            return
        await wizard_state.set_draft(draft.to_data())
        await self._show_home(callback_query, draft)
        await callback_query.answer()

    async def _op_done(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        del data
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        chat_iid = self.connection.db_model.iid
        draft = await self._get_draft(wizard_state, chat_iid)
        if draft is None:
            await callback_query.answer(_("Setup session expired. Please start again."))
            return
        try:
            await self.cfg.context.validate(chat_iid, draft, callback_query, self.connection)
            await self.cfg.context.commit(chat_iid, draft, callback_query, self.connection)
        except (TypeError, ValueError) as error:
            await callback_query.answer(str(error))
            return
        await wizard_state.clear()
        await wizard_state.clear_fsm()
        await callback_query.answer(_("Saved"))

    async def _op_cancel(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        del data
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is not None:
            await wizard_state.clear()
            await wizard_state.clear_fsm()
        if callback_query.message and isinstance(callback_query.message, Message):
            await callback_query.message.edit_text(text=_("Action configuration cancelled."))
        await callback_query.answer(_("Cancelled"))

    async def _show_home(self, callback_query: CallbackQuery, draft: ActionWizardDraft | None = None) -> None:
        msg = callback_query.message
        if not msg or not isinstance(msg, Message):
            return
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            return
        if draft is None:
            draft = await self._get_draft(wizard_state, self.connection.db_model.iid)
        if draft is None:
            await msg.edit_text(_("Setup session expired. Please start again."))
            return
        document, markup = await WizardRenderer.render_home_page(
            self.cfg,
            chat_iid=self.connection.db_model.iid,
            draft=draft,
            chat_title=msg.chat.title,
            wizard_state=wizard_state,
        )
        await self.answer_rich(document, reply_markup=markup)

    async def _start_interactive_setup(
        self, callback_query: CallbackQuery, action_name: str, chat_iid: PydanticObjectId
    ) -> None:
        action = ALL_MODERN_ACTIONS[action_name]
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None or not action.interactive_setup or not action.interactive_setup.setup_message:
            await callback_query.answer(_("Action setup not properly configured"))
            return
        await wizard_state.replace_setup_context(
            action_setup_name=action_name,
            action_setup_chat_tid=str(chat_iid),
            action_setup_callback_prefix=self.cfg.callback_prefix,
        )
        await wizard_state.set_fsm_state(ActionConfigFSM.interactive_setup)
        setup_message = await action.interactive_setup.setup_message(callback_query, self.data)
        setup_document = WizardRenderer.rich_setup_message(setup_message.text, setup_message.reply_markup)
        setup_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("🔙 Back"),
                        callback_data=ACWCoreCallback(mod=self.cfg.callback_prefix, op="back").pack(),
                    )
                ]
            ]
        )
        await self.answer_rich(setup_document, reply_markup=setup_markup)
        await callback_query.answer()


class _ACWSettingsHandler(SophieCallbackQueryHandler):
    """Start a setting setup against the selected action in the draft."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> Any:
        callback_query: CallbackQuery = self.event
        data: ACWSettingCallback = self.data["callback_data"]
        action = ALL_MODERN_ACTIONS.get(data.name)
        wizard_state = _get_wizard_state(self.data)
        if action is None or wizard_state is None:
            await callback_query.answer(_("Invalid action"))
            return
        chat_iid = self.connection.db_model.iid
        if not await wizard_state.is_active(self.cfg.module_name, chat_iid):
            await callback_query.answer(_("Setup session expired. Please start again."))
            return
        draft = ActionWizardDraft.from_data(await wizard_state.get_draft())
        if data.name not in draft.actions:
            await callback_query.answer(_("Action not found."))
            return
        settings = action.settings(self._action_model(action, draft.actions[data.name]))
        setting = settings.get(data.setting)
        if setting is None or not setting.setup_message or not setting.setup_confirm:
            await callback_query.answer(_("Setting configuration not available"))
            return
        await wizard_state.update_data(
            setting_setup_action=data.name,
            setting_setup_setting_id=data.setting,
            setting_setup_chat_tid=str(chat_iid),
            setting_setup_callback_prefix=self.cfg.callback_prefix,
        )
        await wizard_state.set_fsm_state(ActionConfigFSM.interactive_setup)
        setup_message = await setting.setup_message(callback_query, self.data)
        setup_document = WizardRenderer.rich_setup_message(setup_message.text, setup_message.reply_markup)
        setup_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("❌ Cancel"),
                        callback_data=ACWCoreCallback(mod=self.cfg.callback_prefix, op="cancel").pack(),
                        style="danger",
                    )
                ]
            ]
        )
        await self.answer_rich(setup_document, reply_markup=setup_markup)
        await callback_query.answer()

    @staticmethod
    def _action_model(action: Any, data: dict[str, Any] | None) -> Any:
        return convert_action_data_to_model(action, data or {})


class _ACWNoOpHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return ()

    @classmethod
    def register(cls, router: Any) -> None:
        del router

    async def handle(self) -> Any:
        raise SkipHandler
