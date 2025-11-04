import os
import openai
from redis import Redis
from redis.commands.graph import Graph

# ─────────────────────────────
# CONFIGURATION
# ─────────────────────────────
FALKOR_HOST = "localhost"
FALKOR_PORT = 6379
GRAPH_NAME = "graphrag"

MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ─────────────────────────────
# SIMPLE GRAPH CLIENT (FALKORDB)
# ─────────────────────────────
class FalkorDBClient:
    def __init__(self, host=FALKOR_HOST, port=FALKOR_PORT, graph_name=GRAPH_NAME):
        self.redis = Redis(host=host, port=port, decode_responses=True)
        self.graph = Graph(graph_name, self.redis)

    def query(self, cypher):
        try:
            result = self.graph.query(cypher)
            return [record.values() for record in result.result_set]
        except Exception:
            return []

    def add_text_as_node(self, text):
        node = self.graph.node(label="Document", content=text[:500])
        self.graph.add_node(node)
        self.graph.commit()


# ─────────────────────────────
# MODELS
# ─────────────────────────────
class OpenAIModel:
    def __init__(self, api_key):
        openai.api_key = api_key

    def generate(self, prompt: str) -> str:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response["choices"][0]["message"]["content"].strip()


# Optional placeholder for local/offline model
class LocalModel:
    def generate(self, prompt: str) -> str:
        return f"[Local model simulated response]\n\n{prompt[:200]}..."


# ─────────────────────────────
# MAIN MODEL WRAPPER (GUI INTERFACE)
# ─────────────────────────────
class Model:
    def __init__(self):
        # Connect to FalkorDB
        self.graph = FalkorDBClient()

        # Initialize LLM
        if MODEL_PROVIDER == "openai":
            self.llm = OpenAIModel(api_key=OPENAI_API_KEY)
        else:
            self.llm = LocalModel()

        self.system_prompt = (
            "You are a helpful AI that refactors code for clarity and maintainability. "
            "Preserve functionality while improving readability and structure."
        )

    # GUI-required methods ───────────────────────
    def check_connection(self) -> bool:
        try:
            # Try a basic FalkorDB query to confirm connection
            _ = self.graph.query("MATCH (n) RETURN n LIMIT 1")
            return True
        except Exception:
            return False

    def set_system_prompt(self, new_prompt: str):
        self.system_prompt = new_prompt or self.system_prompt

    def add_pdf_to_rag(self, path: str):
        # Minimal placeholder: just store file text in graph
        # For now, FalkorDB acts as a text memory (no embeddings)
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            text = "\n".join(page.get_text() for page in doc)
            self.graph.add_text_as_node(text)
        except Exception as e:
            raise RuntimeError(f"Failed to load PDF: {e}")

    def run(self, input_text: str) -> str:
        # Basic RAG — search for related nodes
        query = "MATCH (n:Document) RETURN n.content LIMIT 3"
        context_nodes = self.graph.query(query)
        context = "\n\n".join([str(n[0]) for n in context_nodes if n])

        prompt = (
            f"{self.system_prompt}\n\n"
            f"Context (from FalkorDB):\n{context}\n\n"
            f"Refactor the following code:\n```python\n{input_text}\n```"
        )

        return self.llm.generate(prompt)
