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
    HnswVectorSearchAlgorithmConfiguration
)

import tiktoken
from pypdf import PdfReader
import logging
import os
import yaml

INDEX_FIELDS = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="page", type=SearchFieldDataType.Int32),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=3072,
            vector_search_configuration="vec-config",
        ),
    ]

class AzureClient:
    def __init__(self, config_path):

        # Variables to be loaded from config
        self.endpoint, self.api_key = None, None
        self.azure_openai_endpoint, self.azure_openai_key, self.azure_openai_version = None, None, None
        self.index_name = None
        self.embedding_model = None
        self.completion_model = None

        self._load_config(config_path)

        # Initialize Azure Search clients
        self.index_client = SearchIndexClient(
            endpoint=self.endpoint,
            credential=AzureKeyCredential(self.api_key)
        )

        if not self._index_exists(self.index_name):
            self.create_index()


        self.index: SearchIndex = self.index_client.get_index(self.index_name)
        self.logger = logging.getLogger(__name__)
        self.logger.level = logging.DEBUG

        self.openai_client = AzureOpenAI(
            azure_endpoint=self.azure_openai_endpoint,
            api_key=self.azure_openai_key,
            api_version=self.azure_openai_version
        )

        self.search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.api_key)
        )

    def _load_config(self, path):
        config: dict = yaml.load(open(path, 'r'), Loader=yaml.FullLoader)
        assert config is not None, "Config file is empty or invalid"

        self.endpoint = config.get("azure_config", {}).get("endpoint", None)
        self.api_key = config.get("azure_config", {}).get("api_key", None)

        self.azure_openai_endpoint = config.get("azure_openai", {}).get("endpoint", None)
        self.azure_openai_key = config.get("azure_openai", {}).get("api_key", None)
        self.azure_openai_version = config.get("azure_openai", {}).get("api_version", None)
        self.embedding_model = config.get("azure_openai", {}).get("embedding_model", None)
        self.completion_model = config.get("azure_openai", {}).get("completion_model", None)

        self.index_name = config.get("index_name")

        self.api_key = os.getenv(self.api_key)
        self.azure_openai_key = os.getenv(self.azure_openai_key)

        assert all([self.endpoint, self.api_key,
                    self.azure_openai_endpoint, self.azure_openai_key, self.azure_openai_version,
                    self.embedding_model, self.completion_model, self.index_name]), "Missing configuration values"
        



    def _index_exists(self, name):
        existing = self.index_client.list_index_names()
        return name in existing
       
    def create_index(self):
        # First, try deleting the old one
        try:
            self.index_client.delete_index(self.index_name)
        except:
            pass

        algorithm = HnswVectorSearchAlgorithmConfiguration(
            name='vec-config',
            kind="hnsw"
        )

        vector_search = VectorSearch(
            algorithm_configurations=[
                algorithm
            ]
        )

        self.index = SearchIndex(
            name=self.index_name,
            fields = INDEX_FIELDS,
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
        response = self.openai_client.embeddings.create(
            model=self.embedding_model,
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


        
