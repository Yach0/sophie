from __future__ import annotations

import aiohttp
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, Italic, KeyValue, Section, Template, Title

from sophie_bot.config import CONFIG
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_summary_model
from sophie_bot.modules.ai.utils.ai_tasks import AIStructuredTask, run_structured_task
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.op.json_schemas.op_task_ai import OpTaskAIResult
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_MESSAGE_HISTORY_LIMIT = 35


async def _create_gitlab_issue(
    token: str,
    project_id: str,
    title: str,
    description: str,
    labels: list[str],
) -> dict:
    url = f"https://gitlab.com/api/v4/projects/{project_id}/issues"
    headers = {"PRIVATE-TOKEN": token}
    data: dict[str, str] = {"title": title, "description": description}
    if labels:
        data["labels"] = ",".join(labels)
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            resp.raise_for_status()
            return await resp.json()


def _format_message(message: MessageType) -> str:
    role = _("bot") if message.user_id == CONFIG.bot_id else _("user")
    sender = message.username or str(message.user_id)
    text = message.text[:200] if message.text else ""
    return f"[{role}] {sender}: {text}"


def _build_history_context(messages: tuple[MessageType, ...]) -> str:
    if not messages:
        return _("No cached messages available.")
    lines = [_format_message(msg) for msg in messages]
    return "\n".join(lines)


def _extract_reply_context(message: Message) -> str | None:
    reply_to = message.reply_to_message
    if reply_to is None:
        return None
    text = reply_to.text or reply_to.caption or ""
    sender = reply_to.from_user
    sender_name = sender.full_name if sender else _("unknown")
    return _("Reply to {name}: {text}").format(name=sender_name, text=text[:500])


@flags.help(description=l_("Generate and create a GitLab issue from chat context."))
class OpTaskHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple:
        return (CMDFilter("op_task"), IsOP(True), FeatureFlagFilter("op_task"))

    async def handle(self) -> None:
        message: Message = self.event

        if not CONFIG.gitlab_token or not CONFIG.gitlab_project_id:
            await message.reply(str(Bold(_("GitLab integration is not configured."))))
            return

        command_text = message.text or ""
        parts = command_text.split(maxsplit=1)
        operator_notes = parts[1].strip() if len(parts) > 1 and parts[1].strip() else ""

        reply_context = _extract_reply_context(message)

        if not operator_notes and reply_context is None:
            await message.reply(str(Bold(_("Please provide a description for the task."))))
            return

        chat_tid = message.chat.id
        chat_model = await ChatModel.get_by_tid(chat_tid)
        if chat_model is None:
            await message.reply(str(Bold(_("Could not find chat model."))))
            return

        messages: tuple[MessageType, ...] = await get_cached_messages(chat_tid, limit=_MESSAGE_HISTORY_LIMIT)
        history_text = _build_history_context(messages)

        history = AIMessageHistory()
        history.add_system(
            "You are a project management assistant for SophieBot, a Telegram bot. "
            "Analyze the provided chat context and operator notes to generate a well-structured GitLab issue. "
            "Return a concise title, a detailed markdown description, and suggested labels."
        )

        prompt_parts: list[str] = []
        if operator_notes:
            prompt_parts.append(f"Operator notes:\n{operator_notes}")
        if reply_context:
            prompt_parts.append(f"Replied message context:\n{reply_context}")
        prompt_parts.append(f"Recent chat history:\n{history_text}")

        history.add_custom("\n\n".join(prompt_parts), name="OperatorTask")

        model = await get_chat_summary_model(chat_model.iid, chat_tid=chat_tid)
        try:
            result = await run_structured_task(
                AIStructuredTask(
                    output_type=OpTaskAIResult,
                    feature=AI_FEATURE_CHATBOT,
                ),
                model,
                history,
                chat_iid=chat_model.iid,
                chat_tid=chat_tid,
            )
        except AIRequestFailed as err:
            await message.reply(
                **ai_request_failed_message(err.sentry_event_id, title=_("Error generating task description"))
            )
            return

        task_result: OpTaskAIResult = result.output

        try:
            issue_data = await _create_gitlab_issue(
                token=CONFIG.gitlab_token,
                project_id=CONFIG.gitlab_project_id,
                title=task_result.title,
                description=task_result.description,
                labels=task_result.labels,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            await message.reply(str(Section(Bold(_("Failed to create GitLab issue")), Italic(str(exc)))))
            return

        issue_url = issue_data.get("web_url", "")
        issue_iid = issue_data.get("iid", "")

        doc = Doc(Title(Bold(_("GitLab Issue Created"))))
        doc += Section(
            KeyValue(_("Title"), Italic(task_result.title)),
            KeyValue(_("Issue"), Code(f"#{issue_iid}")),
            Template('<a href="{url}">{title}</a>', url=issue_url, title=_("Open in GitLab")),
        )
        await message.reply(doc.to_html())
