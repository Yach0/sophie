# Global user whitelist

The global user whitelist is a bot-wide exemption list keyed by Telegram user ID. A chat administrator with
permission to restrict members can use `/whitelist <user>` or `/unwhitelist <user>` in a group or supergroup. Both
commands also accept a reply to the target user's message. A change made in any group applies in every group Sophie
serves.

Whitelisted users are exempt from automated moderation performed by Sophie:

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
undo restrictions that already exist; it only prevents later automated enforcement covered above.

No migration is needed: the feature uses a new `global_user_whitelist` collection and does not change the shape of
existing documents.
