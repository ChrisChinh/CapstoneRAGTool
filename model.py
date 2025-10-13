from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.chains import RetrievalQA
from langchain_openai import ChatOpenAI

PROMPT = """
Please refactor this code snippet to use IPP instead of basic C. Functional parity should be preserved.
"""

class Model:
    def __init__(self, prompt=PROMPT):
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        vstore = FAISS.load_local("ipp_index",
                                       self.embeddings,
                                       allow_dangerous_deserialization=True
                                       )
        self.retriever = vstore.as_retriever(search_type="similarity", search_kwargs={"k": 4})

        self.llm = ChatOpenAI(
            model="gpt-oss-20b",                 # whatever model name you’ve loaded in LM Studio
            openai_api_base="http://localhost:1234/v1",  # LM Studio’s API endpoint
            openai_api_key="lm-studio",          # arbitrary placeholder; LM Studio ignores it
            temperature=0.2                      # tweak creativity if you want
        )

        self.qa = RetrievalQA.from_chain_type(
            llm=self.llm,
            retriever=self.retriever,
            chain_type="stuff",
        )

        self.prompt = prompt

    def run(self, query: str) -> str:
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

