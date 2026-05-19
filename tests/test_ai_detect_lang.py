from __future__ import annotations

import pytest

from sophie_bot.modules.ai.utils.detect_lang import should_auto_translate_text
from sophie_bot.shared.lang_detect import lang_code_to_language


@pytest.mark.parametrize(
    "text",
    (
        "MicroGPlus\n\nEasy #MicroG installer",
        "A quick roundup of the new features in Ubuntu 26.04 LTS. 📹\nhttps://www.youtube.com/watch?v=xpto",
        "https://www.youtube.com/watch?v=xpto",
    ),
)
def test_should_auto_translate_text_ignores_english_false_positives(text: str) -> None:
    assert not should_auto_translate_text(text.lower(), lang_code_to_language("en"))


@pytest.mark.parametrize(
    "text",
    (
        "Guten Morgen wie geht es dir",
        "Привет, как дела?",
    ),
)
def test_should_auto_translate_text_allows_confident_foreign_text(text: str) -> None:
    assert should_auto_translate_text(text.lower(), lang_code_to_language("en"))
