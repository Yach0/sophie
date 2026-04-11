---
name: bot-handler-development
description: Use this skill when creating or modifying aiogram handlers, bot modules, ASS argument parsing, STFU formatting, i18n text, or Telegram chat/database ID flows.
---

# Bot handler development

Use this skill for bot-side work under `sophie_bot/modules/`, `sophie_bot/filters/`, `sophie_bot/middlewares/`, and related utilities.

## Core rules

- Follow the module layout already used in `sophie_bot/modules/<module_name>/`.
- New modules usually need `handlers/` and `utils/`; add other subdirectories only when the feature really needs them.
- Match existing aiogram patterns for handler classes, filters, decorators, and registration.
- Keep imports at the top of the file and annotate every function.

## Handler conventions

- Use aiogram handlers and the established flags/decorator patterns from nearby modules.
- Use `gettext as _` for runtime text and `lazy_gettext as l_` for metadata such as help descriptions.
- All user-visible strings must be translatable.
- Avoid broad exception handlers in handlers; prefer specific exceptions and let framework-level handling surface unexpected failures.

## ASS for arguments

- Always use ASS types for user arguments instead of ad-hoc parsing.
- Common imports live under `ass_tg.types` such as `TextArg`, `IntArg`, and `UserArg`.
- Define argument schemas in the same style as surrounding handlers.
- When you need more detail, read `wiki_docs/Development/Making%20ASS%20args%20definitions.md`.

## STFU for output

- Always use STFU components for structured output.
- Prefer `Doc`, `Title`, `Section`, `Template`, `Bold`, `Code`, and related components over manual string building.
- Never use `.format()` with user input; use `Template` so HTML escaping stays correct.
- Prefer `doc.to_html()` over `str(doc)` when replying.
- When you need more detail, read `wiki_docs/Development/Using%20STFU%20formatting%20tools.md`.

## Chat ID rules

- Do not mix `chat_tid` and `chat_iid`.
- `chat_tid` is the Telegram chat ID (`int`), used for incoming events and Telegram API calls.
- `chat_iid` is the database ID (`PydanticObjectId`), used for Beanie `Link[ChatModel]` relationships.
- Before a `Link[ChatModel]` query, resolve the chat model first:

```python
chat = await ChatModel.get_by_tid(chat_tid)
if not chat:
    ...

model = await SomeModel.find_one(SomeModel.chat.id == chat.iid)
```

- Use explicit variable names: `chat_tid`, `chat_iid`, `chat`.

## Feature flags

- If the handler introduces a new medium/high-risk feature or large behavioral change, use the `feature-flags` skill.
- Handlers must be disabled with `FeatureFlagFilter`, not inline `is_enabled(...)` replies.

## Tests

- New handlers require E2E tests.
- Handler behavior changes should update relevant E2E coverage when existing tests no longer reflect the feature.
- Use the `e2e-testing` skill for the test structure, fixtures, and execution commands.

## Useful references

- `sophie_bot/modules/`
- `sophie_bot/db/models/chat.py`
- `sophie_bot/filters/cmd.py`
- `wiki_docs/Development/Making%20ASS%20args%20definitions.md`
- `wiki_docs/Development/Using%20STFU%20formatting%20tools.md`