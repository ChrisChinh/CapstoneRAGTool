import os
import re
import json
from typing import TypedDict, List
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END, START
from dotenv import load_dotenv

# Load .env variables immediately for key access
load_dotenv() 

# --- UTILITY FUNCTIONS: NORMALIZATION + IPP IDENTIFIER EXTRACTION ---
def _normalize_text_for_ids(text: str) -> str:
    """Normalize text to make IPP identifier detection robust to PDF artifacts."""
    if not text:
        return ""
    # Remove soft hyphen and zero-width chars
    text = text.replace("\u00AD", "")  # soft hyphen
    text = text.replace("\u200B", "")  # zero-width space
    text = text.replace("\u200C", "").replace("\u200D", "")
    # Normalize various unicode dashes to ASCII hyphen
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    # Join hyphenated line breaks: word-<newline>word -> wordword
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    # Join bare line breaks inside tokens: word<newline>word -> word word (then we remove spaces inside ipp tokens later)
    text = re.sub(r"(?<=\w)\s*\n\s*(?=\w)", " ", text)
    # Remove extra spaces inside obvious IPP prefixes like 'ipp s' -> 'ipps'
    text = re.sub(r"(?i)\b(ipp)\s+([a-z])", r"\1\2", text)
    return text

def _normalize_identifier(s: str) -> str:
    """Normalization for comparison: lowercase and strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", s.lower())

def extract_ipp_identifiers(code_text: str) -> set:
    """
    Extract IPP-like identifiers robustly.
    Matches any function/structure starting with 'ipp' + a letter (covers ippi, ipps, ippm, ippr, ippcore, etc.).
    Case-insensitive; returns normalized lowercase originals (but keeps original spelling as well if needed).
    """
    text = _normalize_text_for_ids(code_text)
    # Broad domain coverage: ipp + letter + [a-z0-9_]+
    pattern = re.compile(r"\b(ipp[a-z][a-z0-9_]+)\b", re.IGNORECASE)
    ids = set(m.group(1) for m in pattern.finditer(text))
    # Return normalized case-insensitive set for robust comparison
    return set(i.lower() for i in ids)

# --- 1. CONFIGURATION AND PROMPT DEFINITIONS ---

PROMPT = """
Please refactor this code snippet to use IPP instead of basic C. Functional parity should be preserved.
"""
DEFAULT_SYSTEM_PROMPT = (
    "You are an expert assistant that refactors C code to use Intel Integrated Performance Primitives (IPP). "
    "Use the context provided to inform your refactoring. "
    "Crucially, you MUST maintain functional and logical parity. "
    "It is understood that IPP refactoring REQUIRES replacing standard C memory calls (like malloc/free) "
    "with optimized IPP equivalents (like ippsMalloc) and managing state via IPP structures. "
    "Avoid referencing functions not present in the provided context unless they are standard IPP APIs."
    "You may, however, utilize functions from the original headers if appropriate." 
    "Be sure to retain the original header files."
)
CRITIC_SYSTEM_PROMPT = (
    "You are a meticulous Code Reviewer specializing in Intel IPP. "
    "Your task is to analyze the refactored code and output a critique in JSON format. "
    "Your primary goal is to ensure the generated code is highly idiomatic IPP and uses only VALID functions."
    "If you believe a function is not valid, review the database and provided context. You should triple check the database to be absolutely certain."
    "**CRITICAL VALIDATION CHECK (PRIORITY 1):** You MUST verify that ALL IPP functions and data structures "
    "used in the 'Refactored IPP Code' are justified by the provided 'Context Documents Used'. "
    "If any IPP identifier is used that is not present in the context, the code is unacceptable due to hallucination."
    "If you suspect a hallucination, you are required to use high effort to verify and do not err on the side of caution."
    "Functional Parity Definition (PRIORITY 2): The code must produce the same result for the same input and handle errors equivalently. "
    "You MUST ignore memory allocation technique changes unless SIZE or ALIGNMENT is incorrect. "
    "You MUST output a JSON object with EXACTLY THESE TWO TOP-LEVEL KEYS: "
    "1) 'is_acceptable' (boolean) and 2) 'critique_reasoning' (string). "
    "ABSOLUTELY NO CONVERSATIONAL TEXT, ONLY THE JSON OBJECT IS ALLOWED. DO NOT use any other keys."
    "Use high effort always. If you suspect a hallucination, double check the database and absolutely verify with 100 percent certainty that it is a hallucination."
)

# --- 2. STRUCTURED OUTPUT SCHEMAS (for the Critic) ---
class Critique(BaseModel):
    """Critique of the generated IPP refactoring."""
    is_acceptable: bool = Field(description="Is the refactored code both functionally correct and idiomatic IPP? (True/False)")
    critique_reasoning: str = Field(description="Detailed explanation of the critique and what needs to be fixed if is_acceptable is False.")

# --- 3. WORKFLOW STATE DEFINITION (for LangGraph) ---
class RAGState(TypedDict):
    """Represents the state of the Self-RAG process."""
    original_code: str
    question: str
    generation: str
    documents: List[str]
    critique_status: str
    critique_reason: str
    attempts: int

# --- 4. PROMPT TEMPLATES ---

GENERATOR_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", DEFAULT_SYSTEM_PROMPT),
    ("human", 
     "Refactoring Goal: {instruction}\n\n"
     "Context from IPP Documentation:\n{documents}\n\n"
     "**WARNING:** A critic will verify that every IPP function used is justified by the context."
     "Original C Code to Refactor:\n```c\n{original_code}\n```\n\n"
     "Provide ONLY the refactored code block. Do not include any text, headers, or comments outside the code block.")
])

CRITIC_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", CRITIC_SYSTEM_PROMPT),
    ("human", 
     "Original C Code:\n```c\n{original_code}\n```\n\n"
     "Refactored IPP Code:\n```c\n{generation}\n```\n\n"
     "Context Documents Used:\n{documents}\n\n"
     "Analyze the refactored code and output a JSON critique that STRICTLY conforms to the specified format."
     "Use high effort. Double check that any supposed hallucinations cannot be found in the provided context." 
     "If there is any chance that a suspected hallucination could be found in the database, please search again.")
])

# --- 5. MODEL IMPLEMENTATION ---

class Model:
    def __init__(self, prompt: str = PROMPT, system_prompt: str | None = DEFAULT_SYSTEM_PROMPT):
        self.prompt = prompt
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        
        # --- LLM Setup (OpenRouter/GPT-4o) ---
        openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")
        openrouter_base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        openrouter_key = os.getenv("OPENROUTER_API_KEY")

        self.llm = ChatOpenAI(
            model=openrouter_model,
            openai_api_base=openrouter_base,
            openai_api_key=openrouter_key,
            temperature=0.2,
        )
        
        # --- RAG Setup (Local FAISS) ---
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        # Keep a reference to the vector store for verification sweeps
        self.vstore = FAISS.load_local(
            "ipp_index",
            self.embeddings,
            allow_dangerous_deserialization=True,
        )
        # Use MMR and higher k to diversify retrieval
        self.retriever = self.vstore.as_retriever(
            search_type="mmr",
            search_kwargs={"k": 8, "fetch_k": 20, "lambda_mult": 0.5},
        )

        # --- LangGraph Setup ---
        self.graph = self._build_critique_graph()
    
    # --------------------------------------------------------------------------------
    # --- LANGGRAPH NODE DEFINITIONS ---
    # --------------------------------------------------------------------------------
        
    def _retrieve_node(self, state: RAGState) -> dict:
        """Node 1: Retrieves documents from FAISS using a multi-query approach."""
        print("--- RAG: Retrieving IPP Documents (Multi-Query) ---")
        
        # 1. Define the LLM Chain for query transformation
        QUERY_TRANSFORM_PROMPT = ChatPromptTemplate.from_messages([
            ("system", "You are a query rewriting expert. Based on the C code and goal, generate three highly distinct search queries to find IPP documentation. Output ONLY the queries, separated by newlines."),
            ("human", "Original Code:\n{original_code}\nGoal: {instruction}"),
        ])
        
        query_chain = QUERY_TRANSFORM_PROMPT | self.llm.bind(temperature=0.0) | StrOutputParser()
        
        # 2. Generate and parse queries
        raw_queries = query_chain.invoke({
            "original_code": state["original_code"],
            "instruction": self.prompt
        })
        
        search_queries = [q.strip() for q in raw_queries.split('\n') if q.strip() and len(q.strip()) > 10]
        
        if not search_queries:
            search_queries = [state["original_code"], self.prompt]
            
        print(f"   Searching with queries: {search_queries}")

        # 3. Retrieve documents for ALL queries and ensure unique results
        unique_docs = set()
        for q in search_queries:
            docs = self.retriever.invoke(q)
            for doc in docs:
                unique_docs.add(doc.page_content) 
        
        # --- Context Augmentation Fix (Step 1) ---
        # Add common boilerplate vocabulary to the context to prevent false positives.
        BOILERPLATE_CONTEXT = (
            "\n\n--- Universal IPP Boilerplate ---\n"
            "Standard IPP Memory and Utility Functions: ippsMalloc_8u, ippsMalloc_32f, ippsFree, "
            "ippsSet_32f, ippsCopy_32f, ippsFIRSRGetSize_32f, ippsFIRSRInit_32f."
            "\n----------------------------------"
        )
        final_context_list = list(unique_docs)
        final_context_list.append(BOILERPLATE_CONTEXT)
        # --- End Context Augmentation Fix ---

        # 4. Format result for state
        return {"documents": final_context_list}

    def _generate_node(self, state: RAGState) -> dict:
        """Node 2: Generates (or refines) the refactored code."""
        is_refinement = state.get("attempts", 0) > 0
        print(f"--- RAG: Generating Code (Attempt {state.get('attempts', 0) + 1}) ---")

        critique_reason = ""
        
        # --- ENHANCED REFINEMENT PROMPT LOGIC ---
        if is_refinement and state.get("critique_reason"):
             reason = state['critique_reason']
             
             # Check for JSON or Hallucination failure messages
             if "JSON PARSING FAILED" in reason:
                 critique_reason = f"CRITICAL FIX: Your previous attempt generated invalid JSON. You MUST fix your output format. Error: {reason}\n\n"
             elif "HALLUCINATED" in reason:
                 critique_reason = (
                    f"CRITICAL FIX: Your previous attempt failed because it hallucinated IPP functions NOT found in the documentation. "
                    f"You MUST only use functions EXPLICITLY present in the 'Context Documents Used'. "
                    f"The specific issue was: {reason}\n\n"
                 )
             else:
                 # For general functional errors
                 critique_reason = f"CRITIC FEEDBACK: Fix the following issue: {reason}\n\n"
        # --- END ENHANCED REFINEMENT PROMPT LOGIC ---


        prompt = GENERATOR_PROMPT_TEMPLATE.invoke({
            "instruction": self.prompt + critique_reason,
            "documents": "\n\n---\n\n".join(state["documents"]),
            "original_code": state["original_code"],
        })
        
        response = self.llm.invoke(prompt)
        
        return {
            "generation": response.content,
            "attempts": state.get("attempts", 0) + 1,
            "critique_reason": ""
        }

    def _critique_node(self, state: RAGState) -> dict:
        """Node 3: Critiques the generated code using a structured LLM call and code-level checks."""
        print("--- RAG: Critiquing Generation ---")
        
        # 1. IDENTIFY HALLUCINATIONS PROGRAMMATICALLY (THE HARD FAIL GUARDRAIL)
        context_str = "\n\n".join(state["documents"])
        generated_ids = extract_ipp_identifiers(state["generation"])
        context_ids = extract_ipp_identifiers(context_str)

        missing_ids = generated_ids - context_ids

        # 1b. VERIFICATION SWEEP against the full FAISS index before failing
        newly_added_docs = []
        if missing_ids:
            print(f"--- RAG: Verifying suspected missing identifiers: {sorted(missing_ids)} ---")
            still_missing = set()
            for ident in sorted(missing_ids):
                # Query the index with the raw identifier
                try:
                    candidate_docs = self.retriever.invoke(ident) or []
                except Exception:
                    candidate_docs = []
                # Normalize for robust containment checks
                norm_ident = _normalize_identifier(ident)
                verified = False
                for doc in candidate_docs:
                    content = doc.page_content or ""
                    norm_content = _normalize_text_for_ids(content).lower()
                    # Fast path: case-insensitive exact token match
                    if ident.lower() in norm_content:
                        verified = True
                    else:
                        # Fuzzy path: strip non-alphanumerics and test containment
                        if _normalize_identifier(norm_content).find(norm_ident) != -1:
                            verified = True
                    if verified:
                        newly_added_docs.append(content)
                        break
                if not verified:
                    still_missing.add(ident)

            # If we verified any, extend the context and recompute context_ids
            if newly_added_docs:
                # Ensure uniqueness
                existing = set(state["documents"])
                to_add = [d for d in newly_added_docs if d not in existing]
                if to_add:
                    state["documents"].extend(to_add)
                    context_ids = extract_ipp_identifiers("\n\n".join(state["documents"]))
                    missing_ids = generated_ids - context_ids
                else:
                    missing_ids = still_missing
            else:
                missing_ids = still_missing

        if missing_ids:
            # IMMEDIATE FAILURE after verification sweep
            reason = (
                "CRITICAL ERROR: The generated code HALLUCINATED the following IPP identifiers "
                f"which were NOT found in the provided or verified documentation context: {', '.join(sorted(missing_ids))}. "
                "The model MUST use only the functions and structures explicitly present in the context."
            )
            print(f"--- HARD FAIL: Hallucination Confirmed: {', '.join(sorted(missing_ids))} ---")
            return {
                "critique_status": "REVISE",
                "critique_reason": reason,
                "documents": state["documents"],  # propagate any newly added verified docs
            }

        # 2. INVOKE LLM FOR FUNCTIONAL CRITIQUE (Only runs if no hard hallucination detected)
        critique_llm_chain = (
            CRITIC_PROMPT_TEMPLATE
            | self.llm.with_config(
                tags=["critic"], 
                response_format={"type": "json_object"}
            )
        )
        
        try:
            raw_response = critique_llm_chain.invoke({
                "original_code": state["original_code"],
                "generation": state["generation"],
                "documents": "\n\n---\n\n".join(state["documents"]),
            }).content
        except Exception as e:
            print(f"CRITIC API ERROR: {e}")
            return {"critique_status": "REVISE", "critique_reason": f"API FAILED during critique: {e}", "documents": state["documents"]}

        # 3. ROBUST JSON EXTRACTION AND VALIDATION
        try:
            match = re.search(r'```json\s*(.*?)\s*```', raw_response, re.DOTALL)
            json_text = match.group(1).strip() if match else raw_response.strip()
            raw_output_dict = json.loads(json_text)
            if len(raw_output_dict) == 1 and isinstance(list(raw_output_dict.values())[0], dict):
                payload = list(raw_output_dict.values())[0]
            else:
                payload = raw_output_dict
            critique_instance = Critique.model_validate(payload)
        except Exception as e:
            print(f"CRITIC PARSE FAILED AFTER CLEANING: {e}")
            reason = (f"CRITIC JSON PARSING FAILED. The output did not conform to the required JSON structure. Error: {e}")
            return {"critique_status": "REVISE", "critique_reason": reason, "documents": state["documents"]}
        
        is_acceptable = critique_instance.is_acceptable
        
        return {
            "critique_status": "ACCEPT" if is_acceptable else "REVISE",
            "critique_reason": critique_instance.critique_reasoning,
            "documents": state["documents"],  # keep any augmented context
        }

    # --------------------------------------------------------------------------------
    # --- LANGGRAPH BUILDER AND RUNNER ---
    # --------------------------------------------------------------------------------
    
    def _build_critique_graph(self):
        """Builds the LangGraph workflow structure."""
        
        MAX_ATTEMPTS = 3

        def route_critique(state: RAGState) -> str:
            """Routes the workflow based on the critique result."""
            if state["critique_status"] == "ACCEPT":
                print(f"--- RAG: ACCEPTED after {state['attempts']} attempts. ---")
                return END
            elif state["attempts"] >= MAX_ATTEMPTS:
                print(f"--- RAG: FAILED after {state['attempts']} attempts. ---")
                return END
            else:
                print("--- RAG: REFINEMENT needed. Looping back. ---")
                return "refine"

        workflow = StateGraph(RAGState)

        workflow.add_node("retrieve", self._retrieve_node)
        workflow.add_node("generate", self._generate_node)
        workflow.add_node("critique", self._critique_node)
        
        workflow.add_edge(START, "retrieve")
        workflow.add_edge("retrieve", "generate")
        workflow.add_edge("generate", "critique")
        
        workflow.add_conditional_edges(
            "critique",
            route_critique,
            {
                END: END,
                "refine": "generate" 
            }
        )
        
        return workflow.compile()

    def run(self, query: str) -> str:
        """Runs the Self-RAG critiquing workflow. (Drop-in replacement for gui.py's model.run())."""
        original_code = query.strip()
        
        if not original_code:
            return "Error: Input code snippet is empty. Please enter code to refactor."

        initial_state = {
            "original_code": original_code,
            "question": self.prompt, 
            "generation": "",
            "documents": [],
            "critique_status": "",
            "attempts": 0,
            "critique_reason": ""
        }
        
        final_state = self.graph.invoke(initial_state)

        final_code = final_state.get("generation", 
            "Error: Self-RAG failed to produce an output within the retry limit."
        )
        
        if final_state.get("critique_status") == "REVISE":
            final_code = (
                f"### FAILED REFACTORING AFTER {final_state.get('attempts', 0)} ATTEMPTS ❌\n\n"
                f"The model could not resolve the final issue:\n"
                f"**Critique:** {final_state.get('critique_reason', 'Unknown failure.')}\n\n"
                f"--- Last Generated Code ---\n" + final_code
            )
            
        return final_code

    # --- GUI COMPATIBILITY HELPERS (Same as before) ---
    def check_connection(self) -> bool:
        """Checks if the LLM connection is valid."""
        try:
            test_llm = ChatOpenAI(
                model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
                openai_api_base=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                openai_api_key=os.getenv("OPENROUTER_API_KEY"),
                temperature=0.0
            )
            test_llm.invoke("Test connection")
            return True
        except Exception:
            return False

    def set_system_prompt(self, system_prompt: str) -> None:
        print("WARNING: System prompt changes require manual restart to recompile the LangGraph.")
        
    def add_pdf_to_rag(self, pdf_path: str) -> None:
        """Add a PDF document to the RAG vector store."""
        from langchain_community.document_loaders import PyPDFLoader
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        
        print(f"Adding {pdf_path} to FAISS index. This may take a bit...")