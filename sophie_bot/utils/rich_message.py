from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import (
    RichBlockAnimation,
    RichBlockAnchor,
    RichBlockAudio,
    RichBlockBlockQuotation,
    RichBlockButtons,
    RichBlockCaption,
    RichBlockCollage,
    RichBlockDetails,
    RichBlockDivider,
    RichBlockDocument,
    RichBlockExpandableBlockQuotation,
    RichBlockList,
    RichBlockListItem,
    RichBlockMap,
    RichBlockMathematicalExpression,
    RichBlockPhoto,
    RichBlockPullQuotation,
    RichBlockSlideshow,
    RichBlockTable,
    RichBlockTableCell,
    RichBlockUnion,
    RichBlockVideo,
    RichBlockVoiceNote,
    RichMessage,
    RichMessageButton,
    RichTextAnchor,
    RichTextButton,
    RichTextCustomEmoji,
    RichTextMathematicalExpression,
    RichTextUnion,
)


def _blocks_text(blocks: Sequence[RichBlockUnion]) -> str:
    return "\n".join(text for block in blocks if (text := _visible_text(block)))


def _with_credit(text: str, credit: RichTextUnion | None) -> str:
    return text + (f" ({_visible_text(credit)})" if credit else "")


def _media_text(label: str, caption: RichBlockCaption | None) -> str:
    return f"[{label}]" + (f" {_visible_text(caption)}" if caption else "")


def _visible_text(
    value: RichMessage
    | RichMessageButton
    | RichBlockUnion
    | RichBlockCaption
    | RichBlockListItem
    | RichBlockTableCell
    | RichTextUnion
    | None,
) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # RichText arrays are inline; structural arrays are handled by their
        # owning block so their boundaries are never concatenated away.
        return "".join(_visible_text(item) for item in value)
    if isinstance(value, RichMessage):
        return _blocks_text(value.blocks)
    if isinstance(value, (RichBlockAnchor, RichBlockDivider, RichTextAnchor)):
        return ""
    if isinstance(value, RichTextCustomEmoji):
        return value.alternative_text
    if isinstance(value, RichTextButton):
        return _visible_text(value.button.text)
    if isinstance(value, (RichBlockMathematicalExpression, RichTextMathematicalExpression)):
        return value.expression
    if isinstance(value, RichBlockListItem):
        checkbox = ("[x] " if value.is_checked else "[ ] ") if value.has_checkbox else ""
        return f"{value.label} {checkbox}{_blocks_text(value.blocks)}"
    if isinstance(value, RichBlockList):
        return "\n".join(_visible_text(item) for item in value.items)
    if isinstance(value, RichBlockDetails):
        return "\n".join(part for part in (_visible_text(value.summary), _blocks_text(value.blocks)) if part)
    if isinstance(value, RichBlockTable):
        rows = "\n".join(" | ".join(_visible_text(cell) for cell in row) for row in value.cells)
        return "\n".join(part for part in (rows, _visible_text(value.caption)) if part)
    if isinstance(value, RichBlockButtons):
        return " ".join(_visible_text(button.text) for button in value.buttons)
    if isinstance(value, RichBlockCaption):
        return _with_credit(_visible_text(value.text), value.credit)
    if isinstance(value, RichBlockBlockQuotation):
        return _with_credit(_blocks_text(value.blocks), value.credit)
    if isinstance(value, (RichBlockExpandableBlockQuotation, RichBlockPullQuotation)):
        return _with_credit(_visible_text(value.text), value.credit)
    if isinstance(value, (RichBlockCollage, RichBlockSlideshow)):
        return "\n".join(part for part in (_blocks_text(value.blocks), _visible_text(value.caption)) if part)
    if isinstance(value, RichBlockAnimation):
        return _media_text(value.animation.file_name or "Animation", value.caption)
    if isinstance(value, RichBlockAudio):
        return _media_text(value.audio.file_name or "Audio", value.caption)
    if isinstance(value, RichBlockDocument):
        return _media_text(value.document.file_name or "Document", value.caption)
    if isinstance(value, RichBlockPhoto):
        return _media_text("Photo", value.caption)
    if isinstance(value, RichBlockVideo):
        return _media_text(value.video.file_name or "Video", value.caption)
    if isinstance(value, RichBlockVoiceNote):
        return _media_text("VoiceNote", value.caption)
    if isinstance(value, RichBlockMap):
        return _media_text("Map", value.caption)
    # All remaining block and RichText variants wrap visible inline text.
    return _visible_text(value.text)


def rich_message_to_plain_text(message: RichMessage) -> str:
    """Project a RichMessage to visible text without Telegram markup."""
    return _blocks_text(message.blocks)
