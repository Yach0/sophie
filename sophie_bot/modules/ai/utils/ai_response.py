from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from html import escape
from html.parser import HTMLParser
from typing import Any, Final

from stfu_tg import Doc
from stfu_tg.ai_md import ai_markdown_to_doc
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_header import AI_CHATBOT_CUSTOM_EMOJI_ID, build_ai_message_doc
from sophie_bot.modules.ai.utils.ai_tool import AITool

_ALLOWED_HTML_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href"}),
    "b": frozenset(),
    "blockquote": frozenset({"expandable"}),
    "br": frozenset(),
    "code": frozenset({"class"}),
    "del": frozenset(),
    "em": frozenset(),
    "i": frozenset(),
    "ins": frozenset(),
    "pre": frozenset(),
    "s": frozenset(),
    "span": frozenset({"class"}),
    "strike": frozenset(),
    "strong": frozenset(),
    "tg-emoji": frozenset({"emoji-id"}),
    "tg-spoiler": frozenset(),
    "u": frozenset(),
}
_SAFE_LINK_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https", "mailto", "tg"})
_LANGUAGE_CLASS_PATTERN: Final[re.Pattern[str]] = re.compile(r"language-[A-Za-z0-9_-]+")


def _render_allowed_html_tag(
    tag: str,
    attributes: list[tuple[str, str | None]],
    *,
    closing: bool,
    self_closing: bool,
) -> str | None:
    allowed_attributes = _ALLOWED_HTML_ATTRIBUTES.get(tag)
    if allowed_attributes is None or (self_closing and tag != "br"):
        return None
    if closing:
        return None if tag == "br" else f"</{tag}>"

    rendered_attributes: list[str] = []
    for name, value in attributes:
        name = name.casefold()
        if name not in allowed_attributes:
            continue
        if tag == "blockquote" and name == "expandable":
            rendered_attributes.append("expandable")
        elif tag == "a" and name == "href" and value is not None:
            scheme = value.partition(":")[0].casefold()
            if scheme in _SAFE_LINK_SCHEMES:
                rendered_attributes.append(f'href="{escape(value, quote=True)}"')
        elif tag == "code" and name == "class" and value is not None:
            if _LANGUAGE_CLASS_PATTERN.fullmatch(value):
                rendered_attributes.append(f'class="{value}"')
        elif tag == "span" and name == "class" and value == "tg-spoiler":
            rendered_attributes.append('class="tg-spoiler"')
        elif tag == "tg-emoji" and name == "emoji-id" and value is not None and value.isdigit():
            rendered_attributes.append(f'emoji-id="{value}"')

    attributes_html = f" {' '.join(rendered_attributes)}" if rendered_attributes else ""
    return f"<{tag}{attributes_html}>"


class _SingleHTMLTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.recognized = False
        self.invalid = False
        self.rendered: str | None = None

    def _record(self, rendered: str | None) -> None:
        if self.recognized:
            self.invalid = True
            return
        self.recognized = True
        self.rendered = rendered

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(_render_allowed_html_tag(tag, attrs, closing=False, self_closing=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(_render_allowed_html_tag(tag, attrs, closing=False, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        self._record(_render_allowed_html_tag(tag, [], closing=True, self_closing=False))

    def handle_data(self, data: str) -> None:
        if data:
            self.invalid = True


def _find_html_tag_end(text: str, start: int) -> int | None:
    quote: str | None = None
    for index in range(start + 1, len(text)):
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == ">":
            return index
    return None


def _protect_html_tags_in_line(
    line: str,
    token_prefix: str,
    token_suffix: str,
    replacements: list[str],
) -> str:
    parts: list[str] = []
    cursor = 0
    while cursor < len(line):
        character = line[cursor]
        if character == "`":
            code_end = line.find("`", cursor + 1)
            if code_end != -1:
                parts.append(line[cursor : code_end + 1])
                cursor = code_end + 1
                continue
        if character != "<":
            parts.append(character)
            cursor += 1
            continue

        tag_end = _find_html_tag_end(line, cursor)
        if tag_end is None:
            parts.append(character)
            cursor += 1
            continue
        parser = _SingleHTMLTagParser()
        parser.feed(line[cursor : tag_end + 1])
        parser.close()
        if parser.invalid or not parser.recognized:
            parts.append(character)
            cursor += 1
            continue
        if parser.rendered is not None:
            parts.append(f"{token_prefix}{len(replacements)}{token_suffix}")
            replacements.append(parser.rendered)
        cursor = tag_end + 1
    return "".join(parts)


def _protect_supported_html(text: str) -> tuple[str, tuple[str, ...], str, str]:
    nonce = secrets.token_hex(8)
    while nonce in text:
        nonce = secrets.token_hex(8)
    token_prefix = f"\ue000{nonce}:"
    token_suffix = "\ue001"
    replacements: list[str] = []
    protected_lines: list[str] = []
    in_code_fence = False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            protected_lines.append(line)
        elif in_code_fence:
            protected_lines.append(line)
        else:
            protected_lines.append(_protect_html_tags_in_line(line, token_prefix, token_suffix, replacements))
    return "".join(protected_lines), tuple(replacements), token_prefix, token_suffix


class _ProtectedHTMLDoc(Element):
    def __init__(self, doc: Doc, replacements: tuple[str, ...], token_prefix: str, token_suffix: str) -> None:
        self.doc = doc
        self.replacements = replacements
        self.token_pattern = re.compile(rf"{re.escape(token_prefix)}(\d+){re.escape(token_suffix)}")

    def _restore(self, text: str) -> str:
        return self.token_pattern.sub(
            lambda match: self.replacements[int(match.group(1))],
            text,
        )

    def to_html(self, *_args: Any) -> str:
        return self._restore(self.doc.to_html())

    def to_rich(self) -> str:
        return self._restore(self.doc.to_rich())

    def to_md(self) -> str:
        return self._restore(self.doc.to_md())


def _render_ai_markdown(
    text: str,
    *,
    strip_alien_html_tags: bool,
    max_columns: int | None,
    max_rows: int | None,
    card_threshold: int,
) -> Element:
    if not strip_alien_html_tags or "<" not in text:
        return ai_markdown_to_doc(text, max_columns=max_columns, max_rows=max_rows, card_threshold=card_threshold)
    protected_text, replacements, token_prefix, token_suffix = _protect_supported_html(text)
    doc = ai_markdown_to_doc(protected_text, max_columns=max_columns, max_rows=max_rows, card_threshold=card_threshold)
    if not replacements:
        return doc
    return _ProtectedHTMLDoc(doc, replacements, token_prefix, token_suffix)


def render_ai_response(
    header: Element | str | None,
    output_text: str,
    *body: Element | str | None,
    tool_labels: Sequence[AITool] = (),
    strip_alien_html_tags: bool = False,
    max_columns: int | None = None,
    max_rows: int | None = None,
    card_threshold: int = -1,
) -> Doc:
    """Render AI Markdown and compose its metadata and trailing blocks.

    This synchronous boundary owns Markdown conversion and header layout only.
    Callers own mentions, settings, debug output, and delivery. With no header,
    the Markdown keeps its leading block and no AI marker is added."""
    return build_ai_message_doc(
        header,
        _render_ai_markdown(
            output_text,
            strip_alien_html_tags=strip_alien_html_tags,
            max_columns=max_columns,
            max_rows=max_rows,
            card_threshold=card_threshold,
        ),
        *body,
        tool_labels=tool_labels,
        emoji_id=AI_CHATBOT_CUSTOM_EMOJI_ID,
    )
