from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, TextIO

from debug.adapters import EventRecorder
from debug.capture import TextCapture
from debug.protocol import Category, Level, Outcome, Phase

_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def _debug_level(level: int) -> Level:
    if level >= logging.ERROR:
        return Level.ERROR
    if level >= logging.WARNING:
        return Level.WARNING
    if level >= logging.INFO:
        return Level.INFO
    return Level.DEBUG


class CaptureHandler(logging.Handler):
    def __init__(self, recorder: EventRecorder) -> None:
        super().__init__(level=logging.NOTSET)
        self._recorder = recorder

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message: Any
            structured: dict[str, Any] | None
            if isinstance(record.msg, dict):
                structured = dict(record.msg)
                event_value = structured.get("event")
                message = event_value if isinstance(event_value, str) else type(event_value).__name__
            else:
                structured = None
                message = record.getMessage()
            extra = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _LOG_RECORD_FIELDS and key not in {"msg", "args", "exc_info", "exc_text", "stack_info"}
            }
            exception = None
            if record.exc_info is not None and record.exc_info[0] is not None and record.exc_info[1] is not None:
                exception = {
                    "type": record.exc_info[0].__name__,
                    "message": str(record.exc_info[1]),
                    "traceback": logging.Formatter().formatException(record.exc_info),
                }
            self._recorder.emit(
                category=Category.LOG,
                name=record.name or "root",
                phase=Phase.INSTANT,
                level=_debug_level(record.levelno),
                outcome=Outcome.ERROR if record.levelno >= logging.ERROR else None,
                summary=str(message),
                payload={
                    "logger": record.name,
                    "level": record.levelname.lower(),
                    "message": message,
                    "structured": structured,
                    "extra": extra,
                    "exception": exception,
                    "thread": record.threadName,
                    "process": record.processName,
                },
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            self._recorder.sink.recorder_errors += 1


def install_logging_observer(recorder: EventRecorder) -> Callable[[], None]:
    handler = CaptureHandler(recorder)
    root = logging.getLogger()
    security = logging.getLogger("security")
    root.addHandler(handler)
    security.addHandler(handler)

    original_stdout: TextIO = sys.stdout
    original_stderr: TextIO = sys.stderr

    def emit_stream(stream_name: str, line: str) -> None:
        recorder.emit(
            category=Category.LOG,
            name=stream_name,
            phase=Phase.INSTANT,
            summary=line,
            payload={"stream": stream_name, "message": line, "python_text_stream": True},
        )

    stdout_capture = TextCapture(original_stdout, lambda line: emit_stream("stdout", line))
    stderr_capture = TextCapture(original_stderr, lambda line: emit_stream("stderr", line))
    sys.stdout = stdout_capture
    sys.stderr = stderr_capture

    def restore() -> None:
        stdout_capture.flush()
        stderr_capture.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        root.removeHandler(handler)
        security.removeHandler(handler)
        handler.close()

    return restore
