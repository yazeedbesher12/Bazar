import json
import logging
import os
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)
app.config["CATALOG_DB_PATH"] = os.environ.get("CATALOG_DB_PATH", "/data/catalog.sqlite3")

MAX_INTEGER = 2**63 - 1
MAX_PRICE = Decimal("92233720368547758.07")
SEED_BOOKS = (
    (1, "How to get a good grade in DOS in 40 minutes a day.", "distributed systems"),
    (2, "RPCs for Noobs.", "distributed systems"),
    (3, "Xen and the Art of Surviving Undergraduate School.", "undergraduate school"),
    (4, "Cooking for the Impatient Undergrad.", "undergraduate school"),
)


def connect_db():
    connection = sqlite3.connect(app.config["CATALOG_DB_PATH"], timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db():
    Path(app.config["CATALOG_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_db()) as connection, connection:
        # Serialize initialization across workers; only insert missing seed IDs.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (typeof(quantity) = 'integer' AND quantity >= 0),
                price_cents INTEGER NOT NULL CHECK (typeof(price_cents) = 'integer' AND price_cents >= 0)
            )
        """)
        connection.executemany(
            "INSERT INTO books (id, title, topic, quantity, price_cents) "
            "VALUES (?, ?, ?, 10, 1000) ON CONFLICT(id) DO NOTHING",
            SEED_BOOKS,
        )


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def error_response(code, message, status):
    return jsonify(error={"code": code, "message": message}), status


@app.errorhandler(HTTPException)
def http_error(error):
    return error_response("http_error", error.name, error.code)


@app.errorhandler(sqlite3.Error)
def database_error(error):
    connection = g.get("db")
    if connection is not None:
        connection.rollback()
    # Extended SQLite result codes retain the base code in their low byte.
    code = getattr(error, "sqlite_errorcode", 0) & 0xFF
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        app.logger.warning("Catalog database busy")
        return error_response("database_busy", "Database temporarily busy; retry later.", 503)
    app.logger.error("Catalog database operation failed")
    return error_response("database_error", "Database operation failed.", 500)


def book_json(book):
    return {
        "id": book["id"],
        "title": book["title"],
        "topic": book["topic"],
        "quantity": book["quantity"],
        "price": book["price_cents"] / 100,
    }


@app.get("/health")
def health():
    return jsonify(service="catalog", status="ok"), 200


@app.get("/search/<topic>")
def search(topic):
    books = get_db().execute(
        "SELECT id, title FROM books WHERE topic = ? COLLATE NOCASE ORDER BY id",
        (topic.strip(),),
    ).fetchall()
    return jsonify([dict(book) for book in books])


@app.get("/info/<int:item_number>")
def info(item_number):
    if item_number > MAX_INTEGER:
        return error_response("not_found", "Book not found.", 404)
    book = get_db().execute("SELECT * FROM books WHERE id = ?", (item_number,)).fetchone()
    if book is None:
        return error_response("not_found", "Book not found.", 404)
    return jsonify(book_json(book))


@app.patch("/update/<int:item_number>")
def update(item_number):
    if not request.is_json:
        return error_response("invalid_input", "A JSON object is required.", 400)
    try:
        # Parse decimal tokens directly to avoid rounding before price validation.
        payload = json.loads(request.get_data(), parse_float=Decimal, parse_constant=Decimal)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return error_response("invalid_input", "Invalid JSON.", 400)
    if not isinstance(payload, dict) or not payload or payload.keys() - {"price", "quantity_delta"}:
        return error_response("invalid_input", "Supply only price, quantity_delta, or both.", 400)

    price_cents = None
    if "price" in payload:
        price = payload["price"]
        if type(price) not in (int, Decimal):
            return error_response("invalid_input", "Price must be a number.", 400)
        price = Decimal(price)
        if (
            not price.is_finite() or price < 0 or price > MAX_PRICE
            or price.as_tuple().exponent < -2
        ):
            return error_response(
                "invalid_input",
                "Price must be nonnegative, fit SQLite, and have at most two decimal places.",
                400,
            )
        price_cents = 0 if price == 0 else int(price * 100)

    delta = payload.get("quantity_delta", 0)
    if type(delta) is not int or not -MAX_INTEGER <= delta <= MAX_INTEGER:
        return error_response("invalid_input", "quantity_delta must be an integer within the SQLite range.", 400)
    if item_number > MAX_INTEGER:
        return error_response("not_found", "Book not found.", 404)

    connection = get_db()
    # Acquire the writer lock before updating and diagnosing a failed stock guard.
    # The context commits both changes together, or rolls back on any exception.
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute(
            "UPDATE books SET quantity = quantity + ?, "
            "price_cents = COALESCE(?, price_cents) "
            "WHERE id = ? AND quantity >= ? AND quantity <= ?",
            (delta, price_cents, item_number, max(0, -delta), MAX_INTEGER - max(0, delta)),
        )
        if changed.rowcount == 0:
            book = connection.execute("SELECT quantity FROM books WHERE id = ?", (item_number,)).fetchone()
            connection.rollback()
            if book is None:
                return error_response("not_found", "Book not found.", 404)
            if delta < 0:
                return error_response("insufficient_stock", "Insufficient stock.", 409)
            return error_response("invalid_input", "Resulting quantity exceeds the SQLite range.", 400)
        book = connection.execute("SELECT * FROM books WHERE id = ?", (item_number,)).fetchone()
    return jsonify(book_json(book))


initialize_db()
app.logger.info("Catalog service started")
