---
title: Locks
icon: 🔓
---
### Lock specific message types in chats

> Allows administrators to lock specific types of messages in chats. \
> Prevents users from sending certain content types like stickers, GIFs, URLs, sticker packs, and languages.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/lockable` `/locktypes` | - | Shows all lockable message types | *Disable-able* |
| `/locklanguages` `/locklangs` | - | Shows all supported languages for locking | *Disable-able* |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/lock` | `<Lock type>` | Lock a message type in the chat | *Disable-able* |
| `/unlock` | `<Lock type>` | Unlock a message type in the chat | *Disable-able* |
| `/locksticker` | - | Lock a sticker pack in the chat | *Disable-able* |
| `/locks` `/locked` | - | Show currently locked message types in the chat | *Disable-able* |
{.card-view-on-mobile}