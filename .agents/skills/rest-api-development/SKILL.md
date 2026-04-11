---
name: rest-api-development
description: Use this skill when adding or modifying FastAPI routers, request or response models, API auth dependencies, or module API registration.
---

# REST API development

Use this skill for work in module `api/` packages and related FastAPI code.

## Router structure

- Put API routes in `sophie_bot/modules/<module_name>/api/`.
- Export routers from `sophie_bot/modules/<module_name>/api/__init__.py`.
- Re-export or include the module `api_router` from the module package as needed by the existing pattern.
- Keep bot handlers and REST routes separated even when they belong to the same module.

## Endpoint rules

- Every router must define `tags` for Swagger/OpenAPI organization.
- Use Pydantic models for request and response validation.
- Follow RESTful method semantics (`GET`, `POST`, `PUT`, `DELETE`).
- Return the right HTTP status codes.
- Raise `HTTPException` for API-facing errors.

## Authentication and authorization

- Use `get_current_user` for authenticated user routes.
- Use `get_current_operator` for operator-only routes.
- Keep authorization explicit in the endpoint signature rather than hidden in route logic when possible.

## Data and chat IDs

- If the API touches `ChatModel` relationships, keep `chat_tid` and `chat_iid` separate.
- Resolve Telegram IDs to `ChatModel` before querying `Link[ChatModel]` fields.

## Documentation and verification

- API changes should leave OpenAPI generation healthy; `make commit` will verify this.
- Keep response models and field names stable unless the task explicitly requires an API change.
- When adding a new endpoint, review nearby routers for naming, prefixes, and dependency style consistency.

## Useful references

- `sophie_bot/modules/rest/api/`
- `sophie_bot/utils/api/auth.py`
- `openapi.json`