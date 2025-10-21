import os
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

PROMPT = """
Please refactor this code snippet to use IPP instead of basic C. Functional parity should be preserved.
"""

# Default system prompt to guide overall assistant behavior
DEFAULT_SYSTEM_PROMPT = (
    "You are an expert assistant that refactors C code to use Intel Integrated Performance Primitives (IPP). "
    "Use the context provided to inform your refactoring. "
    "Avoid referencing functions not present in the provided context unless they are standard IPP APIs."
)

class Model:
    def __init__(self, prompt: str = PROMPT, system_prompt: str | None = DEFAULT_SYSTEM_PROMPT):
        """RAG-backed refactoring model.

        Args:
            prompt: Instruction text prepended to user input (treated as part of the user message).
            system_prompt: High-level system instruction passed as a system message to the chat model.
        """
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vstore = FAISS.load_local(
            "ipp_index",
            self.embeddings,
            allow_dangerous_deserialization=True,
        )
        self.retriever = vstore.as_retriever(search_type="similarity", search_kwargs={"k": 4})

        # --- OpenRouter configuration ---
        # Configure ChatOpenAI to use OpenRouter's OpenAI-compatible endpoint.
        # Set OPENROUTER_API_KEY in your environment. You can also override the model and base URL.
        openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
        # Allow both OPENROUTER_BASE_URL and OPENAI_API_BASE for convenience
        openrouter_base = os.getenv("OPENROUTER_BASE_URL", os.getenv("OPENAI_API_BASE", "https://openrouter.ai/api/v1"))
        openrouter_key = os.getenv("OPENROUTER_API_KEY")

        self.llm = ChatOpenAI(
            model=openrouter_model,
            openai_api_base=openrouter_base,
            openai_api_key=openrouter_key,
            temperature=0.2,
        )

        # Build a chat prompt with an optional system message and a human message
        sys_msg = system_prompt or ""
        chat_prompt = ChatPromptTemplate.from_messages([
            ("system", sys_msg),
            (
                "human",
                # Provide both the instruction (existing prompt) and placeholders for context/question
                "{instruction}\n\nContext:\n{context}\n\nQuestion:\n{question}",
            ),
        ])

        # Inject the current instruction prompt as a partial to avoid passing it each call
        chat_prompt = chat_prompt.partial(instruction=prompt)

        self.qa = RetrievalQA.from_chain_type(
            llm=self.llm,
            retriever=self.retriever,
            chain_type="stuff",
            chain_type_kwargs={
                "prompt": chat_prompt,
            },
        )

        self.prompt = prompt
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def run(self, query: str) -> str:
        # Keep existing behavior of including the instruction with the query for backward compatibility.
        # Note: The chat prompt already includes the instruction; appending here further emphasizes it.
        query = self.prompt + "\n" + query
        # RetrievalQA.invoke returns a dict with a 'result' key by default
        out = self.qa.invoke({"query": query})
        return out["result"] if isinstance(out, dict) and "result" in out else str(out)
    
    def check_connection(self) -> bool:
        try:
            response = self.run("Hello")
            return bool(response)
        except Exception as e:
            print(f"Model connection error: {e}")
            return False

    # --------------- Configuration helpers ---------------
    def set_system_prompt(self, system_prompt: str) -> None:
        """Update the system prompt at runtime.

        Note: This recreates the underlying RetrievalQA chain to ensure the new
        system message is used in subsequent calls.
        """
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

        chat_prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            (
                "human",
                "{instruction}\n\nContext:\n{context}\n\nQuestion:\n{question}",
            ),
        ]).partial(instruction=self.prompt)

        self.qa = RetrievalQA.from_chain_type(
            llm=self.llm,
            retriever=self.retriever,
            chain_type="stuff",
            chain_type_kwargs={"prompt": chat_prompt},
        )

    def add_pdf_to_rag(self, pdf_path: str) -> None:
        """Add a PDF document to the RAG vector store."""
        from langchain.document_loaders import PyPDFLoader
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        from langchain_community.vectorstores import FAISS

        # Load and split the PDF document
        loader = PyPDFLoader(pdf_path)
        documents = loader.load()

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        docs = text_splitter.split_documents(documents)

        # Create embeddings for the new documents
        new_embeddings = self.embeddings.embed_documents([doc.page_content for doc in docs])

        # Load existing FAISS index
        vstore = FAISS.load_local(
            "ipp_index",
            self.embeddings,
            allow_dangerous_deserialization=True,
        )

        # Add new documents and their embeddings to the vector store
        vstore.add_texts(
            [doc.page_content for doc in docs],
            new_embeddings,
        )

        # Save the updated index back to disk
        vstore.save_local("ipp_index")

