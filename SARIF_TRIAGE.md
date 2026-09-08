# `PyInconsistentReturnsInspection` triage

Source: `/home/yacha/sarif-priority-findings.json`, reviewed against the working tree on 2026-09-09.

## Result

- Findings reviewed: 78
- Real defects fixed: 2
- Findings excluded as false positives or intentional contracts: 75
- Stale findings (source path no longer exists): 1
- Focused behavior changes: status-change handlers now return the reply produced after a successful
  status mutation, matching their existing early-return paths and preserving the result through
  `handle()`.

## Fixed findings (2)

These findings identified a real inconsistent return contract. `change_status()` returned the
Telegram reply for validation/no-op branches but implicitly returned `None` after a successful
mutation. Since `handle()` forwards `change_status()`'s result, callers observed different result
types for the same operation. Both successful paths now return the reply, covered by focused unit
tests in `tests/test_status_handler.py`.

- `sophie_bot/modules/utils_/status_handler.py`: 75 — `StatusHandlerABC.change_status`
- `sophie_bot/modules/utils_/status_handler.py`: 131 — `StatusIntHandlerABC.change_status`

The remaining findings are excluded because aiogram handlers and middleware use bare returns to stop
processing, while other branches return Telegram API results for convenience, or because the
function's concrete contract is already satisfied. Replacing those sentinels with a value would
change or obscure framework control flow. Functions with concrete value contracts were checked for
reachable fallthrough and all return paths satisfy their declared contract.

## Excluded findings

### Intentional aiogram handler and middleware sentinels (49)

These handlers intentionally return without a value when the update is not applicable, a permission
check fails, a Telegram object is absent, or an action was already handled. Other branches return a
reply/edit result. The return value is not the handler's domain result.

- `sophie_bot/middlewares/sentry_tracing.py`: 48
- `sophie_bot/modules/ai/handlers/research.py`: 114
- `sophie_bot/modules/ai/middlewares/ai_status.py`: 38
- `sophie_bot/modules/antiflood/handlers/antiflood_info.py`: 88
- `sophie_bot/modules/connections/handlers/start_connect.py`: 31, 35, 44, 54
- `sophie_bot/modules/filters/handlers/filter_edit.py`: 50
- `sophie_bot/modules/greetings/handlers/set_join_request.py`: 76
- `sophie_bot/modules/greetings/handlers/status_greetings.py`: 68
- `sophie_bot/modules/notes/handlers/delete.py`: 30, 53
- `sophie_bot/modules/notes/handlers/delete_all.py`: 82
- `sophie_bot/modules/notes/handlers/get.py`: 46, 110
- `sophie_bot/modules/notes/handlers/pmnotes_handler.py`: 48
- `sophie_bot/modules/promotes/handlers/demote.py`: 71, 84
- `sophie_bot/modules/promotes/handlers/promote.py`: 134, 159
- `sophie_bot/modules/purges/handlers/delete.py`: 32, 47
- `sophie_bot/modules/purges/handlers/purge.py`: 34, 73
- `sophie_bot/modules/restrictions/handlers/base.py`: 152
- `sophie_bot/modules/rules/handlers/get.py`: 36
- `sophie_bot/modules/rules/handlers/legacy_button.py`: 24, 38
- `sophie_bot/modules/rules/handlers/set.py`: 42
- `sophie_bot/modules/troubleshooters/handlers/admincache.py`: 29
- `sophie_bot/modules/troubleshooters/handlers/cancel_callback.py`: 37, 55, 67, 75
- `sophie_bot/modules/warns/handlers/callback.py`: 69, 76, 84, 112, 117
- `sophie_bot/modules/warns/handlers/warns_group.py`: 43, 48
- `sophie_bot/modules/welcomesecurity/handlers/captcha_confirm.py`: 62, 71
- `sophie_bot/modules/welcomesecurity/handlers/legacy_button.py`: 75, 79
- `sophie_bot/modules/welcomesecurity/handlers/set_security_message.py`: 77

### Concrete contracts already satisfied (26)

The reported line is the only value-returning branch, or the apparent missing branch is guarded by
an exception/early return. Optional return values are also intentional where the annotation includes
`None`.

- `sophie_bot/db/models/beta.py`: 44
- `sophie_bot/db/models/chat.py`: 100, 109
- `sophie_bot/modules/ai/agent_tools/kagi_search.py`: 56
- `sophie_bot/modules/ai/agent_tools/notes.py`: 23, 35
- `sophie_bot/modules/ai/agent_tools/research.py`: 17
- `sophie_bot/modules/ai/agent_tools/sophie_help.py`: 50
- `sophie_bot/modules/ai/agent_tools/sophie_inspect.py`: 16
- `sophie_bot/modules/ai/agent_tools/tinyfish_search.py`: 52
- `sophie_bot/modules/ai/magic_handlers/modern_action.py`: 90
- `sophie_bot/modules/ai/utils/ai_chatbot_reply.py`: 267
- `sophie_bot/modules/ai/utils/ai_errors.py`: 188
- `sophie_bot/modules/ai/utils/ai_run.py`: 725
- `sophie_bot/modules/federations/middlewares/check_fban.py`: 94
- `sophie_bot/modules/federations/services/ban.py`: 258
- `sophie_bot/modules/filters/handlers/filter_del.py`: 67
- `sophie_bot/modules/op/handlers/op_task.py`: 39
- `sophie_bot/modules/restrictions/actions/base.py`: 121, 149
- `sophie_bot/modules/restrictions/actions/kick.py`: 30, 50
- `sophie_bot/modules/communities/services/ban.py`: 155
- `sophie_bot/modules/warns/magic_handlers/modern_action.py`: 66, 72
- `sophie_bot/services/rest.py`: 51
- `sophie_bot/services/sentry_metrics.py`: 94
- `sophie_bot/utils/i18n.py`: 57

### Stale SARIF path (1)

- `sophie_bot/modules/welcomesecurity/utils_/captcha_done.py`: 48 — the source file is absent from
  the working tree, so this cannot be a current defect.
