---
title: Warnings
icon: ⚠️
---
> Warns users in the chat to keep it safe. \
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
| `/warn_expiration` | `<Warning lifetime (e.g., 2h, 7d, 2w)>` | Shows / changes how long each warning lasts. |  |
| `/disable_warn_expiration` | - | Disables warning expiration in this chat. |  |
| `/warn_action` | - | Configures warn actions. |  |
| `/warn_action_each` | - | Configures actions triggered on each warning. |  |
| `/warn_action_max` | - | Configures actions triggered when the warn limit is reached. |  |
{.card-view-on-mobile}
---
## Warning expiration

Expiration is disabled by default. Bot operators must enable the default-off
`warn_expiration` feature flag globally or for your chat before administrators can
use these commands. The flag also controls expiration at runtime: when it is off,
stored warnings keep counting and appearing, even if a lifetime was configured.

Administrators can use `/warn_expiration` to view the current setting,
`/warn_expiration 7d` to set a lifetime, and `/disable_warn_expiration` to disable it.
Durations use the usual Sophie units, such as `2h`, `7d`, `2w`, or `1d12h`, from one
minute to 3650 days. Each chat has its own setting.

Each warning expires separately, measured from its own creation time. Adding a new
warning never extends older warnings. A warning at or beyond the configured age
does not count toward punishment and does not appear in group, private-message,
or API warning lists. Existing dated warnings use their original creation times,
so enabling expiration or shortening the lifetime can expire them immediately on
the next warning or listing.

Expired records are permanently removed when warnings are added or listed; no
background timer is required. Disabling expiration or turning the flag off stops
further cleanup and does not restore records already removed. Changing the setting
does not itself issue a punishment.

Legacy warning records with no creation date (including an explicit null date)
remain active until an administrator removes or resets them, or the normal warning
limit clears them. Their age cannot be determined safely; reading them never
assigns a new creation time. Old records with an empty administrator field are
still supported. Only records with a scalar stored MongoDB date are automatically expired;
unrecognized legacy timestamp shapes are not deleted by expiration.
