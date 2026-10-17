# Proactive AI reactions

Incoming messages enter the history cache after all module routers finish processing them. Filter replies and direct AI requests therefore use the current input without first caching and then excluding it.

Proactive decisions run after caching and can select an older cached message. Proactive answer history excludes that exact target before adding it as the answer input; other messages, including messages with identical text, remain available. A target can become unavailable before Sophie sends its reaction.

If Telegram returns `Bad Request: message to react not found`, Sophie logs the unavailable target and records the `reaction_skipped` proactive metric with `reason=target_missing`. It skips that reaction and continues the remaining actions so the batch can finish and clear its tracked messages. It records `reaction_sent` only after a successful reaction.

Other Telegram errors and network failures continue to propagate through the error handler; unsuccessful batches retain their existing retry behavior.
