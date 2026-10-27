# Redis persistence

All six application playbooks include `tasks/redis.yml` before changing application
services. Run them with the existing inventory and credentials; this is provisioning
for the existing rootless `containers` account and `redis.service`, not a new Redis
installation. The existing `redis.container` must name the container `redis` and use
`docker.io/redis:8.4`, port 6379 and the image's default `/data/dump.rdb` location.

The persistence drop-in retains the base quadlet's `Exec` command (or the image's
default `redis-server`), appending only RDB settings. Existing authentication,
config-file arguments, image, published ports and default `noeviction` behavior stay
in place. Other drop-ins must not override this drop-in's `Exec` or mount `/data`.
No AOF is enabled. The automatic snapshot policy uses these thresholds:
900 seconds / 1 change, 300 seconds / 10 changes, or 60 seconds / 10000 changes.
Redis loads `/data/dump.rdb` automatically at startup.

Data lives at `/home/containers/.local/share/sophie-redis`, outside Podman's
anonymous-volume lifecycle. All Podman and user-systemd operations run as
`containers`. The directory is initially created by that user; the Redis image's
root entrypoint assigns its container Redis UID through the rootless user namespace.
Later deployments do not reset that mapped ownership. The bind mount uses `:Z` for
a private SELinux label. Do not share this directory with another container.

While the existing container still uses its old `/data` mount, provisioning issues
synchronous `SAVE` and copies `/data/dump.rdb` before restarting Redis, even if a
durable dump remains from a failed rollout. The copy uses a temporary file and
atomically replaces the durable dump only after copying succeeds.
Staging uses no credentials; if configured, the existing `redis_username` and
`redis_password` facts are passed to redis-cli, with the password through
`REDISCLI_AUTH` and sensitive task output suppressed. Snapshot/auth/copy failures
abort before replacing the container. A failed SAVE may mean an RDB background save
is in progress; wait for it to finish before retrying. The live dataset is not
atomically transferred: quiesce writers during the first rollout to retain writes
between SAVE and restart. Without that, the copied snapshot is the recovery point.

Redis restarts when the persistence drop-in changes or its live mount still needs
replacement (including a retry after a failed replacement). Previously active beta,
stable, REST and scheduler services are started again because their `Requires=redis.service`
dependencies can stop them during that restart. Image rollback and promotion retain
the same data directory and do not roll Redis data back. Once the live container
uses the durable mount, subsequent runs skip SAVE/copy, including when no automatic
snapshot has been created yet. A stopped container on the old mount must be started
before retrying so SAVE can capture its current dataset.

If the Redis container is absent, provisioning skips inspection and SAVE/copy and
recreates the service from the durable dump. Recovery requires a nonempty regular
`dump.rdb`; without one, provisioning aborts before changing the drop-in or service.
The snapshot must be a valid Redis RDB file; Redis validates it when starting.
Podman errors other than container absence still abort. A leftover `dump.rdb.pending`
from an interrupted copy is never used for recovery.

For validation on a disposable host, inspect the generated user unit and check
`CONFIG GET save dir dbfilename appendonly maxmemory-policy`, `INFO persistence`, and
the `/data` mount. Write a temporary key, run `SAVE`, restart `redis.service` (which
replaces its container), then confirm the key remains. Repeat provisioning to check
that the drop-in is unchanged and Redis is not restarted. Also exercise stable-only
deployment with all consumers active.
For retry coverage, interrupt the first rollout after copying but before container
replacement, write another key to the old Redis, and confirm a retry retains it.
Also remove the container after a successful SAVE to durable storage and verify
provisioning recreates it with the saved key. Missing or empty durable snapshots
must block recreation when the container is absent.
