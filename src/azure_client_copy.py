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
import trafilatura
import azure.functions as func

# The schema for our search index
INDEX_FIELDS = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="page", type=SearchFieldDataType.Int32),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=3072,
            vector_search_profile_name="vec-profile",  # Changed from vector_search_configuration
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

        # If the specific RAG index listed does not exist, we create it
        if not self._index_exists(self.index_name):
            self.create_index()

        self.index: SearchIndex = self.index_client.get_index(self.index_name)
        self.logger = logging.getLogger(__name__)
        self.logger.level = logging.DEBUG

        # Init the embeddings and completions model
        self.embeddings_model = ModelWrapper(self.embedding_config).get_model()
        self.embedding_name = self.embedding_config.get("model")
        self.completions_model = ModelWrapper(self.completion_config).get_model()

        # Search client for the RAG index
        self.search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.api_key)
        )

    def _load_config(self, path):
        """
        Open a YAML file and parse for the necessary information on endpoints and 
        API keys"""
        config: dict = yaml.load(open(path, 'r'), Loader=yaml.FullLoader)
        assert config is not None, "Config file is empty or invalid"

        self.endpoint = config.get("search_config", {}).get("endpoint", None)
        self.api_key = config.get("search_config", {}).get("api_key", None)

        self.embedding_config = config.get("embedding", {})
        self.completion_config = config.get("completions", {})

        self.index_name = config.get("index_name")

        self.api_key = os.getenv(self.api_key)

        missing = [name for name, val in {
            "endpoint": self.endpoint,
            "api_key": self.api_key,
            "embedding_config": self.embedding_config,
            "completion_config": self.completion_config,
            "index_name": self.index_name
        }.items() if not val]

        if missing:
            raise ValueError(f"AzureClient configuration is missing: {', '.join(missing)}")


    def _index_exists(self, name):
        """
        Check if an index exists within the Azure database
        """
        existing = self.index_client.list_index_names()
        return name in existing
       
    def create_index(self):
        """
        Recreates the RAG index. Useful for testing or initializing a new index for future projects
        """
        try:
            self.index_client.delete_index(self.index_name)
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

        self.index = SearchIndex(
            name=self.index_name,
            fields=INDEX_FIELDS,
            vector_search=vector_search
        )

        self.index_client.create_index(self.index)

    def _chunk_pdf(self, doc):
        enc = tiktoken.get_encoding("cl100k_base")


        def chunk_text(text, max_tokens=800):
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
            

    def upload_pdf(self, pdf, upload_freq=50):
        self.logger.info("Beginning PDF upload, please wait...")
        chunks = self._chunk_pdf(pdf)

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
                self.search_client.upload_documents(documents=batch)
                batch.clear()
        
        if batch:
            self.search_client.upload_documents(documents=batch)

        self.logger.info("All PDF chunks uploaded to Azure Search")

    def query_db(self, query, k = 5):
        query_emb = self._embed_text(query)

        results = self.search_client.search(
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


    def get_model(self):
        return self.completions_model

    def search_url(self, url:str):
        if not url:
            print("Error: no URL provided")
            return False
        try:
            print("Attempting to download and extract content from URL...")
            downloaded = trafilatura.fetch_url(url)
            if downloaded is None:
                print("Error: Failed to download content.")
                return False
            text = trafilatura.extract(downloaded)
            if text is None:
                print("Error: Failed to extract content.")
                return False
            # --- TEST PRINT HERE ---
            print("\n--- SCRAPED CONTENT START ---")
            print(text[:500]) # Print first 500 chars to terminal
            print("--- SCRAPED CONTENT END ---\n")
            return True
        except Exception as e:
            print(f"An error occurred: {str(e)}")
            return False
        
