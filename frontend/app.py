import logging
import os
import sys

from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)
app.config["CATALOG_URL"] = os.environ.get("CATALOG_URL", "http://localhost:5001")
app.config["ORDER_URL"] = os.environ.get("ORDER_URL", "http://localhost:5002")


@app.get("/health")
def health():
    return jsonify(service="frontend", status="ok"), 200


app.logger.info("Frontend service started")
