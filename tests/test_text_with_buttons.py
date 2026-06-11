from ass_tg.types.base_abc import ParsedArg

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData
from sophie_bot.modules.utils_.text_with_buttons import parse_text_with_buttons


def test_parse_text_with_buttons_extracts_text_offset_and_button_layout() -> None:
    text_arg = ParsedArg(fabric=None, value="Hello with buttons", offset=7, length=18)
    buttons_arg = ParsedArg(
        fabric=None,
        value=[
            AssButtonData(button_type="url", title="Docs", arguments=("https://example.com",)),
            AssButtonData(button_type="rules", title="Rules", arguments=("",), same_row=True),
            AssButtonData(button_type="note", title="Note", arguments=("faq",)),
        ],
        offset=25,
        length=42,
    )

    raw_text, text_offset, buttons = parse_text_with_buttons(
        {"text_with_buttons": {"text": text_arg, "buttons": buttons_arg}}
    )

    assert raw_text == "Hello with buttons"
    assert text_offset == 7
    assert len(buttons) == 2
    assert [button.text for button in buttons[0]] == ["Docs", "Rules"]
    assert buttons[0][0].action == ButtonAction.url
    assert buttons[0][0].data == "https://example.com"
    assert buttons[0][1].action == ButtonAction.rules
    assert buttons[0][1].data is None
    assert buttons[1][0].action == ButtonAction.note
    assert buttons[1][0].data == "faq"


def test_parse_text_with_buttons_returns_defaults_for_missing_payload() -> None:
    raw_text, text_offset, buttons = parse_text_with_buttons({})

    assert raw_text is None
    assert text_offset == 0
    assert buttons == []
