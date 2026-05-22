import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.config import CONFIG
from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.compat import parse_legacy_text_buttons
from sophie_bot.modules.notes.utils.buttons.parser import parse_buttons_from_text
from sophie_bot.modules.notes.utils.buttons.renderer import render_button, render_buttons
from sophie_bot.modules.notes.utils.buttons.storage import (
    UnknownMessageButtonTypeError,
    button_from_markup,
    buttons_from_markup,
)


@pytest.fixture(autouse=True)
def setup_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CONFIG, "username", "SophieBot")


def test_legacy_compat_parser_extracts_buttons_and_clean_text() -> None:
    text, buttons = parse_legacy_text_buttons(
        "Menu\n[Open](btnurl:https://example.com)\n[Rules](btnrules)\n[Delete](btndelmsg)"
    )

    assert text == "Menu\n"
    assert buttons == [
        [Button(text="Open", action=ButtonAction.url, data="https://example.com")],
        [Button(text="Rules", action=ButtonAction.rules)],
        [Button(text="Delete", action=ButtonAction.delmsg)],
    ]


def test_legacy_compat_parser_keeps_same_row_buttons_together() -> None:
    _text, buttons = parse_legacy_text_buttons(
        "[One](btnurl:https://one.example) [Two](btnurl:https://two.example:same)"
    )

    assert len(buttons) == 1
    assert [button.text for button in buttons[0]] == ["One", "Two"]


def test_legacy_compat_parser_supports_hash_note_buttons() -> None:
    text, buttons = parse_legacy_text_buttons("Menu\n[Child](#child)")

    assert text == "Menu\n"
    assert buttons == [[Button(text="Child", action=ButtonAction.note, data="child")]]


@pytest.mark.asyncio
async def test_parse_buttons_from_text_uses_current_ass_syntax() -> None:
    buttons = await parse_buttons_from_text("[Open](btnurl:https://example.com) [Rules](btnrules:same)")

    assert buttons == [
        [
            Button(text="Open", action=ButtonAction.url, data="https://example.com"),
            Button(text="Rules", action=ButtonAction.rules),
        ]
    ]


def test_render_button_builds_legacy_payload_for_note_buttons() -> None:
    button = Button(text="Child", action=ButtonAction.note, data="child")

    rendered = render_button(button, -100123)

    assert rendered is not None
    assert rendered.url == "https://t.me/SophieBot?start=btnnotesm_child_-100123"


def test_render_buttons_skips_invalid_urls() -> None:
    markup = render_buttons([[Button(text="Bad", action=ButtonAction.url, data="not-a-url")]], 123)

    assert markup.inline_keyboard == []


def test_button_from_markup_accepts_url_buttons() -> None:
    button = InlineKeyboardButton(text="Open", url="https://example.com", style="success")

    assert button_from_markup(button) == Button(
        text="Open",
        action=ButtonAction.url,
        data="https://example.com",
        style="success",
    )


def test_button_from_markup_rejects_callback_buttons() -> None:
    button = InlineKeyboardButton(text="Callback", callback_data="payload")

    with pytest.raises(UnknownMessageButtonTypeError):
        button_from_markup(button)


def test_buttons_from_markup_drops_rows_with_unsupported_buttons() -> None:
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Open", url="https://example.com")],
            [InlineKeyboardButton(text="Callback", callback_data="payload")],
        ]
    )

    assert buttons_from_markup(markup) == [[Button(text="Open", action=ButtonAction.url, data="https://example.com")]]
