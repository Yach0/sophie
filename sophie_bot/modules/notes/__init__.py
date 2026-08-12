from types import ModuleType

from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_NOTE_BUTTON_PREFIX,
    LegacyButtonAction,
    register_legacy_button_actions,
)
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from ...modes import SOPHIE_MODE
from ...services.scheduler import scheduler
from .api import notes_router
from .handlers.delete import DelNote
from .handlers.delete_all import DelAllNotesCallbackHandler, DelAllNotesHandler
from .handlers.get import GetNote, HashtagGetNote
from .handlers.legacy_button import LegacyStartNoteButton
from .handlers.list import NotesList
from .handlers.pmnotes_handler import (
    PrivateNotesConnectHandler,
    PrivateNotesRedirectHandler,
)
from .handlers.pmnotes_setting import PMNotesControl, PMNotesStatus
from .handlers.save import SaveNote
from .handlers.status_cleannotes import CleanNotesHandlerABC
from .magic_handlers.export import export
from .magic_handlers.reply_action import ReplyModernAction
from .magic_handlers.send_note_action import SendNoteAction
from .schedules.generate_ai_titles import GenerateAITitles
from .schedules.generate_embeddings import GenerateNoteEmbeddings

api_router = APIRouter()
api_router.include_router(notes_router)

router = Router(name="notes")


register_legacy_button_actions(
    LegacyButtonAction("note", LEGACY_NOTE_BUTTON_PREFIX),
    LegacyButtonAction("#", LEGACY_NOTE_BUTTON_PREFIX),
)


async def post_setup(_modules: dict[str, ModuleType]) -> None:
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(GenerateAITitles().handle, "interval", minutes=1, jobstore="ram")
        scheduler.add_job(GenerateNoteEmbeddings().handle, "interval", minutes=1, jobstore="ram")


module_manifest = ModuleManifest(
    name="notes",
    bot_router=router,
    api_router=api_router,
    handlers=(
        PMNotesControl,
        PMNotesStatus,
        PrivateNotesConnectHandler,
        PrivateNotesRedirectHandler,
        NotesList,
        GetNote,
        HashtagGetNote,
        DelNote,
        SaveNote,
        CleanNotesHandlerABC,
        DelAllNotesHandler,
        DelAllNotesCallbackHandler,
        LegacyStartNoteButton,
    ),
    post_setup=post_setup,
    title=l_("Notes"),
    emoji="📗",
    description=l_("Save and retrieve notes in chats"),
    info=LazyProxy(
        lambda: Doc(
            l_(
                "If you want to save some frequently-used content in your chat, such as a FAQ, response templates, your favourite stickers or the whole interactive menu, you can do that with notes."
            ),
            l_(
                "Notes allows saving different kind of content, from normal text messages to stickers and audio messages, notes also support adding inline message buttons."
            ),
        )
    ),
    advertise_wiki_page=True,
    modern_actions=(ReplyModernAction, SendNoteAction),
    export=export,
)
