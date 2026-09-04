from stfu_tg import Code, HList, KeyValue, Section

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.utils.i18n import gettext as _


async def users_stats():
    return Section(
        KeyValue(
            _("Total"),
            HList(
                KeyValue(_("users"), Code(await ChatModel.total_count((ChatType.private,))), title_bold=False),
                KeyValue(
                    _("groups"),
                    Code(await ChatModel.total_count((ChatType.supergroup, ChatType.group))),
                    title_bold=False,
                ),
                KeyValue(_("channels"), Code(await ChatModel.total_count((ChatType.channel,))), title_bold=False),
            ),
        ),
        KeyValue(
            _("New (48h)"),
            HList(
                KeyValue(_("users"), Code(await ChatModel.new_count_last_48h((ChatType.private,))), title_bold=False),
                KeyValue(
                    _("groups"),
                    Code(await ChatModel.new_count_last_48h((ChatType.supergroup, ChatType.group))),
                    title_bold=False,
                ),
                KeyValue(
                    _("channels"), Code(await ChatModel.new_count_last_48h((ChatType.channel,))), title_bold=False
                ),
            ),
        ),
        KeyValue(
            _("Active (48h)"),
            HList(
                KeyValue(
                    _("users"), Code(await ChatModel.active_count_last_48h((ChatType.private,))), title_bold=False
                ),
                KeyValue(
                    _("groups"),
                    Code(await ChatModel.active_count_last_48h((ChatType.supergroup, ChatType.group))),
                    title_bold=False,
                ),
                KeyValue(
                    _("channels"), Code(await ChatModel.active_count_last_48h((ChatType.channel,))), title_bold=False
                ),
            ),
        ),
        title=_("Users (new)"),
    )
