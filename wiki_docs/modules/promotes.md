---
title: Promotes
icon: ⭐️
---

### Manage user promotions in chats

> Allows administrators to promote and demote users in chats. \
> Manage user permissions and administrative privileges.

## Available commands


### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/promote` | `<User>` `<?Admin title>` | Promotes the user to admins. |  |
| `/demote` | `<User>` | Demotes the user from admins. |  |
{.card-view-on-mobile}

---
## Administrator permission changes

If Telegram rejects `/demote` because the bot lacks the required administrator rights, Sophie reports the failure to the caller. It does not report a successful demotion or refresh the administrator cache as if the change succeeded.
