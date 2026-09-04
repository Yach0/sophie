from __future__ import annotations

import copy
import html
import re
from collections.abc import Mapping
from typing import Any, cast

from aiogram.types import (
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaVoiceNote,
    InputRichBlockUnion,
    InputRichMessage,
    RichBlockButtons,
    RichBlockCaption,
    RichBlockParagraph,
    RichMessage,
    RichMessageButton,
    RichTextButton,
    RichTextTextMention,
    RichTextUnion,
)
from pydantic import BaseModel

from sophie_bot.modules.notes.utils._random_parser import parse_random_text
from sophie_bot.utils.i18n import gettext as _

_MEDIA_INPUT_TYPES: dict[str, type[BaseModel]] = {
    "animation": InputMediaAnimation,
    "audio": InputMediaAudio,
    "document": InputMediaDocument,
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "voice_note": InputMediaVoiceNote,
}

_BUTTON_ACTION_FIELDS = (
    "url",
    "callback_data",
    "web_app",
    "login_url",
    "switch_inline_query",
    "switch_inline_query_current_chat",
    "switch_inline_query_chosen_chat",
    "copy_text",
    "disabled",
)


def rich_message_has_bot_bound_actions(message: RichMessage | None) -> bool:
    if message is None:
        return False
    return any(
        getattr(model, field_name, None) is not None
        for model in _walk_models(message)
        if model.__class__.__name__ == "RichMessageButton"
        for field_name in _BUTTON_ACTION_FIELDS[1:7]
    )


def rich_message_has_media(message: RichMessage | None) -> bool:
    if message is None:
        return False
    return any(
        model.__class__.__name__
        in {
            "RichBlockAnimation",
            "RichBlockAudio",
            "RichBlockDocument",
            "RichBlockPhoto",
            "RichBlockVideo",
            "RichBlockVoiceNote",
        }
        for model in _walk_models(message)
    )


def _model_data(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="python", exclude_none=False)


def _largest_media_candidate(candidates: list[Any]) -> Any:
    return max(
        candidates,
        key=lambda candidate: (
            int(getattr(candidate, "width", 0) or 0) * int(getattr(candidate, "height", 0) or 0),
            int(getattr(candidate, "file_size", 0) or 0),
        ),
    )


def _input_media(kind: str, media: Any, *, has_spoiler: bool | None = None) -> BaseModel:
    media_type = _MEDIA_INPUT_TYPES[kind]
    fields = {field_name: None for field_name in media_type.model_fields if field_name not in {"type", "media"}}
    fields["media"] = media.file_id
    if has_spoiler is not None and "has_spoiler" in fields:
        fields["has_spoiler"] = has_spoiler
    return media_type.model_validate(fields)


def _convert_caption(caption: RichBlockCaption | None) -> dict[str, Any] | None:
    if caption is None:
        return None
    return {"text": _convert_text(caption.text), "credit": _convert_text(caption.credit) if caption.credit else None}


def _convert_button(button: RichMessageButton) -> RichMessageButton:
    return button.model_copy(update={"text": _convert_text(button.text)})


def _convert_text(text: RichTextUnion) -> RichTextUnion:
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return [_convert_text(item) for item in text]
    data = _model_data(text)
    if text.__class__.__name__ == "RichTextButton":
        data["button"] = _convert_button(cast("RichTextButton", text).button)
    elif "text" in data and isinstance(data["text"], (BaseModel, list)):
        data["text"] = _convert_text(data["text"])
    text_type = text.__class__
    return text_type.model_validate(data)


def _convert_block(block: Any) -> InputRichBlockUnion:
    block_name = block.__class__.__name__
    if block_name == "RichBlockThinking":
        raise ValueError(_("Thinking Rich blocks cannot be sent as final messages"))

    data = _model_data(block)
    if block_name == "RichBlockList":
        data["items"] = [
            {
                key: value
                for key, value in {
                    "blocks": [_convert_block(item_block) for item_block in item.blocks],
                    "has_checkbox": item.has_checkbox,
                    "is_checked": item.is_checked,
                    "value": item.value,
                }.items()
                if value is not None
            }
            for item in block.items
        ]
    elif block_name in {"RichBlockCollage", "RichBlockSlideshow", "RichBlockBlockQuotation", "RichBlockDetails"}:
        data["blocks"] = [_convert_block(child) for child in block.blocks]
        if block_name == "RichBlockDetails":
            data["summary"] = _convert_text(block.summary)
    elif block_name in {"RichBlockParagraph", "RichBlockSectionHeading", "RichBlockPreformatted"}:
        data["text"] = _convert_text(block.text)
    elif block_name in {"RichBlockExpandableBlockQuotation", "RichBlockPullQuotation", "RichBlockFooter"}:
        data["text"] = _convert_text(block.text)
        if getattr(block, "credit", None) is not None:
            data["credit"] = _convert_text(block.credit)
    elif block_name == "RichBlockButtons":
        data["buttons"] = [_convert_button(button) for button in block.buttons]
    elif block_name == "RichBlockTable":
        data["caption"] = _convert_text(block.caption) if block.caption else None
        data["cells"] = [
            [cell.model_copy(update={"text": _convert_text(cell.text)}) for cell in row] for row in block.cells
        ]
    elif block_name in {
        "RichBlockAnimation",
        "RichBlockAudio",
        "RichBlockDocument",
        "RichBlockPhoto",
        "RichBlockVideo",
        "RichBlockVoiceNote",
    }:
        field_name = {
            "RichBlockAnimation": "animation",
            "RichBlockAudio": "audio",
            "RichBlockDocument": "document",
            "RichBlockPhoto": "photo",
            "RichBlockVideo": "video",
            "RichBlockVoiceNote": "voice_note",
        }[block_name]
        source_media = getattr(block, field_name)
        kind = "voice_note" if field_name == "voice_note" else field_name
        if kind == "photo":
            source_media = _largest_media_candidate(source_media)
        elif kind == "video" and source_media.cover:
            data["cover"] = _largest_media_candidate(source_media.cover).file_id
        data[field_name] = _input_media(kind, source_media, has_spoiler=getattr(block, "has_spoiler", None))
        data["caption"] = _convert_caption(block.caption)
    if "caption" in data and block_name not in {
        "RichBlockAnimation",
        "RichBlockAudio",
        "RichBlockDocument",
        "RichBlockPhoto",
        "RichBlockVideo",
        "RichBlockVoiceNote",
    }:
        data["caption"] = (
            _convert_caption(block.caption) if isinstance(block.caption, RichBlockCaption) else block.caption
        )

    input_name = block_name.replace("RichBlock", "InputRichBlock", 1)
    input_type = getattr(__import__("aiogram.types", fromlist=[input_name]), input_name)
    return input_type.model_validate(data)


def rich_message_to_input(message: RichMessage) -> InputRichMessage:
    """Convert received RichMessage output blocks into reusable input blocks."""
    return InputRichMessage(blocks=[_convert_block(block) for block in message.blocks], is_rtl=message.is_rtl)


def _visible_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_visible_text(item) for item in value)
    if isinstance(value, str):
        return value
    if isinstance(value, RichMessageButton):
        return _visible_text(value.text)
    if not isinstance(value, BaseModel):
        return str(value)

    name = value.__class__.__name__
    data = _model_data(value)
    if name.startswith("RichText"):
        if name == "RichTextCustomEmoji":
            return str(data.get("alternative_text") or "")
        if name == "RichTextButton":
            return _visible_text(value.button)
        if name in {
            "RichTextAnchor",
        }:
            return ""
        return _visible_text(data.get("text") or data.get("expression"))
    if name == "RichBlockListItem":
        return f"{value.label} {_visible_text(value.blocks)}"
    if name == "RichBlockTableCell":
        return _visible_text(value.text)
    if name == "RichBlockCaption":
        return _visible_text(value.text) + (f" ({_visible_text(value.credit)})" if value.credit else "")
    if name == "RichBlockButtons":
        return " ".join(_visible_text(button) for button in value.buttons)
    if name in {
        "RichBlockAnimation",
        "RichBlockAudio",
        "RichBlockDocument",
        "RichBlockMap",
        "RichBlockPhoto",
        "RichBlockVideo",
        "RichBlockVoiceNote",
    }:
        media = next(
            (
                data.get(field)
                for field in ("animation", "audio", "document", "photo", "video", "voice_note")
                if data.get(field)
            ),
            None,
        )
        media_label = getattr(media, "file_name", None) or name.removeprefix("RichBlock")
        return f"[{media_label}]" + (f" {_visible_text(value.caption)}" if value.caption else "")
    if "blocks" in data:
        return _visible_text(data["blocks"])
    if "items" in data:
        return _visible_text(data["items"])
    if "cells" in data:
        return _visible_text(data["cells"])
    if "text" in data:
        return _visible_text(data["text"])
    if "caption" in data:
        return _visible_text(data["caption"])
    return ""


def _text_to_html(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_text_to_html(item) for item in value)
    if isinstance(value, Mapping):
        return _text_to_html(value.get("text") or value.get("expression"))
    if isinstance(value, str):
        return html.escape(value)
    if isinstance(value, RichMessageButton):
        return _text_to_html(value.text)
    name = value.__class__.__name__
    if name == "RichTextCustomEmoji":
        return f'<tg-emoji emoji-id="{html.escape(value.custom_emoji_id, quote=True)}">{html.escape(value.alternative_text)}</tg-emoji>'
    if name == "RichTextUrl":
        label = _text_to_html(value.text)
        return f'<a href="{html.escape(value.url, quote=True)}">{label}</a>'
    if name == "RichTextTextMention":
        return _text_to_html(value.text)
    data = _model_data(value)
    if name == "RichTextButton":
        return _text_to_html(value.button)
    nested = _text_to_html(data.get("text") or data.get("expression"))
    tags = {
        "RichTextBold": ("<b>", "</b>"),
        "RichTextItalic": ("<i>", "</i>"),
        "RichTextUnderline": ("<u>", "</u>"),
        "RichTextStrikethrough": ("<s>", "</s>"),
        "RichTextSpoiler": ("<tg-spoiler>", "</tg-spoiler>"),
        "RichTextMarked": ("<mark>", "</mark>"),
        "RichTextCode": ("<code>", "</code>"),
        "RichTextSubscript": ("<sub>", "</sub>"),
        "RichTextSuperscript": ("<sup>", "</sup>"),
    }
    if name in tags:
        start, end = tags[name]
        return start + nested + end
    return nested


def _block_to_html(block: Any) -> str:
    name = block.__class__.__name__
    if name == "RichBlockDivider":
        return "<hr>"
    if name == "RichBlockAnchor":
        return ""
    data = _model_data(block)
    if name == "RichBlockList":
        items = "".join(f"<li>{html.escape(item.label)}{_blocks_to_html(item.blocks)}</li>" for item in block.items)
        return f"<ul>{items}</ul>"
    if name == "RichBlockTable":
        rows = "".join(
            "<tr>"
            + "".join(
                f"<{'th' if cell.is_header else 'td'}>{_text_to_html(cell.text)}</{'th' if cell.is_header else 'td'}>"
                for cell in row
            )
            + "</tr>"
            for row in block.cells
        )
        return f"<table>{rows}</table>" + (_text_to_html(block.caption) if block.caption else "")
    if name == "RichBlockButtons":
        return " ".join(_text_to_html(button) for button in block.buttons)
    if "blocks" in data:
        inner = _blocks_to_html(block.blocks)
        if name == "RichBlockDetails":
            return f"<details><summary>{_text_to_html(block.summary)}</summary>{inner}</details>"
        if "Quotation" in name:
            return f"<blockquote>{inner}</blockquote>"
        return inner
    if name in {
        "RichBlockParagraph",
        "RichBlockSectionHeading",
        "RichBlockPreformatted",
        "RichBlockFooter",
        "RichBlockExpandableBlockQuotation",
        "RichBlockPullQuotation",
    }:
        inner = _text_to_html(block.text)
        if name == "RichBlockSectionHeading":
            return f"<h{max(1, min(6, int(block.size or 3)))}>{inner}</h{max(1, min(6, int(block.size or 3)))}>"
        if name == "RichBlockPreformatted":
            return f"<pre>{inner}</pre>"
        if name == "RichBlockFooter":
            return f"<footer>{inner}</footer>"
        if "Quotation" in name:
            return f"<blockquote>{inner}</blockquote>"
        return inner
    if name in {
        "RichBlockAnimation",
        "RichBlockAudio",
        "RichBlockDocument",
        "RichBlockPhoto",
        "RichBlockVideo",
        "RichBlockVoiceNote",
        "RichBlockMap",
    }:
        return html.escape(_visible_text(block))
    if data.get("caption"):
        return _text_to_html(data["caption"])
    return _text_to_html(data.get("text") or data.get("expression"))


def _walk_models(value: Any) -> list[BaseModel]:
    if isinstance(value, BaseModel):
        models = [value]
        for field_value in value.__dict__.values():
            models.extend(_walk_models(field_value))
        return models
    if isinstance(value, list):
        return [model for item in value for model in _walk_models(item)]
    if isinstance(value, dict):
        return [model for item in value.values() for model in _walk_models(item)]
    return []


def validate_rich_message_structure(message: RichMessage) -> None:
    """Validate the persisted Rich tree at the capture/API boundary."""
    for model in _walk_models(message):
        if model.model_extra:
            raise ValueError(f"Unknown fields in {model.__class__.__name__}")
        if model.__class__.__name__ == "RichBlockThinking":
            raise ValueError(_("Thinking Rich blocks cannot be saved"))
        if model.__class__.__name__ == "RichMessageButton":
            actions = [
                getattr(model, field_name)
                for field_name in _BUTTON_ACTION_FIELDS
                if getattr(model, field_name, None) is not None
            ]
            if len(actions) != 1:
                raise ValueError(_("Rich buttons must contain exactly one action"))
            callback_data = getattr(model, "callback_data", None)
            if callback_data is not None and len(callback_data.encode()) > 64:
                raise ValueError(_("Rich callback data is too long"))


def is_trusted_rich_source(source_message: Any, bot_user_id: int | None) -> bool:
    """Return whether Telegram proves the Rich source was authored by Sophie."""
    if source_message is None or bot_user_id is None:
        return False
    if getattr(getattr(source_message, "from_user", None), "id", None) == bot_user_id:
        return True
    origin_user = getattr(getattr(source_message, "forward_origin", None), "sender_user", None)
    return getattr(origin_user, "id", None) == bot_user_id


def validate_rich_message_source(source_message: Any, *, bot_user_id: int | None = None) -> None:
    """Reject bot-bound Rich actions unless the source is Sophie-authored."""
    if bot_user_id is None:
        return
    has_bot_bound_action = rich_message_has_bot_bound_actions(getattr(source_message, "rich_message", None))
    if has_bot_bound_action and not is_trusted_rich_source(source_message, bot_user_id):
        raise ValueError(_("Rich buttons from an untrusted source cannot be saved"))


def validate_rich_message_api(message: RichMessage) -> None:
    validate_rich_message_structure(message)
    if rich_message_has_bot_bound_actions(message):
        raise ValueError(_("Bot-bound Rich buttons are not accepted by the API"))


def _strip_rich_text_buttons(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_rich_text_buttons(item) for item in value]
    if isinstance(value, BaseModel):
        if value.__class__.__name__ == "RichTextButton":
            return _strip_rich_text_buttons(value.button.text)
        if value.__class__.__name__ == "RichBlockButtons":
            labels = [_visible_text(button) for button in value.buttons]
            return RichBlockParagraph(text=" ".join(labels))
        updates: dict[str, Any] = {}
        for field_name in ("text", "credit", "summary", "caption", "blocks", "items", "cells"):
            field_value = getattr(value, field_name, None)
            if isinstance(field_value, (BaseModel, list)):
                updates[field_name] = _strip_rich_text_buttons(field_value)
        return value.model_copy(update=updates) if updates else value
    return value


def render_rich_message(
    message: RichMessage,
    *,
    source_message: Any = None,
    user: Any = None,
    additional_fillings: dict[str, str] | None = None,
) -> RichMessage:
    """Copy a Rich tree and apply placeholders only inside visible RichText leaves."""
    members = getattr(source_message, "new_chat_members", None) or []
    users = members or ([user] if user else [])

    def render_plain(value: str) -> str | list[Any]:
        replacements = {
            "{chatid}": str(getattr(getattr(source_message, "chat", None), "id", "{chatid}")),
            "{chatname}": getattr(getattr(source_message, "chat", None), "title", None) or "{chatname}",
            "{chatnick}": getattr(getattr(source_message, "chat", None), "username", None) or "{chatnick}",
        }
        if user:
            replacements.update(
                {
                    "{first}": user.first_name,
                    "{last}": user.last_name or "",
                    "{fullname}": f"{user.first_name} {user.last_name}".strip() if user.last_name else user.first_name,
                    "{id}": str(user.id),
                    "{username}": user.username or user.first_name,
                }
            )
        if additional_fillings:
            replacements.update(
                {
                    f"{{{key}}}": html.unescape(re.sub("<[^>]+>", "", value))
                    for key, value in additional_fillings.items()
                }
            )
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        if "{mention}" in value and users:
            parts: list[Any] = []
            chunks = value.split("{mention}")
            mention_nodes: list[Any] = []
            for index, mention_user in enumerate(users):
                if index:
                    mention_nodes.append(",")
                mention_nodes.append(RichTextTextMention(text=mention_user.first_name, user=mention_user))
            for index, chunk in enumerate(chunks):
                if chunk:
                    parts.append(chunk)
                if index < len(chunks) - 1:
                    parts.extend(mention_nodes)
            return parts
        return parse_random_text(value)

    def render_value(value: Any) -> Any:
        if isinstance(value, str):
            return render_plain(value)
        if isinstance(value, list):
            rendered: list[Any] = []
            for item in value:
                item_rendered = render_value(item)
                rendered.extend(item_rendered if isinstance(item_rendered, list) else [item_rendered])
            return rendered
        if isinstance(value, BaseModel):
            updates: dict[str, Any] = {}
            for field_name in ("text", "credit", "summary", "caption", "blocks", "items", "cells"):
                field_value = getattr(value, field_name, None)
                if isinstance(field_value, (BaseModel, list, str)):
                    updates[field_name] = render_value(field_value)
            return value.model_copy(update=updates) if updates else value
        return value

    return render_value(copy.deepcopy(message))


def strip_rich_buttons(message: RichMessage) -> RichMessage:
    """Replace embedded Rich buttons with readable labels for raw delivery."""
    stripped_blocks: list[Any] = []
    for block in message.blocks:
        if block.__class__.__name__ == "RichBlockButtons":
            labels = [_visible_text(button) for button in cast("RichBlockButtons", block).buttons]
            stripped_blocks.append(RichBlockParagraph(text=" ".join(labels)))
            continue
        updates: dict[str, Any] = {}
        for field_name in ("text", "credit", "summary", "caption", "blocks", "items", "cells"):
            field_value = getattr(block, field_name, None)
            if isinstance(field_value, (BaseModel, list)):
                updates[field_name] = _strip_rich_text_buttons(field_value)
        stripped_blocks.append(block.model_copy(update=updates) if updates else block)
    return message.model_copy(update={"blocks": stripped_blocks})


def _blocks_to_html(blocks: list[Any]) -> str:
    rendered_blocks = [_block_to_html(block) for block in blocks]
    return "\n".join(rendered for rendered in rendered_blocks if rendered)


def rich_message_to_html_fallback(message: RichMessage) -> str:
    """Project a RichMessage into readable ordinary HTML without callback payloads."""
    return _blocks_to_html(message.blocks)
