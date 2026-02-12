# Making ASS Arguments Definitions

ASS (Argument Searcher of Sophie) is used for parsing command arguments in the Sophie Bot. This guide explains how to create custom argument types.

## Core Concepts

ASS arguments inherit from `ArgFabric` and parse user input into structured data. The framework handles:

- Validation of input format
- Entity checking (prevents formatting/mentions in sensitive arguments)
- Returning typed values instead of strings
- Automatic error messages

## Required Methods

### `check(text: str, entities: ArgEntities) -> bool`

Validates the input before parsing. Return `True` if valid, or raise an exception if invalid.

**Use cases:**
- Format validation (e.g., UUIDs, special characters)
- Entity checking (prevent formatting in sensitive args)

**Example:**
```python
def check(self, text: str, entities: ArgEntities) -> bool:
    # Check format
    if text.count("-") != 4:
        raise ArgSimpleTypeError(_("Must contain exactly 4 hyphens"))

    # Check for entities
    if entities:
        raise ArgSimpleTypeError(_("Cannot contain formatting or mentions"))

    return True
```

### `parse(text: str, offset: int, entities: ArgEntities) -> tuple[int, T]`

Parses the text and returns a tuple of `(length, value)`.

- **length**: How many characters were consumed (for multi-argument parsing)
- **value**: The parsed value (can be any type)

**Important:**
- Strip extra text using `split(maxsplit=1)` to consume only what's needed
- Return length of consumed text, not entire input
- Raise `ArgStrictError` for "not found" or lookup errors

**Example:**
```python
async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, Federation]:
    # Strip extra text
    fed_id, *_rest = text.split(maxsplit=1) or (text,)

    # Lookup in database
    federation = await Federation.find_one(Federation.fed_id == fed_id)
    if not federation:
        raise ArgStrictError(_("Federation {fed_id} not found.").format(fed_id=fed_id))

    # Return length of consumed text and the value
    return len(fed_id), federation
```

### `needed_type() -> tuple[LazyProxy, LazyProxy]`

Returns a tuple of singular and plural descriptions for error messages.

**Example:**
```python
def needed_type(self) -> tuple[LazyProxy, LazyProxy]:
    return l_("Federation ID (format: xxxx-xxxx-xxxx-xxxx)"), l_("Federation IDs")
```

## Optional Methods

### `unparse(data: Any, **kwargs) -> str`

Converts a value back to text representation.

**Example:**
```python
def unparse(self, data: Federation, **kwargs) -> str:
    return data.fed_id
```

### `examples` property

Returns a dictionary of example values with optional descriptions.

**Example:**
```python
@property
def examples(self) -> Optional[dict[str, Optional[LazyProxy]]]:
    return {"a1b2-c3d4-e5f6-g7h8": l_("Federation ID example")}
```

## Common Base Classes

### `TextArg`

For plain text arguments.

```python
from ass_tg.types import TextArg

class MyArg(TextArg):
    async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, str]:
        return len(text), text.upper()
```

### `OneWordArgFabricABC`

For arguments that consume exactly one word.

```python
from ass_tg.types.base_abc import OneWordArgFabricABC

class MyArg(OneWordArgFabricABC):
    async def check_type(self, text: str) -> bool:
        return text.isdigit()

    async def value(self, text: str) -> int:
        return int(text)
```

### `MarkdownLinkArgument`

For parsing markdown links `[text](data)`.

## Entity Handling

`ArgEntities` is a list of Telegram `MessageEntity` objects representing formatting (bold, italic, mentions, links, etc.).

### Checking for entities

```python
from ass_tg.entities import ArgEntities

def check(self, text: str, entities: ArgEntities) -> bool:
    # Simple check - any entities at all?
    if entities:
        raise ArgSimpleTypeError(_("Cannot contain formatting"))

    # Check for overlapping entities
    if entities.get_overlapping(0, len(text)):
        raise ArgSimpleTypeError(_("Cannot contain formatting"))

    return True
```

### Ignored entity types

Some entity types can be ignored (e.g., `hashtag`, `cashtag`):

```python
IGNORED_FOR_OVERLAPPING = ('hashtag', 'cashtag')
```

## Common Patterns

### Consuming only the first word

Use `split(maxsplit=1)` to consume only what's needed:

```python
fed_id, *_rest = text.split(maxsplit=1) or (text,)
```

The `or (text,)` handles the case where there's no space to split.

### Database lookups

Return objects from database instead of strings:

```python
async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, Federation]:
    fed_id, *_rest = text.split(maxsplit=1) or (text,)

    federation = await Federation.find_one(Federation.fed_id == fed_id)
    if not federation:
        raise ArgStrictError(_("Federation not found"))

    return len(fed_id), federation
```

### Working with i18n

Always translate user-facing messages:

```python
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

# Runtime translation
_("Error message")

# Lazy translation (for decorators, static contexts)
l_("Error message")
```

## Exception Types

- **`ArgStrictError`**: Hard validation errors (e.g., format errors, not found)
- **`ArgSimpleTypeError`**: Type errors (e.g., wrong format, bad type)
- **`ArgCustomError`**: Custom errors with custom messages
- **`ArgTypeError`**: Full type error with details (used by framework)

## Complete Example

```python
from typing import Any, Optional

from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgStrictError, ArgSimpleTypeError
from ass_tg.types import TextArg
from stfu_tg import Code

from sophie_bot.db.models.federations import Federation
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class FedIdArg(TextArg):
    """Argument type for federation IDs with validation and lookup."""

    def __init__(self, description: Optional[LazyProxy] = None):
        super().__init__(description or l_("Federation ID"))

    def check(self, text: str, entities: ArgEntities) -> bool:
        """Check if text has valid federation ID format and no overlapping entities."""
        fed_id, *_rest = text.split(maxsplit=1) or (text,)

        if fed_id.count("-") != 4:
            raise ArgSimpleTypeError(_("Invalid federation ID format. Federation IDs must contain exactly 4 hyphens."))

        if entities:
            raise ArgSimpleTypeError(_("Federation ID cannot contain formatting or mentions."))

        return True

    async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, Federation]:
        """Parse and validate federation ID, return Federation model."""
        fed_id, *_rest = text.split(maxsplit=1) or (text,)

        # Lookup federation
        federation = await Federation.find_one(Federation.fed_id == fed_id)
        if not federation:
            raise ArgStrictError(_("Federation with ID {fed_id} not found.").format(fed_id=Code(fed_id)))

        return len(fed_id), federation

    def needed_type(self) -> tuple[LazyProxy, LazyProxy]:
        return l_("Federation ID (format: xxxx-xxxx-xxxx-xxxx)"), l_("Federation IDs")

    def unparse(self, data: Any, **kwargs) -> str:
        return data.fed_id

    @property
    def examples(self) -> Optional[dict[str, Optional[LazyProxy]]]:
        return {"a1b2-c3d4-e5f6-g7h8": l_("Federation ID example")}
```

## Using in Handlers

```python
from sophie_bot.modules.federations.args.fed_id import FedIdArg

@classmethod
async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
    return {"fed_id": FedIdArg(l_("Federation ID to join"))}

async def handle(self) -> Any:
    # fed_id is now a Federation object, not a string!
    fed_id: Federation = self.data["fed_id"]
    await message.reply(f"Joined federation: {fed_id.fed_name}")
```

## Key Takeaways

1. **Always use `split(maxsplit=1)`** to strip extra text from arguments
2. **Return correct length** - length of consumed text, not entire input
3. **Check for entities** to prevent formatting in sensitive arguments
4. **Raise appropriate exceptions** - `ArgStrictError` for validation, `ArgSimpleTypeError` for format
5. **Return typed values** from `parse()` - objects, not strings
6. **Use i18n** for all user-facing messages
