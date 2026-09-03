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

from .config import ActionWizardConfig, ActionWizardDraft
from .context import get_active_setup_config as _get_active_setup_config
from .context import get_fsm_context as _get_fsm_context
from .context import get_interactive_setup_chat_iid_raw as _get_interactive_setup_chat_iid_raw
from .context import get_wizard_state as _get_wizard_state
from .helpers import convert_action_data_to_model
from .renderer import WizardRenderer
from .state import ActionConfigFSM, WizardState


class _ACWWizardHandler(SophieMessageHandler):
    """Start a fresh aggregate draft and render the wizard home page."""

    cfg: ActionWizardConfig

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    async def handle(self) -> Any:
        chat_iid = self.connection.db_model.iid
        wizard_state = _get_wizard_state(self.data)
        draft = await self.cfg.context.load(chat_iid)
        if wizard_state is not None:
            await wizard_state.start_session(self.cfg.module_name, chat_iid, draft.to_data())
        document, markup = await WizardRenderer.render_home_page(
            self.cfg,
            chat_iid=chat_iid,
            draft=draft,
            chat_title=self.connection.title,
            wizard_state=wizard_state,
        )
        await self.answer_rich(document, reply_markup=markup)


class _ACWSetupHandler(SophieMessageHandler):
    """Handle text input for action and setting setup."""

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
        if not chat_iid_raw:
            await message.reply(_("Setup data not found. Please try again."))
            await wizard_state.clear_fsm()
            return
        try:
            chat_iid = PydanticObjectId(chat_iid_raw)
        except (InvalidId, TypeError):
            await message.reply(_("Invalid chat context. Please restart the setup."))
            await wizard_state.clear_fsm()
            return
        if not await wizard_state.is_active(cfg.module_name, chat_iid):
            await message.reply(_("Setup session expired. Please start again."))
            await wizard_state.clear_fsm()
            return
        if "setting_setup_action" in state_data:
            await self._handle_setting_setup(message, wizard_state, state_data, cfg, chat_iid)
        else:
            await self._handle_action_setup(message, wizard_state, state_data, cfg, chat_iid)

    async def _handle_action_setup(
        self,
        message: Message,
        wizard_state: WizardState,
        state_data: dict[str, Any],
        cfg: ActionWizardConfig,
        chat_iid: PydanticObjectId,
    ) -> None:
        action_name = state_data.get("action_setup_name")
        if not isinstance(action_name, str):
            await message.reply(_("Invalid action configuration."))
            await wizard_state.clear_fsm()
            return
        action = ALL_MODERN_ACTIONS.get(action_name)
        if not action or not action.interactive_setup or not action.interactive_setup.setup_confirm:
            await message.reply(_("Invalid action configuration."))
            await wizard_state.clear_fsm()
            return
        try:
            action_data = await action.interactive_setup.setup_confirm(message, self.data)
            action_data_dict = (
                action_data.model_dump(mode="json") if hasattr(action_data, "model_dump") else action_data
            )
            draft = ActionWizardDraft.from_data(await wizard_state.get_draft())
            draft.replace_action(action_name, action_data_dict)
            await cfg.context.validate(chat_iid, draft, message, self.connection)
            await wizard_state.set_draft(draft.to_data())
            await WizardRenderer.send_action_configured(
                message,
                action_name=action_name,
                callback_prefix=state_data.get("action_setup_callback_prefix", cfg.callback_prefix),
                success_message=cfg.success_message,
                action_data=action_data_dict,
                show_delete=False,
                show_cancel=True,
                show_done=True,
            )
            await wizard_state.set_fsm_state(None)
        except ActionSetupTryAgainException:
            return
        except ValueError as error:
            await message.reply(str(error))
            await wizard_state.clear_fsm()

    async def _handle_setting_setup(
        self,
        message: Message,
        wizard_state: WizardState,
        state_data: dict[str, Any],
        cfg: ActionWizardConfig,
        chat_iid: PydanticObjectId,
    ) -> None:
        action_name = state_data.get("setting_setup_action")
        setting_id = state_data.get("setting_setup_setting_id")
        if not isinstance(action_name, str) or not isinstance(setting_id, str):
            await message.reply(_("Invalid setting."))
            await wizard_state.clear_fsm()
            return
        action = ALL_MODERN_ACTIONS.get(action_name)
        draft = ActionWizardDraft.from_data(await wizard_state.get_draft())
        if not action or action_name not in draft.actions:
            await message.reply(_("Invalid setting."))
            await wizard_state.clear_fsm()
            return
        setting = action.settings(convert_action_data_to_model(action, draft.actions[action_name] or {})).get(
            setting_id
        )
        if setting is None or not setting.setup_confirm:
            await message.reply(_("Setting configuration not available."))
            await wizard_state.clear_fsm()
            return
        try:
            setting_data = await setting.setup_confirm(message, self.data)
            setting_data_dict = (
                setting_data.model_dump(mode="json") if hasattr(setting_data, "model_dump") else setting_data
            )
            draft.replace_action(action_name, setting_data_dict or {})
            await cfg.context.validate(chat_iid, draft, message, self.connection)
            await wizard_state.set_draft(draft.to_data())
            await WizardRenderer.send_action_configured(
                message,
                action_name=action_name,
                callback_prefix=state_data.get("setting_setup_callback_prefix", cfg.callback_prefix),
                success_message=cfg.success_message,
                action_data=setting_data_dict,
                show_cancel=True,
                show_done=True,
            )
            await wizard_state.set_fsm_state(None)
        except ActionSetupTryAgainException:
            return
        except ValueError as error:
            await message.reply(str(error))
            await wizard_state.clear_fsm()
