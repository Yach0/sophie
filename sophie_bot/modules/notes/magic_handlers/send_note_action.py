from typing import Any

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel
from stfu_tg import Bold, Code, HList, Template, Title
from stfu_tg.doc import Element

from sophie_bot.db.models.notes import NoteModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionSetupTryAgainException,
    ActionWizardSetting,
    ActionWizardSpec,
)
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.shared.actions import ActionDefinition, ActionResult, ModernActionABC
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class SendNoteActionDataModel(BaseModel):
    notename: str


async def setup_confirm(event: Message | CallbackQuery, data: dict[str, Any]) -> SendNoteActionDataModel:
    """Checks given notename and saves it"""
    if isinstance(event, CallbackQuery):
        raise TypeError("This handlers setup_confirm can only be used with messages")

    connection: ChatConnection = data["context"].connection
    notename = (event.text or "").split(" ", 1)[0].lower().removeprefix("#")

    # Check whatever given notename exist
    if not await NoteModel.get_by_notenames(connection.db_model.iid, (notename,)):
        await event.reply(_("Note with this name does not exist. Please try again."))
        raise ActionSetupTryAgainException()

    return SendNoteActionDataModel(notename=notename)


async def setup_message(_event: Message | CallbackQuery, _data: dict[str, Any]) -> Element:
    return Template(_("Please write the note name you want to send as a filter trigger."))


def build_action_wizard_specs() -> dict[str, ActionWizardSpec]:
    return {
        SEND_NOTE_ACTION.name: ActionWizardSpec(
            interactive_setup=ActionWizardSetting(
                title=l_("Send note"),
                setup_message=setup_message,
                setup_confirm=setup_confirm,
            ),
            settings=lambda _data: {
                "send_note": ActionWizardSetting(
                    title=l_("Change note name"),
                    icon="🗒",
                    setup_message=setup_message,
                    setup_confirm=setup_confirm,
                )
            },
        )
    }


SEND_NOTE_ACTION = ActionDefinition[SendNoteActionDataModel](
    name="send_note",
    icon="🗒",
    title=l_("Send note"),
    data_object=SendNoteActionDataModel,
    allow_warns=True,
    has_interactive_setup=True,
)


class SendNoteAction(ModernActionABC[SendNoteActionDataModel]):
    definition = SEND_NOTE_ACTION

    @staticmethod
    def description(data: SendNoteActionDataModel) -> Element | str:
        return Template(
            _("Replies to the message with the note with {notename} note name"), notename=Code("#" + data.notename)
        )

    async def handle(self, message: Message, data: dict, filter_data: SendNoteActionDataModel) -> ActionResult | None:
        connection: ChatConnection = data["context"].connection
        notename = filter_data.notename

        note = await NoteModel.get_by_notenames(connection.db_model.iid, (notename,))

        if not note:
            return await message.reply(Template(_("#{name} note was not found."), name=Bold(notename)).to_html())

        title = Bold(HList(Title(f"📗 #{notename}", bold=False), _("Filter action")))

        sent_messages: list[Message] = []
        await common_try(
            send_saveable(
                message,
                message.chat.id,  # Current chat id
                note,
                title=title,
                reply_to=message.message_id,
                owner_chat_tid=note.chat_tid,
                collect_sent=sent_messages,
                bot=data["services"].bot,
                redis=data["services"].redis,
            )
        )
        return sent_messages
