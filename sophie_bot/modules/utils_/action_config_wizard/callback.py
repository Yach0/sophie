from __future__ import annotations

from typing import Any, Optional

from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from beanie import PydanticObjectId
from pymongo.errors import PyMongoError

from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _

from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig
from .context import get_wizard_state as _get_wizard_state
from .helpers import convert_action_data_to_model
from .renderer import WizardRenderer
from .state import ActionConfigFSM


class _ACWCallbackHandler(SophieCallbackQueryHandler):
    """Unified callback handler that dispatches all ACW operations."""

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
            "done": self._op_done,
            "cancel": self._op_cancel,
        }

        handler_func = dispatch.get(data.op)
        if handler_func is None:
            await callback_query.answer(_("Invalid callback data"))
            return
        await handler_func(callback_query, data)

    async def _op_add(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return
        text, markup = await WizardRenderer.render_add_action_list(
            self.cfg, chat_tid=callback_query.message.chat.id, default_action_name=self.cfg.default_action_name
        )
        await callback_query.message.edit_text(text, reply_markup=markup)

    async def _op_remove(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name:
            await callback_query.answer(_("Invalid callback data"))
            return
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return
        chat_iid: PydanticObjectId = self.connection.db_model.iid
        await self.cfg.remove_action_func(chat_iid, data.name)
        await callback_query.answer(_("Action removed."))
        await self._show_home(callback_query)

    async def _op_configure(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name:
            await callback_query.answer(_("Invalid callback data"))
            return
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return
        if data.name not in ALL_MODERN_ACTIONS:
            await callback_query.answer(_("Invalid action"))
            return

        chat_iid: PydanticObjectId = self.connection.db_model.iid
        action_data = await self._fetch_action_data(chat_iid, data.name)

        wizard_state = _get_wizard_state(self.data)
        has_changes = (
            await wizard_state.has_staged_changes(self.cfg.module_name, chat_iid) if wizard_state is not None else False
        )

        await WizardRenderer.send_action_configured(
            callback_query,
            action_name=data.name,
            callback_prefix=self.cfg.callback_prefix,
            success_message=self.cfg.success_message,
            action_data=action_data,
            show_cancel=False,
            show_done=has_changes,
        )

    async def _op_back(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return
        if self.cfg.on_back_render is not None:
            await self.cfg.on_back_render(self, callback_query)
            return
        await self._show_home(callback_query)
        await callback_query.answer()

    async def _op_show(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        await self._show_home(callback_query)

    async def _op_select(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        if not data.name or data.name not in ALL_MODERN_ACTIONS:
            await callback_query.answer(_("Invalid action"))
            return
        if not callback_query.message or not isinstance(callback_query.message, Message):
            await callback_query.answer(_("Message not found."))
            return

        chat_iid: PydanticObjectId = self.connection.db_model.iid
        action = ALL_MODERN_ACTIONS[data.name]

        if action.interactive_setup and action.interactive_setup.setup_message:
            await self._start_interactive_setup(callback_query, data.name, chat_iid)
            return

        default_data = action.default_data
        if default_data is not None and hasattr(default_data, "model_dump"):
            default_data = default_data.model_dump(mode="json")

        wizard_state = _get_wizard_state(self.data)
        if wizard_state is not None:
            await wizard_state.stage_action(self.cfg.module_name, chat_iid, data.name, default_data)

        await WizardRenderer.send_action_configured(
            callback_query,
            action_name=data.name,
            callback_prefix=self.cfg.callback_prefix,
            success_message=self.cfg.success_message,
            action_data=default_data,
            show_delete=False,
            show_cancel=False,
        )

    async def _op_done(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is not None:
            chat_iid, action_name, action_data = await wizard_state.get_staged()
            if chat_iid is not None and action_name:
                if (
                    action_data is not None
                    and hasattr(action_data, "model_dump")
                    and callable(getattr(action_data, "model_dump", None))
                ):
                    action_data = getattr(action_data, "model_dump")(mode="json")
                await self.cfg.add_action_func(chat_iid, action_name, action_data or {})

            await wizard_state.clear()
            await wizard_state.clear_fsm()

        if callback_query.message and isinstance(callback_query.message, Message):
            await self._show_home(callback_query)
        await callback_query.answer(_("Saved"))

    async def _op_cancel(self, callback_query: CallbackQuery, data: ACWCoreCallback) -> None:
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is not None:
            await wizard_state.clear()
            await wizard_state.clear_fsm()

        if callback_query.message and isinstance(callback_query.message, Message):
            await callback_query.message.edit_text(_("Action configuration cancelled."))
        await callback_query.answer(_("Cancelled"))

    async def _show_home(self, callback_query: CallbackQuery) -> None:
        msg = callback_query.message
        if not msg or not isinstance(msg, Message):
            return
        chat_iid: PydanticObjectId = self.connection.db_model.iid
        wizard_state = _get_wizard_state(self.data)
        html, markup = await WizardRenderer.render_home_page(
            self.cfg, chat_iid=chat_iid, chat_title=msg.chat.title, wizard_state=wizard_state
        )
        await msg.edit_text(html, reply_markup=markup)

    async def _fetch_action_data(self, chat_iid: PydanticObjectId, action_name: str) -> Optional[dict[str, Any]]:
        try:
            model = await self.cfg.get_model_func(chat_iid)
            actions = await self.cfg.get_actions_func(model)
            for action in actions:
                if action.name == action_name:
                    return action.data
        except PyMongoError:
            pass
        return None

    async def _start_interactive_setup(
        self, callback_query: CallbackQuery, action_name: str, chat_iid: PydanticObjectId
    ) -> None:
        action = ALL_MODERN_ACTIONS[action_name]
        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return

        await wizard_state.ensure_session(self.cfg.module_name, chat_iid)
        await wizard_state.replace_setup_context(
            action_setup_name=action_name,
            action_setup_chat_tid=str(chat_iid),
            action_setup_callback_prefix=self.cfg.callback_prefix,
        )
        await wizard_state.set_fsm_state(ActionConfigFSM.interactive_setup)

        if not action.interactive_setup or not action.interactive_setup.setup_message:
            await callback_query.answer(_("Action setup not properly configured"))
            return

        setup_message = await action.interactive_setup.setup_message(callback_query, self.data)
        reply_markup = setup_message.reply_markup
        if not reply_markup:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[])

        reply_markup.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("🔙 Back"),
                    callback_data=ACWCoreCallback(mod=self.cfg.callback_prefix, op="back").pack(),
                )
            ]
        )

        if callback_query.message and isinstance(callback_query.message, Message):
            await callback_query.message.edit_text(setup_message.text, reply_markup=reply_markup)


class _ACWSettingsHandler(SophieCallbackQueryHandler):
    """Handles settings button clicks for action configuration."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> Any:
        callback_query: CallbackQuery = self.event
        data: ACWSettingCallback = self.data["callback_data"]

        parsed_action_name = data.name
        parsed_setting_id = data.setting

        if parsed_action_name not in ALL_MODERN_ACTIONS:
            await callback_query.answer(_("Invalid action"))
            return

        chat_iid: PydanticObjectId = self.connection.db_model.iid

        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return
        await wizard_state.ensure_session(self.cfg.module_name, chat_iid)
        await wizard_state.set_action(parsed_action_name)

        action = ALL_MODERN_ACTIONS[parsed_action_name]

        try:
            model = await self.cfg.get_model_func(chat_iid)
            actions = await self.cfg.get_actions_func(model)
            current_action_data = None
            for act in actions:
                if act.name == parsed_action_name:
                    current_action_data = act.data
                    break
        except PyMongoError:
            current_action_data = None

        settings = action.settings(convert_action_data_to_model(action, current_action_data or {}))
        if parsed_setting_id not in settings:
            await callback_query.answer(_("Invalid setting"))
            return

        setting = settings[parsed_setting_id]
        if setting.setup_message and setting.setup_confirm:
            await self._start_setting_setup(callback_query, parsed_action_name, parsed_setting_id, chat_iid)
        else:
            await callback_query.answer(_("Setting configuration not available"))

    async def _start_setting_setup(
        self, callback_query: CallbackQuery, action_name: str, setting_id: str, chat_iid: PydanticObjectId
    ) -> None:
        action = ALL_MODERN_ACTIONS[action_name]
        try:
            model = await self.cfg.get_model_func(chat_iid)
            actions = await self.cfg.get_actions_func(model)
            current_action_data = None
            for act in actions:
                if act.name == action_name:
                    current_action_data = act.data
                    break
        except PyMongoError:
            current_action_data = None

        settings = action.settings(convert_action_data_to_model(action, current_action_data or {}))
        setting = settings[setting_id]

        wizard_state = _get_wizard_state(self.data)
        if wizard_state is None:
            await callback_query.answer(_("State management not available"))
            return

        await wizard_state.replace_setup_context(
            setting_setup_action=action_name,
            setting_setup_setting_id=setting_id,
            setting_setup_chat_tid=str(chat_iid),
            setting_setup_callback_prefix=self.cfg.callback_prefix,
        )
        await wizard_state.set_fsm_state(ActionConfigFSM.interactive_setup)

        if not setting.setup_message:
            await callback_query.answer(_("Setting setup not properly configured"))
            return

        setup_message = await setting.setup_message(callback_query, self.data)
        reply_markup = setup_message.reply_markup
        if not reply_markup:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[])

        reply_markup.inline_keyboard.append(
            [
                InlineKeyboardButton(
                    text=_("❌ Cancel"),
                    callback_data=ACWCoreCallback(mod=self.cfg.callback_prefix, op="cancel").pack(),
                    style="danger",
                )
            ]
        )

        if callback_query.message and isinstance(callback_query.message, Message):
            await callback_query.message.edit_text(setup_message.text, reply_markup=reply_markup)


class _ACWNoOpHandler(SophieCallbackQueryHandler):
    """Placeholder handler that skips registration.

    Done/Cancel operations are handled by the unified callback handler.
    This class exists so that ``ModuleManifest.handlers`` tuples can still list 6 entries
    without causing errors during ``handler.register(router)``.
    """

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return ()

    @classmethod
    def register(cls, router: Any) -> None:
        pass

    async def handle(self) -> Any:
        raise SkipHandler
