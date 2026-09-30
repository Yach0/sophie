---
title: Welcome Security
icon: 🛡️
---
### Protect your chat from bots and verify new users

> Welcome Security contains a bunch of tools that can help filter bots that tries to join your groups, as well as make sure the new users acknowledged the chat rules before being able to speak

## Available commands


### Only admins

| Commands | Arguments | Description | Remarks |
| --- | --- | --- | --- |
| `/welcomecaptcha` `/enablewelcomecaptcha` | `<?New status or expiry time>` | Shows / changes Welcome Captcha and its expiry time. |  |
| `/welcomerestrict` | `<?New status or restrict time>` | Shows / changes the state of Welcome Restrict (Media restricting). |  |
| `/setwelcomesecurity` | `<Content>` | Sets welcome security message. |  |
| `/delwelcomesecurity` | - | Deletes the welcome security message |  |
| `/welcomesecurity` | - | Shows Welcome Security settings | *Disable-able* |
{.card-view-on-mobile}
---
# Welcome Security expiry settings

`/welcomesecurity` offers expiry presets for pending captcha members. Changing expiry preserves whether captcha is enabled; it does not enable captcha or set the media restriction duration.

Each expiry button is bound to the originating database chat ID. It requires administrator access and the same current target chat. Switching the private-chat connection or disconnecting invalidates the old keyboard: reconnect to the intended group and reopen `/welcomesecurity`. Buttons sent before the chat-bound callback format was introduced must also be reopened; unbound payloads cannot update settings.

## Rejoining while a captcha is pending

A new join starts a fresh pending captcha deadline. Duplicate delivery of the same join does not extend that deadline. Delayed leave processing cannot remove a newer membership or its pending captcha session. Users who already passed verification keep their passed status when they leave and rejoin.

## Expiry and concurrent verification

Pending-session initialization, the initial mute, expiry actions, and captcha completion share a per-user lock. A rejoin cannot reset a pending deadline while an expiry action is still using the old session. Expiry also checks the current membership boundary before unmuting, kicking, or declining a request, so a recorded rejoin invalidates an older pending session even before its new captcha setup completes. If another lifecycle operation still holds the lock when the expiry wait times out, that user is deferred to a later sweep; other expired users continue to be processed. A failed expiry action keeps the pending record for retry.
