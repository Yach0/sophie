from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import IntArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from ass_tg.types.logic import OptionalArg
from stfu_tg import Button, ButtonRow, Buttons, Code, Doc, KeyValue, Section, Template

from sophie_bot.db.models import FiltersModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.callbacks import FilterManagementCallback
from sophie_bot.modules.filters.utils_.filter_action_text import filter_action_text
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Deletes a filter"), args={"handler": TextArg(l_("Text to match"))})
class FilterDeleteHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("delfilter"),
            FeatureFlagFilter("filters"),
            GroupOrConnectedFilter(),
            UserRestricting(admin=True),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"index": OptionalArg(IntArg(l_("?Filter index"))), "handler": TextArg(l_("Text to match"))}

    async def _many_filters_message(self, keyword: str, items: list[FiltersModel]) -> Any:
        button_rows = [
            ButtonRow(
                Button(
                    f"{_('Delete')} #{index}",
                    callback_data=FilterManagementCallback(operation="delete", oid=str(item.id)).pack(),
                    style="danger",
                )
            )
            for index, item in enumerate(items, start=1)
        ]
        document = Doc(
            Template(_("There are multiple filters with keyword {keyword}!"), keyword=Code(keyword)),
            Section(
                *(
                    KeyValue(index + 1, filter_action_text(item.action, list(item.actions.keys())), suffix=" -> ")
                    for index, item in enumerate(items)
                ),
                title=_("Filters"),
            ),
            Template(_("Choose the filter to delete using the buttons below.")),
            Buttons(*button_rows),
        )
        return await self.answer_rich(document)

    async def handle(self) -> Any:
        if not self.event.from_user:
            return
        keyword: str = self.data["handler"]
        index = (self.data["index"] or 1) - 1
        items = await FiltersModel.get_all_by_keyword(self.connection.db_model.iid, keyword)
        if not items:
            return await self.event.reply(
                Doc(
                    Template(_("The filter with keyword {keyword} does not exist!"), keyword=Code(keyword)),
                    Template(_("Please check the available filters using {cmd} command."), cmd="/filters"),
                ).to_html()
            )
        if len(items) > 1 and not self.data["index"]:
            return await self._many_filters_message(keyword, items)
        if index < 0 or index >= len(items):
            return await self.event.reply(_("Filter index is out of range."))
        await items[index].delete()
        await log_event(
            self.connection.tid,
            self.event.from_user.id,
            LogEvent.FILTER_DELETED,
            {"keyword": keyword},
        )
        return await self.event.reply(Template(_("The filter {keyword} was deleted."), keyword=Code(keyword)).to_html())
