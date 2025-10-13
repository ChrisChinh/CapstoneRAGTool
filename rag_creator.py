from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI

import sys

# # 1. Load PDF
# loader = PyPDFLoader("ipps.pdf")
# docs = loader.load()

# # 2. Split into chunks
# splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
# chunks = splitter.split_documents(docs)

# # 3. Local sentence embedding model (fast + small)
# embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

# # 4. Store vectors with FAISS
# vectorstore = FAISS.from_documents(chunks, embeddings)
# vectorstore.save_local("ipp_index")

# Load the vectorstore from disk
vectorstore = FAISS.load_local(
    "ipp_index",
    HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2"),
    allow_dangerous_deserialization=True,
)

# 5. Create retriever
retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 4})

# 6. Connect LangChain to LM Studio (OpenAI-compatible API)
llm = ChatOpenAI(
    model="gpt-oss-20b",                 # whatever model name you’ve loaded in LM Studio
    openai_api_base="http://localhost:1234/v1",  # LM Studio’s API endpoint
    openai_api_key="lm-studio",          # arbitrary placeholder; LM Studio ignores it
    temperature=0.2                      # tweak creativity if you want
)

# 7. Combine into RetrievalQA chain
qa = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=retriever,
    chain_type="stuff",
)

# 8. Example query
prompt = "Please refactor this code snippet to use IPP instead of basic C. Functional parity should be preserved."
query = sys.stdin.readlines()
query = prompt + "\n" + "".join(query)
out = qa.invoke({"query": query})
print(out["result"] if isinstance(out, dict) and "result" in out else out)
