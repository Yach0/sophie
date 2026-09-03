from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from beanie import PydanticObjectId
from stfu_tg import Button, ButtonRow, Buttons, Doc, KeyValue, Section, Template
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit_rich
from sophie_bot.utils.i18n import gettext as _

from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig, ActionWizardDraft
from .helpers import convert_action_data_to_model
from .state import WizardState


class WizardRenderer:
    """Render wizard content with rich action buttons and inline navigation."""

    @staticmethod
    async def render_home_page(
        cfg: ActionWizardConfig,
        *,
        chat_iid: PydanticObjectId,
        draft: ActionWizardDraft,
        chat_title: str | None,
        wizard_state: WizardState | None,
    ) -> tuple[Doc, InlineKeyboardMarkup | None]:
        actions = _all_modern_actions()
        details: list[Any] = [KeyValue(_("Chat"), chat_title or "Unknown")]
        details.extend(KeyValue(label, value) for label, value in cfg.context.render_details(draft))
        action_rows: list[ButtonRow] = []
        for action_name, action_data in draft.actions.items():
            action = actions.get(action_name)
            if action is None:
                continue
            action_model = convert_action_data_to_model(action, action_data or {})
            details.append(KeyValue(action.title, action.description(action_model)))
            action_rows.append(
                ButtonRow(
                    Button(
                        f"{action.icon} {action.title}",
                        callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="configure", name=action_name).pack(),
                    )
                )
            )

        if not draft.actions or (
            cfg.allow_multiple_actions and (cfg.maximum_actions is None or len(draft.actions) < cfg.maximum_actions)
        ):
            text = _("➕ Add another action") if draft.actions else _("➕ Add action")
            action_rows.append(
                ButtonRow(Button(text, callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="add").pack()))
            )

        document = Doc(Section(*details, title=_(str(cfg.wizard_title))))
        if action_rows:
            document += Section(Buttons(*action_rows), title=_("Actions"))
        context_rows = _context_button_rows(cfg, draft)
        if context_rows:
            document += Section(Buttons(*context_rows), title=_("Options"))

        inline_buttons: list[InlineKeyboardButton] = []
        if wizard_state is not None and await wizard_state.has_staged_changes(cfg.module_name, chat_iid):
            inline_buttons.append(
                InlineKeyboardButton(
                    text=_("✅ Done"),
                    callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="done").pack(),
                    style="success",
                )
            )
        if cfg.on_back_render is not None:
            inline_buttons.append(
                InlineKeyboardButton(
                    text=_("🔙 Back"), callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="back").pack()
                )
            )
        return document, _inline_markup(inline_buttons)

    @staticmethod
    async def render_add_action_list(
        cfg: ActionWizardConfig, *, default_action_name: str | None = None
    ) -> tuple[Doc, InlineKeyboardMarkup | None]:
        actions = _all_modern_actions()
        action_rows: list[ButtonRow] = []
        for action_name, action in actions.items():
            if cfg.action_filter is not None and not cfg.action_filter(action):
                continue
            button_text = f"{action.icon} {action.title}"
            if default_action_name == action_name:
                button_text = f"👈 {button_text}"
            action_rows.append(
                ButtonRow(
                    Button(
                        button_text,
                        callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="select", name=action_name).pack(),
                    )
                )
            )
        document = (
            Doc(Section(Buttons(*action_rows), title=_("Actions"))) if action_rows else Doc(_("No actions available."))
        )
        if default_action_name and (action := actions.get(default_action_name)):
            document += Template(_("Default action: {icon} {title}"), icon=action.icon, title=action.title)
        return document, _inline_markup(
            [
                InlineKeyboardButton(
                    text=_("🔙 Back"), callback_data=ACWCoreCallback(mod=cfg.callback_prefix, op="back").pack()
                )
            ]
        )

    @staticmethod
    def render_action_configured(
        *,
        action_name: str,
        callback_prefix: str,
        success_message: str | Any,
        action_data: dict[str, Any] | None = None,
        show_delete: bool = True,
        show_cancel: bool = True,
        show_done: bool = True,
    ) -> tuple[Doc, InlineKeyboardMarkup | None, str]:
        action = _all_modern_actions()[action_name]
        action_model = convert_action_data_to_model(action, action_data)
        action_buttons = [
            ButtonRow(
                Button(
                    f"{setting.icon} {setting.title}" if setting.icon else str(setting.title),
                    callback_data=ACWSettingCallback(mod=callback_prefix, name=action_name, setting=setting_id).pack(),
                )
            )
            for setting_id, setting in action.settings(action_model).items()
        ]
        document = Doc(
            Section(
                KeyValue(_("Action configured"), f"{action.icon} {action.title}"),
                KeyValue(_("Description"), action.description(action_model)),
                title=_("Action Configuration Complete"),
            ),
            Section(Buttons(*action_buttons), title=_("Settings")) if action_buttons else "",
        )
        inline_buttons: list[InlineKeyboardButton] = []
        if show_delete:
            inline_buttons.append(
                InlineKeyboardButton(
                    text=_("🗑️ Delete this action"),
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="remove", name=action_name).pack(),
                    style="danger",
                )
            )
        if show_cancel:
            inline_buttons.append(
                InlineKeyboardButton(
                    text=_("❌ Cancel"),
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="cancel").pack(),
                    style="danger",
                )
            )
        if show_done:
            inline_buttons.append(
                InlineKeyboardButton(
                    text=_("✅ Done"),
                    callback_data=ACWCoreCallback(mod=callback_prefix, op="done").pack(),
                    style="success",
                )
            )
        inline_buttons.append(
            InlineKeyboardButton(
                text=_("🔙 Back"), callback_data=ACWCoreCallback(mod=callback_prefix, op="back").pack()
            )
        )
        return document, _inline_markup(inline_buttons), str(success_message or _("Action configured successfully!"))

    @staticmethod
    async def send_action_configured(
        event: CallbackQuery | Message,
        *,
        action_name: str,
        callback_prefix: str,
        success_message: str | Any,
        action_data: dict[str, Any] | None = None,
        show_delete: bool = True,
        show_cancel: bool = True,
        show_done: bool = True,
    ) -> None:
        document, markup, answer_text = WizardRenderer.render_action_configured(
            action_name=action_name,
            callback_prefix=callback_prefix,
            success_message=success_message,
            action_data=action_data,
            show_delete=show_delete,
            show_cancel=show_cancel,
            show_done=show_done,
        )
        await reply_or_edit_rich(event, document, reply_markup=markup)
        if isinstance(event, CallbackQuery):
            await event.answer(answer_text)

    @staticmethod
    def rich_setup_message(
        text: Element | str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *extra_rows: ButtonRow,
    ) -> Doc:
        """Convert action-specific setup controls to embedded rich buttons."""
        rows = [_rich_button_row(row) for row in (reply_markup.inline_keyboard if reply_markup else [])]
        rows.extend(extra_rows)
        document = Doc(text)
        if rows:
            document += Buttons(*rows)
        return document


def _all_modern_actions() -> dict[str, Any]:
    """Load the registry lazily to keep the filters package importable."""
    from sophie_bot.modules.filters.utils_.all_modern_actions import ALL_MODERN_ACTIONS

    return ALL_MODERN_ACTIONS


def _context_button_rows(cfg: ActionWizardConfig, draft: ActionWizardDraft) -> list[ButtonRow]:
    return [
        ButtonRow(*(_rich_button(button) for button in row))
        for row in cfg.context.render_controls(draft, cfg.callback_prefix)
    ]


def _rich_button(button: InlineKeyboardButton | Button) -> Button:
    if isinstance(button, Button):
        return button
    if button.callback_data is not None:
        return Button(button.text, callback_data=button.callback_data, style=button.style)
    if button.url is not None:
        return Button(button.text, url=button.url, style=button.style)
    raise ValueError("Rich buttons require callback_data or url")


def _rich_button_row(row: list[InlineKeyboardButton]) -> ButtonRow:
    return ButtonRow(*(_rich_button(button) for button in row))


def _inline_markup(buttons: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
