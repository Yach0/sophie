# Group whitelist

The group whitelist is scoped to one Telegram group and keyed by that group's Telegram chat ID together with
the user's Telegram ID. A group administrator with permission to restrict members can use
`/whitelist <user>` or `/unwhitelist <user>` in that group. `/trust` and `/untrust` are aliases for those same
commands. Both commands also accept a reply to the target user's message. `/whitelist` without a target lists the
users whitelisted in the current group.

Whitelisted users are exempt from automated moderation performed by Sophie only in the group where the entry exists:

- Welcome Security CAPTCHA enrollment, join-request CAPTCHA checks, welcome muting, pending-CAPTCHA message deletion,
  and pending-CAPTCHA auto-kicks
- message and outsider-reaction locks
- restrictive filter actions, including automatic delete, warn, mute, kick, and ban actions
- antiflood enforcement
- spam-classifier scanning and AI moderator enforcement
- federation and community ban middleware that automatically enforces an existing ban when the user posts

The whitelist is deliberately not an administrator system. It does not grant Telegram or Sophie administrator
permissions, authorize commands or callbacks, bypass disabled-command checks, grant chat connections, or alter the
result of `/info` admin checks. An administrator can still deliberately apply direct moderation commands such as
`/warn`, `/mute`, `/kick`, `/ban`, federation bans, or community bans to a whitelisted user. Adding a user does not
undo restrictions that already exist; it only prevents later automated enforcement covered above. The exception is
an existing Welcome Security CAPTCHA mute in the current group: `/whitelist` releases that mute and clears the
pending CAPTCHA record. This feature does not alter `/unmute` or add a separate one-time Welcome Security bypass.

No backward-compatibility migration is needed because the feature has not shipped. The collection, feature flag, and
Redis cache prefix use `group_user_whitelist` directly; no aliases or migration path from the earlier development name
are retained. Documents contain both `chat_tid` and `user_tid`, protected by a compound unique index.
