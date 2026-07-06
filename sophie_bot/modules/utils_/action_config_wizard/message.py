from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from beanie import PydanticObjectId
from bson.errors import InvalidId

from sophie_bot.modules.filters.types.modern_action_abc import ActionSetupTryAgainException
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _

from .config import ActionWizardConfig
from .context import (
    get_active_setup_config as _get_active_setup_config,
    get_fsm_context as _get_fsm_context,
    get_interactive_setup_chat_iid_raw as _get_interactive_setup_chat_iid_raw,
    get_wizard_state as _get_wizard_state,
)
from .helpers import convert_action_data_to_model
from .renderer import WizardRenderer
from .state import ActionConfigFSM, WizardState


class _ACWWizardHandler(SophieMessageHandler):
    """Handles the initial command to show the wizard home page."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> Any:
        chat_iid: PydanticObjectId = self.connection.db_model.iid
        wizard_state = _get_wizard_state(self.data)
        html, markup = await WizardRenderer.render_home_page(
            self.cfg, chat_iid=chat_iid, chat_title=self.connection.title, wizard_state=wizard_state
        )
        await self.event.reply(html, reply_markup=markup)


class _ACWSetupHandler(SophieMessageHandler):
    """Handles user text input during interactive setup."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (ActionConfigFSM.interactive_setup,)

    async def handle(self) -> Any:
        message: Message = self.event
        fsm_ctx = _get_fsm_context(self.data)
        if not fsm_ctx:
            return

        wizard_state = WizardState(fsm_ctx)
        state_data = await wizard_state.get_data()
        cfg = _get_active_setup_config(state_data, self.cfg)

        chat_iid_raw = _get_interactive_setup_chat_iid_raw(state_data)
        if chat_iid_raw:
            try:
                chat_iid = PydanticObjectId(chat_iid_raw)
            except (InvalidId, TypeError):
                chat_iid = None
            if chat_iid and not await wizard_state.is_active(cfg.module_name, chat_iid):
                await message.reply(_("Setup session expired. Please start again."))
                await wizard_state.clear_fsm()
                return

        if "setting_setup_action" in state_data:
            await self._handle_setting_setup(message, wizard_state, state_data, cfg)
        else:
            await self._handle_action_setup(message, wizard_state, state_data, cfg)

    async def _handle_action_setup(
        self, message: Message, wizard_state: WizardState, state_data: dict[str, Any], cfg: ActionWizardConfig
    ) -> None:
        action_name = state_data.get("action_setup_name")
        chat_iid_raw = state_data.get("action_setup_chat_tid")

        if not action_name or not chat_iid_raw:
            await message.reply(_("Setup data not found. Please try again."))
            await wizard_state.clear_fsm()
            return

        try:
            chat_iid = PydanticObjectId(chat_iid_raw)
        except (InvalidId, TypeError):
            await message.reply(_("Invalid chat context. Please restart the setup."))
            await wizard_state.clear_fsm()
            return

        action = ALL_MODERN_ACTIONS.get(action_name)
        if not action or not action.interactive_setup or not action.interactive_setup.setup_confirm:
            await message.reply(_("Invalid action configuration."))
            await wizard_state.clear_fsm()
            return

        try:
            action_data = await action.interactive_setup.setup_confirm(message, self.data)
            if hasattr(action_data, "model_dump"):
                action_data_dict = action_data.model_dump(mode="json")
            else:
                action_data_dict = action_data

            await wizard_state.stage_action(cfg.module_name, chat_iid, action_name, action_data_dict)

            callback_prefix = state_data.get("action_setup_callback_prefix", cfg.callback_prefix)
            await WizardRenderer.send_action_configured(
                message,
                action_name=action_name,
                callback_prefix=callback_prefix,
                success_message=cfg.success_message,
                action_data=action_data_dict,
                show_delete=False,
                show_cancel=True,
                show_done=True,
            )
            await wizard_state.set_fsm_state(None)
        except ActionSetupTryAgainException:
            pass

    async def _handle_setting_setup(
        self, message: Message, wizard_state: WizardState, state_data: dict[str, Any], cfg: ActionWizardConfig
    ) -> None:
        action_name = state_data.get("setting_setup_action")
        setting_id = state_data.get("setting_setup_setting_id")
        chat_iid_raw = state_data.get("setting_setup_chat_tid")

        if not action_name or not setting_id or not chat_iid_raw:
            await message.reply(_("Setup data not found. Please try again."))
            await wizard_state.clear_fsm()
            return

        try:
            chat_iid = PydanticObjectId(chat_iid_raw)
        except (InvalidId, TypeError):
            await message.reply(_("Invalid chat context. Please restart the setup."))
            await wizard_state.clear_fsm()
            return

        action = ALL_MODERN_ACTIONS.get(action_name)
        if not action:
            await message.reply(_("Invalid action."))
            await wizard_state.clear_fsm()
            return

        model = await cfg.get_model_func(chat_iid)
        actions = await cfg.get_actions_func(model)
        current_action_data = None
        for act in actions:
            if act.name == action_name:
                current_action_data = act.data
                break
        settings = action.settings(convert_action_data_to_model(action, current_action_data or {}))

        if setting_id not in settings:
            await message.reply(_("Invalid setting."))
            await wizard_state.clear_fsm()
            return

        setting = settings[setting_id]
        if not setting.setup_confirm:
            await message.reply(_("Setting configuration not available."))
            await wizard_state.clear_fsm()
            return

        try:
            setting_data = await setting.setup_confirm(message, self.data)
            if setting_data and hasattr(setting_data, "model_dump"):
                setting_data_dict = setting_data.model_dump(mode="json")
            else:
                setting_data_dict = setting_data

            updated_action_data = setting_data_dict if setting_data_dict else (current_action_data or {})
            await wizard_state.set_action_data(updated_action_data)

            callback_prefix = state_data.get("setting_setup_callback_prefix", cfg.callback_prefix)
            await WizardRenderer.send_action_configured(
                message,
                action_name=action_name,
                callback_prefix=callback_prefix,
                success_message=cfg.success_message,
                action_data=updated_action_data,
                show_cancel=True,
                show_done=True,
            )
            await wizard_state.set_fsm_state(None)
        except ActionSetupTryAgainException:
            pass
