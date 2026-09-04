from __future__ import annotations

from random import choice
from string import printable

from babel.support import LazyProxy as BabelLazyProxy
from bson import ObjectId
from regex import regex
from stfu_tg import Code, Doc, Template
from stfu_tg.doc import Element

from sophie_bot.constants import AI_FILTER_LIMIT_PER_CHAT
from sophie_bot.db.models import FiltersModel
from sophie_bot.modules.locks.utils.conflicts import get_lock_type_owner
from sophie_bot.modules.locks.utils.lock_types import is_supported_lock_type
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


class InvalidFilterHandler(ValueError):
    def __init__(self, message: str, document: Element | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.document = document


async def validate_filter_handler(chat_iid: ObjectId, keyword: str, editing_id: str | None = None) -> None:
    if is_supported_lock_type(keyword):
        existing_lock_owner = await get_lock_type_owner(chat_iid, keyword)
        if existing_lock_owner == "locks":
            raise InvalidFilterHandler(
                _("The lock type is already enforced by the Locks module."),
                Doc(
                    Template(
                        _("The lock type {handler} is already enforced by the Locks module."), handler=Code(keyword)
                    ),
                    Template(
                        _("Delete it there first with {cmd} before adding it as a filter."),
                        cmd=Code(f"/unlock {keyword}"),
                    ),
                ),
            )
        if existing_lock_owner == "filters":
            raise InvalidFilterHandler(
                _("The lock-filter already exists! Please edit the filter instead."),
                Template(
                    _("The lock-filter {name} already exists! Please use {cmd} to edit the filter."),
                    name=Code(keyword),
                    cmd=Code(f"/editfilter {keyword}"),
                ),
            )

    existing = await FiltersModel.get_all_by_keyword(chat_iid, keyword)
    if any(str(found.id) != editing_id for found in existing):
        raise InvalidFilterHandler(
            _("A filter with this handler already exists."),
            Doc(
                Template(_("Filter with the handler {handler} already exists!"), handler=Code(keyword)),
                Template(_("You can edit the filter's actions with {cmd}."), cmd=Code(f"/editfilter {keyword}")),
            ),
        )

    if keyword.startswith("ai:"):
        prompt = keyword[3:].strip()
        if not prompt:
            log.info("validate_filter_handler: empty AI prompt")
            raise InvalidFilterHandler(
                _("AI filter prompt cannot be empty. Please provide a description of when to trigger the filter."),
                Template(
                    _(
                        "AI filter prompt cannot be empty. Please provide a description of when to trigger the filter.\n"
                        "Example: ai:Message contains crypto scam"
                    )
                ),
            )
        is_editing_ai_filter = False
        if editing_id:
            existing_filter = await FiltersModel.get_by_id(ObjectId(editing_id))
            is_editing_ai_filter = bool(existing_filter and existing_filter.handler.startswith("ai:"))
        if not is_editing_ai_filter and await FiltersModel.count_ai_filters(chat_iid) >= AI_FILTER_LIMIT_PER_CHAT:
            log.info("validate_filter_handler: AI filter limit reached", chat_iid=chat_iid)
            raise InvalidFilterHandler(
                _("Maximum number of AI filter handlers reached."),
                Template(
                    _(
                        "Maximum number of AI filter handlers reached ({limit} per chat).\n"
                        "AI filters consume tokens and can overload the system. "
                        "Please remove an existing AI filter before adding a new one."
                    ),
                    limit=AI_FILTER_LIMIT_PER_CHAT,
                ),
            )

    if keyword.startswith("re:"):
        pattern = keyword[3:]
        random_text = "".join(choice(printable) for _ in range(50))
        try:
            regex.match(pattern, random_text, timeout=0.2)
        except TimeoutError:
            log.info("validate_filter_handler: regex too slow")
            raise InvalidFilterHandler(
                _(
                    "Provided regex pattern is too slow to execute. Please review the pattern and try adding the filter again."
                )
            )
        except regex.error:
            log.info("validate_filter_handler: invalid regex pattern")
            raise InvalidFilterHandler(_("Provided regex pattern is invalid. Please check the syntax and try again."))


def describe_filter_handler(keyword: str) -> Element | str | LazyProxy | BabelLazyProxy:
    if keyword.startswith("ai:"):
        return Template(_("When AI detects: {prompt}"), prompt=Code(keyword[3:]))
    if keyword.startswith("re:"):
        return Template(_("When messages matches the regex pattern {pattern}"), pattern=Code(keyword[3:]))
    return Template(_("When {handler} in message"), handler=Code(keyword))
