from __future__ import annotations

from typing import Any, cast

from aiogram import Bot, flags
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from pydantic_ai import ModelHTTPError
from stfu_tg import Code, Doc, KeyValue, Section, Template, Title, VList

from sophie_bot.constants import AI_FILTER_DAILY_LIMIT_PER_CHAT
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.filters.ai_enabled import AIEnabledFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.json_schemas.filter_suggestions import (
    AIFilterSuggestion,
    AIFilterSuggestionsResponse,
)
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate_schema_with_result
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.modules.locks.handlers.lockable import (
    CONTENT_TYPES,
    ENTITY_TYPES,
    FORWARD_TYPES,
    LOCK_TYPE_DESCRIPTIONS,
    SPECIAL_TYPES,
    STICKER_PACK_TYPES,
    SUPPORTED_LANGUAGES,
    TEXT_PATTERN_TYPES,
)
from sophie_bot.utils.ai_features import AI_FEATURE_FILTER
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _validate_suggestions(suggestions: list[AIFilterSuggestion]) -> list[AIFilterSuggestion]:
    normalized: list[AIFilterSuggestion] = []
    seen_handlers: set[str] = set()

    for suggestion in suggestions:
        handler = suggestion.handler.strip()
        description = suggestion.description.strip()
        note = suggestion.note.strip()
        if not handler or not description or not note:
            raise SophieException("AI returned an incomplete filter suggestion")
        if handler in seen_handlers:
            raise SophieException("AI returned duplicate filter handlers")

        normalized.append(
            AIFilterSuggestion(
                handler=handler,
                description=description,
                note=note,
                recommended=suggestion.recommended,
            )
        )
        seen_handlers.add(handler)

    if not normalized:
        raise SophieException("AI must return at least one suggestion")

    return normalized


def _render_suggestion(index: int, suggestion: AIFilterSuggestion) -> tuple[Template, Template]:
    if suggestion.handler.startswith("ai:"):
        note = Template(
            _("Uses AI quota, limited to {limit} evaluations/day per chat"),
            limit=AI_FILTER_DAILY_LIMIT_PER_CHAT,
        )
        note_prefix = "⚠️"
    elif suggestion.recommended:
        note = _("Recommended")
        note_prefix = "✅"
    else:
        note = suggestion.note
        note_prefix = "⚠️"

    title_line = Template(
        "{index}. {handler} — {description}",
        index=index,
        handler=Code(suggestion.handler),
        description=suggestion.description,
    )
    note_line = Template("{prefix} {note}", prefix=note_prefix, note=note)
    return title_line, note_line


def _format_lock_group(title: str, lock_types: tuple[str, ...]) -> Section:
    return Section(
        VList(*[KeyValue(Code(lock_type), str(LOCK_TYPE_DESCRIPTIONS[lock_type])) for lock_type in lock_types]),
        title=title,
    )


def _build_locktype_help_text() -> Section:
    language_lines = [
        KeyValue(
            Code("language:CODE"),
            "Messages in the specified language. Suggest this only when the prompt clearly asks to block a language.",
        )
    ]
    for code, name in sorted(SUPPORTED_LANGUAGES.items()):
        language_lines.append(KeyValue(Code(f"language:{code}"), f"Messages in {name} language"))

    return Section(
        _format_lock_group("Media types", CONTENT_TYPES),
        _format_lock_group("Entities and links", ENTITY_TYPES),
        _format_lock_group("Forwards", FORWARD_TYPES),
        _format_lock_group("Text patterns", TEXT_PATTERN_TYPES),
        _format_lock_group("Sticker types", STICKER_PACK_TYPES),
        _format_lock_group("Special", SPECIAL_TYPES),
        Section(VList(*language_lines), title="Languages"),
        Section(
            KeyValue(
                Code("stickerpack:PACK_ID"),
                "Locks a specific sticker pack by its ID. Suggest this only when the prompt explicitly targets one sticker pack.",
            ),
            title="Special handler forms",
        ),
        title="Lock type definitions from /lockable",
    )


def _build_system_prompt() -> str:
    prompt_doc = Doc(
        "You generate Sophie Bot filter handler suggestions.",
        "Return 1 to 3 unique suggestions as structured data.",
        Section(
            VList(
                Template(
                    "Plain text is the default and does a simple substring match, so it is best for loose matching. Example: {handler}",
                    handler=Code("PHRASE"),
                ),
                Template(
                    "{handler} matches whole words or exact tokenized phrases, so it avoids partial-word false positives. Example: {example}",
                    handler=Code("word:"),
                    example=Code("word:WORD"),
                ),
                Template(
                    "{handler} only matches when the full message text is exactly the same. Example: {example}",
                    handler=Code("exact:TEXT"),
                    example=Code("exact:hello there"),
                ),
                Template(
                    "{handler} uses regular expressions for advanced text patterns. Example: {example}",
                    handler=Code("re:REGEX"),
                    example=Code("re:crypto|btc|bitcoin"),
                ),
                Template(
                    "{handler} uses semantic AI evaluation and should be used only when literal text rules are too weak. Example: {example}",
                    handler=Code("ai:PROMPT"),
                    example=Code("ai:messages promoting cryptocurrency scams"),
                ),
                "Some supported lock types, such as url, photo, language:xx, or stickerpack:..., detect message/content types or structured Telegram properties rather than free-form text. Other lock types such as cyrillic, rtl, or zalgo do match text patterns, but they are still built-in Telegram-content detectors, not plain text / word / exact / regex / ai handlers.",
            ),
            title="Matcher behavior",
        ),
        "Include an ai: suggestion only when semantic matching is genuinely useful.",
        "Put the best overall suggestion first.",
        "If the request clearly maps to a lock type, put that lock type first and mark it recommended.",
        "Descriptions and notes must be one line, concise, and in the same language as the user's request.",
        "Do not include emoji in descriptions or notes.",
        _build_locktype_help_text(),
    )
    return prompt_doc.to_md()


@flags.help(
    description=l_("Suggests filter handlers from a natural language description"),
    alias_to_modules=["filters"],
)
class AIFilterAddHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("aiaddfilter",)),
            FeatureFlagFilter("ai_filters"),
            ChatTypeFilter("group", "supergroup"),
            UserRestricting(admin=True),
            AIEnabledFilter(),
            AIQuotaFilter(AI_FEATURE_FILTER),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"prompt": TextArg(l_("Describe what the filter should catch"))}

    async def handle(self) -> Any:
        prompt: str = self.data["prompt"].strip()
        history = NewAIMessageHistory()
        history.add_system(_build_system_prompt())
        history.prompt = [prompt]

        model = await get_chat_default_model(self.connection.db_model.iid)
        bot = cast(Bot, self.event.bot)

        try:
            async with ChatActionSender.typing(
                bot=bot,
                chat_id=self.event.chat.id,
                message_thread_id=self.event.message_thread_id,
            ):
                result = await new_ai_generate_schema_with_result(
                    history,
                    AIFilterSuggestionsResponse,
                    model,
                    user_tracking_id=self.connection.db_model.iid,
                )
            suggestions = _validate_suggestions(result.output.suggestions)
        except (ModelHTTPError, SophieException, TimeoutError):
            await self.event.reply(_("Could not generate suggestions. Please try again or use /addfilter directly."))
            return

        if result.usage and result.usage.total_tokens:
            await charge_ai_usage(self.connection.db_model.iid, AI_FEATURE_FILTER, model, result.usage)

        doc_parts: list[Any] = [f"🪄 {_('AI Filter Suggestions')}"]
        for index, suggestion in enumerate(suggestions, start=1):
            title_line, note_line = _render_suggestion(index, suggestion)
            doc_parts.extend((title_line, note_line))
            if index != len(suggestions):
                doc_parts.append(" ")

        first_handler = suggestions[0].handler
        doc_parts.extend(
            (
                " ",
                _("Use /addfilter <handler> to create the filter."),
                Template(_("For example, /addfilter {handler}"), handler=first_handler),
            )
        )

        await self.event.reply(Doc(Title(doc_parts[0]), *doc_parts[1:]).to_html(), disable_web_page_preview=True)
