from __future__ import annotations

from typing import Any

from stfu_tg import Button, ButtonRow, Buttons, Doc, Section, Template, Title
from stfu_tg.doc import Element

from sophie_bot.modules.utils_.wizard import WizardCallback, WizardView, build_wizard_navigation
from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.pagination import build_pagination_row, paginate

from .config import ActionDraft, ActionWizardConfig

_PAGE_SIZE = 8


def _callback(config: ActionWizardConfig[Any], session_id: str, op: str, arg: str = "") -> str:
    return WizardCallback(scope=config.scope, op=op, session_id=session_id, arg=arg).pack()


def render_home_view(
    config: ActionWizardConfig[Any],
    draft: ActionDraft,
    session_id: str,
    *,
    header: Element | None = None,
    footer: Element | None = None,
) -> WizardView:
    elements: list[Element] = [Title(config.title)]
    if header is not None:
        elements.append(header)
    rich_rows: list[ButtonRow] = []
    descriptions: list[Element] = []

    for action_name, action_data in draft.actions.items():
        action = ALL_MODERN_ACTIONS.get(action_name)
        controls: list[Button] = []
        if action is None:
            descriptions.append(Template(_("⚠️ Unknown action: {name}"), name=action_name))
        else:
            data_model = action.load_data(action_data)
            descriptions.append(
                Template(
                    "{icon} {title}: {description}",
                    icon=action.icon,
                    title=action.title,
                    description=action.description(data_model),
                )
            )
            if action.settings(data_model) or config.max_actions > 1:
                controls.append(
                    Button(
                        f"⚙️ {action.title}",
                        callback_data=_callback(config, session_id, "configure", action_name),
                    )
                )

        if config.max_actions > 1 or config.min_actions == 0 or action is None:
            controls.append(
                Button(
                    _("🗑️ Remove action"),
                    callback_data=_callback(config, session_id, "remove", action_name),
                    style="danger",
                )
            )
        if config.max_actions == 1:
            controls.append(
                Button(
                    _("Change action"),
                    callback_data=_callback(config, session_id, "add"),
                )
            )
        if controls:
            rich_rows.append(ButtonRow(*controls))

    if descriptions:
        elements.append(Section(*descriptions, title=_("Actions")))
    else:
        elements.append(Template(_("No actions configured.")))

    if len(draft.actions) < config.max_actions:
        label = _("➕ Set action") if config.max_actions == 1 and not draft.actions else _("➕ Add action")
        rich_rows.append(ButtonRow(Button(label, callback_data=_callback(config, session_id, "add"))))
    if rich_rows:
        elements.append(Buttons(*rich_rows))
    if footer is not None:
        elements.append(footer)
    done_callback = _callback(config, session_id, "done") if len(draft.actions) >= config.min_actions else None
    back_callback = _callback(config, session_id, "back") if config.on_back else None
    cancel_callback = _callback(config, session_id, "cancel")
    markup = build_wizard_navigation(
        done_callback=done_callback,
        back_callback=back_callback,
        cancel_callback=cancel_callback,
    )
    return WizardView(Doc(*elements), markup)


def render_add_action_view(
    config: ActionWizardConfig[Any], draft: ActionDraft, session_id: str, requested_page: int = 0
) -> WizardView:
    actions = [
        action
        for action in ALL_MODERN_ACTIONS.values()
        if (config.action_filter is None or config.action_filter(action))
        and (config.max_actions == 1 or action.name not in draft.actions)
    ]
    page = paginate(actions, _PAGE_SIZE, requested_page)
    rows = [
        ButtonRow(
            Button(
                f"{action.icon} {action.title}",
                callback_data=_callback(config, session_id, "select", action.name),
            )
        )
        for action in page.items
    ]
    elements: list[Element] = [Title(_("Select an action")), Template(_("Choose an action from the list below:"))]
    if rows:
        elements.append(Buttons(*rows))
    else:
        elements.append(Template(_("No additional actions available.")))
    pagination = build_pagination_row(
        page,
        lambda page_number: _callback(config, session_id, "add", str(page_number)),
    )
    markup = build_wizard_navigation(
        pagination=pagination,
        back_callback=_callback(config, session_id, "home"),
        cancel_callback=_callback(config, session_id, "cancel"),
    )
    return WizardView(Doc(*elements), markup)


def render_action_settings_view(
    config: ActionWizardConfig[Any],
    action_name: str,
    action_data: dict[str, Any] | None,
    session_id: str,
) -> WizardView:
    action = ALL_MODERN_ACTIONS[action_name]
    data_model = action.load_data(action_data)
    elements: list[Element] = [Title(f"{action.icon} {action.title}"), Template(action.description(data_model))]
    rows = [
        ButtonRow(
            Button(
                f"{setting.icon} {setting.title}",
                callback_data=_callback(config, session_id, "setting", f"{action_name}:{setting_id}"),
            )
        )
        for setting_id, setting in action.settings(data_model).items()
    ]
    if config.max_actions > 1:
        rows.append(
            ButtonRow(
                Button(
                    _("🗑️ Remove action"),
                    callback_data=_callback(config, session_id, "remove", action_name),
                    style="danger",
                )
            )
        )
    if rows:
        elements.append(Buttons(*rows))
    markup = build_wizard_navigation(
        back_callback=_callback(config, session_id, "home"),
        cancel_callback=_callback(config, session_id, "cancel"),
    )
    return WizardView(Doc(*elements), markup)


def render_setup_prompt(config: ActionWizardConfig[Any], prompt: Element, session_id: str) -> WizardView:
    markup = build_wizard_navigation(
        back_callback=_callback(config, session_id, "home"),
        cancel_callback=_callback(config, session_id, "cancel"),
    )
    return WizardView(Doc(Title(_("Action setup")), prompt), markup)
