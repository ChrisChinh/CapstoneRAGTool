from src.azure_client import AzureClient
from flask import Flask, request, jsonify, render_template
import logging



class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)
        self.add_route("/delete_index", self.delete_index)
        self.add_route("/create_index", self.create_index)
        self.add_route("/show_create_index", self.show_create_index)

    def create_index(self):
        form_data = request.form
        index_name = form_data.get('name')
        search_dim = int(form_data.get('dimensions', 3072))
        chunk_size = int(form_data.get('chunk_size', 800))
        uploaded_files = request.files.getlist('files')
        uploaded_url = request.form.get('url')
        if not index_name:
            return jsonify({"error": "Index name is required"}), 400
        try:
            self.azure_client.create_index(index_name, search_dim=search_dim)
            for file in uploaded_files:
                if file.filename.endswith('.pdf'):
                    self.azure_client.upload_pdf(file, index_name, chunk_size=chunk_size)
                elif uploaded_url:
                    self.azure_client.search_url_loop(uploaded_url, index_name, chunk_size=chunk_size)
                    # if text:
                    #     self.azure_client.upload_text(text, index_name, chunk_size=chunk_size)
            return jsonify({"message": f"Index '{index_name}' created and files uploaded successfully"})
        except Exception as e:
            logging.error(f"Error creating index: {e}")
            return jsonify({"error": str(e)}), 500

    def show_create_index(self):
        return render_template("create_index.html")

    def load_page(self):
        indexes: list[dict] = self.azure_client.get_index_names()
        for idx in indexes:
            idx.setdefault("created_at", "N/A")
            idx.setdefault("status", "active")
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