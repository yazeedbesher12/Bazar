import logging
import sys

from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

app = Flask(__name__, static_folder=None)


@app.get("/health")
def health():
    return jsonify(service="catalog", status="ok"), 200


app.logger.info("Catalog service started")
