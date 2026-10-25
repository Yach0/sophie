from aiogram.dispatcher.event.handler import CallbackType
from redis.exceptions import RedisError
from stfu_tg import Code, Doc, Template, Title

from sophie_bot.db.models import FiltersModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.utils_.stats import get_filter_stats
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_REPORT_ROWS_PER_MESSAGE = 8
_HANDLER_EXCERPT_LENGTH = 96


@flags.handler_help(description=l_("Shows successful filter action execution counts for this chat."))
class FilterStatsHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("filterstats"),
            FeatureFlagFilter("filters"),
            FeatureFlagFilter("filter_stats"),
            GroupOrConnectedFilter(),
            UserRestricting(admin=True),
        )

    async def handle(self) -> None:
        try:
            stats = await get_filter_stats(self.connection.db_model.iid, redis=self.services.redis)
        except RedisError:
            await self.answer_rich(Doc(_("Filter statistics are temporarily unavailable.")))
            return
        document = Doc(
            Title(_("Filter statistics")),
            Template(_("Total successful action executions: {count}"), count=stats.total if stats else 0),
            _("Counts start when statistics are enabled; disabled periods are excluded."),
            _("Statistics are noncritical Redis-only data and may be lost."),
        )
        if stats:
            current_filters = await FiltersModel.get_filters(self.connection.db_model.iid) or []
            labels = {str(filter_item.id): filter_item.handler for filter_item in current_filters}
            for index, (filter_id, count) in enumerate(
                sorted(stats.counts.items(), key=lambda item: (-item[1], item[0]))
            ):
                if index and index % _REPORT_ROWS_PER_MESSAGE == 0:
                    await self.answer_rich(document)
                    document = Doc(Title(_("Filter statistics")))
                label = labels.get(filter_id, stats.labels[filter_id])
                if len(label) > _HANDLER_EXCERPT_LENGTH:
                    label = label[:_HANDLER_EXCERPT_LENGTH] + "…"
                document += Template(
                    _("{handler} ({identity}): {count}"),
                    handler=Code(label),
                    identity=Code(filter_id),
                    count=count,
                )
        await self.answer_rich(document)
