from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
)

import tiktoken
from pypdf import PdfReader
import logging
import os
import yaml
# for website scraping
import trafilatura
from lxml import html
from urllib.parse import urlparse
import azure.functions as func

# The schema for our search index
def create_index_schema(search_dim=3072):
    return [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="page", type=SearchFieldDataType.Int32),
            SearchField(
                name="contentVector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                vector_search_dimensions=search_dim,
                vector_search_profile_name="vec-profile",
            ),
        ]

class ModelWrapper:
    """
    Wrapper class to create Azure OpenAI models from YAML configuration dicts
    """
    def __init__(self, config_dict: dict):
        self.model = config_dict.get("model")
        self.api_key = os.getenv(config_dict.get("api_key"))
        self.endpoint = config_dict.get("endpoint")
        self.api_version = config_dict.get("api_version")

        self.model = AzureOpenAI(
            azure_endpoint=self.endpoint,
            api_key=self.api_key,
            api_version=self.api_version
        )

    def get_model(self):
        return self.model


class AzureClient:
    def __init__(self, config_path):

        # Load the YAML configuration values
        self._load_config(config_path)

        # Initialize Azure Search clients
        # This is what is used to search our RAG database
        self.index_client = SearchIndexClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.api_key)
        )

        self.logger = logging.getLogger(__name__)
        self.logger.level = logging.DEBUG

        # Init the embeddings and completions model
        self.embeddings_model = ModelWrapper(self.embedding_config).get_model()
        self.embedding_name = self.embedding_config.get("model")
        self.completions_model = ModelWrapper(self.completion_config).get_model()

    def _load_config(self, path):
        """
        Open a YAML file and parse for the necessary information on endpoints and 
        API keys"""
        config: dict = yaml.load(open(path, 'r'), Loader=yaml.FullLoader)
        assert config is not None, "Config file is empty or invalid"

        self.endpoint = config.get("search_config", {}).get("endpoint", None)
        self.api_key = config.get("search_config", {}).get("api_key", None)
        self.api_key = os.getenv(self.api_key)

        self.embedding_config = config.get("embedding", {})
        self.completion_config = config.get("completions", {})

        # self.api_key = os.getenv(self.api_key)

        assert all([self.endpoint, self.api_key, self.embedding_config]), "Missing configuration values"


    def _index_exists(self, index_name):
        """
        Check if an index exists within the Azure database
        """
        existing = self.index_client.list_index_names()
        return index_name in existing

    def get_index_names(self):
        """
        Returns a list of dictionaries containing index details
        """
        indexes = []
        for name in self.index_client.list_index_names():
            stats = self.index_client.get_index_statistics(name)
            indexes.append({
                "name": name,
                "id": name,
                "document_count": stats['document_count'],
            })
        return indexes

    def _get_search_client(self, index_name):
        """
        Returns a SearchClient for the specified index
        """
        return SearchClient(
            endpoint=self.endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(self.api_key)
        )
       
    def create_index(self, index_name, search_dim=3072):
        """
        Creates a new RAG index with the specified name.
        If the index already exists, it will be deleted and recreated.
        """
        try:
            self.index_client.delete_index(index_name)
        except:
            pass

        algorithm = HnswAlgorithmConfiguration(
            name='vec-config',
        )

        vector_search = VectorSearch(
            algorithms=[algorithm],
            profiles=[
                VectorSearchProfile(
                    name="vec-profile",
                    algorithm_configuration_name="vec-config"
                )
            ]
        )

        index = SearchIndex(
            name=index_name,
            fields=create_index_schema(search_dim),
            vector_search=vector_search
        )

        self.index_client.create_index(index)
        self.logger.info(f"Created index: {index_name}")

    def _chunk_pdf(self, doc, chunk_size=800):
        enc = tiktoken.get_encoding("cl100k_base")


        def chunk_text(text, max_tokens=chunk_size):
            tokens = enc.encode(text)
            chunks = []
            for i in range(0, len(tokens), max_tokens):
                chunk_tokens = tokens[i:i + max_tokens]
                chunk_text = enc.decode(chunk_tokens)
                chunks.append(chunk_text)
            return chunks

        reader = PdfReader(doc)
        all_chunks = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            chunks = chunk_text(text)
            for c in chunks:
                all_chunks.append((i + 1, c))

        self.logger.info(f"Extracted {len(all_chunks)} chunks from PDF")
        return all_chunks
    
    def _embed_text(self, text):
        response = self.embeddings_model.embeddings.create(
            model=self.embedding_name,
            input=text
        )

        return response.data[0].embedding
            
    def upload_pdf(self, pdf, index_name, chunk_size=800, upload_freq=50):
        """
        Uploads a PDF to the specified index.
        Creates the index if it doesn't exist.
        """
        # Create index if it doesn't exist
        if not self._index_exists(index_name):
            self.create_index(index_name)

        self.logger.info(f"Beginning PDF upload to index '{index_name}', please wait...")
        chunks = self._chunk_pdf(pdf, chunk_size=chunk_size)

        search_client = self._get_search_client(index_name)

        batch = []
        for i, (page, text) in enumerate(chunks):
            emb = self._embed_text(text)
            doc = {
                "id": f"doc-{i}",
                "content": text,
                "page": page,
                "contentVector": emb
            }
            batch.append(doc)

            if len(batch) >= upload_freq:
                search_client.upload_documents(documents=batch)
                batch.clear()
        
        if batch:
            search_client.upload_documents(documents=batch)

        self.logger.info(f"All PDF chunks uploaded to index '{index_name}'")

    def query_db(self, query, index_name, k=5):
        """
        Queries the specified index for relevant content.
        """
        if not self._index_exists(index_name):
            raise ValueError(f"Index '{index_name}' does not exist")

        query_emb = self._embed_text(query)
        search_client = self._get_search_client(index_name)

        results = search_client.search(
            search_text=None,
            vectors=[
                {
                    "value": query_emb,
                    "fields": "contentVector",
                    "k": k
                }
            ]
        )
        text_chunks = []
        for r in results:
            text_chunks.append(r['content'])
        return text_chunks

    # def search_url(self, url:str):
    #     if not url:
    #         print("Error: no URL provided")
    #         return None
    #     try:
    #         print("Attempting to download and extract content from URL...")
    #         downloaded = trafilatura.fetch_url(url)
    #         if downloaded is None:
    #             print("Error: Failed to download content.")
    #             return None
    #         text = trafilatura.extract(downloaded)
    #         if text is None:
    #             print("Error: Failed to extract content.")
    #             return None
    #         # --- TEST PRINT HERE ---
    #         print("\n--- SCRAPED CONTENT START ---")
    #         print(text[:500]) # Print first 500 chars to terminal
    #         print("--- SCRAPED CONTENT END ---\n")
    #         return text
    #     except Exception as e:
    #         print(f"An error occurred: {str(e)}")
    #         return None
        
    def upload_text(self, text, index_name, chunk_size=800, upload_freq=50):
        # 1. Debug: Check if we even have text
        if not text or len(text) < 10:
            print("❌ ERROR: Text content is empty or too short!")
            return False

        print(f"✅ Text found: {len(text)} characters. Proceeding...")

        if not self._index_exists(index_name):
            print(f"ℹ️ Creating new index '{index_name}'...")
            self.create_index(index_name) # Ensure this matches your embedding dim!

        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        print(f"📊 Token count: {len(tokens)}")

        chunks = []
        for i in range(0, len(tokens), chunk_size):
            chunk_tokens = tokens[i:i + chunk_size]
            chunks.append(enc.decode(chunk_tokens))
        
        print(f"✂️ Total chunks created: {len(chunks)}")

        search_client = self._get_search_client(index_name)
        batch = []

        for i, chunk_content in enumerate(chunks):
            try:
                # Debug: Print before embedding
                # print(f"   Embedding chunk {i}...") 
                emb = self._embed_text(chunk_content)
                
                # CRITICAL DEBUG: Check dimensions
                if i == 0:
                    print(f"📏 Embedding Dimension Detected: {len(emb)}")
                    # You can compare this to what your index expects here

                doc = {
                    "id": f"url-chunk-{i}", 
                    "content": chunk_content,
                    "page": 1, 
                    "contentVector": emb
                }
                batch.append(doc)

                if len(batch) >= upload_freq:
                    print(f"✅   Uploading batch of {len(batch)}...")
                    #search_client.upload_documents(documents=batch)
                    batch.clear()
                    
            except Exception as e:
                print(f"❌ CRITICAL ERROR on chunk {i}: {str(e)}")
                break 

        if batch:
            print(f"   Uploading final batch of {len(batch)}...")
            try:
                # search_client.upload_documents(documents=batch)
                print("✅ Final batch uploaded successfully!")
            except Exception as e:
                print(f"❌ CRITICAL ERROR on final batch: {str(e)}")

        print(f"🏁 Finished processing.")
        return True
    
        # Fetch, Extract, and Find Links
    def process_url_and_find_links(self, current_url):
        print(f"\n--- Processing: {current_url} ---")
        
        # Download the HTML string
        downloaded = trafilatura.fetch_url(current_url)
        
        if downloaded is None:
            print(f"❌ Failed to download {current_url}")
            return None, []

        # A. Extract Main Text for RAG (Trafilatura's specialty)
        # include_comments=False helps keep RAG indexes clean
        main_text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)

        # B. Extract Links for Navigation (using lxml)
        # parse the raw HTML to find navigation links
        tree = html.fromstring(downloaded)
        
        # This automatically converts relative links (/about) to absolute (https://site.com/about)
        tree.make_links_absolute(current_url)
        
        # Get all <a> tags
        raw_links = tree.xpath('//a')
        
        viable_links = []
        seen_urls = set()
        
        # Get current domain to avoid jumping to Instagram/Facebook/etc unintentionally
        base_domain = urlparse(current_url).netloc

        for link in raw_links:
            href = link.get('href')
            text = link.text_content().strip()
            
            if not href: continue
            
            # Filter: Skip javascript, mailto, and anchors
            if href.startswith(('mailto:', 'javascript:', '#')): continue
            
            # Filter: Optional - Stay on the same domain
            if urlparse(href).netloc != base_domain:
                continue
                
            # Deduplicate
            if href not in seen_urls and len(text) > 0:
                seen_urls.add(href)
                viable_links.append({'text': text, 'url': href})

        return main_text, viable_links

        # INTERACTIVE SELECTION LOOP
    def search_url_loop(self, uploaded_url, index_name, chunk_size=800, upload_freq=50):
        start_url = uploaded_url
        queue = [start_url]
        visited = set()

        while queue:
            current_url = queue.pop(0)
            
            if current_url in visited:
                continue
                
            visited.add(current_url)

            # Run the processor
            extracted_text, links = self.process_url_and_find_links(current_url)
            
            if extracted_text:
                print(f"Pushing {current_url} content to Azure...")
                self.upload_text(extracted_text, index_name, chunk_size=chunk_size, upload_freq=upload_freq)
            else:
                print(f"No usable text found on {current_url}.")

            if not links:
                print("No viable links found.")
                continue

            # Display links to user
            print(f"\nFound {len(links)} links on {current_url}:")
            for i, link in enumerate(links):
                # Print first 50 chars of text to keep it readable
                print(f"[{i}] {link['text'][:50]}... -> {link['url']}")

            print("\nWhich links to add to the queue?")
            print("Type numbers (e.g., '1, 5, 10'), 'all', or press Enter to skip.")
            
            selection = input("Selection > ").strip().lower()

            if selection == 'all':
                for link in links:
                    if link['url'] not in visited:
                        queue.append(link['url'])
                print(f"Added {len(links)} links to queue.")
                
            elif selection:
                try:
                    indices = [int(x.strip()) for x in selection.split(',')]
                    count = 0
                    for i in indices:
                        if 0 <= i < len(links):
                            target_url = links[i]['url']
                            if target_url not in visited and target_url not in queue:
                                queue.append(target_url)
                                count += 1
                    print(f"Added {count} links to queue.")
                except ValueError:
                    print("Invalid input. Moving on...")

    def get_model(self):
        return self.completions_model
    
    def delete_index(self, index_name):
        """
        Deletes the specified index from the Azure Search service.
        """
        if not self._index_exists(index_name):
            raise ValueError(f"Index '{index_name}' does not exist")

        self.index_client.delete_index(index_name)
        self.logger.info(f"Deleted index: {index_name}")

