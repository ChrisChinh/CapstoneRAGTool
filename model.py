import os
import openai
from redis import Redis
from redis.commands.graph import Graph
from redis.commands.graph.node import Node

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
    def __init__(self, host=FALKOR_HOST, port=FALKOR_PORT, graph_name=GRAPH_NAME, api_key=None):
        # Create Redis client
        self.redis = Redis(
            host=host,
            port=port,
            password=api_key,
            decode_responses=True
        )

        # Correct: pass Redis client first, then graph name
        self.graph = Graph(self.redis, graph_name)

    def query(self, cypher: str):
        try:
            result = self.graph.query(cypher)
            return [record.values() for record in result.result_set]
        except Exception:
            return []

    def add_text_as_node(self, text: str):
        node = Node(label="Document", properties={"content": text[:500]})
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


class LocalModel:
    def generate(self, prompt: str) -> str:
        return f"[Local model simulated response]\n\n{prompt[:200]}..."


# ─────────────────────────────
# MAIN MODEL WRAPPER
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
            _ = self.graph.query("MATCH (n) RETURN n LIMIT 1")
            return True
        except Exception:
            return False

    def set_system_prompt(self, new_prompt: str):
        self.system_prompt = new_prompt or self.system_prompt

    def add_pdf_to_rag(self, path: str):
        try:
            import fitz
            doc = fitz.open(path)
            text = "\n".join(page.get_text() for page in doc)
            self.graph.add_text_as_node(text)
        except Exception as e:
            raise RuntimeError(f"Failed to load PDF: {e}")

    def run(self, input_text: str) -> str:
        query = "MATCH (n:Document) RETURN n.content LIMIT 3"
        context_nodes = self.graph.query(query)
        context = "\n\n".join([str(n[0]) for n in context_nodes if n])

        prompt = (
            f"{self.system_prompt}\n\n"
            f"Context (from FalkorDB):\n{context}\n\n"
            f"Refactor the following code:\n```python\n{input_text}\n```"
        )

        return self.llm.generate(prompt)
