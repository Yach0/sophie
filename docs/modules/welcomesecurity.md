# Welcome Security expiry settings

`/welcomesecurity` offers expiry presets for pending captcha members. Changing expiry preserves whether captcha is enabled; it does not enable captcha or set the media restriction duration.

Each expiry button is bound to the originating database chat ID. It requires administrator access and the same current target chat. Switching the private-chat connection or disconnecting invalidates the old keyboard: reconnect to the intended group and reopen `/welcomesecurity`. Buttons sent before the chat-bound callback format was introduced must also be reopened; unbound payloads cannot update settings.

## Rejoining while a captcha is pending

A new join starts a fresh pending captcha deadline. Duplicate delivery of the same join does not extend that deadline. Delayed leave processing cannot remove a newer membership or its pending captcha session. Users who already passed verification keep their passed status when they leave and rejoin.

## Expiry and concurrent verification

Pending sessions use a MongoDB atomic transition to choose completion, expiry, or whitelist exemption. The decision and membership generation survive process restarts. Failed kicks and declines retain the expiry decision for subsequent scheduler sweeps. Failed exemption unmutes retain `exempting` for scheduler retry; failed approvals or unmutes retain the completion decision for retry through the CAPTCHA flow, and are never converted into expiry. Rejoins reset the decision and deadline for the new membership generation. Conditional final deletion cannot remove a newer session. Redis is used for existing CAPTCHA data and messages, not synchronization.

MongoDB chooses the durable state decision. Restriction writes use the existing per-user/group Mongo restriction operation lease across processes. Initial mutes and completion actions carry the original pending generation and transition into that operation; membership and pending state are checked after acquiring the lease and again after asynchronous Telegram lookups and snapshot writes, immediately before the restriction request. A deleted pending record invalidates a delayed initial mute. A retained completion/expiry/exemption decision never authorizes automatic join-request approval or a new CAPTCHA.

This is fencing, not an atomic MongoDB/Telegram transaction. Membership updates and transition claims do not acquire the restriction lease, so they can still change after the final Mongo check, while the Telegram request is in flight. Telegram accepts no generation token or conditional membership version. A request that times out or outlives the existing lease can also be applied remotely after the worker stops. Another process cannot cancel such a remote request. A crash after Telegram succeeds but before conditional deletion can repeat the same action. Completion recovery requires the user to retry the CAPTCHA flow; operators can inspect retained `completing`/`expiring`/`exempting` rows. No claim expires or switches to the opposite action.

Captcha completion also keeps the pending record and recovery messages until the captcha mute is successfully released or replaced with the configured welcome mute, including for administrators and group-whitelisted users. If a join request was already approved, completion still attempts that restriction transition.


## Deploying the pending-user unique index

`ws_user_group_unique` enforces one row for each canonical Beanie user/group DBRef pair. Concurrent Beanie inserts recover from `DuplicateKeyError` by loading the winner, without overwriting its transition. Startup installs this index **after migrations and before starting handlers or schedulers**, even when `mongo_skip_indexes` disables general synchronization. The migration bootstrap uses `skip_indexes=True` and does not build the index before migrations can repair data. No new data migration silently deletes duplicates.

Before deploying to a database that may contain duplicates, stop **all** bot, scheduler, REST, and older-build writers. Back up `ws_users` and the membership data. In MongoDB, identify pairs with:

```javascript
db.ws_users.aggregate([
  {$group: {_id: {user: "$user", group: "$group"}, rows: {$push: "$_id"}, count: {$sum: 1}}},
  {$match: {count: {$gt: 1}}}
])
```

Review each pair against current membership, `added_at`, `passed`, and `transition`; archive the original rows outside `ws_users` and explicitly reconcile to one row. Do not blindly keep the newest row: a competing durable decision or historical passed state may require manual reconciliation. Also repair malformed or noncanonical references and review any existing index on the same keys. Then create the index while writers remain stopped:

```javascript
db.ws_users.createIndex({user: 1, group: 1}, {unique: true, name: "ws_user_group_unique"})
```

Restart all processes on the fixed build. Mongo validates historical rows and concurrent writes during index creation. If duplicates or an incompatible existing index remain, startup fails closed with a repair message and preserves every row; it does not serve Welcome Security without uniqueness. Do not roll back to an older writer while keeping active new workers: older Beanie inserts do not handle the now-enforced duplicate-key conflict. The new index can remain across code rollback, but that older-build error behavior still applies.
