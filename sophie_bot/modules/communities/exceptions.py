from __future__ import annotations


class CommunityBanValidationError(Exception):
    """Raised when a community ban is not allowed (e.g. banning an operator, self, or the bot)."""
