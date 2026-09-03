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


def render_home_view(
    config: ActionWizardConfig[Any],
    draft: ActionDraft,
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
        if action is None:
            continue
        data_model = action.load_data(action_data)
        descriptions.append(
            Template(
                "{icon} {title}: {description}",
                icon=action.icon,
                title=action.title,
                description=action.description(data_model),
            )
        )
        settings = action.settings(data_model)
        controls: list[Button] = []
        if settings or config.max_actions > 1:
            controls.append(
                Button(
                    f"⚙️ {action.title}",
                    callback_data=WizardCallback(scope=config.scope, op="configure", arg=action_name).pack(),
                )
            )
        if config.max_actions > 1:
            controls.append(
                Button(
                    "🗑️",
                    callback_data=WizardCallback(scope=config.scope, op="remove", arg=action_name).pack(),
                    style="danger",
                )
            )
        else:
            controls.append(
                Button(
                    _("Change action"),
                    callback_data=WizardCallback(scope=config.scope, op="add").pack(),
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
        rich_rows.append(ButtonRow(Button(label, callback_data=WizardCallback(scope=config.scope, op="add").pack())))
    if rich_rows:
        elements.append(Buttons(*rich_rows))
    if footer is not None:
        elements.append(footer)
    done_callback = WizardCallback(scope=config.scope, op="done").pack() if draft.actions else None
    back_callback = WizardCallback(scope=config.scope, op="back").pack() if config.on_back else None
    cancel_callback = WizardCallback(scope=config.scope, op="cancel").pack()
    markup = build_wizard_navigation(
        done_callback=done_callback,
        back_callback=back_callback,
        cancel_callback=cancel_callback,
    )
    return WizardView(Doc(*elements), markup)


def render_add_action_view(config: ActionWizardConfig[Any], draft: ActionDraft, requested_page: int = 0) -> WizardView:
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
                callback_data=WizardCallback(scope=config.scope, op="select", arg=action.name).pack(),
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
        lambda page_number: WizardCallback(scope=config.scope, op="add", arg=str(page_number)).pack(),
    )
    markup = build_wizard_navigation(
        pagination=pagination,
        back_callback=WizardCallback(scope=config.scope, op="home").pack(),
        cancel_callback=WizardCallback(scope=config.scope, op="cancel").pack(),
    )
    return WizardView(Doc(*elements), markup)


def render_action_settings_view(
    config: ActionWizardConfig[Any], action_name: str, action_data: dict[str, Any] | None
) -> WizardView:
    action = ALL_MODERN_ACTIONS[action_name]
    data_model = action.load_data(action_data)
    elements: list[Element] = [Title(f"{action.icon} {action.title}"), Template(action.description(data_model))]
    rows = [
        ButtonRow(
            Button(
                f"{setting.icon} {setting.title}",
                callback_data=WizardCallback(
                    scope=config.scope, op="setting", arg=f"{action_name}:{setting_id}"
                ).pack(),
            )
        )
        for setting_id, setting in action.settings(data_model).items()
    ]
    if config.max_actions > 1:
        rows.append(
            ButtonRow(
                Button(
                    _("🗑️ Remove action"),
                    callback_data=WizardCallback(scope=config.scope, op="remove", arg=action_name).pack(),
                    style="danger",
                )
            )
        )
    if rows:
        elements.append(Buttons(*rows))
    markup = build_wizard_navigation(
        back_callback=WizardCallback(scope=config.scope, op="home").pack(),
        cancel_callback=WizardCallback(scope=config.scope, op="cancel").pack(),
    )
    return WizardView(Doc(*elements), markup)


def render_setup_prompt(config: ActionWizardConfig[Any], prompt: Element) -> WizardView:
    markup = build_wizard_navigation(
        back_callback=WizardCallback(scope=config.scope, op="home").pack(),
        cancel_callback=WizardCallback(scope=config.scope, op="cancel").pack(),
    )
    return WizardView(Doc(Title(_("Action setup")), prompt), markup)
