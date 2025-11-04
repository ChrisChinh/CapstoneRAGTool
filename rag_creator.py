import os
from langchain_community.document_loaders import PyMuPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_community.graphs import FalkorDBGraph

api_key = os.getenv("OPENROUTER_API_KEY")
llm = ChatOpenAI(
    model="openai/gpt-5", 
    api_key=api_key,
    base_url="https://openrouter.ai/api/v1",
    temperature=0,
)

# Connect to FalkorDB (default Docker config: localhost:6379, database 'test')
print("Connecting to FalkorDB...")
graph = FalkorDBGraph(
    database="ipp_docs",
    host="localhost",
    port=6379,
)
print("Connected to FalkorDB.")

# Load and process PDF
print("Loading PDF...")
pdf_path = "ipps.pdf"  # Replace with your PDF path
loader = PyMuPDFLoader(pdf_path)
docs = loader.load()
print("PDF loaded.")

# Split into chunks for processing
print("Splitting documents into chunks...")
splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
chunks = splitter.split_documents(docs)
print(f"Document split into {len(chunks)} chunks.")

# Build graph using LLM transformer
print("Building graph documents...")
transformer = LLMGraphTransformer(llm=llm)
graph_docs = transformer.convert_to_graph_documents(chunks)
print("Graph documents created.")

# Add to FalkorDB
graph.add_graph_documents(graph_docs)

print("Graph built and added to FalkorDB successfully!")