from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import Chat, Message, TelegramObject, User
from stfu_tg import Doc

from sophie_bot.config import CONFIG
from sophie_bot.constants import FILTERS_MAX_TRIGGERS, FILTERS_SILENT_MODE_DELETE_DELAY_SECONDS
from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.ai.utils.ai_filter_texts import AI_FILTER_STATUS
from sophie_bot.modules.ai.utils.ai_header import ai_table_header
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.filters.fsm import FilterEditFSM
from sophie_bot.modules.filters.types.modern_action_abc import ActionResult
from sophie_bot.modules.filters.utils_.handle_action import (
    get_effective_filter_actions,
    handle_effective_filter_action,
)
from sophie_bot.modules.filters.utils_.match_handler import match_filter_handler
from sophie_bot.modules.help.utils.extract_info import get_all_cmds_raw
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delayed_delete import schedule_message_deletion
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


class EnforceFiltersMiddleware(BaseMiddleware):
    @staticmethod
    @lru_cache
    def _get_all_cmds() -> tuple[str, ...]:
        return get_all_cmds_raw()

    async def _is_to_drop(self, message: Message, state: FSMContext | None) -> bool:
        sender: User | Chat | None = message.sender_chat or message.from_user

        if not sender:
            log.debug("EnforceFiltersMiddleware: no sender, dropping...")
            return True

        if message.chat.type not in {"group", "supergroup"}:
            log.debug("EnforceFiltersMiddleware: not a group, dropping...")
            return True

        # Check for the filter setup states
        if state and FilterEditFSM.__name__ in (await state.get_state() or ""):
            log.debug("EnforceFiltersMiddleware: filter setup state, dropping...")
            return True

        # Check for the commands
        # This code is a little bit shit but honestly I don't see any other way to do it
        # Outer middlewares runs BEFORE filters, so we cannot access the CMDFilter,
        # therefore, we can't get the command object reliably
        # parsing it here is the only way
        text = message.text
        chat_id = message.chat.id
        if text and len(text) > 3 and any(text.startswith(prefix) for prefix in CONFIG.commands_prefix):
            cmd_text = text[1:].lower().split(" ", 1)[0]

            if cmd_text in self._get_all_cmds() and await is_user_admin(chat_id, sender.id):
                log.debug("EnforceFiltersMiddleware: admin and command, dropping...")
                return True

        return False

    @staticmethod
    async def _handle_filter_actions(
        filter_item: FiltersModel, triggered_actions: list[str], message: Message, data: dict[str, Any]
    ) -> tuple[list[str], list[ActionResult]]:
        log.debug("EnforceFiltersMiddleware: handling filter actions...")

        triggered: list[str] = []
        messages = []

        # Inject filter ID into data for logging purposes
        data["filter_id"] = str(filter_item.id)

        for action in get_effective_filter_actions(filter_item):
            if action.name in triggered_actions:
                log.debug("EnforceFiltersMiddleware: already triggered action, dropping...")
                continue

            log.debug("EnforceFiltersMiddleware: handling action", action=action.name)

            action_message = await handle_effective_filter_action(message, action, data, filter_item)
            if action_message:
                messages.append(action_message)
            triggered.append(action.name)

        return triggered, messages

    @staticmethod
    async def _handle_action_messages(
        message: Message, messages: list[ActionResult], ai_matched: bool = False
    ) -> list[int]:
        """Sends the aggregated filter text and returns the IDs of every message the bot produced.

        Actions that deliver their own message(s) (notes/replies carrying buttons or files, rules)
        return them instead of text, so they only contribute their IDs and stay out of the doc.
        """
        sent_message_ids: list[int] = []
        doc = Doc()
        if ai_matched:
            # An AI filter decided this, so the reply carries the AI header and can be replied to
            # like any other AI message to carry on the conversation.
            doc += ai_table_header(str(AI_FILTER_STATUS))

        for msg in messages:
            if isinstance(msg, Message):
                sent_message_ids.append(msg.message_id)
                continue

            if isinstance(msg, list):
                sent_messages = [sent for sent in msg if isinstance(sent, Message)]
                # stfu elements subclass list, so only a list of actual Messages counts as "already sent"
                if len(sent_messages) == len(msg):
                    sent_message_ids.extend(sent.message_id for sent in sent_messages)
                    continue

            doc += " "
            doc += msg

        if not len(doc):
            return sent_message_ids

        async def send_message():
            return await bot.send_message(chat_id=message.chat.id, text=doc.to_html())

        if ai_matched:
            reply = await common_try(send_ai_rich_message(message, doc), reply_not_found=send_message)
        else:
            reply = await common_try(message.reply(doc.to_html()), reply_not_found=send_message)
        if isinstance(reply, Message):
            sent_message_ids.append(reply.message_id)

        return sent_message_ids

    async def _process_filter(
        self, message: Message, data: dict[str, Any], matched_filter: FiltersModel, triggered_groups=None
    ) -> tuple[list[str], list[ActionResult]]:
        if triggered_groups is None:
            triggered_groups = []
        if get_effective_filter_actions(matched_filter):
            return await self._handle_filter_actions(matched_filter, triggered_groups, message, data)
        raise SophieException("EnforceFiltersMiddleware: no actions found")

    async def _process_filters(self, message: Message, data: dict[str, Any]):
        chat_db = data.get("chat_db")
        if chat_db is None:
            log.debug("EnforceFiltersMiddleware: chat_db is None, skipping...")
            return

        all_filters = await FiltersModel.get_filters(chat_db.iid)
        if not all_filters:
            return

        matched_filters: list[FiltersModel] = []
        user_in_group = data.get("user_in_group")
        ai_filters: list[FiltersModel] = []
        for filter_item in all_filters:
            if filter_item.handler.startswith("ai:"):
                ai_filters.append(filter_item)
                continue

            matched = await match_filter_handler(
                message,
                filter_item.handler,
                user_in_group=user_in_group,
                enable_lock_types=filter_item.effective_version >= 2,
                chat_iid=chat_db.iid,
            )

            if matched:
                matched_filters.append(filter_item)

        if not matched_filters and ai_filters:
            matched = await match_filter_handler(
                message,
                ai_filters[0].handler,
                user_in_group=user_in_group,
                enable_lock_types=ai_filters[0].effective_version >= 2,
                chat_iid=chat_db.iid,
            )
            if matched:
                matched_filters.append(ai_filters[0])
            if len(ai_filters) > 1:
                log.debug("EnforceFiltersMiddleware: skipping extra AI filters for this message")

        all_messages = []
        triggered_groups: list[str] = []  # Handled action groups, to stop same actions from repeating
        silent = False

        for idx, matched_filter in enumerate(matched_filters):
            if idx >= FILTERS_MAX_TRIGGERS:
                log.debug("EnforceFiltersMiddleware: triggered maximum number of filters, dropping...")
                break

            actions, messages = await self._process_filter(
                message, data, matched_filter, triggered_groups=triggered_groups
            )
            all_messages.extend(messages)
            triggered_groups.extend(action for action in actions if action)
            silent = silent or matched_filter.silent

        sent_message_ids: list[int] = []
        if all_messages:
            ai_matched = any(matched.handler.startswith("ai:") for matched in matched_filters)
            sent_message_ids = await self._handle_action_messages(message, all_messages, ai_matched=ai_matched)
            data["ai_filter_handled"] = ai_matched

        # A single reply aggregates every triggered filter, so one silent filter makes the whole exchange silent
        if silent and await is_enabled("filters_silent_mode", chat_tid=message.chat.id):
            schedule_message_deletion(
                message.chat.id,
                [message.message_id, *sent_message_ids],
                delay_seconds=FILTERS_SILENT_MODE_DELETE_DELAY_SECONDS,
            )

        # If filter triggered - skip other handlers
        if matched_filters:
            raise SkipHandler

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        log.debug("EnforceFiltersMiddleware: checking filters...")

        if not isinstance(event, Message):
            raise SophieException("EnforceFiltersMiddleware: not a message")

        if await self._is_to_drop(event, data.get("state")):
            log.debug("EnforceFiltersMiddleware: dropping...")
            return await handler(event, data)

        if not await is_enabled("filters", chat_tid=event.chat.id):
            log.debug("EnforceFiltersMiddleware: filters feature disabled globally, skipping...")
            return await handler(event, data)

        await self._process_filters(event, data)

        return await handler(event, data)
