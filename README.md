# Bazar.com

Bazar.com is a Distributed and Operating Systems lab: a two-tier bookstore with
three independent Python/Flask HTTP services. Step 1 provides only the runnable
service foundation. Each service exposes `GET /health`, returning HTTP 200 with
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

No book data, business endpoints, databases, schemas, client, UI, authentication,
or test infrastructure is implemented in this step. Flask supplies automatic
HEAD/OPTIONS handling for the sole application route, `/health`.

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

These URLs are configured for future interservice calls; health does not use
them. Inside Compose, use service DNS names, since `localhost` refers to the
calling container itself. Outside Compose the Flask apps default to
`http://localhost:5001` and `http://localhost:5002` respectively. Each service can
start independently; no service waits for another service to become available.

The separate named volumes `catalog_data` and `order_data` mount at `/data` in
their respective containers. Future SQLite files will live there, for example
`/data/catalog.sqlite3` and `/data/order.sqlite3`. This step creates no database
files and does not share a volume between backend services.

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
