import pytest
from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgTypeError

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.parse_arg import ButtonArg, ButtonsArg
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import AssButtonData


@pytest.mark.asyncio
async def test_buttons_arg_many():
    arg = ButtonsArg()
    text = "[Google](btnurl:https://google.com) [Rules](btnrules)"
    entities = ArgEntities([])

    assert arg.check(text, entities) is True

    parsed_arg = await arg(text, 0, entities)
    data = parsed_arg.value
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0].button_type == "url"
    assert data[1].button_type == "rules"


@pytest.mark.asyncio
async def test_buttons_arg_many_newlines():
    arg = ButtonsArg()
    text = "[Google](btnurl:https://google.com)\n[Rules](btnrules)"
    entities = ArgEntities([])

    assert arg.check(text, entities) is True

    parsed_arg = await arg(text, 0, entities)
    data = parsed_arg.value
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0].button_type == "url"
    assert data[1].button_type == "rules"


_SINGLE_BUTTON_CASES = [
    pytest.param(
        "[Google](btnurl:https://google.com)",
        "url",
        ("https://google.com",),
        {"same_row": False},
        id="url",
    ),
    pytest.param(
        "[Google](btnurl:https://google.com:same)",
        "url",
        ("https://google.com",),
        {"same_row": True},
        id="url_same_row",
    ),
    pytest.param(
        "[Google](buttonurl#success://google.com)",
        "url",
        ("https://google.com",),
        {"style": "success"},
        id="url_with_style",
    ),
    pytest.param(
        "[Google](buttonurl#primary://google.com:same)",
        "url",
        ("https://google.com",),
        {"same_row": True, "style": "primary"},
        id="url_with_style_same_row",
    ),
    pytest.param(
        "[Note](btnnote:my_note)",
        "note",
        ("my_note",),
        {},
        id="note",
    ),
    pytest.param(
        "[Note](btnnote:my_note:same)",
        "note",
        ("my_note",),
        {"same_row": True},
        id="note_same_row",
    ),
    pytest.param(
        "[Rules](btnrules)",
        "rules",
        ("",),
        {"same_row": False},
        id="rules",
    ),
    pytest.param(
        "[Rules](btnrules:^)",
        "rules",
        ("",),
        {"same_row": True},
        id="rules_same_row",
    ),
    pytest.param(
        "[Delete](delmsg)",
        "delmsg",
        ("",),
        {},
        id="delmsg",
    ),
    pytest.param(
        "[Delete](btndelmsg)",
        "delmsg",
        None,
        {},
        id="delmsg_prefix",
    ),
    pytest.param(
        "[Delete](buttondelmsg)",
        "delmsg",
        None,
        {},
        id="delmsg_prefix_button",
    ),
    pytest.param(
        "[Connect](btnconnect)",
        "connect",
        None,
        {},
        id="connect",
    ),
    pytest.param(
        "[Captcha](btnwelcomesecurity)",
        "welcomesecurity",
        None,
        {},
        id="captcha",
    ),
    pytest.param(
        "[Sophie](btnsophieurl)",
        "sophieurl",
        None,
        {},
        id="sophie_dm",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text,button_type,arguments,extras", _SINGLE_BUTTON_CASES)
async def test_single_button(
    text: str,
    button_type: str,
    arguments: tuple[str, ...] | None,
    extras: dict,
) -> None:
    arg = ButtonArg()
    entities = ArgEntities([])

    assert arg.check(text, entities) is True

    parsed_arg = await arg(text, 0, entities)
    assert parsed_arg.length == len(text)
    data = parsed_arg.value
    assert isinstance(data, AssButtonData)
    assert data.button_type == button_type
    if arguments is not None:
        assert data.arguments == arguments
    for attr, expected in extras.items():
        assert getattr(data, attr) == expected


_INVALID_BUTTON_CASES = [
    pytest.param("[Google](invalidurl:https://google.com)", id="invalid_prefix"),
    pytest.param("[Google](btninvalid:something)", id="invalid_button_type"),
    pytest.param("Not a button", id="not_a_button"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _INVALID_BUTTON_CASES)
async def test_invalid_button(text: str) -> None:
    arg = ButtonArg()
    entities = ArgEntities([])

    with pytest.raises(ArgTypeError):
        await arg(text, 0, entities)
