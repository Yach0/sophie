from __future__ import annotations

import gettext
from pathlib import Path

_TRANSLATION = gettext.translation(
    "debug",
    localedir=Path(__file__).with_name("locales"),
    languages=["en"],
    fallback=True,
)
gettext_debug = _TRANSLATION.gettext
