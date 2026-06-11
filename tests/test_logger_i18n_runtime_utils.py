import logging
from pathlib import Path
from types import SimpleNamespace

from sophie_bot.utils import logger
from sophie_bot.utils.i18n import I18nNew, LocaleStats
from sophie_bot.utils.runtime_proxy import RuntimeProxy


def test_not_handled_filter_suppresses_unhandled_update_noise() -> None:
    log_filter = logger._NotHandledFilter()

    noisy_record = logging.LogRecord("aiogram", logging.INFO, __file__, 1, "update is not handled", (), None)
    normal_record = logging.LogRecord("aiogram", logging.INFO, __file__, 1, "handled", (), None)

    assert log_filter.filter(noisy_record) is False
    assert log_filter.filter(normal_record) is True


def test_logger_processors_adjust_event_dicts() -> None:
    aiogram_event = logger.silence_processor(logging.getLogger("test"), "info", {"logger": "aiogram.event"})
    pymongo_event = logger.mongo_prefix_processor(
        logging.getLogger("test"),
        "info",
        {"logger": "pymongo.command", "event": "find"},
    )
    prefixed_pymongo_event = logger.mongo_prefix_processor(
        logging.getLogger("test"),
        "info",
        {"logger": "pymongo.command", "event": "mongo: already"},
    )
    security_event = logger.security_color_processor(
        logging.getLogger("test"),
        "info",
        {"logger": "security", "event": "blocked"},
    )

    assert aiogram_event["level"] == "debug"
    assert str(pymongo_event["event"]).startswith("mongo: ")
    assert prefixed_pymongo_event["event"] == "mongo: already"
    assert "blocked" in str(security_event["event"])
    assert str(security_event["event"]).startswith("\033[38;5;208m")


def test_extract_from_record_adds_thread_and_process_names() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message", (), None)

    event_dict = logger.extract_from_record(None, None, {"_record": record})

    assert event_dict["thread_name"] == record.threadName
    assert event_dict["process_name"] == record.processName


def test_ensure_log_directory_ignores_os_errors(monkeypatch) -> None:
    def raise_os_error(path: str, exist_ok: bool) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(logger.os, "makedirs", raise_os_error)

    logger._ensure_log_directory()


def test_runtime_proxy_forwards_call_attributes_repr_and_dir() -> None:
    target = SimpleNamespace(value=42, __call__=None)

    def callable_target(prefix: str) -> str:
        return f"{prefix}:result"

    callable_target.value = 42  # type: ignore[attr-defined]
    proxy = RuntimeProxy(lambda: callable_target)

    assert proxy("prefix") == "prefix:result"
    assert proxy.value == 42
    assert repr(proxy) == repr(callable_target)
    assert "value" in dir(proxy)
    assert target.value == 42


def test_locale_stats_percent_translated_handles_empty_and_mixed_stats() -> None:
    assert LocaleStats(translated=0, fuzzy=0, untranslated=0).percent_translated() == 0
    assert LocaleStats(translated=8, fuzzy=1, untranslated=1).percent_translated() == 80


def test_i18n_parse_stats_reads_stats_file(tmp_path: Path) -> None:
    locale_dir = tmp_path / "uk"
    locale_dir.mkdir()
    (locale_dir / "stats.txt").write_text("10 translated messages, 2 fuzzy translation, 3 untranslated messages")
    i18n = object.__new__(I18nNew)
    i18n.path = str(tmp_path)

    assert i18n.parse_stats("uk") == LocaleStats(translated=10, fuzzy=2, untranslated=3)


def test_i18n_parse_stats_returns_none_for_missing_or_unparseable_file(tmp_path: Path) -> None:
    i18n = object.__new__(I18nNew)
    i18n.path = str(tmp_path)

    assert i18n.parse_stats("missing") is None

    locale_dir = tmp_path / "uk"
    locale_dir.mkdir()
    (locale_dir / "stats.txt").write_text("not stats")

    assert i18n.parse_stats("uk") is None


def test_i18n_locale_helpers_use_babel_and_fallback_locale() -> None:
    i18n = object.__new__(I18nNew)
    i18n.default_locale = "en"
    i18n.babels = {"en": I18nNew.babel("en_US"), "uk": I18nNew.babel("uk_UA")}
    i18n.stats = {"uk": LocaleStats(translated=1, fuzzy=2, untranslated=3)}
    i18n.ctx_locale = SimpleNamespace(get=lambda: "missing")

    assert i18n.current_locale_babel == i18n.babels["en"]
    assert "English" in i18n.locale_display(i18n.babels["en"])
    assert i18n.get_locale_stats("uk") == LocaleStats(translated=1, fuzzy=2, untranslated=3)
    assert i18n.is_current_locale_default() is False
    assert I18nNew.to_iso_639_1("uk_UA") == "uk"
