---
title: Group whitelist
icon: 🕊️
---

### Manage the group whitelist for automated moderation

> The group whitelist exempts a Telegram user from Sophie's automated moderation throughout the current group. Entries do not apply in other groups. \
> It covers Welcome Security, locks, automated filters and actions, antiflood, spam detection, AI moderation, and automatic federation or community ban checks. \
> It does not grant administrator rights, authorize admin commands, or prevent an administrator from applying a direct moderation command.

## Available commands


### Commands

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/whitelisted` | `<Export as CSV with ^csv>` | List users in this group's whitelist, or export them with ^csv. |  |
{.card-view-on-mobile}

### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/whitelist` `/trust` | `<User>` | Add a user to the group whitelist for automated moderation. |  |
| `/unwhitelist` `/untrust` | `<User>` | Remove a user from the group whitelist for automated moderation. |  |
{.card-view-on-mobile}
---
# Group whitelist

The group whitelist lets moderators exempt a trusted person from Sophie's automated moderation in one Telegram
group. An entry applies only in the group where it was added; whitelisting the same person elsewhere requires a
separate entry.

Administrators who can restrict members can use `/whitelist <user>` to add someone and `/unwhitelist <user>` to
remove them. Both commands accept a user argument or a reply to the person's message. `/trust` and `/untrust` are
aliases for the same actions.

All group members, including ordinary members, can use `/whitelisted` to browse the current group's entries. Long
lists are split into pages. Administrators who can restrict members also see a **Remove** button for every person
on the current page. Use `/whitelisted ^csv` to download the complete current-group list as a CSV file.

## What the whitelist changes

| Automated feature | Protected by the group whitelist? |
| --- | --- |
| Welcome Security CAPTCHA enrollment and CAPTCHA join requests | Yes |
| Welcome mute and cleanup of pending CAPTCHA users | Yes |
| Message locks and outsider-reaction locks | Yes |
| Automatic filter actions, including delete, warn, mute, kick, and ban | Yes |
| Antiflood actions | Yes |
| Spam detection and AI moderation | Yes |
| Automatic federation and community ban enforcement when a user posts | Yes |
| Direct `/warn`, `/mute`, `/kick`, `/ban`, federation-ban, or community-ban actions by an administrator | No |
| Existing restrictions unrelated to an active Welcome Security CAPTCHA | No |
| Administrator permissions, command permissions, or disabled-command rules | No |

Whitelisting is persistent until an administrator removes the entry. It is not the same as letting someone pass a
CAPTCHA once: a one-time CAPTCHA pass affects that verification attempt, while the group whitelist continues to
protect the person from the automated features above. If someone is currently waiting on Welcome Security when an
administrator whitelists them, Sophie releases that CAPTCHA mute and clears the pending verification entry.

The whitelist never prevents deliberate moderation. Administrators can still moderate a whitelisted person
directly whenever necessary.
