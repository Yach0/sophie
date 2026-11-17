from aiogram import Router

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.locks.handlers.lockable import ListLockableHandler


def test_lockable_aliases_share_registered_handler_filter() -> None:
    router = Router()
    ListLockableHandler.register(router)

    registrations = [
        (registration.callback, event_filter.callback)
        for registration in router.message.handlers
        for event_filter in registration.filters or []
        if isinstance(event_filter.callback, CMDFilter)
    ]
    assert len(registrations) == 1
    handler, command_filter = registrations[0]
    assert handler is ListLockableHandler
    assert command_filter.cmd == ("lockable", "lock_types", "lockables")
    for command in ("lockable", "lockables", "lock_types"):
        parsed = command_filter.extract_command(f"/{command}")
        assert command_filter.validate_command(parsed).command == command
