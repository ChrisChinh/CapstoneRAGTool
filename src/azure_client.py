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

# The schema for our search index
INDEX_FIELDS = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="page", type=SearchFieldDataType.Int32),
        SearchField(
            name="contentVector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            vector_search_dimensions=3072,
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
        Returns a list of all index names stored within the IndexClient
        """
        return list(self.index_client.list_index_names())

    def _get_search_client(self, index_name):
        """
        Returns a SearchClient for the specified index
        """
        return SearchClient(
            endpoint=self.endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(self.api_key)
        )
       
    def create_index(self, index_name):
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
            fields=INDEX_FIELDS,
            vector_search=vector_search
        )

        self.index_client.create_index(index)
        self.logger.info(f"Created index: {index_name}")

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
            

    def upload_pdf(self, pdf, index_name, upload_freq=50):
        """
        Uploads a PDF to the specified index.
        Creates the index if it doesn't exist.
        """
        # Create index if it doesn't exist
        if not self._index_exists(index_name):
            self.create_index(index_name)

        self.logger.info(f"Beginning PDF upload to index '{index_name}', please wait...")
        chunks = self._chunk_pdf(pdf)

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


    def get_model(self):
        return self.completions_model

