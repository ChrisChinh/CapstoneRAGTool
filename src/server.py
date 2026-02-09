from azure_client import AzureClient
from flask import Flask, request, jsonify
import logging



class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)

    def load_page(self):
        return "Hello World"
        

    def add_route(self, route, handler):
        self.app.add_url_rule(route, view_func=handler, methods=['POST', 'GET'])

    def start(self):
        self.app.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = Server(config_path="config/config.yaml")
    server.start()