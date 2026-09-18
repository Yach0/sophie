from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import JsonValue

from debug import collector
from debug.collector import CollectorState, create_app
from debug.protocol import ReplyFrame


def make_state() -> CollectorState:
    return CollectorState(
        session_id="session",
        bearer_token="bearer-secret",
        browser_credential="browser-secret",
        csrf_token="csrf-secret",
        api_origin="http://127.0.0.1:8079",
        ui_origin="http://127.0.0.1:5174",
        sanitized_targets={},
        run_id="run-1",
        worker_pid=123,
        state="ready",
        channel_generation=7,
    )


def bearer_headers() -> dict[str, str]:
    return {"Authorization": "Bearer bearer-secret"}


async def prepare_redis_set(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/actions/prepare",
        headers=bearer_headers(),
        json={"kind": "redis.command", "command": "SET", "args": ["debug:key", "value"]},
    )
    assert response.status_code == 200
    return response.json()


def test_concurrent_action_execute_dispatches_exactly_once() -> None:
    async def scenario() -> None:
        state = make_state()
        dispatched = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def dispatch(
            request_id: str,
            run_id: str,
            operation: str,
            channel_generation: int,
            payload: dict[str, JsonValue],
        ) -> ReplyFrame:
            nonlocal calls
            calls += 1
            assert operation == "redis.command"
            assert channel_generation == 7
            assert payload["args"] == ["debug:key", "value"]
            dispatched.set()
            await release.wait()
            return ReplyFrame(request_id=request_id, run_id=run_id, result={"written": True})

        state.control_dispatch = dispatch
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            prepared = await prepare_redis_set(client)
            execute_body = {
                "run_id": prepared["run_id"],
                "confirmation_text": prepared["confirmation_text"],
            }
            first = asyncio.create_task(
                client.post(
                    f"/api/v1/actions/{prepared['action_id']}/execute",
                    headers=bearer_headers(),
                    json=execute_body,
                )
            )
            await dispatched.wait()
            concurrent = await client.post(
                f"/api/v1/actions/{prepared['action_id']}/execute",
                headers=bearer_headers(),
                json=execute_body,
            )
            assert concurrent.status_code == 202
            assert concurrent.json()["state"] == "running"
            release.set()
            completed = await first
            assert completed.status_code == 200
            assert completed.json()["state"] == "succeeded"

            replay = await client.post(
                f"/api/v1/actions/{prepared['action_id']}/execute",
                headers=bearer_headers(),
                json=execute_body,
            )
            assert replay.status_code == 200
            assert replay.json()["state"] == "succeeded"
            assert calls == 1

    asyncio.run(scenario())


def test_timed_out_write_stays_unknown_until_late_reply_refines_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        state = make_state()
        dispatched = asyncio.Event()
        release = asyncio.Event()

        async def dispatch(
            request_id: str,
            run_id: str,
            operation: str,
            channel_generation: int,
            payload: dict[str, JsonValue],
        ) -> ReplyFrame:
            dispatched.set()
            await release.wait()
            return ReplyFrame(request_id=request_id, run_id=run_id, result={"written": True})

        state.control_dispatch = dispatch
        monkeypatch.setattr(collector, "WRITE_REPLY_DEADLINE_SECONDS", 0.01)
        transport = httpx.ASGITransport(app=create_app(state))
        async with httpx.AsyncClient(transport=transport, base_url=state.api_origin) as client:
            prepared = await prepare_redis_set(client)
            response = await client.post(
                f"/api/v1/actions/{prepared['action_id']}/execute",
                headers=bearer_headers(),
                json={
                    "run_id": prepared["run_id"],
                    "confirmation_text": prepared["confirmation_text"],
                },
            )
            await dispatched.wait()
            assert response.status_code == 504
            assert response.json()["error"]["status_url"] == f"/api/v1/actions/{prepared['action_id']}"

            unknown = await client.get(
                f"/api/v1/actions/{prepared['action_id']}",
                headers=bearer_headers(),
            )
            assert unknown.json()["state"] == "unknown"

            release.set()
            for _attempt in range(20):
                await asyncio.sleep(0)
                status = await client.get(
                    f"/api/v1/actions/{prepared['action_id']}",
                    headers=bearer_headers(),
                )
                if status.json()["state"] == "succeeded":
                    break
            assert status.json()["state"] == "succeeded"

    asyncio.run(scenario())
