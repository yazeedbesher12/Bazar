# Step 4 verification record

Verification was performed and resumed on 2026-11-13 (Asia/Jerusalem) in
isolated host and Docker environments. This is an execution record, not the
final design report.

## Environment and safety

- Repository branch/status before work: `main...origin/main`, clean.
- Root `AGENTS.md`: absent. `.gitignore` already contains `AGENTS.md`, so no
  local instruction file was added or tracked.
- Docker CLI: `29.4.0` (build `9d7ad9f`).
- Docker Compose: `v5.1.2`.
- Host Python: `3.14.3`.
- Ports 5000, 5001, 5002, 15000, 15001, and 15002 were not listening.
- On the first run Docker Engine was unavailable because the Windows pipe did
  not exist. On the resumed run, sandboxed `docker info` received permission
  denied, while the approved read-only retry succeeded against Docker Desktop
  29.4.0 (`desktop-linux`, Linux/WSL2, x86_64, 12 CPUs, 7.577 GiB).
- No existing application database or normal Compose volume was read, changed,
  or deleted. Automated tests used `TemporaryDirectory` databases and removed
  them after each test.

The isolated Compose project name was `bazar_step4_20261113`. `docker compose
-p bazar_step4_20261113 -f compose.yaml config` resolved exactly three services:
`frontend`, `catalog`, and `order`. It also resolved project-specific resources
`bazar_step4_20261113_catalog_data`, `bazar_step4_20261113_order_data`, and
`bazar_step4_20261113_default`. Catalog and Order each mounted only their own
volume at `/data`; Frontend had no database mount. The service URLs resolved to
Compose DNS names and the database paths resolved below `/data`.

The initial `docker compose ... up --build -d` exited 1 before creating
resources. The resumed verification used the new isolated project
`bazar_step4_docker_20261113`; all three images built successfully and Compose
created only its namespaced network, Catalog volume, Order volume, and exactly
three application containers.

## Commands executed

The material commands actually run from the repository root were:

```powershell
git status --short --branch
rg --files -g '!*.db' -g '!*.sqlite' -g '!*.sqlite3'
docker --version
docker compose version
docker info --format '{{.ServerVersion}}|{{.OSType}}|{{.OperatingSystem}}'
py --version
python --version
docker compose config
python -m compileall -q catalog order frontend client
Get-NetTCPConnection -State Listen -LocalPort 5000,5001,5002,15000,15001,15002
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r catalog\requirements.txt -r order\requirements.txt -r frontend\requirements.txt -r client\requirements.txt
docker compose -p bazar_step4_20261113 -f compose.yaml config
docker compose -p bazar_step4_20261113 -f compose.yaml up --build -d
docker compose -p bazar_step4_20261113 -f compose.yaml ps -a
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\capture_sample.py
docker info
docker compose -p bazar_step4_docker_20261113 -f compose.yaml config
docker compose -p bazar_step4_docker_20261113 -f compose.yaml up --build -d
docker compose -p bazar_step4_docker_20261113 -f compose.yaml ps -a
docker compose -p bazar_step4_docker_20261113 -f compose.yaml exec -T SERVICE ...
docker compose -p bazar_step4_docker_20261113 -f compose.yaml restart frontend catalog order
docker compose -p bazar_step4_docker_20261113 -f compose.yaml stop catalog
docker compose -p bazar_step4_docker_20261113 -f compose.yaml start catalog
docker compose -p bazar_step4_docker_20261113 -f compose.yaml stop order
docker compose -p bazar_step4_docker_20261113 -f compose.yaml start order
docker compose -p bazar_step4_docker_20261113 -f compose.yaml logs --no-color frontend catalog order
```

The first dependency install attempt was sandbox-blocked from the network. The
approved retry installed only the repository's pinned dependencies in the
Git-ignored `.venv`.

## Automated results

Final result: **14 tests passed in 2.512 seconds; 0 failed; 0 skipped** on the
last run. Earlier full runs also passed, and the sample capture exited 0. The
tests use the real
Flask route functions, temporary SQLite files, and an in-process HTTP dispatcher;
they do not prove container networking or Linux/Gunicorn behavior.

Passed coverage:

- all three local health handlers;
- the exact two-book result for each required topic, trimmed/case-insensitive
  topic normalization, and an empty unknown-topic result;
- information for all four books and a forwarded 404 for a missing book;
- price replacement, restocking, decrementing, and atomic rejection of a
  combined price/over-decrement update;
- invalid JSON, wrong content type, empty/unsupported fields, numeric strings,
  booleans, null/fractional quantities, negative prices, and excess precision;
- successful Frontend purchase, exactly one stock decrement, exactly one
  completed attempt, and the correct book/price snapshot;
- missing and exhausted books creating no completed attempt;
- meaningful Frontend statuses, sanitized error text, malformed upstream data,
  and bounded service-unavailable responses;
- Catalog-unavailable behavior for search/info/purchase and Order-unavailable
  behavior in which search/info remain available while purchase fails cleanly;
- ambiguous decrement and malformed decrement-response injection: never success,
  no automatic retry or compensation, and a durable inspectable `unknown`
  attempt ID;
- Catalog/Order initialization over existing files preserving changed book data
  and completed attempts;
- CLI search success, empty results, purchase success, missing-book failure,
  connection failure, and nonzero failure exit codes.

## Concurrency evidence

Book 2 was adjusted from 10 to exactly 5 copies in its temporary Catalog file.
Twelve concurrent purchase requests were issued through Frontend. Observed and
asserted results:

- 5 HTTP 201 completed purchases;
- 7 HTTP 409 `insufficient_stock` responses;
- final Catalog quantity 0 (never negative);
- 12 new, unique attempt records: 5 `completed` and 7 `failed`;
- the five successful HTTP `attempt_id` values exactly matched the five
  persisted completed rows;
- no duplicate completed attempt IDs.

The same test was repeated through the published Docker Frontend endpoint with
real Gunicorn processes. Observed statuses were
`201,409,409,201,201,409,201,201,409,409,409,409`. The final stock was 0;
Order contained 5 completed and 7 failed unique rows; and the five response IDs
exactly matched the five completed rows. A subsequent exhausted request added
one failed row without changing the five completed records.

## Persistence evidence

In a temporary environment, book 3 was changed to price 17.25 and quantity 14,
then purchased once. The expected surviving state was price 17.25, quantity 13,
and one completed order with the returned attempt ID. Running both services'
database initialization again against the same files preserved the complete book
record and the byte-for-byte selected attempt fields.

The Docker continuation repeated this with the named volumes. Book 3 was changed
to price 17.25 and quantity 14, then purchased through the real host CLI. Before
restart it had quantity 13 and Order stored completed attempt
`f04517a6e20543f7b3d20d71df169516` with a 1725-cent snapshot. After restarting
all three services without removing volumes, health recovered on poll 2 and the
complete Catalog JSON and selected Order row matched exactly.

## Fixes and documentation changes

No application-service defect was reproduced by the completed checks, so the
service architecture and endpoint contracts were not changed. The implementation
work adds repeatable verification tests and a sample-output capture. README text
that previously said there was no test infrastructure was corrected, and the
verification commands and Docker boundary were documented. An initial test-only
SQLite inspection-handle leak and a missing host-Frontend route in the sample
harness were found during execution, fixed, and verified by reruns.

## Docker continuation results

- Build and Linux/Gunicorn startup passed for exactly three containers.
- All host `/health` endpoints returned 200 on the first initial poll and on the
  final check. After full restart, health recovered on poll 2.
- Frontend resolved `catalog` and `order` through Compose DNS and called both
  internal health endpoints. Order resolved and called Catalog.
- Environment values were correct: Frontend used `http://catalog:5001` and
  `http://order:5002`; Order used `http://catalog:5001`; database paths were
  `/data/catalog.sqlite3` and `/data/order.sqlite3`.
- Catalog and Order mounted different project-specific ext4 volumes at `/data`;
  both database files existed, and Catalog contained exactly four books.
- The real host CLI search and info output were readable. Purchase printed
  `bought book Xen and the Art of Surviving Undergraduate School.` and returned
  completed attempt `f04517a6e20543f7b3d20d71df169516`.
- With Catalog stopped, Frontend health stayed 200. Search, info, and purchase
  returned structured sanitized 503 `service_unavailable` responses in
  3.877–4.008 seconds. Catalog recovered on poll 2, and all operations succeeded.
- With Order stopped, search/info stayed 200. Purchase returned structured 503
  `outcome_unknown` in 3.988 seconds and stock stayed 12. Order recovered on poll
  2; the next purchase returned 201 and stock became 11.
- Missing purchase returned 404 and created no attempt. Exhausted purchase
  returned 409 with an attempt ID, added one failed row, and created no new
  completed row.
- The final snapshot contained exactly three running services. All three health
  checks returned 200. A scan of 282 log lines found zero `Traceback`, unhandled
  exception, or error-level matches; clients received no internal diagnostics.
- No Docker-blocked Step 4 checks remain.
- `docker compose ... down -v --rmi local --remove-orphans` removed only the
  isolated project's three containers, two volumes, network, and three locally
  built images. Follow-up filtered inventories reported zero matching
  containers, volumes, networks, or images.

## Remaining limitations

SQLite transactions make each Catalog stock decrement atomic, but Catalog and
Order do not share a distributed transaction. A response loss after a decrement
can leave an `unknown` attempt; operators must inspect it and stock state rather
than retrying or compensating blindly. There is no reconciliation worker,
idempotency key, authentication, or multi-host database. Very large JSON prices
also retain the already documented floating-point serialization limitation.
