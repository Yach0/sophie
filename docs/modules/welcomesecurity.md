# Welcome Security expiry settings

`/welcome_security` offers expiry presets for pending captcha members. Changing expiry preserves whether captcha is enabled; it does not enable captcha or set the media restriction duration.

Each expiry button is bound to the originating database chat ID. It requires administrator access and the same current target chat. Switching the private-chat connection or disconnecting invalidates the old keyboard: reconnect to the intended group and reopen `/welcome_security`. Buttons sent before the chat-bound callback format was introduced must also be reopened; unbound payloads cannot update settings.

## Rejoining while a captcha is pending

A new join starts a fresh pending captcha deadline. Duplicate delivery of the same join does not extend that deadline. Delayed leave processing cannot remove a newer membership or its pending captcha session. Users who already passed verification keep their passed status when they leave and rejoin.

## Expiry and concurrent verification

Pending-session initialization, the initial mute, expiry actions, and captcha completion share a per-user lock. A rejoin cannot reset a pending deadline while an expiry action is still using the old session. Expiry also checks the current membership boundary before unmuting, kicking, or declining a request, so a recorded rejoin invalidates an older pending session even before its new captcha setup completes. If another lifecycle operation still holds the lock when the expiry wait times out, that user is deferred to a later sweep; other expired users continue to be processed. A failed expiry action keeps the pending record for retry.

Captcha completion also keeps the pending record and recovery messages until the captcha mute is successfully released or replaced with the configured welcome mute, including for administrators and group-whitelisted users. If a join request was already approved, completion still attempts that restriction transition.

## Button-based settings panel

The `welcome_security_settings_panel` feature flag is disabled by default. When enabled for a group, `/welcome_security` opens a settings panel. `/welcome_captcha`, `/enable_welcome_captcha`, and `/welcome_restrict` without arguments also open it. Existing command arguments continue to work.

The panel controls CAPTCHA, its expiry, media restriction and its duration, join/leave message cleanup, and previous welcome message cleanup. It shows effective CAPTCHA service switches and explains that an unanswered challenge causes a member kick or join-request decline. Changing a duration preserves the feature's enabled state.

Administrators can view, edit, or reset the security message. Editing uses a reply prompt with the existing saveable parser, including supported media, buttons, and formatting. `/cancel` abandons editing. Reset has a confirmation step. The pending-user view shows counts and at most ten recent records; it does not change membership or permissions. Existing `/unmute <user>` remains the manual recovery action.

Every action checks administrator access and the originating database chat ID. A private-chat connection must still point to the same group when a button or edit reply is used. Changing settings does not reset pending challenges or ask verified users to solve CAPTCHA again.
