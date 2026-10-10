from __future__ import annotations

import pytest

from sophie_bot.modules.ai.handlers.translate import _build_translate_reply_doc
from sophie_bot.modules.ai.json_schemas.translate import AITranslateResponseSchema
from sophie_bot.modules.ai.utils.ai_header import AI_CUSTOM_EMOJI_ID


def _build_rendered_translation(translation_explanations: str | None) -> str:
    translated = AITranslateResponseSchema(
        needs_translation=True,
        origin_language_name="English",
        origin_language_emoji="🇬🇧",
        translated_text="Translated text",
        translation_explanations=translation_explanations,
    )
    return _build_translate_reply_doc(
        translated,
        "German",
        is_autotranslate=False,
        is_voice=False,
        quota_header=None,
    ).to_rich()


@pytest.mark.parametrize("translation_explanations", [None, "", "   \n\t"])
def test_translate_reply_keeps_header_and_quote_without_notes(
    translation_explanations: str | None,
) -> None:
    rendered = _build_rendered_translation(translation_explanations)

    assert "<b>From 🇬🇧 English to German</b>" in rendered
    assert "Translated text" in rendered
    assert "</blockquote>" in rendered
    assert "Translation Notes" not in rendered
    # The no-notes Doc.to_rich() output has no two blank lines; a screenshot
    # without the heading may therefore reflect rich-message/client rendering.
    assert "\n\n" not in rendered


def test_translate_reply_preserves_header_quote_and_genuine_translation_notes() -> None:
    rendered = _build_rendered_translation("The phrase has a cultural meaning.")

    assert f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">' in rendered
    assert "<b>From 🇬🇧 English to German</b>" in rendered
    assert "Translated text" in rendered
    assert "</blockquote>" in rendered
    assert "Translation Notes" in rendered
    assert "The phrase has a cultural meaning." in rendered
