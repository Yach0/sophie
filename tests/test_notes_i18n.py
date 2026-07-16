"""Regression tests for untranslatable note confirmation strings (finding #42).

`delete.py` titled the multi-note delete confirmation with a bare "Deleted notes" literal, and
`save.py` built its confirmation from bare `KeyValue("Note names", ...)` / `KeyValue("Description",
...)` labels. None of the three reached gettext, so they rendered in English under every locale.

These assert the strings are actually extractable by pybabel -- the property that makes them
translatable at all -- rather than that some call happened.
"""

from __future__ import annotations

from pathlib import Path

from babel.messages.extract import extract_from_file

from sophie_bot.modules.notes.handlers import delete as delete_handler
from sophie_bot.modules.notes.handlers import save as save_handler


def _extracted_msgids(module_file: str) -> set[str]:
    """Every literal pybabel would pull out of `module_file` into the catalog."""
    msgids: set[str] = set()
    for _lineno, messages, _comments, _context in extract_from_file("python", Path(module_file)):
        # Plural calls yield a tuple of msgids; singular calls yield the string itself.
        for message in messages if isinstance(messages, tuple) else (messages,):
            if isinstance(message, str):
                msgids.add(message)
    return msgids


def test_delete_confirmation_title_is_translatable() -> None:
    assert "Deleted notes" in _extracted_msgids(delete_handler.__file__)


def test_save_confirmation_labels_are_translatable() -> None:
    msgids = _extracted_msgids(save_handler.__file__)

    assert "Note names" in msgids
    assert "Description" in msgids
