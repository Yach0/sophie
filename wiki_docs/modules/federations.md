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
| `/newfed` `/fnew` | `<Federation name>` | Create a new federation | *Disable-able* |
| `/fedinfo` `/finfo` | `<?Federation ID>` | Get information about a federation | *Disable-able* |
| `/unfban` `/funban` | `<?Federation ID>` `<User>` | Unban a user from the federation | *Disable-able* |
| `/fbanlist` `/exportfbans` `/fexport` | `<?Federation ID>` | Show list of banned users in federation | *Disable-able* |
| `/fcheck` `/fbanstat` | `<?Federation ID>` `<User to check>` | Check federation bans for a user | *Disable-able* |
| `/transferfed` `/ftransfer` | `<?Federation ID>` `<New owner>` | Transfer federation ownership | *Disable-able* |
| `/accepttransfer` | `<Federation ID to accept transfer for>` | Accept federation ownership transfer | *Disable-able* |
| `/fsetlog` `/setfedlog` | - | Sets the Federation logs channel | *Disable-able* |
| `/funsetlog` `/unsetfedlog` | - | Removes the Federation logs channel | *Disable-able* |
| `/fsub` | `<Federation ID to subscribe to>` | Subscribe federation to another federation | *Disable-able* |
| `/funsub` | `<Federation ID to unsubscribe from>` | Unsubscribe federation from another federation | *Disable-able* |
| `/importfbans` `/fimport` | `<?Federation ID>` | Import federation ban list from CSV file | *Disable-able* |
| `/frename` | `<?Federation ID>` `<New federation name>` | Rename a federation (owner only) | *Disable-able* |
| `/fchats` | `<?Federation ID>` | List all chats in a federation | *Disable-able* |
| `/fpromote` | `<?Federation ID>` `<User>` | Promote a user to federation admin | *Disable-able* |
| `/fdemote` | `<?Federation ID>` `<User>` | Demote a user from federation admin | *Disable-able* |

### PM-only

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/fcheck` `/fbanstat` | `<User to check>` `<'full' to show all bans>` | Check federation bans | *Only in groups*, *Disable-able* |

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/joinfed` `/fjoin` | `<Federation ID to join>` | Join a chat to a federation | *Disable-able* |
| `/leavefed` `/fleave` | - | Leave a federation | *Disable-able* |
| `/fban` `/sfban` | `<?Federation ID>` `<User>` `<?Reason>` | Ban a user from the federation | *Disable-able* |