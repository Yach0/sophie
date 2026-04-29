from __future__ import annotations

import re
from typing import Iterable, Optional

from lingua import (
    ConfidenceValue,
    IsoCode639_1,
    Language,
    LanguageDetector,
    LanguageDetectorBuilder,
)

from sophie_bot.middlewares import i18n
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.logger import log

_FALLBACK_LANGUAGES: tuple[Language, ...] = (Language.ENGLISH,)
_TEXT_LANGUAGE_CONFIDENCE_THRESHOLD = 0.35
_AUTO_TRANSLATE_SOURCE_CONFIDENCE_THRESHOLD = 0.40
_AUTO_TRANSLATE_AMBIGUOUS_CONFIDENCE_GAP = 0.20
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_detector: Optional[LanguageDetector] = None


def lang_code_to_language(lang_code: str) -> Language:
    # IsoCode639_1 is a stupid enum, it doesn't support any kind of magic getter, therefore we get its attribute
    return Language.from_iso_code_639_1(getattr(IsoCode639_1, lang_code.upper()))


def _languages_from_locales(locales: Iterable[str]) -> tuple[Language, ...]:
    languages = tuple(lang_code_to_language(lang_code) for lang_code in locales)

    if not languages:
        log.warning(
            "Language detector: no locales available, falling back to default languages.",
        )
        return _FALLBACK_LANGUAGES

    return languages


def build_language_detector(locales: Optional[Iterable[str]] = None) -> LanguageDetector:
    language_codes = tuple(locales) if locales is not None else i18n.locales_iso_639_1
    languages = _languages_from_locales(language_codes)

    return LanguageDetectorBuilder.from_languages(*languages).with_preloaded_language_models().build()


def get_detector() -> LanguageDetector:
    global _detector

    if _detector is None:
        _detector = build_language_detector()

    return _detector


def detect_languages(text: str) -> list[ConfidenceValue]:
    detector = get_detector()

    confidences = detector.compute_language_confidence_values(text)
    log.debug("detect_languages", confidence=confidences)
    return confidences


def _confidence_for_language(confidences: Iterable[ConfidenceValue], language: Language) -> ConfidenceValue:
    try:
        return next(confidence for confidence in confidences if confidence.language == language)
    except StopIteration:
        raise SophieException("Language detection failed! No required language detected in the confidence list.")


def is_text_language(text: str, language: Language) -> bool:
    confidences = detect_languages(text)
    confidence = _confidence_for_language(confidences, language)

    log.debug("is_text_language", confidence=confidence)
    return confidence.value >= _TEXT_LANGUAGE_CONFIDENCE_THRESHOLD


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
    target_confidence = _confidence_for_language(confidences, target_language)
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
