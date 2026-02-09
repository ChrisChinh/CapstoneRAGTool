from src.azure_client import AzureClient
from flask import Flask, request, jsonify, render_template
import logging



class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)
        self.add_route("/delete_index", self.delete_index)

    def load_page(self):
        indexes = self.azure_client.get_index_names()
        indexes = [{"id": idx, "name": idx, "created_at": "N/A"} for idx in indexes]
        return render_template("index.html", indexes=indexes)
    
    def delete_index(self):
        index_name = request.form.get("index_name")
        if not index_name:
            return jsonify({"error": "Index name is required"}), 400
        
        try:
            self.azure_client.delete_index(index_name)
            return jsonify({"message": f"Index '{index_name}' deleted successfully"})
        except Exception as e:
            logging.error(f"Error deleting index: {e}")
            return jsonify({"error": str(e)}), 500
        

    def add_route(self, route, handler):
        self.app.add_url_rule(route, view_func=handler, methods=['POST', 'GET'])

    def start(self):
        self.app.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = Server(config_path="config/config.yaml")
    server.start()