from __future__ import annotations


def normalize_admin_title(title: str | None) -> str | None:
    """Collapse whitespace and case-fold an admin title for stable comparison."""
    if title is None:
        return None
    normalized_title = " ".join(title.split()).strip()
    return normalized_title.casefold() if normalized_title else None
