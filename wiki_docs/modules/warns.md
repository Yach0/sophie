---
title: Warnings
icon: ⚠️
---> Warns users in the chat to keep it safe. \
> You can set a max warning limit and an action to take when the limit is reached.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/warns` | `<User>` | Shows user's warns in the current chat. | *Only in groups*, *Disable-able* |
{.card-view-on-mobile}

### PM-only

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/warns` | - | Shows all your warns across all chats. |  |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/warn` | `<User to warn>` `<Reason>` | Warns a user. | *Disable-able* |
| `/reset_warns` `/del_warns` | `<User>` | Resets all warnings of a user in the current chat. | *Only in groups* |
| `/reset_all_warns` `/del_all_warns` | - | Resets all warnings of all users in the current chat. | *Only in groups* |
| `/warn_limit` | `<?New value>` | Shows / changes the warn limit for this chat. |  |
| `/warn_action` | - | Configures warn actions. |  |
| `/warn_action_each` | - | - |  |
| `/warn_action_max` | - | - |  |
{.card-view-on-mobile}