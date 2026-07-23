import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramServerError

from sophie_bot.modules.error.utils.ignored import QUIET_EXCEPTIONS, SENTRY_IGNORED_EXCEPTIONS
from sophie_bot.services import sentry


@pytest.mark.parametrize("placeholder", ["No commit", "unknown"])
def test_release_is_bare_version_for_placeholder_commits(monkeypatch: pytest.MonkeyPatch, placeholder: str) -> None:
    monkeypatch.setattr(sentry, "SOPHIE_COMMIT", placeholder)
    monkeypatch.setattr(sentry, "SOPHIE_VERSION", "4.4.1")

    assert sentry.build_release() == "4.4.1"


def test_release_includes_commit_so_builds_are_distinguishable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The version only changes on a bump, so without the commit every build reports the same release."""
    monkeypatch.setattr(sentry, "SOPHIE_COMMIT", "abc1234")
    monkeypatch.setattr(sentry, "SOPHIE_VERSION", "4.4.1")

    assert sentry.build_release() == "4.4.1+abc1234"


def test_telegram_server_error_is_ignored_by_sentry_only() -> None:
    """Transient upstream 5xx: polling retries with backoff, so the getUpdates noise is not a bug.

    But it must NOT be quiet: a 5xx on an outgoing call inside a handler means the command failed,
    and QUIET_EXCEPTIONS returns with no user reply, no Sentry and no FSM cleanup.
    """
    assert TelegramServerError in SENTRY_IGNORED_EXCEPTIONS
    assert TelegramServerError not in QUIET_EXCEPTIONS
    # It does not inherit from TelegramNetworkError, so it is not covered transitively.
    assert not issubclass(TelegramServerError, TelegramNetworkError)
