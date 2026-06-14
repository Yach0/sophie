from __future__ import annotations

from typing import Final, Protocol

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InputRichMessage
from stfu_tg import (
    Anchor,
    Audio,
    BlockQuote,
    Bold,
    Cite,
    Code,
    Collage,
    CustomEmoji,
    Del,
    Details,
    Divider,
    Doc,
    Em,
    Figure,
    Footer,
    Heading,
    HList,
    Image,
    Ins,
    InvisibleSymbol,
    Italic,
    KeyValue,
    ListItem,
    Map,
    Mark,
    Math,
    MathBlock,
    OrderedList,
    Paragraph,
    Pre,
    PreformattedHTML,
    PullQuote,
    Reference,
    ReferencedText,
    RichBlockQuote,
    RichTable,
    RichTableCell,
    Section,
    Slideshow,
    Spacer,
    Spoiler,
    Strike,
    Strikethrough,
    Strong,
    Subscript,
    Superscript,
    Template,
    Time,
    Title,
    Underline,
    UnorderedList,
    Url,
    UserLink,
    Video,
    VList,
)
from stfu_tg.md import HRuler, TableMD

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

_RICH_PHOTO_URL: Final = "https://telegram.org/example/photo.jpg"
_RICH_VIDEO_URL: Final = "https://telegram.org/example/video.mp4"
_RICH_AUDIO_URL: Final = "https://telegram.org/example/audio.mp3"
_RICH_ANIMATION_URL: Final = "https://telegram.org/example/animation.gif"
_RICH_EMOJI_ID: Final = "5368324170671202286"


class _HtmlElement(Protocol):
    def to_html(self) -> str: ...


class _RichElement(Protocol):
    def to_rich(self) -> str: ...


def _source(label: str, value: str) -> KeyValue:
    return KeyValue(label, Code(value), title_bold=False)


def _html_source(label: str, item: _HtmlElement) -> KeyValue:
    return _source(label, item.to_html())


def _rich_source(label: str, item: _RichElement) -> KeyValue:
    return _source(label, item.to_rich())


def _rich_example(label: str, item: _RichElement) -> Doc:
    return Doc(Paragraph(Strong(label)), item)


def _build_core_gallery() -> Doc:
    template = Template(
        _("Template with {style} and escaped {user_input}"),
        style=Bold(_("bold placeholder")),
        user_input="<unsafe>",
    )

    return Doc(
        Title(Bold(_("STFU Formatting Gallery"))),
        Section(
            _html_source("Doc", Doc(_("first line"), _("second line"))),
            _html_source("Title", Title(_("Regular title"))),
            _html_source("Section", Section(KeyValue(_("Field"), Code("value")), title=_("Section title"))),
            _html_source("Template", template),
            _html_source("PreformattedHTML", PreformattedHTML("<b>trusted raw HTML</b>")),
            title=_("Containers and meta elements"),
        ),
        Section(
            _html_source("Bold", Bold(_("bold text"))),
            _html_source("Italic", Italic(_("italic text"))),
            _html_source("Underline", Underline(_("underlined text"))),
            _html_source("Strikethrough", Strikethrough(_("strikethrough text"))),
            _html_source("Spoiler", Spoiler(_("spoiler text"))),
            _html_source("Code", Code("inline code")),
            _html_source("Pre", Pre("print('hello')", language="python")),
            _html_source("Url", Url(_("Telegram"), "https://t.me/")),
            _html_source("UserLink", UserLink(123456789, _("User mention"))),
            _html_source("BlockQuote", BlockQuote(_("quoted text"), expandable=True)),
            title=_("Regular inline formatting"),
        ),
        Section(
            _html_source("KeyValue", KeyValue(_("Key"), _("Value"))),
            _html_source("HList", HList(_("alpha"), _("beta"), prefix="• ", divider=" | ")),
            _html_source("VList", VList(_("first item"), Bold(_("second item")))),
            _html_source("InvisibleSymbol", Doc(_("before"), InvisibleSymbol(), _("after"))),
            _html_source("Spacer", Doc(_("left"), Spacer(), _("right"))),
            _source("TableMD.to_md", TableMD([_("Name"), _("Value")], [_("Rows"), "2"]).to_md()),
            _source("TableMD.to_rich", TableMD([_("Name"), _("Value")], [_("Rows"), "2"]).to_rich()),
            _source("HRuler.to_md", HRuler().to_md()),
            _source("HRuler.to_rich", HRuler().to_rich()),
            title=_("Convenience and Markdown helpers"),
        ),
    )


def _build_rich_text_gallery() -> Doc:
    return Doc(
        Title(Bold(_("STFU Rich HTML Gallery: Text"))),
        Section(
            _rich_example("Strong", Strong(_("bold text"))),
            _rich_example("Em", Em(_("italic text"))),
            _rich_example("Ins", Ins(_("underlined text"))),
            _rich_example("Strike", Strike(_("strikethrough text"))),
            _rich_example("Del", Del(_("deleted text"))),
            _rich_example("Mark", Mark(_("marked text"))),
            _rich_example("Subscript", Subscript("2")),
            _rich_example("Superscript", Superscript("2")),
            _rich_example("Cite", Cite(_("The Author"))),
            _rich_example("CustomEmoji", CustomEmoji(_RICH_EMOJI_ID, "👍")),
            _rich_example("Emoji image", Image(f"tg://emoji?id={_RICH_EMOJI_ID}", alt="👍")),
            _rich_example("Time", Time(1647531900, _("22:45 tomorrow"), time_format="wDT")),
            _rich_example("Math", Math("x^2 + y^2")),
            title=_("Inline rich components"),
        ),
        Section(
            _rich_example("Anchor", Anchor("chapter-1")),
            _rich_example("Reference", Reference("note-1", _("Reference"))),
            _rich_example("ReferencedText", ReferencedText("note-1", _("Referenced text"))),
            _rich_example("Url", Url(_("inline URL"), "https://t.me/")),
            _rich_example("Mail link", Url(_("inline e-mail"), "mailto:user@example.com")),
            _rich_example("Phone link", Url(_("inline phone number"), "tel:+123456789")),
            _rich_example("User mention", Url(_("inline mention of a user"), "tg://user?id=123456789")),
            title=_("Links, anchors, and references"),
        ),
        Section(
            _rich_example("Heading 1", Heading(_("Heading 1"), level=1)),
            _rich_example("Heading 2", Heading(_("Heading 2"), level=2)),
            _rich_example("Heading 3", Heading(_("Heading 3"), level=3)),
            _rich_example("Heading 4", Heading(_("Heading 4"), level=4)),
            _rich_example("Heading 5", Heading(_("Heading 5"), level=5)),
            _rich_example("Heading 6", Heading(_("Heading 6"), level=6)),
            _rich_example("Paragraph", Paragraph(_("Paragraph text"))),
            _rich_example("Pre", Pre("print('rich code')", language="python")),
            _rich_example("Footer", Footer(_("Footer text"))),
            _rich_example("Divider", Divider()),
            title=_("Rich text blocks"),
        ),
    )


def _build_rich_structure_gallery() -> Doc:
    rich_table = RichTable(
        [RichTableCell(_("Header 1"), is_header=True), RichTableCell(_("Header 2"), is_header=True)],
        [RichTableCell(_("Value"), colspan=2, rowspan=2, align="left"), RichTableCell(_("Value 2"), align="center")],
        [RichTableCell(_("Value 4"), valign="top"), RichTableCell(_("Value 5"), valign="middle")],
        bordered=True,
        striped=True,
        caption=_("Table caption"),
    )

    return Doc(
        Title(Bold(_("STFU Rich HTML Gallery: Structure"))),
        Section(
            _rich_example("UnorderedList", UnorderedList(_("unordered list item"))),
            _rich_example("OrderedList", OrderedList(_("ordered list item"))),
            _rich_example(
                "OrderedList attributes",
                OrderedList(_("ordered list item"), start=3, list_type="a", reversed=True),
            ),
            _rich_example("ListItem", OrderedList(ListItem(_("explicit number"), value=7, item_type="i"))),
            title=_("Lists"),
        ),
        Section(
            _rich_example(
                "RichBlockQuote", RichBlockQuote(_("Block quotation started"), _("continued"), credit=_("The Author"))
            ),
            _rich_example("PullQuote", PullQuote(_("Pull quote"), credit=_("The Author"))),
            _rich_example("Details", Details(_("Title"), Paragraph(_("Content")), open=True)),
            title=_("Quotes and collapsible blocks"),
        ),
        Section(
            _rich_example("RichTable", rich_table),
            _rich_example("MathBlock", MathBlock("E = mc^2")),
            title=_("Tables and formulas"),
        ),
    )


def _build_rich_media_gallery() -> Doc:
    return Doc(
        Title(Bold(_("STFU Rich HTML Gallery: Media"))),
        Section(
            _rich_example("Image", Image(_RICH_PHOTO_URL)),
            _rich_example("Video", Video(_RICH_VIDEO_URL)),
            _rich_example("Audio MP3", Audio(_RICH_AUDIO_URL)),
            _rich_example("Animation", Video(_RICH_ANIMATION_URL)),
            _rich_example("Spoiler image", Image(_RICH_PHOTO_URL, spoiler=True)),
            _rich_example("Spoiler video", Video(_RICH_VIDEO_URL, spoiler=True)),
            title=_("Media blocks"),
        ),
        Section(
            _rich_example(
                "Figure image",
                Figure(Image(_RICH_PHOTO_URL, spoiler=True), caption=_("Photo caption"), credit=_("Photo credit")),
            ),
            _rich_example("Figure video", Figure(Video(_RICH_VIDEO_URL, spoiler=True), caption=_("Video caption"))),
            _rich_example("Figure audio", Figure(Audio(_RICH_AUDIO_URL), caption=_("Audio caption"))),
            _rich_example("Map", Map(41.9, 12.5, zoom=14)),
            _rich_example("Figure map", Figure(Map(41.9, 12.5, zoom=14), caption=_("Map caption"))),
            title=_("Figures and maps"),
        ),
        Section(
            _rich_example("Collage", Collage(Image(_RICH_PHOTO_URL), Video(_RICH_VIDEO_URL))),
            _rich_example(
                "Collage caption",
                Collage(Video(_RICH_VIDEO_URL), Image(_RICH_PHOTO_URL), caption=_("Collage caption")),
            ),
            _rich_example("Slideshow", Slideshow(Image(_RICH_PHOTO_URL), Video(_RICH_VIDEO_URL))),
            _rich_example(
                "Slideshow caption",
                Slideshow(Video(_RICH_VIDEO_URL), Image(_RICH_PHOTO_URL), caption=_("Slideshow caption")),
            ),
            title=_("Collages and slideshows"),
        ),
    )


def build_stfu_gallery_docs() -> tuple[Doc, ...]:
    return (
        _build_core_gallery(),
        _build_rich_text_gallery(),
        _build_rich_structure_gallery(),
        _build_rich_media_gallery(),
    )


@flags.help(description=l_("Show an operator-only STFU formatting gallery."))
class StfuGalleryHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter(("op_stfu_gallery", "op_gallery")), IsOP(True)

    async def handle(self) -> None:
        message_thread_id = self.event.message_thread_id if self.event.is_topic_message else None
        for gallery_doc in build_stfu_gallery_docs():
            await self.event.bot.send_rich_message(
                chat_id=self.event.chat.id,
                message_thread_id=message_thread_id,
                rich_message=InputRichMessage(html=gallery_doc.to_rich()),
            )
