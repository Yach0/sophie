# AI Development Guidelines for Sophie Bot

## How to use project skills

- Skills live in `.agents/skills/<skill-name>/SKILL.md`.
- Read the relevant skill before starting domain-specific work instead of keeping that detail in the base prompt.
- If a task spans multiple areas, load multiple skills.
- Before final submission, load `code-review` for a self-review pass.

## Skill catalog

| Skill | Use when |
| --- | --- |
| `bot-handler-development` | Creating or editing bot handlers, module structure, ASS/STFU usage, i18n, and chat ID handling. |
| `rest-api-development` | Adding or changing FastAPI routers, auth dependencies, response models, or REST endpoints. |
| `database-migrations` | Making schema/data migrations under `sophie_bot/db/migrations/`. |
| `feature-flags` | Shipping new medium/high-risk features, external integrations, or large refactors. |
| `e2e-testing` | Writing or updating bot end-to-end tests in `tests/e2e/`. |
| `issue-fixer` | Diagnosing and fixing bugs, errors, or unexpected behavior by tracing root causes instead of masking symptoms. |
| `deep-source-issue-fixer` | Fixing DeepSource static analysis issues, covering common false positives with Beanie async and staticmethod suggestions. |
| `code-review` | Reviewing a change set or doing a pre-submit self-review. |

## Always-on project rules

### Code style and typing

- Use Ruff for formatting and import ordering, and `pycln` for unused imports.
- Put imports at the top of the module only; never import inside functions or conditionals.
- Never use single-character variable names, even in short comprehensions.
- Add type annotations to all function parameters and return values.
- Use `from __future__ import annotations` when it helps with forward references.
- Prefer strict types over `Any`.

### Coding style

- Default to a functional, async-first style.
- Prefer pure functions, immutable data where practical, and small focused units of logic.
- Prefer composition over inheritance.
- Avoid overly guarding code
- Avoid broad `except Exception` handlers; catch specific exceptions and let the framework handle unexpected failures.

### Bot-specific rules

- All user-facing text must go through i18n with `gettext as _` and `lazy_gettext as l_`.
- Use ASS for argument parsing and STFU for message formatting.
- Never use `.format()` with user input; use STFU `Template` so escaping is correct.
- Keep chat identifiers explicit:
  - `chat_tid`: Telegram chat ID (`int`)
  - `chat_iid`: database object ID (`PydanticObjectId`)
- For `Link[ChatModel]` queries, resolve the chat model first (for example with `ChatModel.get_by_tid(...)`) and then use `chat.iid`.

### Delivery workflow

- Run `make commit` after every task. This is very important!
- Keep documentation updated when behavior or workflows change.
- New handlers should have E2E coverage; use the `e2e-testing` skill.
- New medium/high-risk features, integrations, and large refactors must ship behind a feature flag; use the `feature-flags` skill.

### Research tools

- Use Context7 for authoritative library documentation and API reference lookups.
- Use Tavily for broader web research, examples, and recent discussions.
- Never include secrets, credentials, or private config values in MCP queries.

## Quick references

- Handler and library conventions: `wiki_docs/Development/Making%20ASS%20args%20definitions.md`
- STFU usage patterns: `wiki_docs/Development/Using%20STFU%20formatting%20tools.md`
- Feature flag source of truth: `sophie_bot/utils/feature_flags.py`
- Chat lookup helper: `sophie_bot/db/models/chat.py`
- Main bot code: `sophie_bot/`
