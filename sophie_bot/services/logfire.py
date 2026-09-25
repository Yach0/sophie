from __future__ import annotations

import logging

import logfire
from fastapi import FastAPI

from sophie_bot.config import Config
from sophie_bot.versions import SOPHIE_VERSION

_enabled = False
_configured = False
_log_handler: logging.Handler | None = None
_previous_root_level: int | None = None


class _ApplicationLogfireHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("logfire", "opentelemetry", "httpx", "httpcore", "requests", "urllib3")):
            return
        if record.levelno >= logging.CRITICAL:
            level: logfire.LevelName = "fatal"
        elif record.levelno >= logging.ERROR:
            level = "error"
        elif record.levelno >= logging.WARNING:
            level = "warn"
        elif record.levelno >= logging.INFO:
            level = "info"
        elif record.levelno >= logging.DEBUG:
            level = "debug"
        else:
            level = "trace"
        logfire.log(
            level,
            "Application log {message}",
            attributes={"message": record.getMessage(), "logger": record.name},
            exc_info=record.exc_info or False,
        )


def logfire_enabled() -> bool:
    return _enabled


def start_logfire(config: Config, *, app: FastAPI | None = None) -> bool:
    global _enabled, _configured, _log_handler, _previous_root_level
    if config.logfire_token is None:
        return False
    if _enabled:
        return True
    if _configured:
        raise RuntimeError("Logfire is already configured in this process")
    _configured = True
    try:
        logfire.configure(
            token=config.logfire_token.get_secret_value(),
            send_to_logfire=True,
            console=False,
            service_name="sophie-bot",
            service_version=SOPHIE_VERSION,
            environment=f"{config.environment}_{config.mode}",
            scrubbing=False,
        )
        logfire.instrument_pydantic_ai(include_content=True, include_binary_content=True)
        logfire.instrument_system_metrics()
        logfire.instrument_httpx(capture_all=True)
        logfire.instrument_requests()
        if app is not None:
            logfire.instrument_fastapi(app, capture_headers=True)
        handler = _ApplicationLogfireHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        _log_handler = handler
        _previous_root_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)
        _enabled = True
        return True
    except BaseException:
        stop_logfire()
        raise


def stop_logfire() -> None:
    global _enabled, _configured, _log_handler, _previous_root_level
    if _log_handler is not None:
        root_logger = logging.getLogger()
        root_logger.removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None
        if _previous_root_level is not None:
            root_logger.setLevel(_previous_root_level)
            _previous_root_level = None
    _enabled = False
    if _configured:
        logfire.shutdown(flush=True)
        _configured = False


def capture_logfire_error(exception: Exception) -> None:
    if not _enabled:
        return
    logfire.exception("Unhandled application error", _exc_info=exception, error_type=type(exception).__name__)
