"""Small host-side CLI; all requests go through Frontend."""

import argparse
import json
import os
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse

import requests

MAX_INTEGER = 2**63 - 1


def item_number(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("item_number must be a positive integer") from None
    if not 1 <= number <= MAX_INTEGER:
        raise argparse.ArgumentTypeError("item_number must be within 1..9223372036854775807")
    return number


def reject_constant(value):
    raise ValueError("Non-finite JSON number")


def price_value(value):
    if type(value) not in (int, Decimal):
        raise ValueError("Invalid price")
    price = Decimal(value)
    if (not price.is_finite() or not 0 <= price <= Decimal("92233720368547758.07")
            or price.as_tuple().exponent < -2):
        raise ValueError("Invalid price")
    return price


def valid_id(value):
    return type(value) is int and 1 <= value <= MAX_INTEGER


def valid_attempt_id(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def valid_title(book):
    return isinstance(book, dict) and isinstance(book.get("title"), str) and bool(book["title"].strip())


def show_result(command, data, requested_id):
    if command == "search":
        if not isinstance(data, list) or not all(
            valid_title(book) and valid_id(book.get("id")) for book in data
        ):
            raise ValueError("Invalid search response")
        if not data:
            print("No books found.")
        for book in data:
            print(f"{book['id']}: {book['title']}")
        return

    if not valid_title(data):
        raise ValueError("Invalid book response")
    price = price_value(data.get("price"))
    if command == "info":
        if (not valid_id(data.get("id")) or data["id"] != requested_id
                or not isinstance(data.get("topic"), str) or not data["topic"].strip()
                or type(data.get("quantity")) is not int or not 0 <= data["quantity"] <= MAX_INTEGER):
            raise ValueError("Invalid info response")
        print(f"Book {data['id']}: {data['title']}")
        print(f"Topic: {data['topic']}\nQuantity: {data['quantity']}\nPrice: {price:.2f}")
        return

    if (not valid_attempt_id(data.get("attempt_id")) or not valid_id(data.get("book_id"))
            or data["book_id"] != requested_id or data.get("status") != "completed"
            or type(data.get("quantity")) is not int or data["quantity"] != 1):
        raise ValueError("Invalid receipt")
    for key in ("created_at", "updated_at"):
        value = data.get(key)
        if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("Invalid receipt timestamp")
    print(f"bought book {data['title']}")
    print(f"Attempt: {data['attempt_id']}\nBook: {data['book_id']}\nQuantity: 1\nPrice: {price:.2f}")
    print(f"Status: completed\nCompleted at: {data['updated_at']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", default=os.environ.get("FRONTEND_URL", "http://localhost:5000"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("search", help="Find books by topic").add_argument("topic")
    commands.add_parser("info", help="Show a book").add_argument("item_number", type=item_number)
    commands.add_parser("purchase", help="Buy exactly one copy (no automatic retry)").add_argument(
        "item_number", type=item_number
    )
    args = parser.parse_args()
    base_url = args.frontend_url.rstrip("/")
    try:
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            parser.error("--frontend-url must be an HTTP or HTTPS URL")
    except ValueError:
        parser.error("--frontend-url must be a valid HTTP or HTTPS URL")
    requested_id = getattr(args, "item_number", None)
    suffix = quote(args.topic, safe="") if args.command == "search" else str(requested_id)
    method = "POST" if args.command == "purchase" else "GET"
    try:
        # No retries or redirects, especially for non-idempotent purchases.
        with requests.request(
            method, f"{base_url}/{args.command}/{suffix}", timeout=(2, 60), allow_redirects=False,
        ) as response:
            data = json.loads(response.content, parse_float=Decimal, parse_constant=reject_constant)
            if response.status_code != (201 if args.command == "purchase" else 200):
                error = data.get("error") if isinstance(data, dict) else None
                if (not isinstance(error, dict) or not isinstance(error.get("code"), str)
                        or not isinstance(error.get("message"), str)):
                    raise ValueError("Invalid error response")
                attempt_id = error.get("attempt_id")
                if attempt_id is not None and not valid_attempt_id(attempt_id):
                    raise ValueError("Invalid attempt ID")
                print(f"HTTP {response.status_code}: {error['message']} ({error['code']})", file=sys.stderr)
                if attempt_id is not None:
                    print(f"Attempt: {attempt_id}", file=sys.stderr)
                if args.command == "purchase":
                    print("Do not automatically repeat a purchase; repeating creates another attempt.", file=sys.stderr)
                return 1
            show_result(args.command, data, requested_id)
    except requests.RequestException:
        print("Frontend unreachable or request timed out.", file=sys.stderr)
        if args.command == "purchase":
            print("Purchase outcome may be unknown; do not retry automatically.", file=sys.stderr)
        return 1
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        print("Malformed response from Frontend.", file=sys.stderr)
        if args.command == "purchase":
            print("Purchase outcome may be unknown; do not retry automatically.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
