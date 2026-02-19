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
| `/fedinfo` `/finfo` | `<Federation ID to get info about (optional)>` | Get information about a federation | *Disable-able* |
| `/unfban` `/funban` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User to unban>` | Unban a user from the federation | *Disable-able* |
| `/fbanlist` `/exportfbans` `/fexport` | `<Federation ID (optional, uses current chat's federation if not specified)>` | Show list of banned users in federation | *Disable-able* |
| `/fcheck` `/fbanstat` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User to check>` | Check federation bans for a user in current chat | *Disable-able* |
| `/transferfed` `/ftransfer` | `<Federation ID to transfer>` `<New owner username or ID>` | Transfer federation ownership | *Disable-able* |
| `/accepttransfer` | `<Federation ID to accept transfer for>` | Accept federation ownership transfer | *Disable-able* |
| `/fsetlog` `/setfedlog` | - | Sets the Federation logs channel | *Disable-able* |
| `/funsetlog` `/unsetfedlog` | - | Removes the Federation logs channel | *Disable-able* |
| `/fsub` | `<Federation ID to subscribe to>` | Subscribe federation to another federation | *Disable-able* |
| `/funsub` | `<Federation ID to unsubscribe from>` | Unsubscribe federation from another federation | *Disable-able* |
| `/importfbans` `/fimport` | `<Federation ID (optional, uses current chat's federation if not specified)>` | Import federation ban list from CSV file | *Disable-able* |
| `/frename` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<New federation name>` | Rename a federation (owner only) | *Disable-able* |
| `/fchats` | `<Federation ID (optional, uses current chat's federation if not specified)>` | List all chats in a federation | *Disable-able* |
| `/fpromote` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User>` | Promote a user to federation admin | *Disable-able* |
| `/fdemote` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User>` | Demote a user from federation admin | *Disable-able* |

### PM-only

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/fcheck` `/fbanstat` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User to check>` `<Show all bans>` | Check federation bans for a user in PM | *Only in groups*, *Disable-able* |

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/joinfed` `/fjoin` | `<Federation ID to join>` | Join a chat to a federation | *Disable-able* |
| `/leavefed` `/fleave` | - | Leave a federation | *Disable-able* |
| `/fban` `/sfban` | `<Federation ID (optional, uses current chat's federation if not specified)>` `<User to ban>` `<Reason (optional)>` | Ban a user from the federation | *Disable-able* |