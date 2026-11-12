import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import requests


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path, environment=None):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, environment or {}, clear=False):
        spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def flask_response(response):
    return FakeResponse(response.status_code, response.data)


class ServiceHarness:
    def __init__(self, directory):
        self.catalog_db = str(Path(directory) / "catalog.sqlite3")
        self.order_db = str(Path(directory) / "order.sqlite3")
        self.catalog = load_module(
            "bazar_catalog_test", "catalog/app.py", {"CATALOG_DB_PATH": self.catalog_db}
        )
        self.order = load_module(
            "bazar_order_test",
            "order/app.py",
            {"ORDER_DB_PATH": self.order_db, "CATALOG_URL": "http://catalog:5001"},
        )
        self.frontend = load_module(
            "bazar_frontend_test",
            "frontend/app.py",
            {"CATALOG_URL": "http://catalog:5001", "ORDER_URL": "http://order:5002"},
        )

    def dispatch(self, method, url, **kwargs):
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        body = None
        content_type = None
        if "json" in kwargs:
            body = json.dumps(kwargs["json"])
            content_type = "application/json"
        if url.startswith("http://catalog:5001"):
            with self.catalog.app.test_client() as client:
                return flask_response(client.open(path, method=method, data=body, content_type=content_type))
        if url.startswith("http://order:5002"):
            with self.order.app.test_client() as client:
                return flask_response(client.open(path, method=method, data=body, content_type=content_type))
        if url.startswith("http://localhost:5000"):
            with self.frontend.app.test_client() as client:
                return flask_response(client.open(path, method=method, data=body, content_type=content_type))
        raise requests.ConnectionError(f"Unexpected test URL: {url}")

    @contextlib.contextmanager
    def routed_requests(self):
        with mock.patch("requests.request", side_effect=self.dispatch):
            yield

    def catalog_json(self, method, path, payload=None):
        with self.catalog.app.test_client() as client:
            response = client.open(path, method=method, json=payload)
            return response.status_code, response.get_json()

    def frontend_json(self, method, path):
        with self.frontend.app.test_client() as client, self.routed_requests():
            response = client.open(path, method=method)
            return response.status_code, response.get_json()

    def attempts(self):
        with closing(sqlite3.connect(self.order_db)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                "SELECT attempt_id, book_id, title, price_cents, status, created_at, updated_at "
                "FROM attempts ORDER BY created_at, attempt_id"
            )]


class BazarServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.harness = ServiceHarness(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_health_search_normalization_and_unknown_topic(self):
        expected = {
            "distributed systems": [
                {"id": 1, "title": "How to get a good grade in DOS in 40 minutes a day."},
                {"id": 2, "title": "RPCs for Noobs."},
            ],
            "undergraduate school": [
                {"id": 3, "title": "Xen and the Art of Surviving Undergraduate School."},
                {"id": 4, "title": "Cooking for the Impatient Undergrad."},
            ],
        }
        with self.harness.catalog.app.test_client() as client:
            self.assertEqual({"service": "catalog", "status": "ok"}, client.get("/health").get_json())
            for topic, books in expected.items():
                self.assertEqual(books, client.get("/search/" + topic).get_json())
            self.assertEqual(expected["distributed systems"], client.get(
                "/search/%20DISTRIBUTED%20SYSTEMS%20"
            ).get_json())
            self.assertEqual([], client.get("/search/unknown").get_json())

        with self.harness.routed_requests(), self.harness.frontend.app.test_client() as client:
            self.assertEqual({"service": "frontend", "status": "ok"}, client.get("/health").get_json())
            self.assertEqual(expected["undergraduate school"], client.get(
                "/search/undergraduate%20school"
            ).get_json())
            self.assertEqual([], client.get("/search/unknown").get_json())

    def test_information_for_all_books_and_missing_book_status(self):
        titles = [
            "How to get a good grade in DOS in 40 minutes a day.",
            "RPCs for Noobs.",
            "Xen and the Art of Surviving Undergraduate School.",
            "Cooking for the Impatient Undergrad.",
        ]
        with self.harness.routed_requests(), self.harness.frontend.app.test_client() as client:
            for book_id, title in enumerate(titles, 1):
                response = client.get(f"/info/{book_id}")
                self.assertEqual(200, response.status_code)
                self.assertEqual(book_id, response.get_json()["id"])
                self.assertEqual(title, response.get_json()["title"])
            missing = client.get("/info/99999")
            self.assertEqual(404, missing.status_code)
            self.assertEqual("not_found", missing.get_json()["error"]["code"])

    def test_catalog_updates_and_rejected_combined_update_is_atomic(self):
        status, changed = self.harness.catalog_json(
            "PATCH", "/update/1", {"price": 12.5, "quantity_delta": 3}
        )
        self.assertEqual(200, status)
        self.assertEqual(12.5, changed["price"])
        self.assertEqual(13, changed["quantity"])

        status, rejected = self.harness.catalog_json(
            "PATCH", "/update/1", {"price": 1, "quantity_delta": -14}
        )
        self.assertEqual(409, status)
        self.assertEqual("insufficient_stock", rejected["error"]["code"])
        status, unchanged = self.harness.catalog_json("GET", "/info/1")
        self.assertEqual(200, status)
        self.assertEqual((12.5, 13), (unchanged["price"], unchanged["quantity"]))

        status, decremented = self.harness.catalog_json("PATCH", "/update/1", {"quantity_delta": -1})
        self.assertEqual(200, status)
        self.assertEqual(12, decremented["quantity"])

    def test_catalog_rejects_invalid_update_payloads(self):
        invalid_payloads = [
            ({}, "application/json"),
            ({"unknown": 1}, "application/json"),
            ({"price": "1.00"}, "application/json"),
            ({"price": True}, "application/json"),
            ({"price": -1}, "application/json"),
            ({"price": 1.001}, "application/json"),
            ({"quantity_delta": "1"}, "application/json"),
            ({"quantity_delta": True}, "application/json"),
            ({"quantity_delta": 1.5}, "application/json"),
            ({"price": 1, "quantity_delta": None}, "application/json"),
        ]
        with self.harness.catalog.app.test_client() as client:
            for payload, content_type in invalid_payloads:
                with self.subTest(payload=payload):
                    response = client.patch(
                        "/update/1", data=json.dumps(payload), content_type=content_type
                    )
                    self.assertEqual(400, response.status_code)
                    self.assertEqual("invalid_input", response.get_json()["error"]["code"])
            for raw, content_type in ((b'{"price":', "application/json"), (b'{"price": 1}', "text/plain")):
                response = client.patch("/update/1", data=raw, content_type=content_type)
                self.assertEqual(400, response.status_code)
                self.assertEqual("invalid_input", response.get_json()["error"]["code"])

    def test_successful_purchase_stock_and_completed_snapshot(self):
        before = self.harness.catalog_json("GET", "/info/1")[1]
        status, receipt = self.harness.frontend_json("POST", "/purchase/1")
        self.assertEqual(201, status)
        self.assertEqual("completed", receipt["status"])
        after = self.harness.catalog_json("GET", "/info/1")[1]
        self.assertEqual(before["quantity"] - 1, after["quantity"])
        rows = self.harness.attempts()
        self.assertEqual(1, len(rows))
        self.assertEqual(receipt["attempt_id"], rows[0]["attempt_id"])
        self.assertEqual((1, 1000, "completed"), (
            rows[0]["book_id"], rows[0]["price_cents"], rows[0]["status"]
        ))

    def test_missing_and_exhausted_books_never_complete(self):
        missing_status, _ = self.harness.frontend_json("POST", "/purchase/99999")
        self.assertEqual(404, missing_status)
        self.assertEqual([], self.harness.attempts())

        self.harness.catalog_json("PATCH", "/update/1", {"quantity_delta": -10})
        exhausted_status, body = self.harness.frontend_json("POST", "/purchase/1")
        self.assertEqual(409, exhausted_status)
        self.assertEqual("insufficient_stock", body["error"]["code"])
        rows = self.harness.attempts()
        self.assertEqual(1, len(rows))
        self.assertEqual("failed", rows[0]["status"])
        self.assertFalse(any(row["status"] == "completed" for row in rows))

    def test_twelve_concurrent_frontend_purchases_from_five_copies(self):
        self.harness.catalog_json("PATCH", "/update/2", {"quantity_delta": -5})
        baseline = len(self.harness.attempts())

        def purchase_once(_):
            with self.harness.frontend.app.test_client() as client:
                response = client.post("/purchase/2")
                return response.status_code, response.get_json()

        with self.harness.routed_requests(), ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(purchase_once, range(12)))

        self.assertEqual(5, sum(status == 201 for status, _ in results))
        self.assertEqual(7, sum(
            status == 409 and body["error"]["code"] == "insufficient_stock"
            for status, body in results
        ))
        final = self.harness.catalog_json("GET", "/info/2")[1]
        self.assertEqual(0, final["quantity"])
        new_rows = self.harness.attempts()[baseline:]
        completed = [row for row in new_rows if row["status"] == "completed"]
        self.assertEqual(5, len(completed))
        self.assertEqual(12, len(new_rows))
        self.assertEqual(5, len({row["attempt_id"] for row in completed}))
        response_ids = {body["attempt_id"] for status, body in results if status == 201}
        self.assertEqual(response_ids, {row["attempt_id"] for row in completed})
        self.assertGreaterEqual(final["quantity"], 0)

    def test_persistence_when_databases_are_reinitialized(self):
        self.harness.catalog_json("PATCH", "/update/3", {"price": 17.25, "quantity_delta": 4})
        status, receipt = self.harness.frontend_json("POST", "/purchase/3")
        self.assertEqual(201, status)
        expected_book = self.harness.catalog_json("GET", "/info/3")[1]
        expected_attempts = self.harness.attempts()

        self.harness.catalog.initialize_db()
        self.harness.order.initialize_db()

        self.assertEqual(expected_book, self.harness.catalog_json("GET", "/info/3")[1])
        self.assertEqual(expected_attempts, self.harness.attempts())
        self.assertEqual(receipt["attempt_id"], expected_attempts[0]["attempt_id"])

    def test_frontend_forwards_meaningful_errors_and_sanitizes_malformed_data(self):
        with mock.patch("requests.request", return_value=FakeResponse(
            404, {"error": {"code": "not_found", "message": "internal detail"}}
        )), self.harness.frontend.app.test_client() as client:
            response = client.get("/info/9")
            self.assertEqual(404, response.status_code)
            self.assertEqual("Book not found.", response.get_json()["error"]["message"])

        with mock.patch("requests.request", return_value=FakeResponse(200, b"not-json")), \
                self.harness.frontend.app.test_client() as client:
            response = client.get("/info/1")
            self.assertEqual(502, response.status_code)
            self.assertEqual("bad_upstream", response.get_json()["error"]["code"])

        with mock.patch("requests.request", return_value=FakeResponse(201, b"not-json")), \
                self.harness.frontend.app.test_client() as client:
            response = client.post("/purchase/1")
            self.assertEqual(502, response.status_code)
            self.assertEqual("outcome_unknown", response.get_json()["error"]["code"])

    def test_frontend_returns_bounded_errors_when_services_are_unavailable(self):
        with mock.patch("requests.request", side_effect=requests.ConnectionError("offline")), \
                self.harness.frontend.app.test_client() as client:
            for path in ("/search/distributed%20systems", "/info/1"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(503, response.status_code)
                    self.assertEqual("service_unavailable", response.get_json()["error"]["code"])
            purchase = client.post("/purchase/1")
            self.assertEqual(503, purchase.status_code)
            self.assertEqual("outcome_unknown", purchase.get_json()["error"]["code"])

        # Search/info use Catalog and therefore remain available when only Order is down.
        def order_only_failure(method, url, **kwargs):
            if url.startswith("http://order:5002"):
                raise requests.ConnectionError("order offline")
            return self.harness.dispatch(method, url, **kwargs)

        with mock.patch("requests.request", side_effect=order_only_failure), \
                self.harness.frontend.app.test_client() as client:
            self.assertEqual(200, client.get("/search/distributed%20systems").status_code)
            self.assertEqual(200, client.get("/info/1").status_code)
            self.assertEqual(503, client.post("/purchase/1").status_code)

    def test_ambiguous_decrement_is_not_retried_and_remains_inspectable(self):
        calls = []

        def ambiguous(method, url, **kwargs):
            calls.append((method, url))
            if method == "GET":
                return FakeResponse(200, self.harness.catalog_json("GET", "/info/1")[1])
            raise requests.ConnectionError("response lost after possible decrement")

        with mock.patch("requests.request", side_effect=ambiguous), self.harness.order.app.test_client() as client:
            response = client.post("/purchase/1")
        self.assertEqual(503, response.status_code)
        error = response.get_json()["error"]
        self.assertEqual("outcome_unknown", error["code"])
        self.assertEqual(2, len(calls))
        rows = self.harness.attempts()
        self.assertEqual(1, len(rows))
        self.assertEqual(error["attempt_id"], rows[0]["attempt_id"])
        self.assertEqual("unknown", rows[0]["status"])

    def test_malformed_catalog_response_never_returns_purchase_success(self):
        responses = [
            FakeResponse(200, self.harness.catalog_json("GET", "/info/1")[1]),
            FakeResponse(200, {"id": 1, "title": "broken"}),
        ]
        with mock.patch("requests.request", side_effect=responses), self.harness.order.app.test_client() as client:
            response = client.post("/purchase/1")
        self.assertEqual(502, response.status_code)
        self.assertEqual("outcome_unknown", response.get_json()["error"]["code"])
        self.assertEqual("unknown", self.harness.attempts()[0]["status"])


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_module = load_module("bazar_client_test", "client/client.py")

    def run_client(self, arguments, response=None, exception=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        patcher = mock.patch("requests.request", side_effect=exception) if exception else mock.patch(
            "requests.request", return_value=response
        )
        with patcher, mock.patch.object(sys, "argv", ["client.py", *arguments]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.client_module.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_success_empty_and_missing(self):
        code, output, error = self.run_client(
            ["search", "distributed systems"],
            FakeResponse(200, [{"id": 1, "title": "DOS book"}]),
        )
        self.assertEqual((0, "1: DOS book\n", ""), (code, output, error))

        code, output, error = self.run_client(["search", "unknown"], FakeResponse(200, []))
        self.assertEqual((0, "No books found.\n", ""), (code, output, error))

        code, output, error = self.run_client(
            ["info", "99999"],
            FakeResponse(404, {"error": {"code": "not_found", "message": "Book not found."}}),
        )
        self.assertEqual(1, code)
        self.assertEqual("", output)
        self.assertIn("HTTP 404", error)

    def test_cli_purchase_output_and_connection_failure_exit_nonzero(self):
        receipt = {
            "attempt_id": "a" * 32,
            "book_id": 1,
            "title": "How to get a good grade in DOS in 40 minutes a day.",
            "price": 10,
            "quantity": 1,
            "status": "completed",
            "created_at": "2026-11-13T10:00:00+00:00",
            "updated_at": "2026-11-13T10:00:01+00:00",
        }
        code, output, error = self.run_client(["purchase", "1"], FakeResponse(201, receipt))
        self.assertEqual(0, code)
        self.assertIn("bought book How to get a good grade in DOS in 40 minutes a day.", output)
        self.assertEqual("", error)

        code, output, error = self.run_client(
            ["info", "1"], exception=requests.ConnectionError("offline")
        )
        self.assertEqual(1, code)
        self.assertEqual("", output)
        self.assertIn("Frontend unreachable", error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
