---
name: rest-api-development
description: Use when adding or modifying FastAPI routers, request/response models, auth dependencies, or endpoint registration.
---

# REST API Development

## Architecture

- Keep bot handlers and REST routers strictly separated.
- Module routers must register via `ModuleManifest.api_router_factory` in `sophie_bot/modules/<module>/__init__.py`.
- Define explicit OpenAPI `tags` for all routers.

## Conventions

- Strict Pydantic models for request bodies and response schemas.
- Adhere to REST semantics (`GET`, `POST`, `PUT`, `DELETE`) and explicit HTTP status codes.
- Use `get_current_user` for authenticated routes and `get_current_operator` for operator endpoints.
- Separate `chat_tid` (int) from `chat_iid` (`PydanticObjectId`). Resolve `ChatModel` before querying `Link[ChatModel]`.
- Verify OpenAPI spec validity with `make commit`.
