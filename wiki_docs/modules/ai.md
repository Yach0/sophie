---
title: Sophie AI
icon: ✨
---

### Rainbow sparkles and shininess

> Sophie supports quite a few ways to use AI features. \
> From a simple chat-bot, to the automatic translator. Have fun. \
>   \
> By using AI features you agree to our Privacy Policy (/privacy) and third party AI services used. \
> Please note that each chat has a limited monthly AI quota. \
> Use /ai_usage to check your remaining quota.

## Available commands

In group chats using Entertainment mode, mentioning the running bot's username in a message or caption also asks the AI a question. For example, `@YourBot help` sends `help` as the prompt. Username matching is case-insensitive. Messages and captions starting with a configured command prefix remain reserved for commands, including disabled commands. Existing reply routes take priority, so overlapping triggers produce one answer. Mention requests pass through the existing filters, antiflood, and locks enforcement before the AI quota check or reply, including when quota is exhausted. Allowed mentions with exhausted quota receive the usual quota notice.


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/research` | `<Research topic>` | Research a topic using multistage web search and return a summary with sources. | *Disable-able* |
| `/ai_translate` `/translate` `/tr` | `<Text to translate>` | Translates the given (or replied) text to the chat's selected language. Also transcribes the replied voice message to text. | *Disable-able* |
| `/ai_usage` | - | Check AI quota and usage details. |  |
| `/ai` | `<Prompt>` | Ask Sophie a question. | *Disable-able* |
{.card-view-on-mobile}

### Memory

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/ai_memory` | `<?@username or search text>` | Browse your memories, search by text, or view a member's memories by mention or reply. |  |
| `/ai_memory_delete` | `<Memory IDs separated by spaces>` | Delete saved memories by fact ID. |  |
{.card-view-on-mobile}

### PM-only

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/ai` | - | Start the AI ChatBot mode. |  |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/ai_mode` | - | Select what the AI does in this chat. |  |
| `/ai_moderator` | `<New state>` | Enable or disable the AI moderator in this chat. |  |
| `/ai_moderator` | - | Tune what the AI moderator detects in this chat. |  |
| `/ai_auto_translate` `/auto_translate` | `<?New status>` `<?Language code>` | Controls AI Auto translator. |  |
| `/ai_summaries` | `<?New status>` | Controls AI chat summaries. |  |
| `/ai_summaries_pin` | `<?New status>` | Controls automatic pinning of AI chat summaries. |  |
| `/ai_summaries_time` | `<UTC time (HH:MM)>` | Sets the daily AI chat summary generation time in UTC. |  |
| `/ai_note_titles` | `<?New status>` | Controls AI note title generation. |  |
| `/ai_add_filter` | `<Describe what the filter should catch>` | Suggests filter handlers from a natural language description. |  |
| `/ai_reset` | - | Reset the chat's AI context and AI memory. |  |
{.card-view-on-mobile}
