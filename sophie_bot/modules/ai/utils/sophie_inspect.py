from __future__ import annotations

from datetime import datetime, timedelta, timezone

from beanie import PydanticObjectId
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models import Model

from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.modules.ai.utils.ai_catalog import resolve_model_name
from sophie_bot.modules.ai.utils.ai_mode import get_chat_mode
from sophie_bot.modules.ai.utils.ai_model_factory import get_ai_model
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions, run_ai_text
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.sophie_inspect_source import read_source, search_source
from sophie_bot.services.redis import aredis
from sophie_bot.utils.ai_features import AI_FEATURE_SOPHIE_INSPECT
from sophie_bot.utils.feature_flags import FeatureType, get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

_SYSTEM_PROMPT = (
    "You are given read access to the source code of Sophie, a Telegram group management bot, to "
    "answer one question about how Sophie behaves.\n"
    "Search for the relevant code, read only what you need, and stop as soon as you can answer.\n"
    "Answer in at most four sentences, describing observable behaviour in plain words: what the bot "
    "does, when, and what the user can control. Name the setting or command involved when there is one.\n"
    "Do not paste code, do not name internal functions or files, and do not speculate. If the code "
    "does not answer the question, say so plainly."
)


def _parse_chat_ids(raw_value: object) -> frozenset[int]:
    identifiers = set()
    for entry in str(raw_value).replace(",", " ").split():
        try:
            identifiers.add(int(entry))
        except ValueError:
            log.warning("sophie_inspect: ignoring an unparsable chat id", entry=entry)
    return frozenset(identifiers)


async def is_sophie_inspect_chat(chat_tid: int | None) -> bool:
    """Whether a group is on the list allowed to use source inspection.

    Sophie-help gets it from its mode; this is the escape hatch for the chats where people ask how
    Sophie works, without turning it on for every group.
    """
    if chat_tid is None:
        return False
    return chat_tid in _parse_chat_ids(await get_value("ai_sophie_inspect_chats", chat_tid=chat_tid))


def _daily_limit_key(chat_iid: PydanticObjectId, now: datetime) -> str:
    return f"ai_sophie_inspect_daily:{chat_iid}:{now.strftime('%Y%m%d')}"


def _seconds_until_next_utc_day(now: datetime) -> int:
    next_day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((next_day - now).total_seconds()), 1)


async def _feature_int(feature: FeatureType, chat_tid: int | None, minimum: int = 1) -> int:
    value = await get_value(feature, chat_tid=chat_tid)
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return minimum


async def _consume_daily_quota(chat_iid: PydanticObjectId, chat_tid: int | None) -> bool:
    """Cap how often one chat can start a sub-agent per day, on top of the chat's credit quota."""
    limit = await _feature_int("ai_sophie_inspect_daily_chat_limit", chat_tid)
    now = datetime.now(timezone.utc)
    key = _daily_limit_key(chat_iid, now)

    async with aredis.pipeline() as pipe:
        pipe.incr(key)
        pipe.expire(key, _seconds_until_next_utc_day(now))
        results = await pipe.execute()

    return int(results[0]) <= limit


def _build_agent(model: Model) -> Agent[None, str]:
    agent = Agent(model, output_type=str, instructions=_SYSTEM_PROMPT)

    @agent.tool_plain
    def search_sophie_source(query: str) -> list[str]:
        """Search Sophie's source code for a substring.

        Args:
            query: Substring to look for, such as a command name, setting name or user-visible text.
        """
        return search_source(query)

    @agent.tool_plain
    def read_sophie_source(path: str, start_line: int = 1) -> str:
        """Read a window of one Sophie source file.

        Args:
            path: Path from a search result, relative to the sophie_bot package.
            start_line: First line to read; use the continuation hint to page through a long file.
        """
        return read_source(path, start_line) or "No such Sophie source file."

    return agent


async def run_sophie_inspect(question: str, chat_iid: PydanticObjectId, chat_tid: int | None = None) -> str:
    """Answer a question about Sophie's behaviour by inspecting its own source.

    Experimental and off by default: it costs several model requests, so it is rate limited per
    chat per day and charged against the chat's AI quota like any other feature.
    """
    if not await is_enabled("ai_sophie_inspect", chat_tid=chat_tid):
        return _("Source inspection is not available.")

    if not await _consume_daily_quota(chat_iid, chat_tid):
        return _("The daily limit for source inspection in this chat has been reached.")

    model_name = str(await get_value("ai_sophie_inspect_model", chat_tid=chat_tid)) or await resolve_model_name(
        await get_chat_mode(chat_iid), AIModelPurpose.sophie_inspect
    )
    usage_limits = UsageLimits(
        request_limit=await _feature_int("ai_sophie_inspect_request_limit", chat_tid),
        tool_calls_limit=await _feature_int("ai_sophie_inspect_tool_calls_limit", chat_tid),
        output_tokens_limit=await _feature_int("ai_sophie_inspect_output_tokens_limit", chat_tid),
    )

    log.debug("sophie_inspect: started", question=question, chat_iid=str(chat_iid), model=model_name)

    model = get_ai_model(model_name)
    agent = _build_agent(model)
    try:
        result = await run_ai_text(
            agent,
            user_prompt=question,
            usage_limits=usage_limits,
            request_options=AIRequestOptions(user_tracking_id=chat_iid, session_id=f"{chat_iid}:sophie_inspect"),
        )
    except (AIRequestFailed, UsageLimitExceeded) as error:
        # Running out of budget is a normal outcome for a bounded sub-agent, and must not take the
        # conversation down with it: the main agent gets a plain answer it can pass on.
        log.info("sophie_inspect: gave up", chat_iid=str(chat_iid), error=str(error))
        return _("I could not find the answer in my own sources within the allowed budget.")

    await charge_ai_usage(chat_iid, AI_FEATURE_SOPHIE_INSPECT, model, result.usage)

    log.debug("sophie_inspect: finished", chat_iid=str(chat_iid), tokens=result.usage.total_tokens)
    return result.output
