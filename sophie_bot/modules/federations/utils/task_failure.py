from __future__ import annotations

from stfu_tg import Doc, KeyValue

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import FederationTask
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.services.bot import bot
from sophie_bot.utils.i18n import gettext as _


def build_task_failed_doc(error_message: str | None = None) -> Doc:
    """Format the user-facing notice for a federation task that did not complete.

    Shared by every deferred federation task type: ban/unban propagation, and CSV
    import/export. The task is left in ``FAILED`` and kept indefinitely so the failure
    can be investigated and the task re-done, rather than silently disappearing.
    """
    doc = Doc(_("❌ The task has been failed, I will tag it as failed and retry later."))
    if error_message:
        doc += KeyValue(_("Error"), error_message)
    return doc


async def notify_task_failed(task: FederationTask, error_message: str | None = None) -> None:
    """Tell the user their federation task failed, however the task announced itself.

    Ban/unban tasks carry the in-progress reply they must edit, so the "Propagating…"
    message becomes the failure instead of hanging forever. Import/export tasks have no
    reply to edit and announce their result with a fresh message, so match that.
    """
    text = build_task_failed_doc(error_message).to_html()

    async def send_replacement() -> None:
        message = await bot.send_message(task.reply_chat_id, text)
        task.reply_message_id = message.message_id

    if task.reply_chat_id and task.reply_message_id:
        await common_try(
            bot.edit_message_text(text, chat_id=task.reply_chat_id, message_id=task.reply_message_id),
            edit_not_found=send_replacement,
        )
        return

    chat = await task.chat.fetch()
    # Beanie hands back the Link itself when the referenced chat is gone.
    if isinstance(chat, ChatModel):
        await common_try(bot.send_message(chat.tid, text))
