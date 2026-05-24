---
title: Restrictions
icon: 🛑
---
### Manage user restrictions in chats

> Provides commands to restrict users in chats. \
> Includes ban, kick, mute, and temporary restrictions.

## Available commands


### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/kick` | `<User>` `<Reason>` | Kicks the user from the chat. The user would be able to join back. |  |
| `/skick` | `<User>` `<Reason>` | Silently kicks the user from the chat. Deletes messages after 10 seconds. | *Only in groups* |
| `/ban` | `<User>` `<Reason>` | Bans the user from the chat. |  |
| `/sban` | `<User>` `<Reason>` | Silently bans the user from the chat. Deletes messages after 10 seconds. | *Only in groups* |
| `/tban` | `<User>` `<Time (e.g., 2h, 7d, 2w)>` `<Reason>` | Temporarily bans the user from the chat. |  |
| `/stban` `/tsban` | `<User>` `<Time (e.g., 2h, 7d, 2w)>` `<Reason>` | Silently temporarily bans the user from the chat. Deletes messages after 10 seconds. | *Only in groups* |
| `/mute` | `<User>` `<Reason>` | Mutes the user in the chat. |  |
| `/smute` | `<User>` `<Reason>` | Silently mutes the user in the chat. Deletes messages after 10 seconds. | *Only in groups* |
| `/tmute` | `<User>` `<Time (e.g., 2h, 7d, 2w)>` `<Reason>` | Temporarily mutes the user in the chat. |  |
| `/stmute` `/tsmute` | `<User>` `<Time (e.g., 2h, 7d, 2w)>` `<Reason>` | Silently temporarily mutes the user in the chat. Deletes messages after 10 seconds. | *Only in groups* |
| `/unmute` | `<User>` `<Reason>` | Unmutes the user in the chat. |  |
| `/unban` | `<User>` `<Reason>` | Unbans the user from the chat. |  |
{.card-view-on-mobile}

### Aliased commands from [✨ Sophie AI](ai)

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/aimoderator` | `<?New status>` | Controls AI Moderator features |  |