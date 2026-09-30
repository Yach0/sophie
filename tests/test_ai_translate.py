import pytest

from sophie_bot.modules.ai.handlers.translate import _build_translate_reply_doc
from sophie_bot.modules.ai.json_schemas.translate import AITranslateResponseSchema


def _translation_response(text: str, notes: str | None = None) -> AITranslateResponseSchema:
    return AITranslateResponseSchema(
        needs_translation=True,
        origin_language_name="English",
        origin_language_emoji="🇬🇧",
        translated_text=text,
        translation_explanations=notes,
    )


@pytest.mark.parametrize(
    ("translated_text", "expandable"),
    [
        ("a" * 120, False),
        ("a" * 121, True),
        ("a" * 40 + "\n" + "b" * 40 + "\n" + "c" * 40, False),
        ("a" * 40 + "\n" + "b" * 40 + "\n" + "c" * 40 + "\n" + "d", True),
        ("This is a short sentence.", False),
    ],
)
def test_translation_expandability_is_based_on_estimated_visible_lines(translated_text: str, expandable: bool) -> None:
    doc = _build_translate_reply_doc(
        _translation_response(translated_text),
        "German",
        False,
        False,
        None,
        "disable",
    )

    rich_html = doc.to_rich()

    if expandable:
        assert "<blockquote expandable>" in rich_html
    else:
        assert "<blockquote expandable>" not in rich_html
        assert "<blockquote>" in rich_html
    assert ("<blockquote expandable>" in rich_html) is expandable
    assert "\n\n" not in rich_html


def test_translation_notes_are_preserved_for_short_translation() -> None:
    doc = _build_translate_reply_doc(
        _translation_response("Hi", "A brief clarification."),
        "German",
        False,
        False,
        None,
        "disable",
    )

    rich_html = doc.to_rich()

    assert "<blockquote>Hi</blockquote>" in rich_html
    assert "Translation Notes" in rich_html
    assert "A brief clarification." in rich_html


def test_voice_translation_without_header_keeps_short_translation_non_expandable() -> None:
    doc = _build_translate_reply_doc(
        _translation_response("Hi"),
        "German",
        False,
        True,
        None,
        "disable",
    )

    rich_html = doc.to_rich()

    assert rich_html == "<blockquote>Hi</blockquote>"


def test_simple_header_is_preserved_for_short_translation() -> None:
    doc = _build_translate_reply_doc(_translation_response("Hi"), "German", False, False, None, "simple")

    assert doc.to_rich() == (
        '<tg-emoji emoji-id="5325547803936572038">✨</tg-emoji> '
        "<b>From 🇬🇧 English to German</b> <blockquote>Hi</blockquote><br><p>🔋</p>"
    )


def test_voice_translation_keeps_notes_after_expandable_translation() -> None:
    text = "a" * 121
    doc = _build_translate_reply_doc(_translation_response(text), "German", False, False, None, "disable")
    doc_with_notes = _build_translate_reply_doc(
        _translation_response(text, "A brief clarification."), "German", False, True, None, "disable"
    )

    assert f"<blockquote expandable>{text}</blockquote>" in doc.to_rich()
    rich_html = doc_with_notes.to_rich()
    assert f"<blockquote expandable>{text}</blockquote>" in rich_html
    assert "Translation Notes" in rich_html
    assert "A brief clarification." in rich_html
