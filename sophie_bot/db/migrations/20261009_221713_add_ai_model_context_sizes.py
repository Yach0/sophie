"""Migration: add_ai_model_context_sizes

Description:
    Fill undefined AI catalog context_window_tokens from the public OpenRouter
    models API, matching exact api_name first and exact name second. No model
    aliases, provider credentials, or guessed capacities are used.

Affected Collections:
    - ai_catalog_model

Impact:
    - Low risk: configured capacities are preserved. The complete remote catalog
      is fetched once and validated before any writes; network or schema errors
      abort the migration. Unmatched models remain undefined and are reported.
    - Small collection; conditional per-document writes are idempotent and do
      not require transaction support.

Rollback:
    A document-local migration marker records the added capacity, source ID, and
    whether the old value was null or absent. Only unchanged migration-added
    capacities are restored to their original undefined state. Later operator
    edits are preserved, and rollback removes every owned provenance marker.
"""

from __future__ import annotations

import httpx2
from beanie import free_fall_migration
from pymongo.asynchronous.client_session import AsyncClientSession

from sophie_bot.services.db import get_collection
from sophie_bot.services.migrations import MigrationResources

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_PROVENANCE_FIELD = "_migration_add_ai_model_context_sizes"


def _parse_context_sizes(payload: object) -> dict[str, int]:
    """Validate the entire response before making any catalog mutation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not payload["data"]:
        raise ValueError("OpenRouter models response must contain a nonempty data array; no catalog values changed")

    sizes: dict[str, int] = {}
    for index, model in enumerate(payload["data"]):
        if not isinstance(model, dict):
            raise TypeError(f"OpenRouter data[{index}] must be an object; no catalog values changed")
        model_id = model.get("id")
        capacity = model.get("context_length")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError(f"OpenRouter data[{index}].id must be a nonempty string; no catalog values changed")
        if type(capacity) is not int or capacity <= 0:
            raise ValueError(
                f"OpenRouter data[{index}].context_length must be a positive integer; no catalog values changed"
            )
        if model_id in sizes:
            raise ValueError(f"OpenRouter data[{index}] has a duplicate model ID; no catalog values changed")
        sizes[model_id] = capacity
    return sizes


async def _fetch_context_sizes() -> dict[str, int]:
    """Fetch public metadata without consulting configured providers or keys."""
    try:
        async with httpx2.AsyncClient(timeout=30.0) as client:
            response = await client.get(_OPENROUTER_MODELS_URL)
            response.raise_for_status()
    except httpx2.HTTPError as error:
        raise RuntimeError("Unable to fetch OpenRouter models context sizes; no catalog values changed") from error

    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("OpenRouter models response is not valid JSON; no catalog values changed") from error
    return _parse_context_sizes(payload)


class Forward:
    """Add only authoritative context sizes to previously undefined models."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        sizes = await _fetch_context_sizes()
        models = get_collection(resources.database.database, "ai_catalog_model")
        updated = 0
        unmatched = 0
        async for model in models.find(
            {"context_window_tokens": None, _PROVENANCE_FIELD: {"$exists": False}},
            {"name": 1, "api_name": 1, "context_window_tokens": 1},
            session=session,
        ):
            identifiers = tuple(
                value for value in (model.get("api_name"), model.get("name")) if isinstance(value, str) and value
            )
            source_id = next((value for value in identifiers if value in sizes), None)
            if source_id is None:
                unmatched += 1
                print(
                    f"AI model {model.get('name')!r}: context_window_tokens remains undefined; "
                    f"no exact OpenRouter model ID match for {identifiers!r}"
                )
                continue

            was_null = "context_window_tokens" in model
            marker = {
                "context_window_tokens": sizes[source_id],
                "openrouter_id": source_id,
                "was_null": was_null,
            }
            result = await models.update_one(
                {
                    "_id": model["_id"],
                    "name": model.get("name"),
                    "api_name": model.get("api_name"),
                    _PROVENANCE_FIELD: {"$exists": False},
                    "$and": [
                        {"context_window_tokens": None},
                        {"context_window_tokens": {"$exists": was_null}},
                    ],
                },
                {"$set": {"context_window_tokens": sizes[source_id], _PROVENANCE_FIELD: marker}},
                session=session,
            )
            updated += result.modified_count
        print(
            f"Added authoritative AI context sizes to {updated} models; {unmatched} unmatched models remain undefined"
        )


class Backward:
    """Remove owned capacities only when still equal to the migrated value."""

    @free_fall_migration(document_models=[])
    async def migrate(self, session: AsyncClientSession | None, *, resources: MigrationResources) -> None:
        models = get_collection(resources.database.database, "ai_catalog_model")
        restored = 0
        preserved = 0
        async for model in models.find(
            {_PROVENANCE_FIELD: {"$exists": True}},
            {_PROVENANCE_FIELD: 1},
            session=session,
        ):
            marker = model[_PROVENANCE_FIELD]
            owned = {"_id": model["_id"], _PROVENANCE_FIELD: marker}
            update: dict[str, dict[str, object]] = {"$unset": {_PROVENANCE_FIELD: ""}}
            if marker["was_null"]:
                update["$set"] = {"context_window_tokens": None}
            else:
                update["$unset"]["context_window_tokens"] = ""
            result = await models.update_one(
                {**owned, "context_window_tokens": marker["context_window_tokens"]},
                update,
                session=session,
            )
            restored += result.modified_count
            if not result.modified_count:
                # A changed or removed capacity belongs to the operator, not this migration.
                cleaned = await models.update_one(owned, {"$unset": {_PROVENANCE_FIELD: ""}}, session=session)
                preserved += cleaned.modified_count
        print(
            f"Restored {restored} undefined AI context sizes; preserved {preserved} later edits and removed provenance"
        )
