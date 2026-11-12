import logging
import os
import sys

from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)
app.config["CATALOG_URL"] = os.environ.get("CATALOG_URL", "http://localhost:5001")


@app.get("/health")
def health():
    return jsonify(service="order", status="ok"), 200


app.logger.info("Order service started")
