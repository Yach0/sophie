---
title: Federations
icon: 🏛
---
### Manage federations across multiple chats

> Federations allow you to manage multiple chats as a group. You can ban users across all chats in a federation, subscribe to other federations, and manage permissions.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/new_fed` `/fnew` | `<Federation name>` | Create a new federation |  |
| `/fed_info` `/finfo` | `<?Federation ID>` | Get information about a federation | *Disable-able* |
| `/fban` | `<?Federation ID>` `<User>` `<?Reason>` | Ban a user from the federation. |  |
| `/sfban` | `<?Federation ID>` `<User>` `<?Reason>` | Ban a user from the federation. Deletes related messages after 10 seconds. |  |
| `/unfban` `/funban` | `<?Federation ID>` `<User>` | Unban a user from the federation |  |
| `/fban_list` `/export_fbans` `/fexport` | `<?Federation ID>` | Show list of banned users in federation |  |
| `/fcheck` `/fban_stat` | `<?Federation ID>` `<User to check>` | Check federation bans for a user | *Disable-able* |
| `/transfer_fed` `/ftransfer` | `<?Federation ID>` `<New owner>` | Transfer federation ownership |  |
| `/accept_transfer` | `<Federation ID to accept transfer for>` | Accept federation ownership transfer |  |
| `/f_set_log` `/set_fed_log` | - | Sets the Federation logs channel |  |
| `/f_unset_log` `/unset_fed_log` | - | Removes the Federation logs channel |  |
| `/fsub` | `<Federation ID to subscribe to>` | Subscribe federation to another federation |  |
| `/funsub` | `<Federation ID to unsubscribe from>` | Unsubscribe federation from another federation |  |
| `/import_fbans` `/fimport` | `<?Federation ID>` | Import federation ban list from CSV file |  |
| `/frename` | `<?Federation ID>` `<New federation name>` | Rename a federation (owner only) |  |
| `/fdelete` | `<?Federation ID>` | Delete a federation (owner only) |  |
| `/fchats` | `<Federation ID to list chats for (optional)>` | List all chats in a federation |  |
| `/fadmins` `/fed_admins` | `<?Federation ID>` | List all admins of a federation | *Disable-able* |
| `/fpromote` | `<?Federation ID>` `<User>` | Promote a user to federation admin |  |
| `/fdemote` | `<?Federation ID>` `<User>` | Demote a user from federation admin |  |
{.card-view-on-mobile}

### PM-only

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/fcheck` `/fban_stat` | `<User to check>` `<'full' to show all bans>` | Check federation bans |  |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/join_fed` `/fjoin` | `<Federation ID to join>` | Join a chat to a federation |  |
| `/leave_fed` `/fleave` | - | Leave a federation |  |
{.card-view-on-mobile}