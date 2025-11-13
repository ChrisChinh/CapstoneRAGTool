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

ENDPOINT = "https://chris-rag-testing.search.azure.us"
ADMIN_KEY = os.getenv("AZURE_ADMIN_KEY")
INDEX_NAME = "ipp-documentation"


AZURE_OPENAI_ENDPOINT = "https://sdi-byu-capstone-azure-openai.openai.azure.us/"
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_EMBEDDING = "embedding"
AZURE_OPENAI_VERSION = "2024-02-01"

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
    def __init__(self, name):
        self.index_client = SearchIndexClient(
            endpoint=ENDPOINT,
            credential=AzureKeyCredential(ADMIN_KEY)
        )
        self.name = name

        if not self._index_exists(INDEX_NAME):
            self.create_index()


        self.index: SearchIndex = self.index_client.get_index(INDEX_NAME)
        self.logger = logging.getLogger(__name__)
        self.logger.level = logging.DEBUG

        self.openai_client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
            api_version=AZURE_OPENAI_VERSION
        )

        self.search_client = SearchClient(
            endpoint=ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(ADMIN_KEY)
        )

    def _index_exists(self, name):
        existing = self.index_client.list_index_names()
        return name in existing
       
    def create_index(self):
        # First, try deleting the old one
        try:
            self.index_client.delete_index(INDEX_NAME)
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
            name=INDEX_NAME,
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
            model=AZURE_EMBEDDING,
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


        
