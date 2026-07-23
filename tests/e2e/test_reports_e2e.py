"""End-to-end tests for /report (and the @admin alias)."""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory

from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id, send_reply_command


async def _group_with_reporter(test_client: TestClient) -> tuple[object, object, object]:
    """A group with one admin, a reporter and an offender (both plain members)."""
    admin, group, _model = await create_test_user_and_group(test_client, first_name="Boss", group_title="Report Group")
    await grant_admin(group.id, admin.id)

    reporter = test_client.create_user(user_id=next_user_id(), first_name="Reporter", username="reporter")
    offender = test_client.create_user(user_id=next_user_id(), first_name="Offender", username="offender")
    await test_client.send_message(text="init", from_user=reporter.user, chat=group)
    await test_client.send_message(text="init", from_user=offender.user, chat=group)
    return group, reporter.user, offender.user


@pytest.mark.asyncio
async def test_report_notifies_the_chat(test_client: TestClient) -> None:
    group, reporter, offender = await _group_with_reporter(test_client)
    offending = MessageFactory.create(text="rule-breaking message", from_user=offender, chat=group)

    requests = await send_reply_command(
        test_client, command="report", from_user=reporter, group=group, replied=offending
    )

    assert any("has been reported" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_report_needs_a_reply(test_client: TestClient) -> None:
    group, reporter, _offender = await _group_with_reporter(test_client)

    requests = await test_client.send_command(command="report", from_user=reporter, chat=group)

    assert any("reply to a message" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_admin_reporting_is_told_they_dont_need_to(test_client: TestClient) -> None:
    group, _reporter, offender = await _group_with_reporter(test_client)
    admin = test_client.create_user(user_id=next_user_id(), first_name="ReporterAdmin", username="reporter_admin")
    await test_client.send_message(text="init", from_user=admin.user, chat=group)
    await grant_admin(group.id, admin.user.id)
    offending = MessageFactory.create(text="something", from_user=offender, chat=group)

    requests = await send_reply_command(
        test_client, command="report", from_user=admin.user, group=group, replied=offending
    )

    assert any("don't need to report" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_cannot_report_an_admin(test_client: TestClient) -> None:
    group, reporter, _offender = await _group_with_reporter(test_client)
    target_admin = test_client.create_user(user_id=next_user_id(), first_name="TargetAdmin", username="target_admin")
    await test_client.send_message(text="init", from_user=target_admin.user, chat=group)
    await grant_admin(group.id, target_admin.user.id)
    admin_message = MessageFactory.create(text="admin message", from_user=target_admin.user, chat=group)

    requests = await send_reply_command(
        test_client, command="report", from_user=reporter, group=group, replied=admin_message
    )

    assert any("cannot report an admin" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_at_admin_alias_reports(test_client: TestClient) -> None:
    group, reporter, offender = await _group_with_reporter(test_client)
    offending = MessageFactory.create(text="spam", from_user=offender, chat=group)
    trigger = MessageFactory.create(text="@admin", from_user=reporter, chat=group, reply_to_message=offending)

    from aiogram.types import Update

    from tests.e2e.helpers import next_message_id

    start = len(test_client.capture)
    await test_client.dispatcher.feed_update(
        bot=test_client.bot, update=Update(update_id=next_message_id(), message=trigger)
    )
    requests = test_client.capture.all_requests[start:]

    assert any("has been reported" in (request.text or "").lower() for request in requests)
