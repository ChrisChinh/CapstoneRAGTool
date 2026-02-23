from src.azure_client import AzureClient
from flask import Flask, request, jsonify, render_template
from src.schemas import IndexList, IndexInfo
import datetime
import logging



class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)
        self.index_info_list = IndexList()

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)
        self.add_route("/delete_index", self.delete_index)
        self.add_route("/create_index", self.create_index)
        self.add_route("/show_create_index", self.show_create_index)
        self.add_route("/edit_index_page", self.edit_index_page)
        self.add_route("/edit_index", self.edit_index)

    def _return_message(self, message, success=True):
        if success:
            return render_template("success_message.html", message=message)
        else:
            return render_template("failure_message.html", message=message)

    def create_index(self):
        form_data = request.form
        index_name = form_data.get('name')
        search_dim = int(form_data.get('dimensions', 3072))
        chunk_size = int(form_data.get('chunk_size', 800))
        description = form_data.get('description', 'None provided')
        uploaded_files = request.files.getlist('files')

        if not index_name:
            return jsonify({"error": "Index name is required"}), 400
        try:
            self.azure_client.create_index(index_name, search_dim=search_dim)
            for file in uploaded_files:
                if file.filename.endswith('.pdf'):
                    self.azure_client.upload_pdf(file, index_name, chunk_size=chunk_size)

            index_info = IndexInfo(
                name=index_name,
                dimensions=search_dim,
                created_at=datetime.datetime.now().isoformat(timespec='minutes'),
                description=description[:100], # Limit description to 100 chars for display
                status='active'
            )
            self.index_info_list.save_index_info(index_info)
            return self._return_message(f"Index '{index_name}' created and files uploaded successfully")
        except Exception as e:
            logging.error(f"Error creating index: {e}")
            return self._return_message(f"Error creating index: {e}", success=False)
        
    def edit_index(self):
        form_data = request.form
        index_name = form_data.get('name')
        description = form_data.get('description', 'None provided')
        files = request.files.getlist('files')

        if not index_name:
            return jsonify({"error": "Index name is required"}), 400

        try:
            self.index_info_list.update_index_info(index_name, description=description)
            for file in files:
                chunk_size = int(form_data.get('chunk_size', 800))
                if file.filename.endswith('.pdf'):
                    self.azure_client.upload_pdf(file, index_name, chunk_size=chunk_size)
            return self._return_message(f"Index '{index_name}' updated successfully")
        except Exception as e:
            logging.error(f"Error editing index: {e}")
            return self._return_message(f"Error editing index: {e}", success=False)

    def edit_index_page(self):
        index_name = request.form.get("index_name")
        if not index_name:
            return self._return_message("Index name is required to edit", success=False)
        info = self.index_info_list.get_index_info(index_name)
        if info is None:
            info = IndexInfo(name=index_name, dimensions=0, created_at="N/A", description="No description provided", status="unknown")
        names = self.azure_client.get_index_names()
        for idx in names:
            if idx['name'] == index_name:
                info.documents = idx.get('document_count')
                break
        return render_template("edit_index.html", index=info)
        

    def show_create_index(self):
        return render_template("create_index.html")

    def load_page(self):
        indexes: list[dict] = self.azure_client.get_index_names()
        for idx in indexes:
            idx.setdefault("created_at", "N/A")
            idx.setdefault("status", "active")
            idx.setdefault("description", "No description provided")
            info = self.index_info_list.get_index_info(idx['name'])
            if info is not None:
                idx["created_at"] = info.created_at
                idx["status"] = info.status
                idx["description"] = info.description
        return render_template("index.html", indexes=indexes)
    
    def delete_index(self):
        index_name = request.form.get("index_name")
        if not index_name:
            return jsonify({"error": "Index name is required"}), 400
        
        try:
            self.azure_client.delete_index(index_name)
            self.index_info_list.delete_index_info(index_name)
            return self._return_message(f"Index '{index_name}' deleted successfully")
        except Exception as e:
            logging.error(f"Error deleting index: {e}")
            return self._return_message(f"Error deleting index: {e}", success=False)
        

    def add_route(self, route, handler):
        self.app.add_url_rule(route, view_func=handler, methods=['POST', 'GET'])

    def start(self):
        self.app.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = Server(config_path="config/config.yaml")
    server.start()