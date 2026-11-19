from collections.abc import Awaitable, Callable
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage

from sophie_bot.utils.logger import log


class _PhotoLinkParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = [0] + [index + 1 for index, character in enumerate(source) if character == "\n"]
        self.cursor = 0
        self.parts: list[str] = []
        self.replaced = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        attributes = dict(attrs)
        source = attributes.get("src") or ""
        label = escape(attributes.get("alt") or source)
        try:
            url = urlsplit(source)
            is_web_url = (
                url.scheme.lower() in {"http", "https"}
                and bool(url.hostname)
                and not any(character.isspace() or ord(character) < 32 for character in source)
            )
        except ValueError:
            is_web_url = False
        replacement = f'<a href="{escape(source, quote=True)}">{label}</a>' if is_web_url else label
        if "tg-spoiler" in attributes:
            replacement = f"<tg-spoiler>{replacement}</tg-spoiler>"
        line, column = self.getpos()
        start = self.line_starts[line - 1] + column
        raw_tag = self.get_starttag_text()
        assert raw_tag is not None
        self.parts.extend((self.source[self.cursor : start], replacement))
        self.cursor = start + len(raw_tag)
        self.replaced = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def render(self) -> str:
        self.feed(self.source)
        self.close()
        self.parts.append(self.source[self.cursor :])
        return "".join(self.parts)


async def with_photo_url_fallback[Result](
    rich_html: str, send: Callable[[InputRichMessage], Awaitable[Result]]
) -> Result:
    """Retry a rejected AI photo payload once as links, preserving the rest of the answer."""
    try:
        return await send(InputRichMessage(html=rich_html))
    except TelegramBadRequest as error:
        if error.message.removeprefix("Bad Request: ") != "RICH_MESSAGE_PHOTO_URL_INVALID":
            raise
        parser = _PhotoLinkParser(rich_html)
        fallback = parser.render()
        if not parser.replaced:
            raise
        log.info("Telegram rejected AI photo URLs; retrying with image links")
        return await send(InputRichMessage(html=fallback))
