---
title: Filters
icon: 🪄
---> Filters allows to invoke different actions for different messages. \
> For example muting the users when they mention crypto. \
> Sophie supports many different actions you can configure to automatize chat moderation in many different ways.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/filters` | - | Lists all filters in the chat | *Disable-able* |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/addfilter` `/newfilter` | `<Text to match>` | Adds a new filter |  |
| `/delfilter` | `<Text to match>` | Deletes a filter |  |
| `/editfilter` | `<Filter's keyword>` | Edits filter settings |  |
{.card-view-on-mobile}

### Aliased commands from [✨ Sophie AI](ai)

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/aiaddfilter` | `<Describe what the filter should catch>` | Suggests filter handlers from a natural language description |  |
---
## Filter handlers

When you create a filter with `/addfilter`, the first argument is the **handler**.
The handler tells Sophie what kind of message should trigger the filter.

It helps to think of handlers in two families:
- text matchers: plain text, `word:`, `exact:`, `re:`, `ai:`
- built-in detectors: lock-based handlers such as `url`, `photo`, `language:ru`, `stickerpack:FunnyCats`, `cyrillic` (The full list obtainable by writing `/lockable`)

### Plain text handlers

Plain text is the default matcher.
If you do not add a prefix, Sophie performs a loose text match against normalized message text.

Examples:

```
/addfilter crypto
/addfilter t.me
/addfilter free nitro
```

Use plain text when you want a simple substring match and do not care about exact wording.
This is usually the easiest option for common spam phrases.

Remarks:

- Sophie normalizes message text before matching
- This helps catch spacing tricks and Unicode decoration
- It can be broader than you expect if the text appears inside a larger phrase

### `word:` handlers

Use `word:` when you want to match a whole word, or an exact tokenized phrase, without matching partial words.

Examples:

```
/addfilter word:crypto
/addfilter word:free money
```

This is useful when plain text would be too broad.
For example, `word:crypto` is safer than plain `crypto` if you do not want partial-word false positives.

Remarks:

- Good for whole words and short phrases
- Helps avoid partial-word false positives
- Less flexible than regex for more advanced patterns

### `exact:` handlers

Use `exact:` when the entire message must match exactly.

Examples:

```
/addfilter exact:hello
/addfilter exact:t.me/+
```

This is the strictest text matcher.
It only triggers when the full message text is exactly the same as the handler.

Remarks:

- Best when users repeatedly send the exact same message
- Useful for very specific invite links or canned spam messages
- Too strict for most varied spam patterns

### `re:` handlers

Use `re:` for regular expressions when simple text matching is not enough.

Examples:

```
/addfilter re:@\w+
/addfilter re:crypto|btc|bitcoin
```

Regex is powerful, but should be used carefully.
It is best when you need to match several variants or a structured pattern.

Some regex could be very slow to handle,
the [DoS practice](https://en.wikipedia.org/wiki/ReDoS) of invoking slow regex patterns exists,
therefore, Sophie will test the entered regex against the speed of execution.
In the case the pattern is too slow, it'll be rejected from adding to filters.

Remarks:

- Best for advanced text patterns
- Good for multiple variants in one handler
- Harder to read and maintain than plain text or `word:`

## AI Filter handlers

Sophie introduces powerful AI-powered filter handlers that intelligently determine whether to trigger filter actions
based on message content. Powered by Mistral AI, an industry-leading AI provider known for its commitment to data
privacy, this feature allows you to create intelligent filters that understand context and meaning rather than just
matching text patterns.

### How to Use AI Filters

Simply use the `ai:` prefix followed by your prompt when adding filters:

```
/addfilter ai:Your prompt describing when to trigger
```

### When to use `ai:`

Use `ai:` only when literal text rules are too weak.

Examples:

```
/addfilter ai:Has anything regarding money or cryptocurrency
/addfilter ai:Message contains scam or phishing attempt
/addfilter ai:Promotion or advertisement content
```

Choose `ai:` when the intent matters more than the exact words.
For example, it can help with scammy wording that changes often but keeps the same meaning.

Avoid `ai:` when a plain text, `word:`, `exact:`, `re:`, or lock-based handler would already solve the problem.

### Examples

```
/addfilter ai:Has anything regarding money or cryptocurrency
/addfilter ai:Message contains scam or phishing attempt
/addfilter ai:Promotion or advertisement content
/addfilter ai:Spam or unsolicited messages
/addfilter ai:Political content
```

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
/addfilter sticker
/addfilter language:ru
/addfilter url
/addfilter stickerpack:FunnyCats
```

The full list is obtainable by using `/lockable` command.

When you use a lock-based handler, Sophie does not match literal text.
Instead, it checks the message the same way the `Locks` module does.

Lock-based handlers are best when you are targeting message structure or Telegram-specific content rather than an
arbitrary text phrase.

Examples:

- `sticker` matches messages with stickers
- `url` matches messages containing URL entities
- `language:ru` matches messages detected as Russian
- `stickerpack:FunnyCats` matches stickers from that sticker pack
- `cyrillic` matches text containing Cyrillic characters using the built-in lock detector

This makes it possible to configure custom actions for lock types, such as warning, muting, banning, or replying,
instead of only deleting the message.

## Choosing the right handler

Use this rule of thumb:

- use plain text for a simple loose phrase match
- use `word:` when partial-word matches would be annoying
- use `exact:` when the whole message must be identical
- use `re:` when you need a structured or multi-variant pattern
- use `ai:` when the meaning matters more than the exact wording
- use a lock type when you are targeting message structure or Telegram content types rather than free-form text

## Using `/aiaddfilter`

If you are not sure which handler to use, Sophie can suggest one for you.

Use:

`/aiaddfilter <describe what you want to catch>`

Examples:

```
/aiaddfilter block crypto spam
/aiaddfilter catch phishing links
/aiaddfilter block users asking people to DM for investment help
```

Sophie will reply with 1 to 3 suggested handlers.

### Legacy filters compatibility

Older filters created before lock-based filters were introduced keep their previous behavior.
If an old filter handler happens to have the same text as a lock type, Sophie still treats it as a normal text filter.

## Multiple filter actions and multiple filters

Sophie supports having many filter actions for one filter handler.
This means you can trigger complicated actions like warning users and deleting the user's message.
Or triggering the AI Respond with a prompt that will try to describe users why is it bad to tag admins in chats, while
in the same time deleting the trigger message and muting the user.

However, limits exist.
The maximum number of actions per filter is currently three, and Sophie will only trigger the first two of matching
filters.

# How the Filters Engine Works for Admins vs. Non-Admins

To enable admins to manage the bot's settings, Sophie ignores filters for known commands (listed in /help) when they're
used by admins.
For regular users, filters apply to all messages, even overriding known commands.
If a filter is triggered, Sophie skips executing any part of the command.

For example, if the command is `/notes`, a regular user will trigger a filter action (like message deletion) instead of
getting the notes list.
But admins? They get the list of notes instead.
Also, filters don't apply in PMs, regardless of user status, even if using chat connections.

## Remarks about AI Response filter action

The AI Response action does not require AI features to be enabled. Since only admins can add filters and filter actions,
adding the AI Response filter implies consent for AI features (see the AI help page).

AI Responses enhance interactions in situations where standard responses may seem "too dry".
Sophie will use the message content as context to generate more suitable responses.
Admins can provide additional information in the prompt to help Sophie's AI module better assist users with their
specific needs.

Once the AI Response is generated, users can interact with it by simply replying to the message, which can be invaluable
for addressing follow-up questions.
