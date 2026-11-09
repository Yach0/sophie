# AI request recovery

AI agent runs use Pydantic AI 2.64 or later. Each call to `run_ai_text`,
`run_ai_structured`, or `run_ai_stream` starts one agent run. Recovery never
starts that run again.

## Retry and fallback

The provider SDK owns transient request retries. The default `max_retries` is
four, which permits five attempts at one model request. An explicit model,
agent, or call setting takes precedence. Do not add a transport retry loop on
these requests: that would multiply the retry count.

Pydantic AI's `ModelFallback` capability handles the model chain. Candidate
selection still applies image support, the chain size limit, and the last-resort
model. With `sticky=True`, later steps can use the model that answered after a
fallback. Completed tool calls and results stay in the same run.

Usage limits and provider configuration errors stop recovery. Tool failures and
output-validator failures also propagate; they do not restart the agent. Native
validation retries remain available through Pydantic AI's tool and output retry
budgets. A tool that changes state must not request a validation retry after the
change unless the tool can safely execute again.

For non-streamed requests, blank text can trigger native response-based fallback.
A response that contains tool calls is not rejected as blank text. A blank answer
from the last candidate is left to native output handling.

## Candidate preparation

Operators can include `^llm_debug` in a chatbot prompt to receive `llm_debug.txt`
as a UTF-8 document replying to the original message in the same chat/topic.
The caption is brief; diagnostics are not shortened to Telegram's text limit.
The attachment includes the built history/current prompt, final prepared request
with role order, instructions and tool definitions, effective model settings,
usage limits, context budgets, and relevant flag values. Context has already
been trimmed by the normal pipeline; discarded content and earlier attempt
settings are not retained. Credentials and authorization data are redacted
before serialization. The existing explicit caller debug override is unchanged.
Embedded JSON strings are decoded for redaction, including escaped credential
names and values, then re-encoded. Usage uses JSON-aware serialization so Decimal
costs retain their precision. After `AIRequestFailed`, the attachment retains
built context and labels execution data unavailable: effective model/provider,
settings, usage, and prepared request are null. The model plan and default service
tier remain available as configured defaults, not evidence of what executed.

A `prepare_model_request` hook applies each candidate's service tier, cache
settings, and output token limit. Modern context is projected for each attempt
without changing stored session history. Projection reserves space for all live
messages from the current run, including completed tool results. It can remove
old session history, but it never removes the current tool exchange.

Candidates that cannot fit the current request are skipped before a provider
call. If no later candidate can fit it, the request fails with a budget error.

## Streaming and reporting

Native fallback can recover a stream failure before its first event. After
stream output starts, a failure reaches the caller. The agent is not restarted.
Response-based fallback is disabled for streamed requests, including text calls
with an event stream handler.

The `on_retry` callback reports model fallback. Provider SDK retries occur below
the agent and do not call this callback. Request metrics name the model attempted;
token metrics use the usage recorded on each response and rejected attempt.
Terminal fallback errors retain the native exception group and are reported
against the last candidate.

The separate moderation request helper still uses its own retry policy. It does
not execute agent tools.
