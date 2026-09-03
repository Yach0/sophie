from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel
from stfu_tg import Template

from sophie_bot.modules.utils_.wizard import WizardCallback, WizardSession, WizardView
from sophie_bot.shared.action_registry import ALL_MODERN_ACTIONS
from sophie_bot.shared.actions import ActionSetupTryAgainException, ModernActionABC
from sophie_bot.utils.handlers import SophieBaseHandler, SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _

from .config import ActionDraft, ActionWizardConfig
from .views import (
    render_action_settings_view,
    render_add_action_view,
    render_home_view,
    render_setup_prompt,
)


class ActionWizard[DRAFT: ActionDraft]:
    def __init__(self, config: ActionWizardConfig[DRAFT]) -> None:
        self.config = config

    def _session(self, handler: SophieBaseHandler[Any]) -> WizardSession:
        return WizardSession(handler.state, self.config.scope)

    def render_home(self, draft: DRAFT) -> WizardView:
        return render_home_view(self.config, draft)

    async def start(self, handler: SophieBaseHandler[Any], draft: DRAFT | None = None) -> None:
        chat_iid = handler.connection.db_model.iid
        if draft is None:
            draft = await self.config.load_draft(chat_iid) if self.config.load_draft else self.config.draft_model()
        session = self._session(handler)
        await session.start(chat_iid, draft.model_dump(mode="json"))
        view = self.render_home(draft)
        await handler.answer_rich(view.doc, reply_markup=view.markup)

    async def handle_callback(self, handler: SophieCallbackQueryHandler, callback: WizardCallback) -> None:
        session = self._session(handler)
        callback_query = handler.event
        if callback.op == "open":
            if await session.is_active(handler.connection.db_model.iid):
                if await session.get_input_context() is not None:
                    await session.clear_input()
                draft = await self._get_draft(session)
                await self._render_home(handler, session, draft)
                await callback_query.answer()
                return
            if self.config.load_draft is None:
                await self._alert(callback_query, _("Invalid callback data."))
                return
            await self.start(handler)
            await callback_query.answer()
            return

        if not await session.is_active(handler.connection.db_model.iid):
            await session.clear()
            await self._alert(callback_query, _("This session has expired. Please run the command again."))
            return

        if await session.get_input_context() is not None:
            await session.clear_input()
        draft = await self._get_draft(session)

        match callback.op:
            case "home":
                await self._render_home(handler, session, draft)
            case "add":
                await self._add(handler, session, draft, callback.arg)
            case "select":
                await self._select(handler, session, draft, callback.arg)
            case "configure":
                await self._configure(handler, callback.arg, draft)
            case "setting":
                await self._setting(handler, session, draft, callback.arg)
            case "remove":
                await self._remove(handler, session, draft, callback.arg)
            case "done":
                await self._done(handler, session, draft)
            case "cancel":
                await self._cancel(handler, session)
            case "back":
                await self._back(handler, session, draft)
            case _:
                await self._alert(callback_query, _("Invalid callback data."))
                return
        await callback_query.answer()

    async def handle_input(self, handler: SophieBaseHandler[Message]) -> None:
        session = self._session(handler)
        if not await session.is_active(handler.connection.db_model.iid):
            await session.clear()
            await handler.event.reply(_("This session has expired. Please run the command again."))
            return

        context = await session.get_input_context()
        if not isinstance(context, dict):
            await session.clear()
            await handler.event.reply(_("This session has expired. Please run the command again."))
            return
        action_name = context.get("action_name")
        if not isinstance(action_name, str):
            await session.clear()
            await handler.event.reply(_("Invalid callback data."))
            return

        draft = await self._get_draft(session)
        action = self._action(action_name)
        if action is None:
            await session.clear()
            await handler.event.reply(_("Invalid callback data."))
            return

        setting_id = context.get("setting_id")
        setting = (
            action.interactive_setup
            if setting_id is None
            else action.settings(action.load_data(draft.actions.get(action_name))).get(setting_id)
        )
        if setting is None or setting.setup_confirm is None:
            await session.clear()
            await handler.event.reply(_("Invalid callback data."))
            return

        try:
            value = await setting.setup_confirm(handler.event, handler.data)
        except ActionSetupTryAgainException as error:
            if str(error):
                await handler.event.reply(str(error))
            return

        if self.config.max_actions == 1 and setting_id is None:
            draft.actions.clear()
        draft.actions[action_name] = self._dump_value(value)
        await session.set_draft(draft.model_dump(mode="json"))
        await session.clear_input()
        await self._render_home(handler, session, draft)

    async def _add(
        self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT, argument: str
    ) -> None:
        if not argument:
            view = render_add_action_view(self.config, draft)
        else:
            try:
                page = int(argument)
            except ValueError:
                await self._alert(handler.event, _("Invalid callback data."))
                return
            view = render_add_action_view(self.config, draft, page)
        await handler.answer_rich(view.doc, reply_markup=view.markup)

    async def _select(
        self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT, action_name: str
    ) -> None:
        action = self._action(action_name)
        if action is None or not self._allowed(action):
            await self._alert(handler.event, _("Unknown action."))
            return
        if (action_name in draft.actions and self.config.max_actions > 1) or (
            self.config.max_actions > 1 and len(draft.actions) >= self.config.max_actions
        ):
            await self._alert(handler.event, _("This action cannot be added."))
            return
        if action.interactive_setup and action.interactive_setup.setup_message:
            await session.start_input(action_name=action_name)
            prompt = await action.interactive_setup.setup_message(handler.event, handler.data)
            view = render_setup_prompt(self.config, prompt)
            await handler.answer_rich(view.doc, reply_markup=view.markup)
            return
        if self.config.max_actions == 1:
            draft.actions.clear()
        draft.actions[action_name] = None
        await session.set_draft(draft.model_dump(mode="json"))
        await self._render_home(handler, session, draft)

    async def _configure(self, handler: SophieCallbackQueryHandler, action_name: str, draft: DRAFT) -> None:
        if action_name not in draft.actions or self._action(action_name) is None:
            await self._alert(handler.event, _("Unknown action."))
            return
        view = render_action_settings_view(self.config, action_name, draft.actions[action_name])
        await handler.answer_rich(view.doc, reply_markup=view.markup)

    async def _setting(
        self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT, argument: str
    ) -> None:
        if argument.count(":") != 1:
            await self._alert(handler.event, _("Invalid callback data."))
            return
        action_name, setting_id = argument.split(":", 1)
        action = self._action(action_name)
        if action is None or action_name not in draft.actions:
            await self._alert(handler.event, _("Unknown action."))
            return
        setting = action.settings(action.load_data(draft.actions[action_name])).get(setting_id)
        if setting is None or setting.setup_message is None:
            await self._alert(handler.event, _("Unknown setting."))
            return
        await session.start_input(action_name=action_name, setting_id=setting_id)
        prompt = await setting.setup_message(handler.event, handler.data)
        view = render_setup_prompt(self.config, prompt)
        await handler.answer_rich(view.doc, reply_markup=view.markup)

    async def _remove(
        self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT, action_name: str
    ) -> None:
        if action_name not in draft.actions:
            await self._alert(handler.event, _("Unknown action."))
            return
        del draft.actions[action_name]
        await session.set_draft(draft.model_dump(mode="json"))
        await self._render_home(handler, session, draft)

    async def _done(self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT) -> None:
        if not draft.actions:
            await self._alert(handler.event, _("No actions configured."))
            return
        try:
            await self.config.save_draft(handler.connection.db_model.iid, draft, handler.event, handler.connection)
        except (ValueError, TypeError) as error:
            await self._alert(handler.event, str(error))
            return
        await session.clear()
        await handler.answer_rich(Template(self.config.done_message, keyword=getattr(draft, "handler", "")))

    async def _cancel(self, handler: SophieCallbackQueryHandler, session: WizardSession) -> None:
        await session.clear()
        await handler.answer_rich(Template(_("Configuration cancelled.")))

    async def _back(self, handler: SophieCallbackQueryHandler, session: WizardSession, draft: DRAFT) -> None:
        if self.config.on_back is not None:
            await session.clear()
            await self.config.on_back(handler, handler.event)
            return
        await self._render_home(handler, session, draft)

    async def _render_home(self, handler: SophieBaseHandler[Any], session: WizardSession, draft: DRAFT) -> None:
        await session.set_draft(draft.model_dump(mode="json"))
        view = self.render_home(draft)
        await handler.answer_rich(view.doc, reply_markup=view.markup)

    async def _get_draft(self, session: WizardSession) -> DRAFT:
        draft = await session.get_draft()
        return self.config.draft_model.model_validate(draft or {})

    def _action(self, name: str) -> ModernActionABC[Any] | None:
        return ALL_MODERN_ACTIONS.get(name)

    def _allowed(self, action: ModernActionABC[Any]) -> bool:
        return self.config.action_filter is None or self.config.action_filter(action)

    def _dump_value(self, value: BaseModel | None) -> dict[str, Any] | None:
        return value.model_dump(mode="json") if isinstance(value, BaseModel) else None

    @staticmethod
    async def _alert(callback: CallbackQuery, text: str) -> None:
        await callback.answer(text, show_alert=True)


__all__ = ["ActionWizard"]
