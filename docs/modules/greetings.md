## Service message cleanup

When clean-service mode is enabled, Sophie attempts to delete leave-service messages. An unavailable message or an expected Telegram deletion restriction does not interrupt leave-event processing. Unexpected Telegram failures still propagate.
