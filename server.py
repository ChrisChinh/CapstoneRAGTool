from src.azure_client import AzureClient
from flask import Flask, request, jsonify, render_template
import logging



class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)

    def load_page(self):
        indexes = self.azure_client.get_index_names()
        indexes = [{"id": idx, "name": idx, "created_at": "N/A"} for idx in indexes]
        return render_template("index.html", indexes=indexes)
        

    def add_route(self, route, handler):
        self.app.add_url_rule(route, view_func=handler, methods=['POST', 'GET'])

    def start(self):
        self.app.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = Server(config_path="config/config.yaml")
    server.start()