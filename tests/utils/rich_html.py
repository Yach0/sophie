"""Parse native rich HTML for structural assertions, including boolean attributes."""

from __future__ import annotations

from html.parser import HTMLParser
from xml.etree.ElementTree import Element, TreeBuilder


class _RichHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tree = TreeBuilder()
        self.tree.start("root", {})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tree.start(tag, {name: value or "" for name, value in attrs})
        if tag in {"br", "hr", "img"}:
            self.tree.end(tag)

    def handle_endtag(self, tag: str) -> None:
        self.tree.end(tag)

    def handle_data(self, data: str) -> None:
        self.tree.data(data)


def parse_rich_html(html: str) -> Element:
    parser = _RichHTMLParser()
    parser.feed(html)
    parser.close()
    parser.tree.end("root")
    return parser.tree.close()
