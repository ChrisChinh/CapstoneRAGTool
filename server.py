from src.azure_client import AzureClient
from flask import Flask, request, jsonify, render_template
import logging
import threading
import uuid


class Server:
    def __init__(self, config_path):
        self.azure_client = AzureClient(config_path)

        self.app  = Flask(__name__)
        self.add_route("/", self.load_page)
        self.add_route("/delete_index", self.delete_index)
        self.add_route("/create_index", self.create_index)
        self.add_route("/show_create_index", self.show_create_index)
        self.add_route("/process_selected_links", self.handle_selected_links)        
        self.job_progress = {} # shared tracker for background jobs
        self.app.add_url_rule("/status/<task_id>", view_func=self.get_status, methods=['GET'])
   
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
            if uploaded_url:
                extracted_text, links = self.azure_client.process_url_and_find_links(uploaded_url)
                # Upload the base text
                if extracted_text:
                    self.azure_client.upload_text(extracted_text, index_name, chunk_size=chunk_size)
                # If we found links, intercept the JSON response and send them to the HTML page instead!
                if links:
                    return render_template('select_links.html', links=links, current_url=uploaded_url, index_name=index_name)     
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
        
        # --- URL ---
    def handle_selected_links(self):
        selected_urls = request.form.getlist('selected_links')
        index_name = request.form.get('index_name', 'my-index') # Fallback if missing

        if not selected_urls:
            return "No links selected."

        task_id = str(uuid.uuid4())

        # Start the background thread
        thread = threading.Thread(
            target=self.background_scraping_job, 
            args=(task_id, selected_urls, index_name)
        )
        thread.start()
        # Immediately load the progress bar
        return render_template('download_progress.html', task_id=task_id)
    
    def get_status(self, task_id):
        data = self.job_progress.get(task_id, {"status": "unknown"})
        return jsonify(data)

    def background_scraping_job(self, task_id, url_list, index_name):
        total_urls = len(url_list)
        # Initialize the job on the bulletin board
        self.job_progress[task_id] = {"total": total_urls, "completed": 0, "status": "running"}

        for i, url in enumerate(url_list):
            try:
                print(f"Processing: {url}")
                extracted_text, _ = self.azure_client.process_url_and_find_links(url)
                if extracted_text:
                    self.azure_client.upload_text(extracted_text, index_name)
            except Exception as e:
                print(f"Error on {url}: {e}")
            
            # Update the board after finishing a URL
            self.job_progress[task_id]["completed"] = i + 1

        # Mark as finished when the loop exits
        self.job_progress[task_id]["status"] = "completed"

    def add_route(self, route, handler):
        self.app.add_url_rule(route, view_func=handler, methods=['POST', 'GET'])

    def start(self):
        self.app.run()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = Server(config_path="config/config.yaml")
    server.start()