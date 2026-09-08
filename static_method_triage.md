# PyMethodMayBeStaticInspection triage

Source: `/home/yacha/.hermes/cache/documents/doc_5ad27c08200e_3Pjr1_p5l0d_b7dc9491_33fc_4bc2_979b_875d07c540ef_qodana_sarif.json`

The SARIF contains 55 findings. Two high-confidence private serialization helpers were
converted to `@staticmethod`; the remaining 53 were intentionally left unchanged because
they are framework callbacks, parser/provider interfaces, handlers, middleware, schedules,
Beanie-adjacent/public APIs, or otherwise lack concrete proof that static dispatch is
appropriate.

| # | Finding | Disposition | Reason |
|---:|---|---|---|
| 1 | `sophie_bot/services/rest.py:42` `dispatch` | Exclude | REST middleware callback; framework override. |
| 2 | `sophie_bot/args/chats.py:12` `check_type` | Exclude | ASS argument-type override. |
| 3 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/connect_button.py:10` `needed_type` | Exclude | Button argument metadata interface override. |
| 4 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/url_button.py:21` `examples` | Exclude | Button argument metadata interface override. |
| 5 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/connect_button.py:13` `examples` | Exclude | Button argument metadata interface override. |
| 6 | `sophie_bot/modules/utils_/action_config_wizard/wizard.py:245` `_cancel` | Exclude | Wizard callback workflow helper; retain bound/private dispatch semantics. |
| 7 | `sophie_bot/args/lock_type.py:22` `value` | Exclude | ASS parser API override. |
| 8 | `sophie_bot/filters/command_start.py:67` `_encode_value` | Include | Private pure serializer; no instance state or overrides found. Converted. |
| 9 | `sophie_bot/modules/utils_/action_config_wizard/wizard.py:271` `_dump_value` | Include | Private pure Pydantic serializer; no instance state or overrides found. Converted. |
| 10 | `sophie_bot/modules/federations/args/fed_id.py:49` `needed_type` | Exclude | ASS argument metadata interface override. |
| 11 | `sophie_bot/modules/federations/args/fed_id.py:21` `check` | Exclude | ASS parser API override. |
| 12 | `sophie_bot/modules/federations/args/fed_id.py:52` `unparse` | Exclude | ASS parser serialization API override. |
| 13 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/note_button.py:15` `examples` | Exclude | Button argument metadata interface override. |
| 14 | `sophie_bot/middlewares/admincache.py:101` `_get_oldest_admin` | Exclude | Telegram middleware implementation. |
| 15 | `sophie_bot/middlewares/beta.py:75` `get_data` | Exclude | Middleware payload hook; retain framework/instance dispatch. |
| 16 | `sophie_bot/modules/ai/utils/message_history.py:234` `_format_context_line` | Exclude | False positive: reads `self.services`. |
| 17 | `sophie_bot/modules/federations/schedules/process_exports.py:141` `_build_caption` | Exclude | Scheduler service helper; schedule scope explicitly excluded. |
| 18 | `sophie_bot/middlewares/beta.py:42` `is_beta` | Exclude | Middleware decision path; bound method is part of runtime flow. |
| 19 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/parse_arg.py:31` `needed_type` | Exclude | ASS argument metadata interface. |
| 20 | `sophie_bot/args/lock_type.py:18` `check` | Exclude | ASS parser API override. |
| 21 | `sophie_bot/metrics/middleware.py:107` `_get_handler_name` | Exclude | Middleware helper used in callback instrumentation. |
| 22 | `sophie_bot/modules/ai/utils/moderation/providers/mistral.py:51` `classify` | Exclude | Moderation provider contract implementation. |
| 23 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/captcha_button.py:10` `needed_type` | Exclude | Button argument metadata interface override. |
| 24 | `sophie_bot/modules/federations/schedules/process_exports.py:129` `_extract_chat_iid` | Exclude | Scheduler service helper; schedule scope explicitly excluded. |
| 25 | `sophie_bot/utils/logger.py:16` `filter` | Exclude | `logging.Filter` callback override. |
| 26 | `sophie_bot/args/lock_type.py:40` `needed_type` | Exclude | ASS argument metadata interface override. |
| 27 | `sophie_bot/modules/notes/magic_handlers/descriptions_scheduler.py:16` `handle` | Exclude | Handler callback. |
| 28 | `sophie_bot/services/rest.py:58` `dispatch` | Exclude | REST middleware callback; framework override. |
| 29 | `sophie_bot/services/rest.py:117` `dispatch` | Exclude | REST middleware callback; framework override. |
| 30 | `sophie_bot/modules/__init__.py:106` `_api_routers` | Exclude | Module registry/API assembly helper; public runtime composition contract. |
| 31 | `sophie_bot/modules/communities/middlewares/check_cban.py:22` `is_cbanned` | Exclude | Telegram middleware method. |
| 32 | `sophie_bot/modules/ai/utils/moderation/providers/openai.py:87` `classify` | Exclude | Moderation provider contract implementation. |
| 33 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/rules_button.py:10` `needed_type` | Exclude | Button argument metadata interface override. |
| 34 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/sophie_dm_button.py:13` `examples` | Exclude | Button argument metadata interface override. |
| 35 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/sophie_dm_button.py:10` `needed_type` | Exclude | Button argument metadata interface override. |
| 36 | `sophie_bot/modules/utils_/action_config_wizard/wizard.py:265` `_action` | Exclude | Wizard workflow helper; handler-like callback flow. |
| 37 | `sophie_bot/utils/i18n.py:80` `locale_display` | Exclude | Public i18n instance API and subclass method. |
| 38 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/url_button.py:18` `needed_type` | Exclude | Button argument metadata interface override. |
| 39 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/note_button.py:12` `needed_type` | Exclude | Button argument metadata interface override. |
| 40 | `sophie_bot/middlewares/spam_detection.py:35` `_check_spam` | Exclude | Middleware processing helper. |
| 41 | `sophie_bot/modules/notes/schedules/generate_embeddings.py:13` `process_chat` | Exclude | Scheduler callback/helper. |
| 42 | `sophie_bot/modules/federations/args/fed_id.py:38` `parse` | Exclude | ASS parser API override and Beanie lookup. |
| 43 | `sophie_bot/modules/antiflood/middlewares/enforcer.py:88` `_execute_action` | Exclude | Middleware action execution helper; uses `self.services`. |
| 44 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/rules_button.py:13` `examples` | Exclude | Button argument metadata interface override. |
| 45 | `sophie_bot/modules/federations/handlers/transfer.py:117` `_parse_user_id` | Exclude | Handler helper; handler scope explicitly excluded. |
| 46 | `sophie_bot/modules/federations/schedules/process_exports.py:151` `_update_task_status` | Exclude | Scheduler persistence helper. |
| 47 | `sophie_bot/args/users.py:73` `needed_type` | Exclude | ASS argument metadata interface override. |
| 48 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/captcha_button.py:13` `examples` | Exclude | Button argument metadata interface override. |
| 49 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/delete_button.py:14` `examples` | Exclude | Button argument metadata interface override. |
| 50 | `sophie_bot/modules/federations/middlewares/check_fban.py:19` `is_fbanned` | Exclude | Telegram middleware method. |
| 51 | `sophie_bot/modules/notes/utils/buttons_processor/ass_types/delete_button.py:11` `needed_type` | Exclude | Button argument metadata interface override. |
| 52 | `sophie_bot/modules/notes/handlers/save.py:102` `save` | Exclude | Handler method and persistence workflow. |
| 53 | `sophie_bot/modules/__init__.py:80` `_modules` | Exclude | Module registry/runtime composition contract. |
| 54 | `sophie_bot/args/chats.py:54` `needed_type` | Exclude | ASS argument metadata interface override. |
| 55 | `sophie_bot/modules/antiflood/middlewares/enforcer.py:109` `_get_action_text` | Exclude | Middleware helper. |

## Totals

- Included and changed: **2**
- Excluded and unchanged: **53**
- Total findings: **55**

The SARIF line locations for findings 30, 36, and 53 do not match the current source
method layout exactly; they are retained verbatim as SARIF findings and dispositioned by
the reported symbol/context.
