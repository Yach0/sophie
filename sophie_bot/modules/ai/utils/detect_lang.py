from __future__ import annotations

import re

from lingua import Language

from sophie_bot.shared.lang_detect import confidence_for_language
from sophie_bot.shared.lang_detect import detect_languages as detect_languages
from sophie_bot.shared.lang_detect import is_text_language as is_text_language  # nopycln: import
from sophie_bot.shared.lang_detect import lang_code_to_language as lang_code_to_language  # nopycln: import
from sophie_bot.utils.logger import log

_AUTO_TRANSLATE_SOURCE_CONFIDENCE_THRESHOLD = 0.40
_AUTO_TRANSLATE_AMBIGUOUS_CONFIDENCE_GAP = 0.20
_TEXT_LANGUAGE_CONFIDENCE_THRESHOLD = 0.35
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Keep backward-compatible private alias
_confidence_for_language = confidence_for_language


def _normalize_auto_translate_detection_text(text: str) -> str:
    return " ".join(_URL_RE.sub(" ", text).split())


def should_auto_translate_text(text: str, target_language: Language) -> bool:
    """Return whether auto-translate should send text to the AI translator.

    Auto translation should be conservative because a false positive is noisy and also
    spends quota. Short product names, hashtags, link-preview titles, and mixed
    snippets often produce low-confidence or ambiguous language detection results;
    those should be ignored unless the detector is reasonably confident the text is
    in another language.
    """
    detection_text = _normalize_auto_translate_detection_text(text)
    if not detection_text:
        log.debug("should_auto_translate_text: no text after normalization, skipping")
        return False

    confidences = detect_languages(detection_text)
    if not confidences:
        log.debug("should_auto_translate_text: no language confidences, skipping")
        return False

    top_confidence = confidences[0]
    target_confidence = confidence_for_language(confidences, target_language)
    confidence_gap = top_confidence.value - target_confidence.value

    log.debug(
        "should_auto_translate_text",
        top_confidence=top_confidence,
        target_confidence=target_confidence,
        confidence_gap=confidence_gap,
    )

    if top_confidence.language == target_language:
        return False
    if target_confidence.value >= _TEXT_LANGUAGE_CONFIDENCE_THRESHOLD:
        return False
    if top_confidence.value < _AUTO_TRANSLATE_SOURCE_CONFIDENCE_THRESHOLD:
        return False
    return confidence_gap >= _AUTO_TRANSLATE_AMBIGUOUS_CONFIDENCE_GAP
