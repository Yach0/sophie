# Welcome Security join-error audit

Audit date: 2026-11-02. Related GitLab issue: [#141](https://gitlab.com/SophieBot/sophie/-/issues/141).

## Evidence and scope

The sampled Sentry events all ran release `4.5.1+8381bf08` in `production-stable_bot`. This is older than the audited main revision `a9a89358af9866bbf1734167c0e0770f3cd56b2c`. Their continued occurrence does not prove that current main lacks the existing fixes.

The events belong to [SOPHIE-28J](https://sophiebot.sentry.io/issues/SOPHIE-28J). Each is tagged `update:chat_join_request`, but each displayed stack goes through `NewUserMiddleware -> send_welcome -> send_saveable -> common_try`. The failing operation in these samples is greeting delivery, not CAPTCHA approval or decline. The transaction tag alone is not sufficient to classify the incoming chat as a channel or group. The surfaced event details did not include an authoritative chat-type field.

| Error | Sample event | Time (UTC) | Current-main behavior | Classification |
| --- | --- | --- | --- | --- |
| `REPLY_MESSAGE_ID_INVALID` | `2bb1f967296b48d9999e32b1921f068d` | 2026-11-02 07:42:26 | `common_try` recognizes both missing-reply error forms and invokes the supplied retry without the reply target. Recipient and thread context remain attached. | Expected stale reply target; retry is already implemented. |
| `USER_NOT_PARTICIPANT` | `36b3dfcd36304eac9d62e8c9a0f86396` | 2026-10-19 23:56:42 | `_emit_with_recipient` skips only an unavailable ephemeral recipient, including after a missing-reply retry. It does not resend publicly. The same error on a public send still propagates. | Expected recipient departure on ephemeral sends; narrowly handled already. |
| `BOT_NOT_ADMIN` | `db1c3be82ebc440eadf002784a862031` | 2026-10-25 21:31:39 | The error propagates from saveable delivery to the error handler. | Bot permission/configuration failure; preserve reporting. It is not equivalent to recipient departure. |

## Supported chat behavior

`ChatJoinRequestHandler` explicitly ignores channel join requests before running group CAPTCHA or approval logic. Group and supergroup handling remains in place. An E2E regression checks that a channel request does not issue Telegram requests or create CAPTCHA state. This validates current source behavior; it does not identify the chat type of the historical samples.

## Verification

Existing saveable and greeting tests cover missing-reply retries, departed ephemeral recipients, recipient/thread preservation, and the absence of public resends. Additional tests cover the exact `BOT_NOT_ADMIN` error, its prefixed form, propagation through the real greeting dispatcher, and the channel boundary. No broad exception suppression or runtime fallback was added.

## Follow-up after deployment

Deploying or merging is outside this audit. After an authorized deployment of the relevant current-main changes:

1. Filter Sentry by the deployed release and environment rather than the unresolved group's aggregate count.
2. Check missing-reply and departed-recipient events separately; they should follow the tested recovery/skip paths.
3. For `BOT_NOT_ADMIN`, inspect the actual Telegram method, target chat type, and current bot permissions. Correct configuration where required. Keep unexpected permission errors visible.
4. If an event still reproduces on the deployed revision, capture its method parameters and chat type before proposing another runtime fix.

Keep #141 open until production behavior has been checked. These samples justify regression coverage and deployment verification, not another shared exception-swallowing rule.
