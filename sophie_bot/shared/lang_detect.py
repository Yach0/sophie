"""Language detection utilities shared across modules.

Extracted from sophie_bot.modules.ai.utils.detect_lang to break the
ai ↔ locks circular dependency.
"""

from __future__ import annotations

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
_detector: Optional[LanguageDetector] = None


def lang_code_to_language(lang_code: str) -> Language:
    """Convert an ISO 639-1 language code string to a lingua Language enum."""
    # IsoCode639_1 is a strict enum — access its attribute by uppercased name.
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
    """Build a lingua LanguageDetector for the given (or configured) locales."""
    language_codes = tuple(locales) if locales is not None else i18n.locales_iso_639_1
    languages = _languages_from_locales(language_codes)

    return LanguageDetectorBuilder.from_languages(*languages).with_preloaded_language_models().build()


def get_detector() -> LanguageDetector:
    """Return the singleton LanguageDetector, building it on first access."""
    global _detector

    if _detector is None:
        _detector = build_language_detector()

    return _detector


def detect_languages(text: str) -> list[ConfidenceValue]:
    """Run language detection and return confidence values for all languages."""
    detector = get_detector()

    confidences = detector.compute_language_confidence_values(text)
    log.debug("detect_languages", confidence=confidences)
    return confidences


def confidence_for_language(confidences: Iterable[ConfidenceValue], language: Language) -> ConfidenceValue:
    """Find the confidence value for a specific language in a list of results."""
    try:
        return next(confidence for confidence in confidences if confidence.language == language)
    except StopIteration:
        raise SophieException("Language detection failed! No required language detected in the confidence list.")


def is_text_language(text: str, language: Language) -> bool:
    """Return True if the given text is detected as the specified language."""
    confidences = detect_languages(text)
    confidence = confidence_for_language(confidences, language)

    log.debug("is_text_language", confidence=confidence)
    return confidence.value >= _TEXT_LANGUAGE_CONFIDENCE_THRESHOLD
