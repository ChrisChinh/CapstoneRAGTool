from openai import AzureOpenAI
from rag_creator import IndexCreator
import logging

MODEL_NAME = "gpt-4o-deployment"

RAG_PROMPT = """
You are a higly skilled software engineer that refactors IPP code. Use the reference documentation
provided to improve and refactor code.

DOCUMENTATION:
{context}

USER REQUEST:
{query}

INSTRUCTIONS:
- Only use the information from the documentation if it is relevant and useful.
- If something is unclear, only reason using the information you have.
- Code should prioritize functionality over all else.
- Include brief explanations of what you have done.

"""

SYSTEM_PROMPT = "You are an expert refactoring software engineer."

class Model:
    def __init__(self, index_creator: IndexCreator):
        self.index_creator = index_creator
        self.logger = logging.getLogger(__name__)
        self.logger.level = logging.INFO
        self.client = index_creator.openai_client

        self.temperature = 0.3
        self.max_tokens = 1500
        self.system_prompt = SYSTEM_PROMPT


    def set_system_prompt(self, prompt):
        self.system_prompt = prompt


    def run(self, query):
        print("Querying DB...")
        context = self.index_creator.query_db(query, k=5)
        context = "\n\n----\n\n".join(context)

        prompt = RAG_PROMPT.format(context=context, query=query)
        print("Running query with the following prompt:\n", prompt)


        response = self.client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature
        )
        print("Response received....")

        return response.choices[0].message.content