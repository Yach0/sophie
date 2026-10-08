# Welcome Security expiry settings

`/welcomesecurity` offers expiry presets for pending captcha members. Changing expiry preserves whether captcha is enabled; it does not enable captcha or set the media restriction duration.

Each expiry button is bound to the originating database chat ID. It requires administrator access and the same current target chat. Switching the private-chat connection or disconnecting invalidates the old keyboard: reconnect to the intended group and reopen `/welcomesecurity`. Buttons sent before the chat-bound callback format was introduced must also be reopened; unbound payloads cannot update settings.

## Rejoining while a captcha is pending

A new join starts a fresh pending captcha deadline. Duplicate delivery of the same join does not extend that deadline. Delayed leave processing cannot remove a newer membership or its pending captcha session. Users who already passed verification keep their passed status when they leave and rejoin.

## Expiry and concurrent verification

Pending sessions use a MongoDB atomic transition to choose completion, expiry, or whitelist exemption. The decision and membership generation survive process restarts. Failed kicks and declines retain the expiry decision for subsequent scheduler sweeps. Failed exemption unmutes retain `exempting` for scheduler retry; failed approvals or unmutes retain the completion decision for retry through the CAPTCHA flow, and are never converted into expiry. Rejoins reset the decision and deadline for the new membership generation. Conditional final deletion cannot remove a newer session. Redis is used for existing CAPTCHA data and messages, not synchronization.

MongoDB serializes the state decision, not Telegram API execution. The initial mute and same-decision retries can overlap with other Telegram calls, and a crash after Telegram succeeds but before deletion can repeat the call. There is no transaction spanning MongoDB and Telegram, nor a guarantee against a rejoin arriving after the final membership check. Completion recovery requires the user to retry the CAPTCHA flow; operators can inspect retained `completing`/`expiring`/`exempting` rows. No claim expires or switches to the opposite action.

Captcha completion also keeps the pending record and recovery messages until the captcha mute is successfully released or replaced with the configured welcome mute, including for administrators and group-whitelisted users. If a join request was already approved, completion still attempts that restriction transition.
