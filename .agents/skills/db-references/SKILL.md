---
name: db-references
description: Use when adding or changing Beanie document links, reference queries, DBRef handling, or linked-model migrations.
---

# Database References

## Rules

- Use Beanie `Link[Model]` for relationships. Assign model instances to link fields, not raw IDs.
- Strict identifier separation: `chat_tid` (`int`) vs `chat_iid` (`PydanticObjectId`).
- Never query `Link[Model]` directly with a Telegram ID (`tid`).
- Query link fields using Beanie link syntax: `SomeModel.chat.id == chat.id`.
- Never add production fallbacks that scan raw pymongo documents to compensate for bad queries.

## Lookup Pattern

```python
chat = await ChatModel.get_by_tid(chat_tid)
if not chat:
    return None
item = await SomeModel.find_one(SomeModel.chat.id == chat.id)
```

## Link Resolution

- Use `fetch_links=True` for eager population or `await document.link_field.fetch()` for lazy resolution.
- If a fetched link returns an unresolved `Link` sentinel, handle it as a missing document.
- Raw `DBRef` comparisons and collection scans belong strictly in migrations or repair tools, not application code.
