# Attributed AI memory

The design follows [SpeakerMem-R1, section 3 and appendices A, B, and I](https://arxiv.org/html/2609.26780v2#S3).
It implements source-backed storage and retrieval. It does not train a writer or reproduce the paper's benchmark results.

## Enable and roll out

All new boolean flags default to **false**. The writer-model setting is a string, not an enable switch. Existing memory tools, prompt injection, reply decisions, and streaming stay
unchanged when the flags are off. Each flag supports the existing global/chat override and cohort rollout system.

| Flag | Effect when enabled |
| --- | --- |
| `ai_memory_capture` | Retain original human messages in entertainment mode, including silent conversations. |
| `ai_memory_writer_model` | Select the independent writer model; default `gpt-6.1-terra` through OpenRouter. |
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

`ai_memory_retention_days` defaults to `30` and is clamped to 1–365 days. Attribution, source checks, scope validation,
chat boundaries, retention, and deletion propagation are mandatory integrity rules whenever the new store is used.
They cannot be disabled to admit invalid records.

Use chat overrides for a small initial cohort. Enable capture alone first: it does not change the answer path.
Then enable the writer with personal extraction, leaving retrieval disabled, to assess extraction in the background.
Enable group extraction, updates, and shared decisions separately after checking their outputs. Next enable additive
retrieval and compare the same replay questions. Enable branch constraints, member coverage, history, participation,
and the answer recheck separately. Keep legacy replacement off until the new path is validated.
Enable controls before giving people access to retained evidence, and leave controls enabled when disabling capture
or retrieval so that people can still inspect and delete existing records.

The writer uses the `memory_writer` purpose and its own `ai_memory_writer_model` setting, with standard provider fallback.
Its default is `gpt-6.1-terra` through OpenRouter; it does not inherit the summary model override.
The catalog migration adds that model and its entertainment-mode writer role. It preserves existing model/provider
settings. If OpenRouter is absent, it adds an empty provider entry; configure its key before enabling the writer.
The migration has a tested rollback and does not enable any memory flag. It charges the chat's chatbot quota
and checks quota before each batch. Disabling a writer category stops new extraction in that category; it does not hide
already stored facts. Messages accepted as NOOP are not automatically replayed when another category is enabled later.
Capture, extraction, retrieval, and participation can each be disabled independently. Retention cleanup still runs.
Leaving entertainment mode clears evidence and facts. Chat reset also clears existing new-store data. Both operations
preserve opt-outs. No new memory document is created by normal mode changes or reset when the new features are unused.

The new `ai_evidence_memory` collection is registered with Beanie. Startup creates its unique chat index.
No old records need a schema migration. Legacy text memories have no evidence and are not copied into the new store.
Legacy prompt memory and `write_memory`/`forget_memory` remain available during additive retrieval.
Only `ai_memory_replace_legacy`, together with retrieval, removes them.

## Storage and writer

Each chat has one revision-protected document. It holds two bounded tracks:

1. Original human text or captions, speaker ID, name/username snapshots, sent time, Telegram topic, reply parent, and reply root.
2. Derived records with a stable ID, speaker, subjects, person/group scope, layer, claim kind, event name,
   observed time, record time, evidence message IDs, branch IDs, and the previous version ID.

Layers distinguish personal facts/preferences, observations about other people, shared events, and group norms.
Kinds distinguish facts, preferences, proposals, commitments, decisions, questions, jokes, and disputes.
Reports about a person do not establish that person's position. A decision or norm must cite at least two speakers.
This structural check does not establish semantic agreement. The writer must classify dissent as disputed;
retrieval explicitly tells the answerer that silence does not prove consensus.

Capture runs in the message middleware after moderation, whether or not Sophie replies.
Anonymous chat senders and bots are excluded to avoid assigning their statements to a user.
`/ai_memory` and `/ai_reset` are not captured as ordinary conversation. A correction stores its command as evidence.
The bot receives new message events; Telegram does not supply ordinary deleted-message events to bots.
Use the memory controls to delete retained copies. Original text may contain authored names; modern context aliases
identity fields but does not rewrite the authored text.

The scheduler checks every minute. It reads at most 40 pending messages, relevant current fact heads, their source
exchanges, and a speaker roster. It requests typed ADD/UPDATE/NOOP actions. Validation rejects missing sources,
unknown actors, cross-topic evidence, invalid scope/layer pairs, and changes to another speaker's fact chain.
UPDATE appends a new version with the same speaker, subjects, layer, scope, topic, and event identity.
Several reply roots can support one explicitly named event. Event matching remains a writer judgment.
All actions in a batch pass validation before any fact is changed. After three invalid outputs, that batch's messages
remain available as raw evidence but no longer block extraction of later messages. Failed provider requests remain pending.

Beanie revisions and a unique chat index prevent lost concurrent writes. Sources are marked processed only with the
accepted batch, so concurrent scheduler instances cannot apply it twice. Privacy changes rotate a generation token;
a writer result from an earlier generation cannot restore deleted records.

Both tracks expire together. Reads enforce the cutoff immediately; the scheduler removes expired data from MongoDB,
even with capture flags disabled. The store keeps at most 1,000 sources and 1,000 fact versions per chat. Size eviction
also removes facts that lost any evidence. Thus old history is available only within retention and capacity limits.

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
every selected fact keeps all its evidence. One reply-parent expansion supplies local context. Structured identities use
the current session's speaker aliases in modern context. Cross-chat retrieval is not supported.

Participation decisions get relevant norms, preferences, open questions, and source exchanges. The decision prompt asks
for silence when someone has answered. Just before sending a generated proactive answer, Sophie checks the latest cache
for a reply to the target. Any such reply suppresses the answer conservatively. This check detects explicit reply links;
semantic answers without a reply link still depend on the decision model and recent history.

## Personal controls

These commands require `ai_memory_controls` and work in the current connected chat. They require no admin rights:

- `/ai_memory show [PAGE]`: inspect facts where you are the speaker or a subject, including IDs, versions, timestamps,
  and evidence references. Each page holds three items, with separate ID, text, speaker, source, version, and time fields.
  `/ai_memory show 2` shows the next page; `/ai_memory help` lists examples.
- `/ai_memory sources [PAGE]`: inspect your retained original text. Long messages span pages.
- `/ai_memory correct FACT_ID TEXT`: append a correction to your current personal statement. Maximum 600 characters.
  Send corrections in the original chat and topic. You cannot rewrite someone else's observation or a group decision. You can state your own position in chat.
- `/ai_memory forget`: clear retained information without disabling future capture.
- `/ai_memory off`: forget and disable capture of your messages and derived facts about you in this chat.
- `/ai_memory on`: allow future capture. Deleted history is not restored.

Forget and off clear **all chat evidence, facts, legacy text memory, summaries, cached messages, tool exchanges, and native
context sessions**. This deliberate coarse deletion removes indirect copies in other members' text. It can remove other
members' useful memories too. It does not delete Telegram messages, notes, provider logs, or backups. Off prevents new
structured records targeting an excluded user; other people's future authored text may still mention them.
Generation and context-epoch checks prevent stale writer or cache operations from restoring earlier data.
With retrieval or controls enabled, replies finish before delivery instead of streaming answer text, so a privacy reset
can discard an obsolete result. Summary generation also checks the privacy epoch before using its output.
Deletion cannot recall data already sent to Telegram or an AI provider.

## Evaluation before training

Run `tests/test_ai_evidence_memory.py` and `tests/e2e/test_ai_memory_e2e.py` for deterministic replay and command checks.
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
