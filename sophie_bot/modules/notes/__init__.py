from __future__ import annotations

from aiogram import Router
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter
from stfu_tg import Doc

from sophie_bot.modules import ModuleManifest, track_scheduler_callback
from sophie_bot.modules.utils_.legacy_buttons import (
    LEGACY_NOTE_BUTTON_PREFIX,
    LegacyButtonAction,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_

from .api import notes_router
from .handlers.delete import DelNote
from .handlers.delete_all import DelAllNotesCallbackHandler, DelAllNotesHandler
from .handlers.get import GetNote, HashtagGetNote
from .handlers.legacy_button import LegacyStartNoteButton
from .handlers.list import NotesList, NotesPageHandler
from .handlers.pmnotes_handler import (
    PrivateNotesConnectHandler,
    PrivateNotesRedirectHandler,
)
from .handlers.pmnotes_setting import PMNotesControl, PMNotesStatus
from .handlers.save import SaveNote
from .handlers.status_cleannotes import CleanNotesHandlerABC
from .magic_handlers.export import export
from .magic_handlers.reply_action import (
    ReplyModernAction,
)
from .magic_handlers.reply_action import (
    build_action_wizard_specs as build_reply_action_wizard_specs,
)
from .magic_handlers.send_note_action import (
    SendNoteAction,
)
from .magic_handlers.send_note_action import (
    build_action_wizard_specs as build_send_note_action_wizard_specs,
)
from .schedules.generate_ai_titles import GenerateAITitles
from .schedules.generate_embeddings import GenerateNoteEmbeddings

api_router = APIRouter()
api_router.include_router(notes_router)

router = Router(name="notes")


def build_action_wizards() -> dict:
    return {
        **build_reply_action_wizard_specs(),
        **build_send_note_action_wizard_specs(),
    }


def setup_scheduler(scheduler: AsyncIOScheduler, services: ApplicationServices) -> None:
    scheduler.add_job(
        track_scheduler_callback(GenerateAITitles(services).handle, services),
        "interval",
        minutes=1,
        jobstore="ram",
    )
    scheduler.add_job(
        track_scheduler_callback(GenerateNoteEmbeddings(services).handle, services),
        "interval",
        minutes=1,
        jobstore="ram",
    )


module_manifest = ModuleManifest(
    name="notes",
    bot_router_factory=lambda: Router(name=router.name),
    api_router_factory=lambda: api_router,
    handlers=(
        PMNotesControl,
        PMNotesStatus,
        PrivateNotesConnectHandler,
        PrivateNotesRedirectHandler,
        NotesList,
        NotesPageHandler,
        GetNote,
        HashtagGetNote,
        DelNote,
        SaveNote,
        CleanNotesHandlerABC,
        DelAllNotesHandler,
        DelAllNotesCallbackHandler,
        LegacyStartNoteButton,
    ),
    legacy_buttons=(
        LegacyButtonAction("note", LEGACY_NOTE_BUTTON_PREFIX),
        LegacyButtonAction("#", LEGACY_NOTE_BUTTON_PREFIX),
    ),
    setup_scheduler=setup_scheduler,
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
    build_action_wizards=build_action_wizards,
    export=export,
)
