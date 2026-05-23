from __future__ import annotations

from typing import Any, Optional, Tuple

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from beanie import PydanticObjectId
from stfu_tg import KeyValue, Section, Template

from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS
from sophie_bot.utils.i18n import gettext as _

from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig
from .helpers import convert_action_data_to_model
from .state import WizardState


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
        del chat_tid
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
        """Build the 'action configured' screen."""
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
