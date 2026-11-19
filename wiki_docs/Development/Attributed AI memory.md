# Attributed AI memory

The design follows [SpeakerMem-R1, section 3 and appendices A, B, and I](https://arxiv.org/html/2609.26780v2#S3).
It implements source-backed storage and retrieval. It does not train a writer or reproduce the paper's benchmark results.

## Enable and roll out

All new boolean flags default to **false**. Writer model settings take model names. Existing memory tools, prompt injection, reply decisions, and streaming stay
unchanged when the flags are off. Each flag supports the existing global/chat override and cohort rollout system.

| Flag | Effect when enabled |
| --- | --- |
| `ai_memory_capture` | Mark original human messages in the existing Redis history as memory-eligible in entertainment mode, including silent conversations. |
| `ai_memory_writer_model` | Select the review model for tiered writing. Defaults to `gpt-6.1-terra` through OpenRouter. |
| `ai_memory_writer_tiered` | Enable a separate review stage. Defaults off. |
| `ai_memory_writer_routine_model` | Select the single-pass writer or the first stage of tiered writing. Defaults to `openai/gpt-6-luna` through OpenRouter. |
| `ai_memory_writer` | Run batch extraction independently of replies. Requires at least one extraction scope below. |
| `ai_memory_personal` | Allow the writer to create personal facts and observations about other people. |
| `ai_memory_group` | Allow the writer to create shared events and group records. |
| `ai_memory_updates` | Allow the writer to append corrected or changed fact versions. |
| `ai_memory_shared_decisions` | Allow writer decisions and norms; requires evidence from at least two speakers. |
| `ai_memory_retrieval` | Add query-specific evidence to answers and expose `search_memory`. |
| `ai_memory_branches` | Apply reply-root constraints during retrieval; topic and event coordinates remain explicit. |
| `ai_memory_member_coverage` | Reserve evidence slots for requested or lexically relevant people. |
| `ai_memory_history` | Allow historical timestamps and full version chains in `search_memory`. |
| `ai_memory_participation` | Add norms, preferences, questions, and evidence to the participation decision. |
| `ai_memory_recheck_answers` | Suppress a proactive answer when a newer explicit reply already exists. |
| `ai_memory_controls` | Enable personal inspect/correct/forget/off/on commands and privacy fences. |
| `ai_memory_replace_legacy` | With retrieval enabled, remove legacy memory tools and legacy prompt memory. |

With retrieval enabled in a mode that permits memory, `ai_memory_request_limit` defaults to `8` model requests
and `ai_memory_tool_calls_limit` defaults to `12` tool calls per answer. Each model request is one agent turn,
including the final answer request. These limits give the agent room to search several people or events before
answering. Both numeric flags support per-chat overrides. Zero or negative values fall back to their defaults.
The existing response-token limit still applies. Background capture, extraction, or participation context alone
does not select these larger limits. Legacy requests keep `ai_chatbot_request_limit` (default `4`) and
`ai_chatbot_tool_calls_limit` (default `6`), including their existing overrides.

`ai_memory_retention_days` controls extracted facts only; it defaults to `30` and is clamped to 1–365 days. Original messages always use the shared Redis history’s 48-hour retention. Attribution, source checks, scope validation,
chat boundaries, retention, and deletion propagation are mandatory integrity rules whenever the new store is used.
They cannot be disabled to admit invalid records.

Use chat overrides for a small initial cohort. Enable capture alone first: it does not change the answer path.
Then enable the writer with personal extraction, leaving retrieval disabled, to assess extraction in the background.
Enable group extraction, updates, and shared decisions separately after checking their outputs. Next enable additive
retrieval and compare the same replay questions. Enable branch constraints, member coverage, history, participation,
and the answer recheck separately. Keep legacy replacement off until the new path is validated.
Enable controls before giving people access to retained evidence, and leave controls enabled when disabling capture
or retrieval so that people can still inspect and delete existing records.

The writer defaults to Luna through OpenRouter and extracts all enabled memory categories in one pass. The catalog
migration adds Terra and its entertainment-mode writer role for use in tiered review. It preserves existing model/provider
settings. If OpenRouter is absent, it adds an empty provider entry. Configure its key before enabling the writer.
The migration has a tested rollback and does not enable any memory flag. The writer uses the chat's existing shared AI
quota and reports usage separately as **AI Memory**. Disabling a writer category stops new extraction in that category; it does not hide
already stored facts. Messages accepted as NOOP are not automatically replayed when another category is enabled later.
Capture, extraction, retrieval, and participation can each be disabled independently. Retention cleanup still runs.
Leaving entertainment mode clears evidence and facts. Chat reset also clears existing new-store data. Both operations
preserve opt-outs. No new memory document is created by normal mode changes or reset when the new features are unused.

The `20261117_190031_unify_memory_sources_in_redis` migration copies unexpired `state.sources` into the existing Redis history before removing that field from MongoDB. Existing cache metadata, extracted facts, opt-outs, writer progress, and generation fences are preserved. Older originals are discarded under the 48-hour policy. The migration is idempotent and stops before deleting originals if Redis writes fail. Stop the bot and scheduler, run `make migrate_up`, then restart both on the new build; old processes must not write MongoDB sources after migration. Rollback reconstructs only still-retained Redis sources; expired originals are deliberately not backed up.

The new `ai_evidence_memory` collection is registered with Beanie. Startup creates its unique chat index.
No legacy text memories need a schema migration. Legacy text memories have no evidence and are not copied into the new store.
Legacy prompt memory and `write_memory`/`forget_memory` remain available during additive retrieval.
Only `ai_memory_replace_legacy`, together with retrieval, removes them.

## Storage and writer

Each chat has one revision-protected MongoDB document for extracted facts, opt-outs, generation fences, and writer progress IDs. Original message bodies are stored only in the existing `messages:<chat_tid>` Redis sorted set. The working memory view combines:

1. Original human text or captions, speaker ID, name/username snapshots, sent time, Telegram topic, reply parent, and reply root.
2. Derived records with a stable ID, speaker, subjects, person/group scope, layer, claim kind, event name,
   observed time, record time, evidence message IDs, branch IDs, and the previous version ID.

Layers distinguish personal facts/preferences, observations about other people, shared events, and group norms.
Kinds distinguish facts, preferences, proposals, commitments, decisions, questions, jokes, and disputes.
Reports about a person do not establish that person's position. A decision or norm must cite at least two speakers.
This structural check does not establish semantic agreement. The writer must classify dissent as disputed;
retrieval explicitly tells the answerer that silence does not prove consensus.

Capture runs in the message middleware after moderation, whether or not Sophie replies. The ordinary history write includes memory eligibility, speaker-name, and optional branch metadata; there is no second message body or per-message MongoDB write. Bots, anonymous senders, excluded users, and messages captured before the flag was enabled are filtered out of memory reads.
Anonymous chat senders and bots are excluded to avoid assigning their statements to a user.
AI memory commands and `/ai_reset` are not captured as ordinary conversation. A correction stores its command as evidence.
The bot receives new message events; Telegram does not supply ordinary deleted-message events to bots.
Use the memory controls to delete retained copies. Original text may contain authored names; modern context aliases
identity fields but does not rewrite the authored text.

The scheduler checks every 10 minutes and calls the writer only when at least 10 eligible unprocessed messages are available. It processes at most 60 pending messages per chat per run, relevant current fact heads, their source
exchanges, and a speaker roster. It requests typed ADD/UPDATE/NOOP actions. Validation rejects missing sources,
unknown actors, cross-topic evidence, invalid scope/layer pairs, and changes to another speaker's fact chain.
UPDATE appends a new version with the same speaker, subjects, layer, scope, topic, and event identity.
Several reply roots can support one explicitly named event. Event matching remains a writer judgment.
All actions in a batch pass validation before any fact is changed. After three invalid outputs, that batch's messages
remain available as raw evidence but no longer block extraction of later messages. Failed provider requests remain pending. Expected provider, configuration, quota, and storage errors are logged per chat;
the scheduler continues to later chats. Redis discovery failures preserve already discovered chats; malformed cache keys are skipped, and per-key cleanup failures do not abort the run. Shared history writes use Redis WATCH transactions and context epochs so in-flight messages cannot restore a reset history. Unexpected programming errors still reach the scheduler error handler.

Beanie revisions and a unique chat index prevent lost concurrent writes. Sources are marked processed only with the
accepted batch, so concurrent scheduler instances cannot apply it twice. Privacy changes rotate a generation token;
a writer result from an earlier generation cannot restore deleted records.

Original messages expire after 48 hours in Redis. Reads enforce that window, writes prune expired entries and refresh the active cache TTL, and the writer also cleans expired rows every 10 minutes. Validated facts remain in MongoDB independently of the original text, until their configured fact retention expires or they are explicitly deleted. The fact store keeps at most 1,000 versions per chat. Evidence message IDs remain on facts; retrieval reports unavailable originals in `expired_source_ids` and must not invent quotations. Writer progress stores only message IDs and attempt counters and is bounded by the retained cache.

### Writer routing and cost evaluation

Enable `ai_memory_writer_tiered` to use Luna for clear first-person facts and preferences and Terra for reviewing
selected messages about updates, reports about other people, or group events.
The app also sends proposed changes and same-event conflicts to review. A valid empty routine result completes the batch
without a review call. Chats with only group extraction enabled go directly to review.

Each chat has one expiring writer claim across scheduler instances. The writer skips exact repeats within the
same speaker, topic, reply branch and parent. Independent speakers and changes in the conversation keep their own evidence.
In tiered mode, partial batches can run after 30 minutes. Prompts use request-local IDs and an estimated 8,000-token data budget, including
selected current facts and supporting messages. If one message alone exceeds the batch budget, the model sees its
opening and closing excerpts; the full source remains in Redis. The model can cite only IDs in its own prompt.
Storage still uses the original IDs and validates attribution, version chains, and retention.

Each stage uses one configured model with at most two model responses and a 3,000-output-token budget. The writer checks
quota again before a review request. Responses are charged to **AI Memory** even when their output is later rejected.
Sentry includes stage, model, token totals, retry responses, and provider-reported cost when present, plus accepted,
rejected, and stale batches, reviewed messages, skipped duplicates, and saved memories. A valid result also requires the
original generation and evidence at commit.

Run `uv run python -m tools.ai_memory_replay` to validate the bundled synthetic cases. With an OpenRouter API key in
`OPENROUTER_API_KEY`, add `--run` to compare Terra alone, Luna alone, and tiered writing. Use
`--against z-ai/glm-5.3-flash` for a fourth comparison or `--cases` for approved labeled examples. The replay reports
correct, missed, and extra memories by case, validation failures, model usage, and provider-reported cost per correct memory when all
responses report cost. Review the results on representative chats before expanding the rollout.

## Retrieval and participation

Every answer request retrieves records for its text and Telegram topic. Search is a bounded lexical baseline,
not an embedding index. `search_memory` can search by up to 16 speaker references, reporter, event, historical timestamp, and full history.
The history flag controls historical queries. Use several calls for more than 16 requested people.
Current queries select version heads. As-of queries use both record time and source time so that later knowledge cannot
change an earlier answer. Full-history queries return the retained versions. Event and branch filters keep exchanges
separate. Requested people get reserved evidence slots, including people with few messages. Missing direct positions
remain explicitly unknown even when another person has made a claim about them.

When member coverage is enabled, up to 16 relevant authors get reserved slots. Larger rosters need separate queries.
The two budgets are independent: up to 16 facts and 16 raw matches. Fact selection also has a 48-source provenance budget;
every selected fact keeps all still-retained evidence. Facts whose original text expired remain available with explicit missing-source IDs. One reply-parent expansion supplies local context. Structured identities use
the current session's speaker aliases in modern context. Cross-chat retrieval is not supported.

Participation decisions get relevant norms, preferences, open questions, and source exchanges. The decision prompt asks
for silence when someone has answered. Just before sending a generated proactive answer, Sophie checks the latest cache
for a reply to the target. Any such reply suppresses the answer conservatively. This check detects explicit reply links;
semantic answers without a reply link still depend on the decision model and recent history.

## Personal controls

The **Memory** section under **Sophie AI** in `/help` lists these commands when `ai_memory_controls` is enabled in
the help catalog. The commands work in the current connected chat. Chat admins can clear shared memory with
`/ai_memory_forget` or `/ai_memory_off`. Former subcommands such as `/ai_memory off` show the replacement command
without changing any memory settings.

- `/ai_memory` lists current memories where you are the speaker or a subject, newest first. Use
  `/ai_memory <text>` to search them, or `/ai_memory @username` to view another user's current memories in this chat.
  Reply to a user's message with `/ai_memory` to view their memories without a username. Rich lists display up to
  16 memories per page. The numbered buttons open full memory text. Source references, history, and corrections
  are available from your own view.
- `/ai_memory_history` or **History** lists superseded versions. **Sources** or `/ai_memory_sources` lists your
  original messages from the shared 48-hour Redis history. Tap a number to open the full source message.
- `/ai_memory_help` or **Help** shows command examples. Only the requester can use the list buttons. Each click
  reloads current data, and navigation expires 15 minutes after the command.
- `/ai_memory_correct FACT_ID TEXT`: append a correction to your current personal statement. Maximum 600 characters.
  **Copy correction command** in the memory details copies the command with its ID ready for your replacement text.
  Send corrections in the original chat and topic. You cannot rewrite someone else's observation or a group decision. You can state your own position in chat.
- `/ai_memory_forget` (admins): clear retained information without disabling future capture.
- `/ai_memory_off` (admins): forget and disable capture of your messages and derived facts about you in this chat.
- `/ai_memory_on`: allow future capture. Deleted history is not restored.

Forget and off require the requesting administrator to confirm within five minutes. They clear **all chat evidence,
facts, legacy text memory, summaries, cached messages, tool exchanges, and native
context sessions**. This deliberate coarse deletion removes indirect copies in other members' text. It can remove other
members' useful memories too. It does not delete Telegram messages, notes, provider logs, or backups. Off prevents new
structured records targeting an excluded user; other people's future authored text may still mention them.
Generation and context-epoch checks prevent stale writer or cache operations from restoring earlier data.
With retrieval or controls enabled, replies finish before delivery instead of streaming answer text, so a privacy reset
can discard an obsolete result. Summary generation also checks the privacy epoch before using its output.
Deletion cannot recall data already sent to Telegram or an AI provider.

## Evaluation before training

Run `tests/test_ai_evidence_memory.py`, `tests/test_shared_memory_cache.py`, `tests/test_memory_sources_migration.py`, and `tests/e2e/test_ai_memory_e2e.py` for deterministic replay and command checks.
The cases cover attribution, reports versus direct positions, quiet members, changes over time, overlapping topics,
branch and event filters, invalid consensus evidence, source expiry, personal correction ownership, opt-out, and deletion
while the writer is running. The existing proactive and native-context tests cover the surrounding answer path.
These are architecture tests with controlled writer outputs. They do not measure a live model's extraction quality.

Before expanding the cohort, replay representative busy-group conversations with the configured model. Compare raw-only
and dual-track answers with fixed questions and budgets. Score attribution errors, unsupported consensus, forgotten quiet
members, temporal accuracy, abstention, interruption rate, token cost, and rejected writer actions. Include paraphrases
and several simultaneous events: lexical recall and event naming are known limits of this first implementation.
Use those results to decide whether semantic retrieval or a trained small writer is needed. Keep training separate from
storage, retrieval, and the answerer, as in the paper's writer-quality study.
