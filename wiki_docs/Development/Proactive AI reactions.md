# Proactive AI reactions

Proactive decisions use cached message targets. A target can become unavailable before Sophie sends its reaction.

If Telegram returns `Bad Request: message to react not found`, Sophie logs the unavailable target and records the `reaction_skipped` proactive metric with `reason=target_missing`. It skips that reaction and continues the remaining actions so the batch can finish and clear its tracked messages. It records `reaction_sent` only after a successful reaction.

Other Telegram errors and network failures continue to propagate through the error handler; unsuccessful batches retain their existing retry behavior.
