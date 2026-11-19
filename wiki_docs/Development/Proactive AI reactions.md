# Proactive AI reactions

Proactive decisions use cached message targets. A target can become unavailable before Sophie sends its reaction.

If Telegram returns `Bad Request: message to react not found`, Sophie logs the unavailable target and records the `reaction_skipped` proactive metric with `reason=target_missing`. It skips that reaction and continues the remaining actions so the batch can finish and clear its tracked messages. It records `reaction_sent` only after a successful reaction.

Other Telegram errors and network failures continue to propagate through the error handler; unsuccessful batches retain their existing retry behavior.

## Proactive Support

Support mode can volunteer a reply backed by a group note. To pilot it in one group, use
`/op_ff ^chat=<chat_id> ai_proactive_support true`. The flag is off by default.

Sophie gives people 16 seconds to respond before evaluating a member's message. It can
evaluate a single message and uses the recent conversation in the same Telegram topic.
Owners, admins, operators, bots, and messages sent as a group or channel cannot trigger
proactive replies. Owners, admins, and operators can continue by replying directly to an AI
message from Sophie. The usual command rules apply to `/ai`.

A staff reply on the same branch during the grace period cancels the pending response before
Sophie looks up notes. Sophie checks again for staff replies before sending. It stays quiet
if the issue is already resolved or the notes do not support a concrete next step. If a note
explicitly says to ask the member for specific missing details, Sophie can ask for them.
Support replies go directly to the member, include the source note name, and use the
existing proactive decision model and chatbot quota. Support sends no proactive reactions.

A member can reply to the original message with `/ai` to take over while the proactive
reply is still pending. Sophie also checks recent answers in the same topic to avoid
repeating an answer that already covers another member's report.

Proactive metrics use `feature=ai_proactive_support`. The `answer_skipped` event includes
a reason such as `staff_replied`, `no_notes`, `already_answered`, or `insufficient_evidence`.
