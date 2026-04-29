---
name: db-references
description: Use this skill when adding or changing Beanie document links, reference queries, DBRef handling, or migrations involving linked models.
---

# Database references

Use this skill when working with Beanie `Link[...]` fields and database references.

## Core rules

- Prefer Beanie `Link[Model]` fields for document relationships.
- Store links by assigning the linked document/model object where possible, not raw IDs.
- Query linked documents by their database object ID with Beanie's link syntax:
  - `SomeModel.chat.id == chat_iid`
  - `SomeModel.user.id == user_iid`
- Keep identifiers explicit:
  - `chat_tid`: Telegram chat ID (`int`)
  - `chat_iid`: database object ID (`PydanticObjectId`)
- Resolve `chat_tid` to `ChatModel` before querying `Link[ChatModel]` fields:
  - `chat = await ChatModel.get_by_tid(chat_tid)`
  - then query with `chat.iid`

## What not to do

- Do not query a `Link[ChatModel]` field with a Telegram ID.
- Do not add production fallbacks that scan raw pymongo documents to compensate for a bad link query.
- Do not use `get_pymongo_collection()` in normal model helpers just to make tests pass.
- Do not manually compare `DBRef` values in feature code unless you are working in a migration or a narrowly-scoped repair utility.

## Correct lookup pattern

For a model with `chat: Link[ChatModel]`:

```python
chat = await ChatModel.get_by_tid(chat_tid)
if not chat:
    return None

model = await SomeModel.find_one(SomeModel.chat.id == chat.iid)
```

For a model with `user: Link[ChatModel]`:

```python
user = await ChatModel.get_by_tid(user_tid)
if not user:
    return None

model = await SomeModel.find_one(SomeModel.user.id == user.iid)
```

## Fetching linked documents

- Use `fetch_links=True` when you need linked document fields in the same query.
- Use `await document.link_field.fetch()` for on-demand fetching.
- If fetching a direct link returns a Beanie `Link` object, the linked document is missing; handle that as dangling data, not as normal behavior.

## Tests

- Tests should create or use valid `ChatModel` documents and pass model objects into linked fields.
- If an E2E test is about handler behavior rather than Beanie internals, mock the model lookup and assert the handler passes the correct `chat_iid` / `user_iid`.
- Do not add model-level raw DB fallbacks because a mocked DB stores or queries links differently.

## Migrations and repair utilities

Raw `DBRef`, `get_pymongo_collection()`, and manual document scans are acceptable in migrations because migrations operate on historical data shapes.

When converting legacy integer fields to links:

- Resolve the legacy Telegram ID to the referenced `ChatModel`.
- Store a proper Beanie-compatible reference / `DBRef` for the linked document.
- Remove the legacy field only after the new reference is written.

## Lesson learned

A failing `Link` query should be treated as a contract issue: either the code used the wrong identifier type, the test setup created invalid link data, or a migration/fixture needs correction. Production model helpers should keep the correct Beanie relation query instead of silently falling back to raw collection scans.
