# Attributed AI memory

The design follows [SpeakerMem-R1, section 3 and appendices A, B, and I](https://arxiv.org/html/2609.26780v2#S3).
It implements source-backed storage and retrieval. It does not train a writer or reproduce the paper's benchmark results.

## Enable and roll out

All three flags default to **false**. Each supports global overrides, chat overrides, and cohort rollouts.

| Flag | Effect when enabled |
| --- | --- |
| `ai_memory_writer` | Capture eligible human messages in entertainment mode and extract attributed memories with Luna. |
| `ai_memory_retrieval` | Use attributed evidence in answers and proactive decisions, expose `search_memory`, and replace legacy prompt memory and its tools. |
| `ai_memory_controls` | Enable the browser, privacy controls, and exact-ID deletion. With retrieval enabled, also allow chat requests to add, update, or delete facts. |

Enable the writer first to assess extraction without changing answers. Enable retrieval to use the collected evidence.
Leave controls enabled when pausing the writer or retrieval so members can still inspect and delete existing records.
The policy migration preserves legacy retention overrides outside the feature-flag registry. It enables the combined
writer only where both the old writer and capture settings allowed it. It snapshots any prior rollout for existing
chats. Enable the combined writer explicitly for a former capture-only chat or to start a new rollout.
Stop old bot and scheduler processes, run `make migrate_up`, and then start the new build. Sophie refuses to start
with legacy memory settings when this migration is pending.

The writer uses one Luna pass through OpenRouter, charges the shared AI quota, and reports usage as **AI Memory**.
Retrieval uses up to eight model requests and twelve tool calls per answer. The existing response-token limit still
applies. With retrieval disabled, the normal chatbot request and tool-call settings apply.

Facts expire after 30 days by default. Migrated chats retain their former effective retention policy.
Original messages stay in shared Redis history for up to 48 hours. Retention cleanup
continues with the writer disabled. Leaving entertainment mode or resetting the chat clears evidence and facts and
preserves opt-outs.

The `20261117_190031_unify_memory_sources_in_redis` migration copies unexpired `state.sources` into the existing Redis history before removing that field from MongoDB. Existing cache metadata, extracted facts, opt-outs, writer progress, and generation fences are preserved. Older originals are discarded under the 48-hour policy. The migration is idempotent and stops before deleting originals if Redis writes fail. Stop the bot and scheduler, run `make migrate_up`, then restart both on the new build; old processes must not write MongoDB sources after migration. Rollback reconstructs only still-retained Redis sources; expired originals are deliberately not backed up.

The new `ai_evidence_memory` collection is registered with Beanie. Startup creates its unique chat index.
No legacy text memories need a schema migration. Legacy text memories have no evidence and are not copied into the new store.
Legacy prompt memory and its tools remain available when attributed retrieval is disabled.

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
The AI message cache excludes memory commands and `/ai_reset`, even when a feature or permission check blocks them.
Explicit chatbot additions and updates use the requesting message as evidence.
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

Original messages expire after 48 hours in Redis. Reads enforce that window, writes prune expired entries and refresh the active cache TTL, and the writer also cleans expired rows every 10 minutes. Validated facts remain in MongoDB independently of the original text, until their retention period expires or they are explicitly deleted. The fact store keeps at most 1,000 versions per chat. Evidence message IDs remain on facts. Retrieval lists unavailable originals in `<unavailable_sources>`, so Sophie can tell which sources it can quote. Writer progress stores only message IDs and attempt counters and is bounded by the retained cache.

### Writer routing and cost evaluation

Each chat has one expiring writer claim across scheduler instances. The writer skips exact repeats within the
same speaker, topic, reply branch and parent. Independent speakers and changes in the conversation keep their own evidence.
Prompts use request-local IDs and an estimated 8,000-token data budget, including selected current facts and supporting
messages. If one message alone exceeds the batch budget, the model sees its opening and closing excerpts. The full
source remains in Redis. The model can cite only IDs in its own prompt.

The writer allows at most two model responses per batch and a 3,000-output-token budget. Sentry tracks model usage,
retry responses, provider-reported cost when present, and batch results. A valid result requires the original
generation and evidence at commit.

Run `uv run python -m tools.ai_memory_replay` to validate the bundled synthetic cases. With an OpenRouter API key in
`OPENROUTER_API_KEY`, add `--run` to compare Terra alone, Luna alone, and tiered writing. Use
`--against z-ai/glm-5.3-flash` for a fourth comparison or `--cases` for approved labeled examples. The replay reports
correct, missed, and extra memories by case, validation failures, model usage, and provider-reported cost per correct memory when all
responses report cost. Review the results on representative chats before expanding the rollout.

## Retrieval and participation

Every answer request retrieves records for its text and Telegram topic. Search is a bounded lexical baseline,
not an embedding index. `search_memory` can search by up to 16 speaker references, reporter, event, historical timestamp, and full history.
Use several calls for more than 16 requested people.
Current queries select version heads. As-of queries use both record time and source time so that later knowledge cannot
change an earlier answer. Full-history queries return the retained versions. Event and branch filters keep exchanges
separate. Requested people get reserved evidence slots, including people with few messages. Missing direct positions
remain explicitly unknown even when another person has made a claim about them.

Chatbot context, proactive decisions, and the `search_memory` tool use the same `<memory_evidence>` XML format.
Facts and sources carry their text, IDs, attribution, and timestamps. Fact attributes link to supporting sources and
previous versions when present. Historical queries also include the time a fact was recorded. The XML omits topic
and reply IDs, empty fields, and storage-only metadata.

Up to 16 relevant authors get reserved slots. Larger rosters need separate queries.
The two budgets are independent: up to 16 facts and 16 raw matches. Fact selection also has a 48-source provenance budget;
every selected fact keeps all still-retained evidence. Facts whose original text expired remain available with explicit missing-source IDs. One reply-parent expansion supplies local context. Structured identities use
the current session's speaker aliases in modern context. Cross-chat retrieval is not supported.

Participation decisions get relevant norms, preferences, open questions, and source exchanges. The decision prompt asks
for silence when someone has answered. Just before sending a generated proactive answer, Sophie checks the latest cache
for a reply to the target. Any such reply suppresses the answer conservatively. This check detects explicit reply links;
semantic answers without a reply link still depend on the decision model and recent history.

## Personal controls

The **Memory** section under **Sophie AI** in `/help` lists two commands when `ai_memory_controls` is enabled.

- `/ai_memory` lists your current memories. Add search text, mention a member, or reply to one of their messages to view
  their memories. The numbered buttons open full memory text and let you copy a fact ID. Use **History**, **Sources**,
  **Privacy**, or **Help** in the browser. Navigation is restricted to the requester and expires after 15 minutes.
- `/ai_memory_delete ID [ID ...]` deletes one or more exact fact IDs and their complete version chains. It accepts up to
  20 IDs separated by spaces. Members can delete their own personal facts. Chat administrators can delete other
  members’ or shared facts. The command preserves original conversation messages in the normal chat cache.

Enable `ai_memory_controls` with `ai_memory_retrieval` in entertainment mode to use natural-language memory edits.
Ask Sophie directly to remember, update, or forget a fact. Sophie uses `search_memory` to find a fact for an update or
deletion when needed and shows the proposed change with a confirmation button. The requester must confirm it in the
original chat and topic within five minutes. Sophie checks permissions again on confirmation. One proposal can
delete multiple facts. The chatbot tool only proposes changes until the requester confirms.
Members can manage only their own personal facts. Administrators can edit other members’ personal memories and delete
shared facts. A fact added for another person is attributed to the administrator who reported it. An administrator's
correction keeps the original owner and records the editor separately. Updates append a new version in the original topic.
Proactive replies and messages from bots, anonymous senders, or forwards cannot use the editing tool.

The **Privacy** panel lets administrators clear shared memory or stop saving their messages. Anyone who has opted out
can resume capture for themselves there. Resuming capture does not restore deleted history.

Clearing shared memory or stopping capture requires the requesting administrator to confirm within five minutes. They clear **all chat evidence,
facts, legacy text memory, summaries, cached messages, tool exchanges, and native
context sessions**. This deliberate coarse deletion removes indirect copies in other members' text. It can remove other
members' useful memories too. It does not delete Telegram messages, notes, provider logs, or backups. Off prevents new
structured records targeting an excluded user; other people's future authored text may still mention them.
Generation and context-epoch checks prevent stale writer or cache operations from restoring earlier data.
Fact edits track pending cache cleanup in MongoDB. The bot retries the cleanup before loading chatbot history and when
retrying an exact-ID deletion, even if the selected facts are already gone.
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
