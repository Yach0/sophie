# Proactive AI reactions

Proactive decisions use cached message targets. A target can become unavailable before Sophie sends its reaction.

If Telegram returns `Bad Request: message to react not found`, Sophie logs the unavailable target and records the `reaction_skipped` proactive metric with `reason=target_missing`. It skips that reaction and continues the remaining actions so the batch can finish and clear its tracked messages. It records `reaction_sent` only after a successful reaction.

Other Telegram errors and network failures continue to propagate through the error handler; unsuccessful batches retain their existing retry behavior.

Proactive answers also require their reply target to remain available. If Telegram rejects delivery with `message to be replied not found` or `REPLY_MESSAGE_ID_INVALID` (with or without the `Bad Request: ` prefix), Sophie skips that answer, logs the unavailable target, and records `answer_skipped` with `reason=target_missing`. This response does not establish why the target is unavailable. Sophie never retries an unsolicited answer without reply context, since doing so could resurface removed content or intent. Explicit `/ai` and reply delivery keep their existing behavior.

Generation and tool execution have already completed and usage has already been charged before delivery. These side effects remain intact; skipping delivery neither regenerates the answer nor reruns tools. Undelivered output is not cached or committed to modern conversation history, and the existing context cleanup releases its lease. Remaining actions continue and successful batch cleanup clears tracked messages. Rich formatting and photo URL fallback still use the shared sender, with reply context preserved. Unrelated Telegram and network errors continue to propagate.
