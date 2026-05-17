from aiogram import Router
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest
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
from .handlers.search import NotesSearchHandler
from .magic_handlers.export import export
from .magic_handlers.filter import get_filter
from .magic_handlers.reply_action import ReplyModernAction
from .magic_handlers.send_note_action import SendNoteAction
from .schedules.generate_ai_titles import GenerateAITitles
from .schedules.generate_embeddings import GenerateNoteEmbeddings
from .utils.buttons_processor.legacy import BUTTONS

api_router = APIRouter()
api_router.include_router(notes_router)

router = Router(name="notes")

__module_name__ = l_("Notes")
__module_emoji__ = "📗"
__module_description__ = l_("Save and retrieve notes in chats")
__module_info__ = LazyProxy(
    lambda: Doc(
        l_(
            "If you want to save some frequently-used content in your chat, such as a FAQ, response templates, your favourite stickers or the whole interactive menu, you can do that with notes."
        ),
        l_(
            "Notes allows saving different kind of content, from normal text messages to stickers and audio messages, notes also support adding inline message buttons."
        ),
    )
)
__advertise_wiki_page__ = True

__filters__ = get_filter()
__modern_actions__ = (ReplyModernAction, SendNoteAction)

__export__ = export

BUTTONS.update({"note": "btnnotesm", "#": "btnnotesm"})


async def __pre_setup__():
    # PM notes
    router.message.register(PMNotesControl, *PMNotesControl.filters())
    router.message.register(PMNotesStatus, *PMNotesStatus.filters())

    router.message.register(PrivateNotesConnectHandler, *PrivateNotesConnectHandler.filters())
    router.message.register(PrivateNotesRedirectHandler, *PrivateNotesRedirectHandler.filters())

    router.message.register(NotesList, *NotesList.filters())
    router.message.register(GetNote, *GetNote.filters())
    router.message.register(HashtagGetNote, *HashtagGetNote.filters())
    router.message.register(NotesSearchHandler, *NotesSearchHandler.filters())

    router.message.register(DelNote, *DelNote.filters())
    router.message.register(SaveNote, *SaveNote.filters())

    router.message.register(DelAllNotesHandler, *DelAllNotesHandler.filters())
    router.callback_query.register(DelAllNotesCallbackHandler, *DelAllNotesCallbackHandler.filters())

    # Legacy note buttons
    router.message.register(LegacyStartNoteButton, *LegacyStartNoteButton.filters())


async def __post_setup__(_):
    if SOPHIE_MODE == "scheduler":
        scheduler.add_job(GenerateAITitles().handle, "interval", minutes=1, jobstore="ram")
        scheduler.add_job(GenerateNoteEmbeddings().handle, "interval", minutes=1, jobstore="ram")


module_manifest = ModuleManifest(
    name="notes",
    bot_router=router,
    api_router=api_router,
    pre_setup=__pre_setup__,
    post_setup=__post_setup__,
    metadata={
        "name": __module_name__,
        "emoji": __module_emoji__,
        "description": __module_description__,
        "info": __module_info__,
        "advertise_wiki_page": __advertise_wiki_page__,
        "filter_actions": __filters__,
        "modern_actions": __modern_actions__,
        "export": __export__,
    },
)
