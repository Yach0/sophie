from html.parser import HTMLParser
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from beanie import PydanticObjectId
from bson.errors import InvalidId
from stfu_tg import Button, ButtonRow, Buttons, Doc, KeyValue, Section, Template

from sophie_bot.db.models import FiltersModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.callbacks import (
    FilterDeleteConfirmCallback,
    FilterManagementCallback,
    FiltersPageCallback,
)
from sophie_bot.modules.filters.filter_wizard import FILTER_WIZARD, FilterDraft
from sophie_bot.modules.filters.utils_.filter_action_text import filter_action_text
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.reply_or_edit import reply_or_edit_rich
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils import flags
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.handlers import SophieCallbackQueryHandler, SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.pagination import build_pagination_row, paginate

_PAGE_SIZE = 8
_MAX_FILTER_TEXT_LENGTH = 3900  # Keep room below Telegram's 4096-character text limit.
_FILTER_EXCERPT_LENGTH = 512


class _VisibleTextLength(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data.encode("utf-16-le")) // 2


def _fits_telegram_message(document: Doc) -> bool:
    counter = _VisibleTextLength()
    counter.feed(document.to_rich())
    return counter.length <= _MAX_FILTER_TEXT_LENGTH


@flags.disableable(name="filters")
@flags.handler_help(description=l_("Lists all filters in the chat."))
class FiltersListHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("filters"),
            FeatureFlagFilter("filters"),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        filters = await FiltersModel.get_filters(self.connection.db_model.iid) or []
        await _render_filter_page(
            self.event,
            self.connection.tid,
            self.connection.title,
            filters,
            0,
            services=self.services,
        )


class FiltersPageHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            FiltersPageCallback.filter(),
            FeatureFlagFilter("filters"),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        filters = await FiltersModel.get_filters(self.connection.db_model.iid) or []
        await _render_filter_page(
            callback,
            self.connection.tid,
            self.connection.title,
            filters,
            self.data["callback_data"].page,
            services=self.services,
        )
        await callback.answer()


class FilterEditFromListHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            FilterManagementCallback.filter(F.operation == "edit"),
            FeatureFlagFilter("action_config_wizard"),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        data: FilterManagementCallback = self.data["callback_data"]
        filter_model = await _get_owned_filter(self.connection.db_model.iid, data.oid)
        if filter_model is None:
            await callback.answer(_("Filter not found."), show_alert=True)
            return
        await FILTER_WIZARD.start(self, FilterDraft.from_model(filter_model))
        await callback.answer()


class FilterDeletePromptHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            FilterManagementCallback.filter(F.operation == "delete"),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        data: FilterManagementCallback = self.data["callback_data"]
        filter_model = await _get_owned_filter(self.connection.db_model.iid, data.oid)
        if filter_model is None:
            await callback.answer(_("Filter not found."), show_alert=True)
            return
        await _show_delete_confirmation(callback, filter_model, services=self.services)


class FilterDeleteConfirmHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            FilterDeleteConfirmCallback.filter(),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        callback: CallbackQuery = self.event
        data: FilterDeleteConfirmCallback = self.data["callback_data"]
        filter_model = await _get_owned_filter(self.connection.db_model.iid, data.oid)
        if filter_model is None:
            await callback.answer(_("Filter not found."), show_alert=True)
            return
        if not callback.from_user:
            return
        keyword = filter_model.handler
        await filter_model.delete()
        await log_event(self.connection.tid, callback.from_user.id, LogEvent.FILTER_DELETED, {"keyword": keyword})
        await callback.answer(_("Filter deleted."))
        if callback.message and isinstance(callback.message, Message):
            document = Template(_("🗑 The filter with keyword {keyword} was deleted!"), keyword=keyword)
            await reply_or_edit_rich(callback, document, bot=self.services.bot)


async def _get_owned_filter(chat_iid: PydanticObjectId, raw_oid: str) -> FiltersModel | None:
    try:
        oid = PydanticObjectId(raw_oid)
    except (InvalidId, TypeError):
        return None
    return await FiltersModel.find_one(FiltersModel.id == oid, FiltersModel.chat.id == chat_iid)


async def _show_delete_confirmation(
    callback: CallbackQuery,
    filter_model: FiltersModel,
    *,
    services: ApplicationServices,
) -> None:
    if not callback.message or not isinstance(callback.message, Message):
        await callback.answer(_("Message not found."))
        return
    summary = filter_action_text(
        filter_model.action,
        list(filter_model.actions),
        services.modules.actions,
    )
    document = Doc(
        Template(_("Delete filter {handler}?"), handler=filter_model.handler),
        KeyValue(_("Actions"), summary),
        Buttons(
            ButtonRow(
                Button(
                    _("🗑 Delete"),
                    callback_data=FilterDeleteConfirmCallback(oid=str(filter_model.id)).pack(),
                    style="danger",
                )
            )
        ),
    )
    await reply_or_edit_rich(
        callback,
        document,
        bot=services.bot,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=_("Cancel"), callback_data=FiltersPageCallback(page=0).pack())]]
        ),
    )
    await callback.answer()


async def _render_filter_page(
    event: Message | CallbackQuery,
    chat_tid: int,
    chat_title: str | None,
    all_filters: list[FiltersModel],
    requested_page: int,
    *,
    services: ApplicationServices,
) -> None:
    if not all_filters:
        document = Doc(_("There are no filters in this chat!\nUse /addfilter <handler> to create one."))
        await reply_or_edit_rich(event, document, bot=services.bot)
        return

    # Page boundaries are rebuilt from the same ordered filters for commands and callbacks.
    # Include the title, controls and fixed footer in every size check.
    edit_enabled = await is_enabled(
        "action_config_wizard",
        chat_tid=chat_tid,
        redis=services.redis,
    )
    title = Template(_("Filters in {chat_name}"), chat_name=chat_title or "Unknown")

    def document_for(rows: list[Any]) -> Doc:
        document = Doc(Section(*rows, title=title))
        document += " "
        document += _("Additionally rules from 'Antiflood' module can be enforced.")
        document += _("Additionally rules from 'Locks' module can be enforced.")
        return document

    pages: list[list[Any]] = []
    rows: list[Any] = []
    count = 0
    for item in all_filters:
        action_text = filter_action_text(item.action, list(item.actions), services.modules.actions)
        handler = item.handler
        controls: list[Button] = []
        if edit_enabled:
            controls.append(
                Button(_("Edit"), callback_data=FilterManagementCallback(operation="edit", oid=str(item.id)).pack())
            )
        controls.append(
            Button(
                _("Delete"),
                callback_data=FilterManagementCallback(operation="delete", oid=str(item.id)).pack(),
                style="danger",
            )
        )
        buttons = Buttons(ButtonRow(*controls))

        candidate = [KeyValue(handler, action_text, suffix=" -> "), buttons]
        if not _fits_telegram_message(document_for(candidate)):
            # Truncate source text, not rendered markup, so STFU still escapes it.
            if len(handler) > _FILTER_EXCERPT_LENGTH:
                handler = handler[:_FILTER_EXCERPT_LENGTH] + "…"
                candidate = [KeyValue(handler, action_text, suffix=" -> "), buttons]
            if not _fits_telegram_message(document_for(candidate)):
                action_text = _("Action description is too long to display.")
                candidate = [KeyValue(handler, action_text, suffix=" -> "), buttons]

        if rows and (count == _PAGE_SIZE or not _fits_telegram_message(document_for(rows + candidate))):
            pages.append(rows)
            rows = []
            count = 0
        rows.extend(candidate)
        count += 1
    pages.append(rows)
    page = paginate(pages, 1, requested_page)
    document = document_for(page.items[0])

    navigation = build_pagination_row(page, lambda page_number: FiltersPageCallback(page=page_number).pack())
    markup: InlineKeyboardMarkup | None = None
    if navigation:
        markup = InlineKeyboardMarkup(inline_keyboard=[navigation])
    await reply_or_edit_rich(event, document, bot=services.bot, reply_markup=markup)
