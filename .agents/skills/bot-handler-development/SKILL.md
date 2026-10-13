---
name: bot-handler-development
description: Use when creating or modifying aiogram handlers, bot modules, ASS parsing, STFU formatting, i18n text, or chat ID flows.
---

# Bot Handler Development

## Rules

- Imports at top only. Strict type annotations on all parameters and returns. Functional, async-first style.
- User-facing text must use `gettext as _` for runtime or `lazy_gettext as l_` for metadata.
- Catch specific exceptions; never use broad `except Exception`.

## ASS & STFU

- Parse command arguments with ASS types (`TextArg`, `IntArg`, `UserArg`).
- Format messages with STFU components (`Doc`, `Title`, `Section`, `Template`, `Bold`, `Code`). Use `doc.to_html()`.
- Never use `.format()` or raw f-strings with user input; use `Template` to guarantee HTML escaping.

## Chat Identifiers

- Explicit naming: `chat_tid` (Telegram integer ID) vs `chat_iid` (database `PydanticObjectId`).
- Resolve `ChatModel` via `ChatModel.get_by_tid(chat_tid)` before querying `Link[ChatModel]`: `Model.chat.id == chat.iid`.

## Feature Flags & Tests

- Gate handlers with `FeatureFlagFilter`, never inline `is_enabled(...)`.
- New handlers require E2E tests. Update existing tests on behavior changes.
