# Bazar.com

Bazar.com is a Distributed and Operating Systems lab: a two-tier bookstore with
three independent Python/Flask HTTP services. Step 2 adds Catalog persistence,
search, info, and updates to the runnable foundation. Each service exposes
`GET /health`, returning HTTP 200 with
`{"service":"frontend","status":"ok"}` (or `catalog` / `order`). Health checks
check only the local process and never call another service.

## Architecture

| Service | Host port | Eventual responsibility |
| --- | --- | --- |
| Frontend | 5000 | Accept search, info, and purchase requests; call Catalog for search/info and Order for purchases. |
| Catalog | 5001 | Own books, prices, topics, stock, and its own SQLite database. |
| Order | 5002 | Handle purchases, call Catalog to check/decrement stock, and own the order log in its own SQLite database. |

Frontend is the entry tier; Catalog and Order form the backend tier. Each service
runs in its own Linux container with Gunicorn, one worker, and four request
threads, bound to `0.0.0.0`. Startup messages and HTTP access logs go to stdout.
Gunicorn diagnostic logs go to stderr, also visible in Compose logs.

Frontend and Order remain health-only services. There are no purchases, frontend
proxy routes, client, UI, authentication, or test infrastructure. Flask also
supplies automatic HEAD/OPTIONS handling where appropriate.

## Prerequisites

- Docker Engine with the Docker Compose v2 plugin, or Docker Desktop configured
  for Linux containers.
- Available host ports 5000, 5001, and 5002.
- A terminal and curl (`curl.exe` in Windows PowerShell).

Python 3.12 and the service dependencies are installed inside the images; no host
Python installation is required. Dockerfiles use the repository root as their
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

These URLs are configured for future interservice calls; health does not use
them. Inside Compose, use service DNS names, since `localhost` refers to the
calling container itself. Outside Compose the Flask apps default to
`http://localhost:5001` and `http://localhost:5002` respectively. Each service can
start independently; no service waits for another service to become available.

The separate named volumes `catalog_data` and `order_data` mount at `/data` in
their respective containers. Catalog uses `/data/catalog.sqlite3`; Order's volume
is reserved for its future database. Keep `CATALOG_DB_PATH` under `/data` inside
Compose so it persists. Outside containers, set it to a writable local file path.
Catalog creates parent directories, the table, and missing seed IDs on startup;
existing quantities and prices are never reset. No database is shared.

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
