---
title: Filters
icon: 🪄
---
> Filters allow invoking different actions for different messages. \
> For example, muting the users when they mention crypto. \
> Sophie supports many different actions you can configure to automatize chat moderation in many different ways.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/filters` | - | Lists all filters in the chat. | *Disable-able* |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/add_filter` `/new_filter` | `<Text to match>` | Adds a new filter. |  |
| `/edit_filter` | `<Filter's keyword>` | Edits filter settings. |  |
| `/del_filter` | `<Text to match>` | Deletes a filter. |  |
| `/filterstats` | - | Shows successful filter action execution counts for this chat. | Requires `filter_stats` |
{.card-view-on-mobile}

### Aliased commands from [✨ Sophie AI](ai)

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/ai_add_filter` | `<Describe what the filter should catch>` | Suggests filter handlers from a natural language description. |  |
---
Use `/filters` to browse filters along with the `Previous` and `Next` buttons. Sophie keeps each page within Telegram's message limit; very long handlers are shortened in the list, but the saved filter and its edit/delete controls are unchanged.

## Filter handlers

When you create a filter with `/add_filter`, the first argument is the **handler**. The handler tells Sophie what kind of message should trigger the filter.

Handlers come in two groups:

- **Text matchers**: plain text, `word:`, `exact:`, `re:`, `ai:`
- **Built-in detectors**: lock types like `url`, `photo`, `language:ru`, `stickerpack:FunnyCats`, `cyrillic` (see `/lockable` for the full list)

### What is text normalization?

Some text matchers clean up text before checking it. This is called **normalization**.

Special characters are stripped from **both** your filter keyword and the incoming message.

When normalization is active:

- **Capitalization does not matter**: `CRYPTO`, `Crypto`, and `crypto` all match.
- **Accents do not matter**: Accents are turned into plain letters (e.g. `crýptò` matches `crypto`).
- **Fancy fonts do not matter**: Decorative Unicode letters (like `𝓬𝓻𝔂𝓹𝓽𝓸` or `𝗰𝗿𝘆𝗽𝘁𝗼`, often used by spammers) are turned into regular letters.
- **Punctuation and symbols are stripped**: Characters like `!`, `?`, `,`, and `@` are removed, while dots, hyphens, and slashes (`.`, `-`, `/`) become spaces.
- **Extra spaces are cleaned up**: Multiple spaces, tabs, or newlines are shrunk into a single space.

> **Common gotcha:** Because symbols are removed from both your filter and the incoming message (when you're using the plain/default or `word:` matchers), `/add_filter @Raccoon` turns into a search for `raccoon`. It will accidentally trigger on someone saying `"I saw 3 raccoons today."` even though they didn't use an `@` sign! To match a specific handle with `@`, use a regex filter instead: `/add_filter re:(?i)@Raccoon\b`.

### Quick comparison table

| Matcher | Example | Cleans text? | Whole words only? | What part of the message it checks |
|---|---|:---:|:---:|---|
| **Plain text** | `/add_filter crypto` | **Yes** | **No** (matches inside longer words) | Matches anywhere in the message |
| **`word:`** | `/add_filter word:crypto` | **Yes** | **Yes** (only full words or phrases) | Matches complete words anywhere in the message |
| **`exact:`** | `/add_filter exact:hello` | **No** | **N/A** | The whole message must be an identical match |
| **`re:`** | `/add_filter re:(?i)crypto` | **No** | **No** (unless you add `\b` boundaries) | Matches your regex pattern anywhere in the message |
| **`ai:`** | `/add_filter ai:crypto scam` | **No** | **N/A** | Evaluates the overall meaning (new users only) |

### 1. Plain text handlers (Default)

Plain text is the default matcher when you don't use any prefix.

It cleans up the text and checks if your keyword appears **anywhere** inside the message, even as part of a longer word.

**Example**:

```
/add_filter crypto
```

**Will match**:
  - `"Buy crypto now"` (standard message)
  - `"BUY CRYPTO NOW"` (capital letters still match)
  - `"crýptò"` (accents still match)
  - `"𝓬𝓻𝔂𝓹𝓽𝓸"` (fancy spam fonts still match)
  - `"free cryptocurrency"` (matches because `crypto` is part of `cryptocurrency`)

**Won't match**:
  - `"c r y p t o"` (spaced-out letters remain separate words)
  - `"decryption"` (does not contain `crypto`)
  - `"bitcoin"` (different word)

> **Best for**: Common spam keywords where you want to catch all variations, including longer compound words.

### 2. `word:` handlers (Whole word or phrase)

Use `word:` when you want to match a full word or phrase without triggering on longer words that happen to contain those letters.
It cleans up text just like plain text, but splits the message into separate words. Punctuation at the end of a sentence is stripped, so it still matches.

**Single-word example**:
```
/add_filter word:crypto
```

**Will match**:
  - `"Buy crypto today"` (matches the full word)
  - `"CRYPTO is great"` (capital letters still match)
  - `"crýptò is here"` (accents and fancy fonts still match)
  - `"Need crypto?"` (question mark is stripped, so it matches)

**Won't match**:
  - `"free cryptocurrency"` (blocked: `crypto` is only part of a longer word)
  - `"cryptography basics"` (blocked: part of a longer word)

**Multi-word phrase example**:
```
/add_filter word:free nitro
```

**Will match**:
  - `"Get free nitro now"` (the words appear side by side)
  - `"Free Nitro."` (capital letters match and the period is stripped)

**Won't match**:
  - `"free discord nitro"` (the words must appear together in order)

> **Best for**: Short words or phrases that might accidentally match innocent longer words.

### 3. `exact:` handlers (Entire message)

Use `exact:` when the **entire message** must match your text from start to finish.

Sophie does **not** clean up the text for `exact:`. Capitals, punctuation, and spacing must match letter-for-letter.

**Example**:
```
/add_filter exact:hello
```

**Will match**:
  - `"hello"` (only this exact text)
**Won't match**:
  - `"Hello"` (capital `H` does not match lowercase `h`)
  - `"hello world"` (has extra words)
  - `"hello!"` (has punctuation)
  - `" hello "` (has leading or trailing spaces)

> **Best for**: Canned spam messages, repeated bot commands, or specific invite links where users send the exact same text every time.

### 4. `re:` handlers (Regular expressions)

Use `re:` for regular expressions when you need advanced pattern matching.

Sophie runs the regex directly on the raw message without cleaning it up.

By default, regex is **case-sensitive**. Add `(?i)` at the start if you want it to match both uppercase and lowercase.

**Matching a list of words (e.g. swear words)**:

To match several different words in a single filter without matching partial words, combine `(?i)`, word boundaries (`\b`), and `|` (OR):

```
/add_filter re:(?i)\b(wordA|wordB|wordC)\b
```

**Matching usernames or channels**:

Because plain text filters strip the `@` symbol, use regex if you want to strictly match messages containing the handle:

```
/add_filter re:(?i)@channelname\b
```

**Matching specific links or domains**:

```
/add_filter re:(?i)badsite\.com
/add_filter re:(?i)t\.me/(\+|joinchat/)
```

**Safety check**:
Sophie tests your pattern when you add it. If it takes longer than 0.2 seconds to run, Sophie rejects it to protect the bot from slow patterns (ReDoS attacks). During live chats, regex matching has a 0.5-second timeout.

> **Best for**: Channel names with `@`, specific website domains, or matching lists of words in a single rule.

## AI Filter handlers

Sophie supports AI-powered filter handlers that determine whether to trigger filter actions
based on message content, evaluating semantic context rather than literal text matches.

### How to Use AI Filters

Simply use the `ai:` prefix followed by your prompt when adding filters:

```
/add_filter ai:Your prompt describing when to trigger
```

### When to use `ai:`

Use `ai:` only when literal text rules are too weak.

Examples:

```
/add_filter ai:Has anything regarding money or cryptocurrency
/add_filter ai:Message contains scam or phishing attempt
/add_filter ai:Promotion or advertisement content
/add_filter ai:Spam or unsolicited messages
/add_filter ai:Political content
```

Choose `ai:` when the intent matters more than the exact words.
For example, it can help with scammy wording that changes often but keeps the same meaning.

Avoid `ai:` when a plain text, `word:`, `exact:`, `re:`, or lock-based handler would already solve the problem.

### Supported Content Types

AI filters work with various message types:

- **Text messages**: Analyzes the message text or caption
- **Photos**: Analyzes both the caption and the image content
- **Videos**: Analyzes the caption and video thumbnail
- **GIFs/Animations**: Analyzes the caption and animation thumbnail
- **Stickers**: Analyzes sticker images (static, animated, or video)

### Limitations

To reduce resource usage and focus on potential spam/scam threats, AI filters only trigger for users who joined the chat
less than **2 days** ago. Established members of the chat are exempt from AI-powered filter evaluations.

## Lock-based filters

Filters can also use lock types as handlers.
This lets you attach normal filter actions to message-type detection that is shared with the `Locks` module.

Examples:

```
/add_filter sticker
/add_filter language:ru
/add_filter url
/add_filter stickerpack:FunnyCats
/add_filter cyrillic
```

The full list is obtainable by using the `/lockable` command.

When you use a lock-based handler, Sophie does not match literal text.
Instead, it checks the message the same way the `Locks` module does.

Lock-based handlers are best when you are targeting message structure or Telegram-specific content rather than an
arbitrary text phrase:

- `sticker` matches messages with stickers
- `url` matches messages containing any web link
- `language:ru` matches messages detected as Russian
- `stickerpack:FunnyCats` matches stickers from that sticker pack
- `cyrillic` matches text containing Cyrillic characters using the built-in lock detector

This makes it possible to configure custom actions for lock types, such as warning, muting, banning, or replying,
instead of only deleting the message.

## Choosing the right handler

Use this rule of thumb:

- use **plain text** for a simple loose phrase match
- use `word:` when partial-word matches would cause false positives
- use `exact:` when the whole message must be identical
- use `re:` for usernames (`@channel`), specific web domains, word lists (`wordA|wordB`), or other complex patterns
- use `ai:` when the meaning matters more than the exact wording
- use a **lock type** (like `url`, `photo`, or `cyrillic`) when you are targeting message types rather than text

## Using `/ai_add_filter`

If you are not sure which handler to use, Sophie can suggest one for you.

Use:

```
/ai_add_filter <describe what you want to catch>
```

Examples:

```
/ai_add_filter weird unicode characters
/ai_add_filter crypto spam
/ai_add_filter health-related topics
/ai_add_filter asking to PM
```

Sophie will reply with 1 to 3 suggested handlers.

### Lock-type filter compatibility

Older filters created before lock-based filters were introduced keep their previous behavior.
If an existing filter handler happens to have the same text as a lock type, Sophie still treats it as a normal text
filter.

## Multiple filter actions and multiple filters

Sophie supports having many filter actions for one filter handler.


This means you can trigger combined actions like warning a user and deleting their message at the same time.

However, limits exist:
The maximum number of actions per filter is currently three, and Sophie will only trigger the first two matching filters on any single message.

## Silent mode

Filters can be switched to **silent mode** from the filter setup screen of `/add_filter` and `/edit_filter`.

The 🔊 / 🔇 button toggles it, and the setting is saved together with the rest of the filter.

When a filter includes reply actions (such as sending a note, warning the user, muting them, or providing an AI response), Sophie normally leaves those notices in the chat permanently.

When **silent mode** is enabled, Sophie waits 30 seconds after triggering, then cleans up the chat by deleting:

- **the user's message that triggered the filter** (even if the filter did not have the "Delete the message" action attached!)
- **every response message Sophie sent in reply to it** (such as warning or mute notices)

This ensures the user sees the warning or note, but the exchange is completely removed 30 seconds later without cluttering the chat. It behaves like the silent restriction commands (`/sban`, `/smute`, and friends), which remove their own traces too.

Remarks:

- Sophie needs the *Delete messages* admin right, otherwise the cleanup is silently skipped
- If a filter's only action is *Delete the message*, Sophie already deletes the message immediately without sending a reply
- The 30-second cleanup is scheduled in memory, so a bot restart within those 30 seconds cancels the pending cleanup
- When several filters match one message, a single silent filter makes the whole exchange silent, because Sophie aggregates all filter replies into one message

# How the Filters Engine Works for Admins vs. Non-Admins

To enable admins to manage the bot's settings, Sophie ignores filters for known commands (listed in `/help`) when they are used by admins.
For regular users, filters apply to all messages, even overriding known commands.

If a filter is triggered, Sophie skips executing any part of the command.

For example, if the command is `/notes`, a regular user will trigger a filter action (like message deletion) instead of getting the notes list. Admins get the list of notes instead.

Filters do not apply in private messages (PMs), regardless of user status, even if using chat connections.

### Admin exemptions and linked announcement channels

- **Personal admin accounts**: Human administrators sending messages from their personal accounts are exempt from restrictive filter actions (like deleting messages, warnings, mutes, kicks, and bans).

- **Linked announcement channels**: If your discussion group is linked to a broadcast channel (for comments threads), Telegram delivers auto-posted channel messages as **channel posts** rather than user messages. Because channel posts do not carry a regular user account, Sophie's admin exemption does not apply to them. Any words in an announcement post that match active filters will trigger those filter actions.

## Remarks about AI Response filter action

The AI Response action does not require AI features to be enabled. Since only admins can add filters and filter actions,
adding the AI Response filter implies consent for AI features (see the AI help page).

AI Responses enhance interactions in situations where standard responses may seem "too dry".
Sophie will use the message content as context to generate more suitable responses.
Admins can provide additional information in the prompt to help Sophie's AI module better assist users with their
specific needs.

Once the AI Response is generated, users can interact with it by simply replying to the message, which can be invaluable
for addressing follow-up questions.

## Filter statistics

The `filter_stats` feature flag defaults to off and gates both counting and the admin-only
`/filterstats` command. The `filters` flag must also be enabled. The command uses the current
group or the connected group in a private chat.

Each successfully completed action adds one to its filter's count: a filter that successfully
warns, deletes, and replies adds three. Matches alone, exempt users' skipped actions, duplicate
actions suppressed by the filter dispatcher, failed actions, and incomplete deliveries add nothing.
Text replies count after delivery; successful moderation actions count even if their notification
cannot be delivered. Later logging or history-cache errors do not undo a successful action
observation. An album counts once only after all required parts are delivered; saveable callers
still receive its primary message when a follow-up fails.

Identity is the existing filter document ID within the chat's internal ID. Editing a filter keeps
its identity; deleting and recreating it creates a new one. The report uses current handler names
for existing filters and last-counted names for deleted filters whose counters remain in Redis.
Long names are shortened; full identities remain visible. Large reports are sent in batches.

Counters and saved labels live only in the existing application Redis instance. Increments use
Redis `HINCRBY`; a transaction updates the counter and label together, and totals are computed from
a single hash snapshot. This is noncritical data that may be lost through Redis loss, eviction,
or errors; it has no MongoDB copy or durability guarantee. Recording errors do not interrupt filter
enforcement, and a failed statistics flag lookup skips that observation. There is no backfill,
reset command, expiry, or replay of observations missed while disabled or unavailable. Disabling
the flag leaves existing counters in Redis.
