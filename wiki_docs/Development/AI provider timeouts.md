# AI provider timeouts

`AI_REQUEST_TIMEOUT_SECONDS` sets the provider transport timeout for each attempt. It defaults to 15 seconds and must be positive. It replaces the former `AI_TIMEOUT_SECONDS` setting, which limited the whole handler to 120 seconds.

Provider requests use four SDK retries: up to five attempts per model, including the initial request. SDK backoff adds time between attempts. Successful requests stop retrying. Model failover can extend the total duration beyond one model's retry budget.

There is no whole-handler timeout that can cancel the retry budget. Retries repeat the provider request, not the whole agent run, so completed tools are not replayed. Transport timeouts apply to connection and I/O waits, not the total time spent generating an active stream. A timeout after streaming has started is not retried by the SDK; replaying that stream could duplicate output.

Note-search embedding requests use the same timeout and five-attempt SDK budget. If transport errors or retryable HTTP errors exhaust that budget, note retrieval falls back to text search so AI prompt construction can continue. Invalid credentials and other nonretryable API errors still propagate.
