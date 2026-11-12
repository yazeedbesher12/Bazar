import json
import logging
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import requests
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)
app.config["CATALOG_URL"] = os.environ.get("CATALOG_URL", "http://localhost:5001")
app.config["ORDER_DB_PATH"] = os.environ.get("ORDER_DB_PATH", "/data/order.sqlite3")

HTTP_TIMEOUT = (2, 10)  # Separate connect/read timeouts, in seconds.
MAX_INTEGER = 2**63 - 1
ERRORS = {
    "invalid_input": (400, "Invalid purchase request."),
    "not_found": (404, "Book not found."),
    "insufficient_stock": (409, "Insufficient stock."),
    "database_busy": (503, "Catalog database temporarily busy; retry later."),
}


def connect_db():
    connection = sqlite3.connect(app.config["ORDER_DB_PATH"], timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db():
    Path(app.config["ORDER_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_db()) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                book_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'unknown'))
            )
        """)


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def error_response(code, message, status, attempt_id=None):
    error = {"code": code, "message": message}
    if attempt_id is not None:
        error["attempt_id"] = attempt_id
    return jsonify(error=error), status


@app.errorhandler(HTTPException)
def http_error(error):
    return error_response("http_error", error.name, error.code)


@app.errorhandler(sqlite3.Error)
def database_error(error):
    # Transaction context managers already roll back failed writes.
    app.logger.warning("order database unavailable attempt_id=%s", g.get("attempt_id", "none"))
    return error_response(
        "database_unavailable", "Order database unavailable.", 503, g.get("attempt_id")
    )


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def read_json(response):
    return json.loads(response.content, parse_float=Decimal, parse_constant=reject_constant)


def book_price_cents(book, item_number):
    if not isinstance(book, dict) or type(book.get("id")) is not int or book["id"] != item_number:
        raise ValueError("Invalid book ID")
    if any(not isinstance(book.get(key), str) or not book[key].strip() for key in ("title", "topic")):
        raise ValueError("Invalid book text")
    if type(book.get("quantity")) is not int or not 0 <= book["quantity"] <= MAX_INTEGER:
        raise ValueError("Invalid stock")
    price = book.get("price")
    if type(price) not in (int, Decimal):
        raise ValueError("Invalid price")
    price = Decimal(price)
    if (not price.is_finite() or not 0 <= price <= Decimal("92233720368547758.07")
            or price.as_tuple().exponent < -2):
        raise ValueError("Invalid price")
    return 0 if price == 0 else int(price * 100)


def catalog_error(response, data):
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict) or not isinstance(error.get("message"), str):
        return None
    code = error.get("code")
    if isinstance(code, str) and code in ERRORS and response.status_code == ERRORS[code][0]:
        return code
    return None


def catalog_request(method, path, **kwargs):
    # No retries or redirects: replaying a decrement could buy a second copy.
    return requests.request(
        method, app.config["CATALOG_URL"].rstrip("/") + path,
        timeout=HTTP_TIMEOUT, allow_redirects=False, **kwargs,
    )


def save_outcome(attempt_id, status, book=None, price_cents=None):
    connection = get_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    with connection:
        changed = connection.execute(
            "UPDATE attempts SET status = ?, updated_at = ?, "
            "title = COALESCE(?, title), price_cents = COALESCE(?, price_cents) "
            "WHERE attempt_id = ?",
            (status, updated_at, book["title"] if book else None, price_cents, attempt_id),
        )
        if changed.rowcount != 1:
            raise sqlite3.DatabaseError("Attempt record missing")
    app.logger.info("order attempt_id=%s status=%s", attempt_id, status)
    return updated_at


def outcome_unknown(attempt_id, status=503):
    try:
        save_outcome(attempt_id, "unknown")
    except sqlite3.Error:
        # The durable pending record remains if this best-effort write fails.
        app.logger.warning("order attempt_id=%s outcome unknown; status write failed", attempt_id)
    return error_response(
        "outcome_unknown", "Purchase outcome is unknown; do not retry automatically.", status, attempt_id
    )


@app.get("/health")
def health():
    return jsonify(service="order", status="ok"), 200


@app.post("/purchase/<int:item_number>")
def purchase(item_number):
    if not 1 <= item_number <= MAX_INTEGER or request.get_data().strip():
        return error_response("invalid_input", "Use a positive book ID and no request body.", 400)

    try:
        with catalog_request("GET", f"/info/{item_number}") as response:
            book = read_json(response)
            if response.status_code != 200:
                code = catalog_error(response, book)
                if code:
                    status, message = ERRORS[code]
                    return error_response(code, message, status)
                if response.status_code >= 500:
                    return error_response("service_unavailable", "Catalog unavailable.", 503)
                return error_response("bad_upstream", "Invalid Catalog response.", 502)
            price_cents = book_price_cents(book, item_number)
    except requests.RequestException:
        return error_response("service_unavailable", "Catalog unavailable.", 503)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return error_response("bad_upstream", "Invalid Catalog response.", 502)

    attempt_id = uuid4().hex
    g.attempt_id = attempt_id
    created_at = datetime.now(timezone.utc).isoformat()
    connection = get_db()
    # Commit the pending record BEFORE the network call; never hold a DB
    # transaction while waiting for Catalog. Even stock=0 is decided by PATCH.
    with connection:
        connection.execute(
            "INSERT INTO attempts (attempt_id, book_id, title, price_cents, created_at, updated_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (attempt_id, item_number, book["title"], price_cents, created_at, created_at),
        )
    app.logger.info("order attempt_id=%s book_id=%s status=pending", attempt_id, item_number)

    try:
        with catalog_request("PATCH", f"/update/{item_number}", json={"quantity_delta": -1}) as response:
            result = read_json(response)
            if response.status_code != 200:
                code = catalog_error(response, result)
                if code is None:
                    return outcome_unknown(attempt_id, 502 if response.status_code < 500 else 503)
                # Only Catalog's recognized rejection responses confirm no decrement.
                save_outcome(attempt_id, "failed")
                status, message = ERRORS[code]
                return error_response(code, message, status, attempt_id)
            price_cents = book_price_cents(result, item_number)
    except requests.RequestException:
        return outcome_unknown(attempt_id)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return outcome_unknown(attempt_id, 502)

    try:
        # The committed decrement response supplies the final title/price snapshot.
        updated_at = save_outcome(attempt_id, "completed", result, price_cents)
    except sqlite3.Error:
        return outcome_unknown(attempt_id)
    app.logger.info("order attempt_id=%s bought book %s", attempt_id, result["title"])
    return jsonify(
        attempt_id=attempt_id, book_id=item_number, title=result["title"],
        price=price_cents / 100, quantity=1, status="completed",
        created_at=created_at, updated_at=updated_at,
    ), 201


initialize_db()
app.logger.info("Order service started")
