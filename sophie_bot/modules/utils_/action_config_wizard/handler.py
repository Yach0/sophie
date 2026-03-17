from __future__ import annotations

from typing import Any, Optional, Tuple

from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from beanie import PydanticObjectId
from bson.errors import InvalidId
from pymongo.errors import PyMongoError
from stfu_tg import KeyValue, Section, Template

from sophie_bot.modules.filters.types.modern_action_abc import ActionSetupTryAgainException
from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _

from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig
from .helpers import convert_action_data_to_model
from .state import ActionConfigFSM, WizardState

_ACTION_WIZARD_CONFIGS: dict[str, ActionWizardConfig] = {}

# ---------------------------------------------------------------------------
# Renderer — stateless rendering helpers
# ---------------------------------------------------------------------------


class WizardRenderer:
    """Pure rendering utilities for Action Config Wizard screens."""

    @staticmethod
    async def render_home_page(
        cfg: ActionWizardConfig,
        *,
        chat_iid: PydanticObjectId,
        chat_title: str | None,
        wizard_state: WizardState | None,
    ) -> Tuple[str, Any]:
        """Build home page text and keyboard."""
        model = await cfg.get_model_func(chat_iid)
        actions = await cfg.get_actions_func(model)

        items: list[Any] = [KeyValue(_("Chat"), chat_title or "Unknown")]
        builder = InlineKeyboardBuilder()

        if actions:
            for action in actions:
                action_meta = ALL_MODERN_ACTIONS.get(action.name)
                if not action_meta:
                    continue
                action_text = (
                    action_meta.description(convert_action_data_to_model(action_meta, action.data))
                    if action_meta and action.data
                    else action.name
                )
                items.append(KeyValue(action_meta.title, action_text))
                builder.add(
                    InlineKeyboardButton(
                        text=f"{action_meta.icon} {action_meta.title}",
                        callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="configure", name=action.name).pack(),
                    )
                )

            if not cfg.allow_multiple_actions and len(actions) == 1:
                action = actions[0]
                action_meta = ALL_MODERN_ACTIONS.get(action.name)
                if action_meta:
                    items.append(KeyValue(_("Current Action"), f"{action_meta.icon} {action_meta.title}"))

        # Save button if staged changes exist
        if wizard_state is not None:
            has_changes = await wizard_state.has_staged_changes(cfg.module_name, chat_iid)
            if has_changes:
                builder.add(
                    InlineKeyboardButton(
                        text=_("✅ Save"),
                        callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="done").pack(),
                        style="success",
                    )
                )

        # Add-new / change button
        if cfg.allow_multiple_actions or not actions:
            if cfg.allow_multiple_actions:
                add_text = _("➕ Add another action")
            elif not actions:
                add_text = _("➕ Add action")
            else:
                add_text = _("🔄 Change action")
            builder.add(
                InlineKeyboardButton(
                    text=add_text,
                    callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="add").pack(),
                )
            )
        builder.adjust(1)

        # Back button
        if cfg.on_back_render is not None:
            builder.row(
                InlineKeyboardButton(
                    text=_("🔙 Back"),
                    callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="back").pack(),
                )
            )

        doc = Section(
            *items,
            title=_(cfg.wizard_title),
        )
        return doc.to_html(), builder.as_markup()

    @staticmethod
    async def render_add_action_list(
        cfg: ActionWizardConfig,
        *,
        chat_tid: int,
        default_action_name: Optional[str] = None,
    ) -> Tuple[str, Any]:
        """Build the 'select an action to add' page."""
        builder = InlineKeyboardBuilder()
        for action_name, action in ALL_MODERN_ACTIONS.items():
            if cfg.action_filter is not None and not cfg.action_filter(action):
                continue
            button_text = f"{action.icon} {action.title}"
            if default_action_name and action_name == default_action_name:
                button_text = f"👈 {button_text}"
            callback_data = ACWCoreCallback(mod=cfg.callback_prefix, op="select", name=action_name).pack()
            builder.add(InlineKeyboardButton(text=str(button_text), callback_data=callback_data))
        builder.adjust(2)
        builder.add(
            InlineKeyboardButton(
                text=_("🔙 Back"),
                callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="back").pack(),
            )
        )

        text = _("Select an action to add:")
        if default_action_name:
            default_action = ALL_MODERN_ACTIONS.get(default_action_name)
            if default_action:
                text += "\n\n"
                text += Template(
                    _("Default action: {icon} {title}"), icon=default_action.icon, title=default_action.title
                ).to_html()

        return text, builder.as_markup()

    @staticmethod
    async def render_action_configured(
        *,
        action_name: str,
        callback_prefix: str,
        success_message: str | Any,
        action_data: Optional[dict[str, Any]] = None,
        show_delete: bool = True,
        show_cancel: bool = True,
        show_done: bool = True,
    ) -> Tuple[str, Any, str]:
        """Build the 'action configured' screen.

        Returns (text_html, reply_markup, answer_text).
        """
        action = ALL_MODERN_ACTIONS[action_name]
        action_model = convert_action_data_to_model(action, action_data)

        doc = Section(
            KeyValue(_("Action configured"), f"{action.icon} {action.title}"),
            KeyValue(_("Description"), action.description(action_model)),
            title=_("Action Configuration Complete"),
        )

        settings = action.settings(action_model)
        builder = InlineKeyboardBuilder()
        if settings:
            for setting_id, setting in settings.items():
                button_text = f"{setting.icon} {setting.title}" if setting.icon else str(setting.title)
                cb_data = ACWSettingCallback(mod=callback_prefix, name=action_name, setting=setting_id).pack()
                builder.add(InlineKeyboardButton(text=button_text, callback_data=cb_data))
            builder.adjust(2)

        if show_delete:
            builder.row(
                InlineKeyboardButton(
                    text=_("🗑️ Delete this action"),
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="remove", name=action_name).pack(),
                    style="danger",
                )
            )
        builder.row(
            InlineKeyboardButton(
                text=_("🔙 Back"),
                callback_data=ACWCoreCallback(mod=callback_prefix, op="back").pack(),
            )
        )

        if show_done:
            done_button = InlineKeyboardButton(
                text=_("✅ Done"),
                callback_data=ACWCoreCallback(mod=callback_prefix, op="done").pack(),
                style="success",
            )
            if show_cancel:
                cancel_button = InlineKeyboardButton(
                    text=_("❌ Cancel"),
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="cancel").pack(),
                    style="danger",
                )
                builder.row(cancel_button, done_button)
            else:
                builder.row(done_button)

        answer_text = str(success_message) if success_message else str(_("Action configured successfully!"))
        return str(doc), builder.as_markup(), answer_text

    @staticmethod
    async def send_action_configured(
        event: CallbackQuery | Message,
        *,
        action_name: str,
        callback_prefix: str,
        success_message: str | Any,
        action_data: Optional[dict[str, Any]] = None,
        show_delete: bool = True,
        show_cancel: bool = True,
        show_done: bool = True,
    ) -> None:
        """Render and send/edit the 'action configured' message."""
        text, reply_markup, answer_text = await WizardRenderer.render_action_configured(
            action_name=action_name,
            callback_prefix=callback_prefix,
            success_message=success_message,
            action_data=action_data,
            show_delete=show_delete,
            show_cancel=show_cancel,
            show_done=show_done,
        )
        if isinstance(event, CallbackQuery):
            if event.message and isinstance(event.message, Message):
                await event.message.edit_text(text, reply_markup=reply_markup)
            await event.answer(answer_text)
        else:
            await event.reply(text, reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# Helper to get WizardState from handler data dict
# ---------------------------------------------------------------------------


def _get_wizard_state(data: dict[str, Any]) -> WizardState | None:
    state = data.get("state")
    if isinstance(state, FSMContext):
        return WizardState(state)
    return None


def _get_fsm_context(data: dict[str, Any]) -> FSMContext | None:
    state = data.get("state")
    return state if isinstance(state, FSMContext) else None


def _get_interactive_setup_chat_iid_raw(state_data: dict[str, Any]) -> Any:
    """Read the chat context for the currently active interactive setup mode."""
    if "setting_setup_action" in state_data:
        return state_data.get("setting_setup_chat_tid")
    return state_data.get("action_setup_chat_tid")


def _get_active_setup_config(state_data: dict[str, Any], fallback_cfg: ActionWizardConfig) -> ActionWizardConfig:
    """Return the config for the active ACW session, falling back to the current handler config."""
    active_module_name = state_data.get("acw_module")
    if isinstance(active_module_name, str):
        return _ACTION_WIZARD_CONFIGS.get(active_module_name, fallback_cfg)
    return fallback_cfg


# ---------------------------------------------------------------------------
# Unified Callback Handler
# ---------------------------------------------------------------------------


class _ACWCallbackHandler(SophieCallbackQueryHandler):
    """Unified callback handler that dispatches all ACW operations."""

    cfg: ActionWizardConfig  # set by factory

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError  # overridden by factory

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

    # -- operations ----------------------------------------------------------

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

        # Non-interactive: stage
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
                    action_data = action_data.model_dump(mode="json")  # type: ignore[union-attr]
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

    # -- shared helpers ------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Settings Callback Handler
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Wizard (command) Message Handler
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Setup (interactive input) Message Handler
# ---------------------------------------------------------------------------


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

        # Validate TTL
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


# ---------------------------------------------------------------------------
# Factory — creates the 6 handler classes from a single config
# ---------------------------------------------------------------------------


def create_action_config_system(
    cfg: ActionWizardConfig,
) -> tuple[type, type, type, type, type, type]:
    """Create a complete set of handler classes from a single config.

    Returns (WizardHandler, CallbackHandler, SetupHandler, DoneHandler, CancelHandler, SettingsHandler).

    DoneHandler and CancelHandler are the same class as CallbackHandler (unified dispatch),
    but provided as separate references for backward compatibility with ``__handlers__`` registration.
    """

    _ACTION_WIZARD_CONFIGS[cfg.module_name] = cfg

    # Wizard (command) handler
    wizard_cls = type(
        "ACWWizard",
        (_ACWWizardHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (cfg.command_filter, cfg.admin_filter)),
        },
    )

    # Unified callback handler (handles add/remove/configure/back/show/select/done/cancel)
    callback_cls = type(
        "ACWCallback",
        (_ACWCallbackHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (ACWCoreCallback.filter(F.mod == cfg.callback_prefix),)),
        },
    )

    # Setup (interactive input) handler
    setup_cls = type(
        "ACWSetup",
        (_ACWSetupHandler,),
        {
            "cfg": cfg,
        },
    )

    # Settings handler
    settings_cls = type(
        "ACWSettings",
        (_ACWSettingsHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (ACWSettingCallback.filter(F.mod == cfg.callback_prefix),)),
        },
    )

    # Done and Cancel are now handled inside the unified callback handler.
    # We return the callback_cls for those slots so __handlers__ registration
    # doesn't break. The register() method is idempotent for duplicate classes,
    # but to avoid double-registration we create thin no-op subclasses.
    done_cls = type("ACWDone", (_ACWNoOpHandler,), {})
    cancel_cls = type("ACWCancel", (_ACWNoOpHandler,), {})

    return wizard_cls, callback_cls, setup_cls, done_cls, cancel_cls, settings_cls


class _ACWNoOpHandler(SophieCallbackQueryHandler):
    """Placeholder handler that skips registration.

    Done/Cancel operations are handled by the unified callback handler.
    This class exists so that ``__handlers__`` tuples can still list 6 entries
    without causing errors during ``handler.register(router)``.
    """

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return ()

    @classmethod
    def register(cls, router: Any) -> None:
        # Intentionally no-op: done/cancel are dispatched by the unified callback handler
        pass

    async def handle(self) -> Any:
        raise SkipHandler
