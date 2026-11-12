import json
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)
app.config["CATALOG_URL"] = os.environ.get("CATALOG_URL", "http://localhost:5001")
app.config["ORDER_URL"] = os.environ.get("ORDER_URL", "http://localhost:5002")

HTTP_TIMEOUT = (2, 45)  # Allow Order's sequential HTTP calls and short DB writes.
MAX_INTEGER = 2**63 - 1
ERROR_MESSAGES = {
    "invalid_input": "Invalid request.",
    "not_found": "Book not found.",
    "insufficient_stock": "Insufficient stock.",
    "database_busy": "Database temporarily busy.",
    "database_error": "Database operation failed.",
    "database_unavailable": "Order database unavailable.",
    "service_unavailable": "Downstream service unavailable.",
    "bad_upstream": "Invalid downstream response.",
    "outcome_unknown": "Purchase outcome is unknown; do not retry automatically.",
    "http_error": "Request could not be handled.",
}


def error_response(code, status, attempt_id=None):
    error = {"code": code, "message": ERROR_MESSAGES[code]}
    if attempt_id is not None:
        error["attempt_id"] = attempt_id
    return jsonify(error=error), status


@app.errorhandler(HTTPException)
def http_error(error):
    return error_response("http_error", error.code)


def valid_attempt_id(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def valid_price(price):
    if type(price) not in (int, Decimal):
        return False
    price = Decimal(price)
    return (price.is_finite() and 0 <= price <= Decimal("92233720368547758.07")
            and price.as_tuple().exponent >= -2)


def valid_book_name(book):
    return isinstance(book, dict) and isinstance(book.get("title"), str) and bool(book["title"].strip())


def valid_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def valid_success(operation, data, item_number):
    if operation == "search":
        return isinstance(data, list) and all(
            valid_book_name(book) and type(book.get("id")) is int and 1 <= book["id"] <= MAX_INTEGER
            for book in data
        )
    if not valid_book_name(data) or not valid_price(data.get("price")):
        return False
    if operation == "info":
        return (
            type(data.get("id")) is int and data["id"] == item_number
            and isinstance(data.get("topic"), str) and bool(data["topic"].strip())
            and type(data.get("quantity")) is int and 0 <= data["quantity"] <= MAX_INTEGER
        )
    return (
        valid_attempt_id(data.get("attempt_id"))
        and type(data.get("book_id")) is int and data["book_id"] == item_number
        and type(data.get("quantity")) is int and data["quantity"] == 1
        and data.get("status") == "completed"
        and all(valid_timestamp(data.get(key)) for key in ("created_at", "updated_at"))
    )


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def forward(operation, path, item_number=None):
    purchase = operation == "purchase"
    known_attempt_id = None
    base_url = app.config["ORDER_URL" if purchase else "CATALOG_URL"]
    app.logger.info("frontend operation=%s book_id=%s", operation, item_number)
    try:
        # Disable redirects as well as retries so POST cannot be replayed.
        with requests.request(
            "POST" if purchase else "GET", base_url.rstrip("/") + path,
            timeout=HTTP_TIMEOUT, allow_redirects=False,
        ) as response:
            data = json.loads(response.content, parse_float=Decimal, parse_constant=reject_constant)
            if purchase and isinstance(data, dict):
                candidate = data.get("attempt_id")
                if isinstance(data.get("error"), dict):
                    candidate = data["error"].get("attempt_id", candidate)
                if valid_attempt_id(candidate):
                    known_attempt_id = candidate
            expected_status = 201 if purchase else 200
            if response.status_code == expected_status and valid_success(operation, data, item_number):
                app.logger.info(
                    "frontend operation=%s status=%s attempt_id=%s", operation,
                    response.status_code, data.get("attempt_id", "none") if purchase else "none",
                )
                # Forward the original JSON bytes, preserving numeric values and fields.
                return Response(response.content, status=response.status_code, mimetype="application/json")
            if response.status_code in (400, 404, 405, 409, 500, 502, 503):
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    code = error.get("code")
                    attempt_id = error.get("attempt_id")
                    if (isinstance(code, str) and code in ERROR_MESSAGES
                            and (attempt_id is None or valid_attempt_id(attempt_id))):
                        app.logger.info(
                            "frontend operation=%s status=%s code=%s attempt_id=%s",
                            operation, response.status_code, code, attempt_id or "none",
                        )
                        # Keep meaningful statuses/IDs, but never relay arbitrary diagnostics.
                        return error_response(code, response.status_code, attempt_id)
    except requests.RequestException:
        app.logger.warning("frontend operation=%s downstream unavailable", operation)
        return error_response("outcome_unknown" if purchase else "service_unavailable", 503, known_attempt_id)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        pass
    # A bad purchase response cannot prove whether Order completed the purchase.
    app.logger.warning("frontend operation=%s malformed response attempt_id=%s", operation, known_attempt_id)
    return error_response("outcome_unknown" if purchase else "bad_upstream", 502, known_attempt_id)


@app.get("/health")
def health():
    return jsonify(service="frontend", status="ok"), 200


@app.get("/search/<topic>")
def search(topic):
    return forward("search", "/search/" + quote(topic, safe=""))


@app.get("/info/<int:item_number>")
def info(item_number):
    return forward("info", f"/info/{item_number}", item_number)


@app.post("/purchase/<int:item_number>")
def purchase(item_number):
    if not 1 <= item_number <= MAX_INTEGER or request.get_data().strip():
        return error_response("invalid_input", 400)
    return forward("purchase", f"/purchase/{item_number}", item_number)


app.logger.info("Frontend service started")
