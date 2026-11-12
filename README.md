# Bazar.com

Bazar.com is a Distributed and Operating Systems lab: a two-tier bookstore with
three independent Python/Flask HTTP services. Expanded Step 3 completes search,
book info, and single-copy purchases through Frontend, with a host-side CLI.
Each service exposes
`GET /health`, returning HTTP 200 with
`{"service":"frontend","status":"ok"}` (or `catalog` / `order`). Health checks
check only the local process and never call another service.

## Architecture

| Service | Host port | Responsibility |
| --- | --- | --- |
| Frontend | 5000 | Accept search, info, and purchase requests; call Catalog for search/info and Order for purchases. |
| Catalog | 5001 | Own books, prices, topics, stock, and its own SQLite database. |
| Order | 5002 | Handle purchases, call Catalog to check/decrement stock, and own the order log in its own SQLite database. |

Frontend is the entry tier; Catalog and Order form the backend tier. Each service
runs in its own Linux container with Gunicorn, one worker, and four request
threads, bound to `0.0.0.0`. Startup messages and HTTP access logs go to stdout.
Gunicorn diagnostic logs go to stderr, also visible in Compose logs.

The host client calls Frontend only. Frontend calls Catalog for search/info and
Order for purchases; Order calls Catalog for book details and an atomic stock
decrement. Only Catalog accesses the books database, and only Order accesses the
attempt log. There are exactly three service containers; the CLI runs on the host.
There is no GUI, authentication, message broker, or test infrastructure. Flask
also supplies automatic HEAD/OPTIONS handling where appropriate.

## Prerequisites

- Docker Engine with the Docker Compose v2 plugin, or Docker Desktop configured
  for Linux containers.
- Available host ports 5000, 5001, and 5002.
- A terminal and curl (`curl.exe` in Windows PowerShell).
- Python 3.12 on the host if using the optional command-line client.

Python 3.12 and the service dependencies are installed inside the images; no host
Python installation is required for curl-only use. Dockerfiles use the repository root as their
build context.

## Configuration

Compose works with built-in development defaults. To customize them, copy
`.env.example` to `.env` in the repository root and edit it. In PowerShell:

```powershell
Copy-Item .env.example .env
```

| Variable | Compose default | Read by |
| --- | --- | --- |
| `CATALOG_URL` | `http://catalog:5001` | Frontend and Order |
| `ORDER_URL` | `http://order:5002` | Frontend |
| `CATALOG_DB_PATH` | `/data/catalog.sqlite3` | Catalog |
| `ORDER_DB_PATH` | `/data/order.sqlite3` | Order |
| `FRONTEND_URL` | `http://localhost:5000` | Host CLI only (environment or `--frontend-url`) |

Service URLs configure interservice calls; health does not use
them. Inside Compose, use service DNS names, since `localhost` refers to the
calling container itself. Outside Compose the Flask apps default to
`http://localhost:5001` and `http://localhost:5002` respectively. Each service can
start independently; no service waits for another service to become available.

The separate named volumes `catalog_data` and `order_data` mount at `/data` in
their respective containers. Catalog uses `/data/catalog.sqlite3`; Order uses
`/data/order.sqlite3`. Keep both configured paths under `/data` inside Compose so
they persist. Outside containers, set them to writable local file paths.
Catalog creates parent directories, the table, and missing seed IDs on startup;
existing quantities and prices are never reset. Order creates its table without
resetting previous attempts. No database is shared.

For deployment across machines, set each calling service's environment to the
reachable HTTP(S) base URL of its destination and publish the corresponding port.
Use `--frontend-url http://FRONTEND_HOST:5000` for the client. Service DNS names
work only on the Compose network. Database paths remain local to their owners.
Compose reads `.env` automatically; the host CLI does not load that file. Set
`$env:FRONTEND_URL` in PowerShell or pass `--frontend-url` before the subcommand.

Every HTTP call has explicit connect/read timeouts in seconds: Order `(2, 10)`,
Frontend `(2, 45)`, and CLI `(2, 60)`. The longer outer read budgets allow inner
calls and SQLite writes to finish. Read timeouts bound inactivity, not the total
wall-clock duration. No layer automatically retries or follows HTTP redirects.

`.env`, generated SQLite files, virtual environments, caches, and local
`AGENTS.md` instructions are ignored by Git and excluded from Docker build
contexts. `.env.example` remains versioned.

## Start and check

Run from the repository root:

```sh
docker compose up --build -d
docker compose logs -f
```

Stop following logs with Ctrl+C; the containers continue running. In another
terminal, check all three services:

```sh
curl -i http://localhost:5000/health
curl -i http://localhost:5001/health
curl -i http://localhost:5002/health
```

In Windows PowerShell, use `curl.exe` in place of `curl`. Each response should
have status 200 and JSON identifying the corresponding service with status `ok`.

To run a single service independently, use for example:

```sh
docker compose up --build -d catalog
```

## Shutdown

```sh
docker compose down
```

This removes the containers and network while retaining the two named volumes.

See [docs/README.md](docs/README.md) for eventual submission documentation.

## Endpoint table

| Service | Method and path | Success |
| --- | --- | --- |
| All three | `GET /health` | 200: service identity and `status: ok`; process only |
| Frontend, Catalog | `GET /search/<topic>` | 200: array of `{id, title}`; unknown topic gives `[]` |
| Frontend, Catalog | `GET /info/<item_number>` | 200: `{id, title, topic, quantity, price}` |
| Frontend, Order | `POST /purchase/<item_number>` | 201: completed single-copy purchase receipt |
| Catalog only | `PATCH /update/<item_number>` | 200: updated complete book |

Purchases take a positive book ID in the path and no body. Repeating POST creates
another attempt and can buy another copy; there is no idempotency key. Frontend
does not expose Catalog's update route and never opens a database. Successful
upstream JSON bodies and statuses are preserved; error statuses and attempt IDs
are retained with consistent, sanitized messages. Invalid/malformed upstream
responses give 502; unavailable services/databases give 503. Invalid input is
400, a missing book is 404, and insufficient stock is 409. Invalid route/method
errors are also JSON. Catalog retains its generic 500 for other internal SQLite
failures; Order reports unavailable database operations as 503.

A completed purchase receipt has this shape (illustration, not captured output):

```json
{
  "attempt_id": "0123456789abcdef0123456789abcdef",
  "book_id": 1,
  "title": "How to get a good grade in DOS in 40 minutes a day.",
  "price": 10.0,
  "quantity": 1,
  "status": "completed",
  "created_at": "2026-01-01T12:00:00+00:00",
  "updated_at": "2026-01-01T12:00:01+00:00"
}
```

## Purchase persistence and consistency limits

Order first validates a Catalog lookup, then commits a UUID-based `pending`
attempt with the book ID, title, integer-cent price snapshot, and UTC timestamps.
It requests `quantity_delta: -1` even if the earlier lookup showed zero stock:
only the atomic decrement's result decides availability. After success, it
commits `completed` with the title/price from the decrement response before
returning 201 and logging `bought book <book_name>`. The pending snapshot is
provisional; the completed snapshot is the price at the committed decrement,
not a reservation of the earlier lookup price.

Recognized Catalog rejections mark an attempt `failed`. A lost, malformed, or
unexpected decrement response, or failure to save completion, produces an
`outcome_unknown` error (503 for unavailable operations, 502 for malformed
responses), including the attempt ID when available:

```json
{"error":{"code":"outcome_unknown","message":"Purchase outcome is unknown; do not retry automatically.","attempt_id":"0123456789abcdef0123456789abcdef"}}
```

Order tries to save status `unknown`; if that write also fails, `pending` remains.
An interrupted process can also leave `pending`. A confirmed rejection whose
status cannot be saved returns database-unavailable with its attempt ID and can
leave `pending`. Frontend/client response loss can hide an already completed
purchase; Frontend uses `outcome_unknown` for purchase transport failures even
when it cannot tell whether Order received the request. Keep any returned ID.

These are separate SQLite transactions, **not a distributed atomic transaction**.
There is no retry/reconciliation framework or automatic stock compensation. Do
not blindly restore stock or repeat an uncertain purchase: inspect the attempt
log and Catalog state first. Order holds no database transaction open during an
HTTP call. Its connections are per request with a five-second SQLite busy timeout.
Completed/failed/unknown writes and the pending insert are separate short
transactions. Startup initialization preserves existing data; a startup database
failure prevents that service starting. Health never queries a database.

Prices are integer cents in both databases, exposed as JSON numeric currency
units. Existing Catalog serialization uses floating-point JSON numbers, so very
large prices can lose cent precision over HTTP; use the documented demo-sized
prices for this lab. Catalog is unchanged in this step.

## Host CLI (Windows PowerShell)

Install only the client dependency in an ignored virtual environment; activation
is unnecessary:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r client/requirements.txt
.\.venv\Scripts\python.exe client/client.py search "distributed systems"
.\.venv\Scripts\python.exe client/client.py search "undergraduate school"
.\.venv\Scripts\python.exe client/client.py info 1
.\.venv\Scripts\python.exe client/client.py --frontend-url http://localhost:5000 purchase 1
```

The purchase command buys an additional copy each time. Success prints a readable
receipt and `bought book <book_name>`. Empty search prints `No books found.` and
exits zero. Argument, HTTP, connection, timeout, or malformed-response failures
exit nonzero without a traceback. Purchase failures warn against automatic replay.

## Request-flow manual checks (Windows PowerShell)

These are instructions only; none have been executed for this implementation.
Run from the repository root after installing the client as above. Wait for the
health GETs to succeed after starts/restarts; repeat GETs as needed, never POST
automatically. Use a quiet demo environment without other stock writers, and keep
the same PowerShell session for the saved stock snapshot.

```powershell
docker compose up --build -d
curl.exe -i http://localhost:5000/health
curl.exe -i http://localhost:5001/health
curl.exe -i http://localhost:5002/health
curl.exe -i "http://localhost:5000/search/%20DISTRIBUTED%20SYSTEMS%20"
curl.exe -i "http://localhost:5000/search/undergraduate%20school"
curl.exe -i http://localhost:5000/search/unknown
curl.exe -i http://localhost:5000/info/1
curl.exe -i http://localhost:5000/info/99999
# Expect two books per seeded topic, [] for unknown, 200 for book 1, 404 for 99999.
.\.venv\Scripts\python.exe client/client.py search "unknown"
.\.venv\Scripts\python.exe client/client.py info 99999
# The missing-book command exits nonzero.

$beforeFlow = curl.exe -fsS http://localhost:5000/info/1 | ConvertFrom-Json
# Ensure at least two copies for the two intentional purchases below.
$extra = [Math]::Max(0, 2 - [long]$beforeFlow.quantity)
if ($extra -gt 0) {
    @{quantity_delta = $extra} | ConvertTo-Json -Compress | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'
}

# One copy through curl, then another through the CLI. Each must return success.
curl.exe -i -X POST http://localhost:5000/purchase/1
.\.venv\Scripts\python.exe client/client.py purchase 1
curl.exe -i http://localhost:5000/info/1
# Expect quantity = original + extra - 2; compare both receipts and their unique IDs.
curl.exe -i -X POST http://localhost:5000/purchase/99999
# Expect 404, with no stock change.

# Read Order's own log administratively (no public order-list endpoint).
docker compose exec order python -c "import os,sqlite3; db=sqlite3.connect(os.environ['ORDER_DB_PATH']); print(db.execute('SELECT attempt_id,book_id,title,price_cents,status,created_at,updated_at FROM attempts ORDER BY created_at').fetchall()); db.close()"
docker compose restart catalog order
# Wait for the health GETs to return 200 before continuing.
curl.exe -i http://localhost:5001/health
curl.exe -i http://localhost:5002/health
curl.exe -i http://localhost:5000/info/1
docker compose exec order python -c "import os,sqlite3; db=sqlite3.connect(os.environ['ORDER_DB_PATH']); print(db.execute('SELECT attempt_id,book_id,title,price_cents,status,created_at,updated_at FROM attempts ORDER BY created_at').fetchall()); db.close()"
# Compare stock and completed attempt IDs before/after restart: neither resets.

# Exhaust stock without issuing a loop of purchases.
$current = curl.exe -fsS http://localhost:5000/info/1 | ConvertFrom-Json
@{quantity_delta = -([long]$current.quantity)} | ConvertTo-Json -Compress | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'
curl.exe -i -X POST http://localhost:5000/purchase/1
curl.exe -i http://localhost:5000/info/1
# Expect 409 and quantity still zero; Order records a failed attempt.

# Restore the exact original quantity; prices were not changed by these checks.
# Proceed only after confirming the above purchases have known outcomes.
$current = curl.exe -fsS http://localhost:5000/info/1 | ConvertFrom-Json
@{quantity_delta = ([long]$beforeFlow.quantity - [long]$current.quantity)} | ConvertTo-Json -Compress | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'
curl.exe -i http://localhost:5000/info/1
# Attempt records remain for inspection; restoring demo stock does not delete them.

# Catalog unavailable: gateway health remains 200; search and purchase return 503.
docker compose stop catalog
curl.exe -i http://localhost:5000/health
curl.exe -i "http://localhost:5000/search/distributed%20systems"
curl.exe -i -X POST http://localhost:5000/purchase/1
docker compose start catalog
curl.exe -i http://localhost:5001/health

# Order unavailable: purchase returns 503/outcome_unknown conservatively.
docker compose stop order
curl.exe -i -X POST http://localhost:5000/purchase/1
docker compose start order
curl.exe -i http://localhost:5002/health

# Unreachable Frontend is a clean nonzero client failure.
docker compose stop frontend
.\.venv\Scripts\python.exe client/client.py info 1
docker compose start frontend
docker compose logs --tail 100 frontend catalog order
docker compose down
```

`down` preserves both volumes. These stop-service examples do not exercise lost
responses after an actual decrement, database lock exhaustion, or crash timing;
those paths have only been statically reviewed. Do not mistake unavailable-service
checks for proof of cross-service atomicity. No execution output or completed
design report is supplied.

## Catalog data and API

The assignment does not specify initial quantities or prices. **Demo assumption:**
every seed book starts with quantity **10** and price **10.00** currency units
(1000 integer cents). No particular currency is assumed.

| ID | Exact title | Topic |
| --- | --- | --- |
| 1 | How to get a good grade in DOS in 40 minutes a day. | distributed systems |
| 2 | RPCs for Noobs. | distributed systems |
| 3 | Xen and the Art of Surviving Undergraduate School. | undergraduate school |
| 4 | Cooking for the Impatient Undergrad. | undergraduate school |

- `GET /search/<topic>` trims surrounding whitespace and matches topics without
  regard to ASCII case. It returns an array containing only IDs and titles, e.g.
  `[{"id":1,"title":"How to get a good grade in DOS in 40 minutes a day."},{"id":2,"title":"RPCs for Noobs."}]`.
  Unknown topics return `[]` with HTTP 200.
- `GET /info/1` returns
  `{"id":1,"title":"How to get a good grade in DOS in 40 minutes a day.","topic":"distributed systems","quantity":10,"price":10.0}`
  for an unchanged seed book.
- `PATCH /update/1` accepts `{"price":12.50}`, `{"quantity_delta":3}`, or both
  fields together. It returns the complete updated book with HTTP 200. Price
  replaces the current price; the integer delta adds to or subtracts from stock.
  Both changes commit together, or neither does.

Requests must contain a nonempty JSON object with only the supported fields.
Numeric strings, booleans, fractional deltas, nulls, negative/non-finite prices,
and prices with more than two decimal places (including trailing zeros) are
rejected. Integer quantities/deltas and integer cents must fit SQLite's signed
64-bit storage; quantities cannot exceed 9223372036854775807 and prices cannot
exceed 92233720368547758.07. JSON responses expose numeric currency units; use
normal demo-sized prices, since JSON consumers may round very large numbers.

Errors use `{"error":{"code":"insufficient_stock","message":"Insufficient stock."}}`:
invalid input is HTTP 400, a missing book is 404, insufficient stock is 409, and
temporary SQLite lock exhaustion is 503. Other database failures return a generic
500 without internal details. Each request opens its own connection lazily and
closes it afterward. SQLite waits at most five seconds per lock acquisition.
Updates acquire a write transaction and use a guarded atomic SQL increment to
prevent negative stock and integer overflow under concurrency. Startup seeding
also uses a write transaction; a startup database failure prevents startup.
`GET /health` still checks only the process and does not query SQLite.

## Catalog manual checks (Windows PowerShell)

These commands have **not** been executed as part of this implementation. Run
from the repository root. A here-string piped to `--data-binary '@-'` avoids
PowerShell/native-command JSON quoting differences. Perform updates without
other writers if you want to restore the exact original values afterward.

```powershell
docker compose up --build -d catalog
curl.exe -i http://localhost:5001/health
curl.exe -i "http://localhost:5001/search/%20DISTRIBUTED%20SYSTEMS%20"
curl.exe -i "http://localhost:5001/search/undergraduate%20school"
curl.exe -i http://localhost:5001/search/unknown
curl.exe -i http://localhost:5001/info/1

# Save the current values; do not assume an existing database still has seed values.
$before = curl.exe -fsS http://localhost:5001/info/1 | ConvertFrom-Json

# Price change: expect 200 and price 12.5.
@'
{"price":12.50}
'@ | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'

# Restock: expect 200 and quantity increased by 3.
@'
{"quantity_delta":3}
'@ | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'

# Attempt to remove one more than available AND change the price: expect 409.
$current = curl.exe -fsS http://localhost:5001/info/1 | ConvertFrom-Json
@{quantity_delta = -([long]$current.quantity + 1); price = 1} | ConvertTo-Json -Compress | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'
curl.exe -i http://localhost:5001/info/1
# Quantity and price must be unchanged by the rejected request.

# Persistence: expect the changed price and restocked quantity after restart.
docker compose restart catalog
# If the service is still starting, retry the next command once it is ready.
curl.exe -i http://localhost:5001/info/1

# Restore both original values after the checks, using the saved snapshot.
$current = curl.exe -fsS http://localhost:5001/info/1 | ConvertFrom-Json
@{price = $before.price; quantity_delta = ([long]$before.quantity - [long]$current.quantity)} | ConvertTo-Json -Compress | curl.exe -i -X PATCH http://localhost:5001/update/1 -H "Content-Type: application/json" --data-binary '@-'
curl.exe -i http://localhost:5001/info/1
docker compose down
```

On unchanged seeds, the restock raises quantity from 10 to 13 and restoration
returns it to 10, with price restored to 10.00. On existing data, the saved values
are restored instead. `docker compose down` retains database volumes.
