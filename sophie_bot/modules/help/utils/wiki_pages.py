from __future__ import annotations

from functools import cache
from pathlib import Path

from sophie_bot.utils.logger import log

WIKI_ROOT = Path(__file__).resolve().parents[4] / "wiki_docs"

# Contributor documentation: of no use to someone asking how to use the bot.
_EXCLUDED_DIRECTORIES = frozenset({"Development"})


def _slug(path: Path) -> str:
    return path.stem.lower().replace(" ", "-").replace("%20", "-")


@cache
def get_wiki_pages() -> dict[str, Path]:
    """User-facing wiki pages by slug.

    Empty when the pages are not deployed alongside the code, which callers must tolerate rather
    than fail on: they are documentation, not a dependency.
    """
    if not WIKI_ROOT.is_dir():
        log.warning("Wiki pages are not available", path=str(WIKI_ROOT))
        return {}

    pages = {
        _slug(path): path
        for path in sorted(WIKI_ROOT.rglob("*.md"))
        if _EXCLUDED_DIRECTORIES.isdisjoint(path.relative_to(WIKI_ROOT).parts)
    }
    log.debug("Wiki pages discovered", count=len(pages))
    return pages


def read_wiki_page(slug: str) -> str | None:
    path = get_wiki_pages().get(slug.lower())
    if path is None:
        return None
    return path.read_text(encoding="utf-8")
