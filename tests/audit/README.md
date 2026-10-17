# AI current-message lifecycle regression

Run offline from the repository root using the existing virtualenv:

```bash
env -u PYTHONPATH TESTING=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -p pytest_asyncio.plugin tests/audit/test_ai_duplicate_lifecycle.py tests/test_ai_filter_history_boundary.py -q
```

The lifecycle tests feed real Telegram updates through the assembled dispatcher,
middlewares, handlers, actions, history builders, quota checks and cache writers.
External boundaries use in-memory MongoDB/Redis, captured Telegram responses and
Pydantic AI FunctionModel providers. Socket connections are denied. Sync aiogram
callbacks and the Mongo mock executor execute immediately to avoid worker-thread
scheduling; the unrelated spam classifier returns zero probabilities.

Each scenario asserts one source delivery, one cache write and one persisted cache
entry for the current Telegram identity. Cache reads and provider occurrences are:

| Scenario | Current identity per cache read | Provider calls | Current input per call |
| --- | --- | --- | --- |
| Lexical filter | 0 | reply | 1 |
| AI filter | 0 | matching, reply | 1, 1 |
| Proactive batch | 1, 1 | decision, answer | 1, 1 |
| `/ai` | 0 | command | 1 |
| Earlier `users` router `/id` command | no reads | none | — |

Incoming messages are cached only after all module routing finishes. Filter and
`/ai` requests therefore read history without the current identity; their tests
assert that the current user's single cache write follows every provider call and
precedes dispatcher return. The real `/id` handler runs on the earlier `users`
router, produces its Telegram response without an AI provider call, and still
persists the incoming command once after handling.

The proactive batch naturally accumulates 15 messages before its decision and
answer providers run; both cache reads retain all 15 identities, and both providers
see each once. Only proactive answers exclude their selected cached target from
rendered history, because that target may be an older message and is supplied
separately as the answer input. Filter replies retain their configured system
prompt, while matching, proactive and command providers retain their respective
system prompts or dynamic instructions.

Provider text has no general Telegram identity field. These controlled scenarios
correlate complete rendered lines with source and cache metadata, and independently
count a sentinel. Distinct same-text identities, reply quotation, retries and
system/non-user/tool-part preservation are covered by the boundary unit suite.
The lifecycle fixture does not replace handlers, routing gates, history builders
or cache writers and does not write evidence files. Concurrent updates, media,
tool execution and paid-provider serialization are outside its scope.
