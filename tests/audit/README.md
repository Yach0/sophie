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

Each scenario asserts one source delivery and one cache write for the current
Telegram identity. Cache read observations and provider occurrences are:

| Scenario | Current identity per cache read | Provider calls | Current input per call |
| --- | --- | --- | --- |
| Lexical filter | 1 | reply | 1 |
| AI filter | 1 | matching, reply | 1, 1 |
| Proactive batch | 1, 1 | decision, answer | 1, 1 |
| `/ai` | 0 | command | 1 |

The proactive batch naturally accumulates 15 messages; both cache reads retain
all 15 identities, and both providers see each once. The command is cached after
its provider call. Filter replies retain their configured system prompt, while
matching, proactive and command providers retain their respective system prompts
or dynamic instructions.

Provider text has no general Telegram identity field. These controlled scenarios
correlate complete rendered lines with source and cache metadata, and independently
count a sentinel. Distinct same-text identities, reply quotation, retries and
system/non-user/tool-part preservation are covered by the boundary unit suite.
The lifecycle fixture does not replace handlers, routing gates, history builders
or cache writers and does not write evidence files. Concurrent updates, media,
tool execution and paid-provider serialization are outside its scope.
