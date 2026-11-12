"""Print a repeatable CLI sample through the in-process three-service harness."""

import sys
import tempfile
from unittest import mock

from test_bazar import ServiceHarness, load_module


def run(command, client_module):
    with mock.patch.object(sys, "argv", ["client.py", *command]):
        result = client_module.main()
    if result:
        raise SystemExit(result)


def main():
    client_module = load_module("bazar_sample_client", "client/client.py")
    with tempfile.TemporaryDirectory() as directory:
        harness = ServiceHarness(directory)
        with harness.routed_requests():
            print("> client.py search \"distributed systems\"")
            run(["search", "distributed systems"], client_module)
            print("\n> client.py info 1")
            run(["info", "1"], client_module)
            print("\n> client.py purchase 1")
            run(["purchase", "1"], client_module)


if __name__ == "__main__":
    main()
