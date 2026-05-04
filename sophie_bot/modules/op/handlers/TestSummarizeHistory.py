from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, KeyValue, Section, Title

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.cache_messages import cache_message
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@dataclass(frozen=True)
class SeedHistoryLine:
    user_id: int
    username: str
    text: str
    minutes_ago: int


TEST_HISTORY: tuple[SeedHistoryLine, ...] = (
    SeedHistoryLine(910005, "erin", "Morning standup: let's review yesterday's summary generation output first.", 480),
    SeedHistoryLine(
        910001,
        "alice",
        "The cache window looks good, but the prompt summaries still need cleaner formatting.",
        474,
    ),
    SeedHistoryLine(910002, "bob", "I want the system prompt summaries behind a separate feature flag.", 470),
    SeedHistoryLine(
        910006, "frank", "Please keep the scheduler flag independent so we can roll them out separately.", 466
    ),
    SeedHistoryLine(910003, "carol", "We should make the summary title shorter and more human.", 460),
    SeedHistoryLine(910004, "dave", "I can review the rendered HTML once the collapsible quote lands.", 455),
    SeedHistoryLine(910005, "erin", "The users list should stay inline; HList is probably the right fit there.", 448),
    SeedHistoryLine(910001, "alice", "After that, let's add a command to seed fake history for testing.", 442),
    SeedHistoryLine(
        910002, "bob", "I also want more data points so the summarizer can split multiple discussions.", 438
    ),
    SeedHistoryLine(910006, "frank", "Please add some migration chatter too, not only UI changes.", 432),
    SeedHistoryLine(910003, "carol", "Good point, and include a small moderation-related thread for variety.", 428),
    SeedHistoryLine(910004, "dave", "We can seed a fake appeal conversation and one technical refactor thread.", 422),
    SeedHistoryLine(910005, "erin", "Lunch later, but first let's get the operator test command done.", 360),
    SeedHistoryLine(910001, "alice", "We should do some vibecoding on the summary scheduler today.", 210),
    SeedHistoryLine(910002, "bob", "I can wire the daily job after lunch and keep the cache time-based.", 205),
    SeedHistoryLine(910003, "carol", "Please keep the summary lines structured with emojis and usernames.", 198),
    SeedHistoryLine(910004, "dave", "If the model returns message IDs, we can derive timestamps ourselves.", 192),
    SeedHistoryLine(910005, "erin", "Yes, no reason to ask the model for dates we already know.", 188),
    SeedHistoryLine(910006, "frank", "That should reduce hallucinations in the stored summaries too.", 184),
    SeedHistoryLine(910001, "alice", "Let's also keep first_message_id in every summary line.", 178),
    SeedHistoryLine(910002, "bob", "And save usernames in cache so history reflects what users had at the time.", 172),
    SeedHistoryLine(910003, "carol", "We need a compact header plus an overview paragraph in the posted summary.", 166),
    SeedHistoryLine(910004, "dave", "Then the topic bullets can carry time, emoji, title, and participants.", 160),
    SeedHistoryLine(910005, "erin", "Make the history lines collapsible so the post does not look too heavy.", 154),
    SeedHistoryLine(
        910001, "alice", "We also need a dedicated summary model instead of the regular chatbot default.", 120
    ),
    SeedHistoryLine(910004, "dave", "Let's backfill the summary model to openai/gpt-5.4 with a migration.", 114),
    SeedHistoryLine(910002, "bob", "I will add an op command to seed fake history for testing.", 108),
    SeedHistoryLine(910006, "frank", "The prompt-builder side should read from a separate feature flag.", 102),
    SeedHistoryLine(910003, "carol", "That way we can disable prompt summaries without stopping the scheduler.", 96),
    SeedHistoryLine(
        910001, "alice", "Please rename the old flag only for the system prompt path, not the whole feature.", 92
    ),
    SeedHistoryLine(910004, "dave", "I also want an emoji on each generated summary line for easier scanning.", 86),
    SeedHistoryLine(
        910005, "erin", "For the testing command, make sure it works in the current chat context only.", 80
    ),
    SeedHistoryLine(
        910002, "bob", "We should validate the seeded cache through the real get_cached_messages helper.", 74
    ),
    SeedHistoryLine(910006, "frank", "Once all that is in, rerun make commit and check the migration status.", 68),
    SeedHistoryLine(
        910003,
        "carol",
        "Finally, we can hand the tester a command that populates enough history for a useful summary.",
        62,
    ),
)


@flags.help(description=l_("Seed test history into the current chat AI cache"))
class OpTestSummarizeHistoryHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_test_sumarrize_history"), IsOP(True)

    async def handle(self) -> Any:
        chat_tid = self.event.chat.id
        summary_date = datetime.now(timezone.utc).date() - timedelta(days=1)
        seed_anchor = self._build_seed_anchor(summary_date)
        first_message_id = max(1, self.event.message_id - len(TEST_HISTORY) - 20)

        for offset, line in enumerate(TEST_HISTORY):
            await cache_message(
                line.text,
                chat_tid,
                line.user_id,
                first_message_id + offset,
                seed_anchor - timedelta(minutes=line.minutes_ago),
                line.username,
            )

        doc = Doc(
            Title(_("Test summarize history added")),
            Section(
                KeyValue(_("Chat ID"), chat_tid),
                KeyValue(_("Summary day"), summary_date.isoformat()),
                KeyValue(_("Messages added"), len(TEST_HISTORY)),
                KeyValue(_("First seeded message ID"), first_message_id),
            ),
        )
        await self.event.reply(doc.to_html())

    @staticmethod
    def _build_seed_anchor(summary_date: date) -> datetime:
        return datetime.combine(summary_date, time(hour=23, minute=30), tzinfo=timezone.utc)
