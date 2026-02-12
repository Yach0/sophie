# Using STFU Formatting Tools

STFU (Sophie Text Formatting Utility) is used to build structured, formatted messages for Telegram. It handles HTML/Markdown conversion and provides reusable components.

## Important: Never Use .format()

**CRITICAL**: Never use Python's `.format()` method when formatting user input with STFU elements.

```python
# ❌ WRONG - .format() doesn't escape HTML properly
bad = "Status: {status}\nChat: {chat}".format(status=Bold("OK"), chat=Code(chat_id))

# ✅ CORRECT - Template handles HTML escaping automatically
good = Template("Status: {status}\nChat: {chat}", status=Bold("OK"), chat=Code(chat_id))
```

**Why?** When you pass STFU elements (like `Bold()`, `Code()`) to `.format()`, their string representation includes HTML tags that get embedded literally. `Template` properly escapes HTML when converting, preventing XSS issues and malformed output.

## Basic Concept

All STFU components implement the `Element` base class and provide:
- `to_html()` - Convert to HTML format
- `to_md()` - Convert to Markdown format

**Important**: Always use `doc.to_html()` instead of `str(doc)` when sending messages. This is the preferred, non-legacy approach.

```python
# ❌ LEGACY - implicit conversion
await message.reply(str(doc))

# ✅ PREFERRED - explicit conversion
await message.reply(doc.to_html())
```

## Doc - Container for Elements

The main container that holds multiple elements.

```python
from stfu_tg import Doc, Bold, Code

doc = Doc(
    Bold("Title"),
    "Some text",
    Code("value")
)

await message.reply(str(doc))
```

Outputs:
```
<b>Title</b>
Some text
<code>value</code>
```

## Title - Headings

Creates title/headings for sections.

```python
from stfu_tg import Title

# HTML mode (default)
title1 = Title("Main Title")

# Markdown mode - uses level parameter
title2 = Title("Section Title", level=4)

# Disable bold
title3 = Title("Plain Title", bold=False)
```

**Parameters:**
- `item` - Title text
- `bold` - Make bold (HTML mode, default: `True`)
- `level` - Heading level 1-6 (Markdown mode, default: `1`)
- `prefix`/`postfix` - Custom delimiters (HTML mode, default: `[` / `]`)

## Section - Grouped Content

Groups related content with optional title and indentation.

```python
from stfu_tg import Section, KeyValue, Code

section = Section(
    KeyValue("Status", "Active"),
    KeyValue("Chat", Code(chat_id)),
    title="User Info",
    title_bold=False,
    title_underline=True,
    indent=2,
    title_postfix=":"
)
```

**Parameters:**
- `*items` - Content items to include
- `title` - Section title (default: `''`)
- `title_bold` - Bold the title (default: `False`)
- `title_underline` - Underline the title (default: `True`)
- `indent` - Indentation level (default: `1`)
- `indent_text` - Indentation text (default: `'  '`)
- `title_postfix` - Text after title (default: `':'`)

## KeyValue - Key-Value Pairs

Displays labeled values with formatting.

```python
from stfu_tg import KeyValue

# Default usage
kv1 = KeyValue("Username", "@user")

# Custom suffix
kv2 = KeyValue("Status", "Online", suffix=": ")

# Title not bold
kv3 = KeyValue("Date", "2024-01-15", title_bold=False)

# With nested formatting
kv4 = KeyValue("Chat ID", Code("123456"))
```

**Parameters:**
- `title` - Label for the key
- `value` - The value to display
- `suffix` - Text between title and value (default: `': '`)
- `title_bold` - Bold the title (default: `True`)

## HList - Horizontal List

Displays items horizontally with optional prefix/divider.

```python
from stfu_tg import HList, Title, Code

# Basic horizontal list
hlist1 = HList("Item 1", "Item 2", "Item 3")

# With custom divider
hlist2 = HList("A", "B", "C", divider=" | ")

# With prefix
hlist3 = HList("✓", "Done", prefix="Status: ")

# Combining with other elements
header = HList(
    Title("AI Response"),
    Title("gpt-4", bold=False),
    Code("(model)")
)
```

**Parameters:**
- `*args` - Items to display
- `prefix` - Text before each item (default: `''`)
- `divider` - Text between items (default: `' '`)

## VList - Vertical List

Displays items vertically with bullets and indentation.

```python
from stfu_tg import VList

# Basic list
vlist1 = VList("Item 1", "Item 2", "Item 3")

# Custom bullet
vlist2 = VList("First", "Second", prefix="• ")

# With indentation
vlist3 = VList(
    "Nested Item 1",
    "Nested Item 2",
    indent=2
)
```

**Parameters:**
- `*items` - List items
- `indent` - Indentation level (default: `0`)
- `prefix` - Bullet/bullet text (default: `'- '`)

## Template - String Templates

Replaces placeholders in a string with values. **This is the preferred way to format strings with STFU elements.**

```python
from stfu_tg import Template, Code, Bold

# Simple substitution
msg1 = Template("Hello {name}!", name="User")

# Multiple placeholders
msg2 = Template(
    "{idx}. {user} — {count}",
    idx=Code(1),
    user=Bold("Alice"),
    count=Code(42)
)

# With nested STFU elements
msg3 = Template(
    "Status: {status}\nCode: {code}",
    status=Bold("OK"),
    code=Code("ABC123")
)
```

**Important**: Always use `Template()` instead of `.format()` when your template includes STFU elements. `Template` properly handles HTML escaping.

**Parameters:**
- `item` - Template string with `{placeholder}` markers
- `**kwargs` - Placeholder replacements (values can be strings or STFU elements)

## Special Components

### InvisibleSymbol

An invisible character for spacing/hiding content.

```python
from stfu_tg import InvisibleSymbol

doc = Doc(
    "Visible text",
    InvisibleSymbol(),
    "More text"
)
```

### Spacer

A space character (useful for forcing spacing in lists).

```python
from stfu_tg import Spacer

hlist = HList("A", Spacer(), "B")
```

### PreformattedHTML

Pass through HTML without escaping (use when you have valid HTML).

```python
from stfu_tg import PreformattedHTML

html = PreformattedHTML("<b>Bold</b> and <i>italic</i>")
```

### EscapedStr

Automatically escapes HTML when converting. (Used internally, usually not needed directly).

## UserLink

Creates a clickable user link.

```python
from stfu_tg import UserLink

# Link to user by ID
link1 = UserLink(user_id=123456, name="John Doe")

# For ChatModel objects
link2 = UserLink(chat.tid, chat.first_name_or_title)
```

## Basic Formatting Elements

These wrap text in Telegram's basic formatting.

```python
from stfu_tg import Bold, Italic, Code, Pre, Underline, Strikethrough, Url, BlockQuote

text = Doc(
    Bold("Bold text"),
    Italic("Italic text"),
    Code("Code text"),
    Pre("Preformatted text"),
    Underline("Underlined"),
    Strikethrough("Struck through"),
    Url("Click here", "https://example.com"),
    BlockQuote("Quoted text")
)
```

## Combining Components

STFU elements can be nested and combined.

```python
from stfu_tg import Doc, Section, KeyValue, VList, Template

doc = Doc(
    Title("Report"),
    Section(
        KeyValue("User", user_name),
        KeyValue("Chat", Code(chat_id)),
        title="Info"
    ),
    Section(
        VList(
            Template("• {item}", item=item) for item in items
        ),
        title="Details",
        title_underline=False
    )
)

await message.reply(str(doc))
```

## Real-World Examples

### Command Status Response

```python
from stfu_tg import Doc, Section, KeyValue, Italic

doc = Doc(
    Section(
        KeyValue(_("Chat"), connection.title),
        KeyValue(_("Command"), Code(cmd_name)),
        Italic(handler.description),
        title=_("Command disabled")
    )
)
await message.reply(doc.to_html())
```

### AI Provider Selection

```python
from stfu_tg import Doc, Section, KeyValue

doc = Doc(
    Section(
        KeyValue(_("Current provider"), provider_name),
        title=_("AI Provider")
    )
)
await self.event.reply(doc.to_html(), reply_markup=kb)
```

### Statistics Display

```python
from stfu_tg import Doc, HList, Template, Code, UserLink

lines = [
    Template(
        "{idx}. {name} — {count}",
        idx=Code(idx),
        name=UserLink(user_id, username),
        count=Code(count)
    )
    for idx, (user_id, count) in enumerate(ranking, start=1)
]

doc = Doc(HList(*lines))
await message.reply(doc.to_html())
```

### Nested Sections

```python
from stfu_tg import Doc, Section, KeyValue, VList

doc = Doc(
    Section(
        KeyValue("Main Key", "Main Value"),
        Section(
            KeyValue("Nested Key 1", "Value 1"),
            KeyValue("Nested Key 2", "Value 2"),
            title="Subsection",
            indent=2
        ),
        title="Main Section"
    )
)
await message.reply(doc.to_html())
```

## Key Takeaways

1. **Use `Doc` as main container** for messages with multiple elements
2. **Nest components freely** - Sections can contain other Sections, VLists, etc.
3. **Use `Template` for string interpolation** with `{placeholder}` syntax
4. **NEVER use `.format()` with STFU elements** - use `Template` instead for proper HTML escaping
5. **`Section` provides structure** with titles and indentation
6. **`KeyValue` for labeled data** with consistent formatting
7. **`HList` for horizontal layouts**, `VList` for vertical lists
8. **`Title` for headings** - supports both HTML and Markdown modes
9. **All components auto-escape HTML** (except `PreformattedHTML`)
10. **Combine with formatting** - Bold, Code, Italic work inside all components
11. **Use `doc.to_html()` instead of `str(doc)`** - preferred method for clarity and explicit format
