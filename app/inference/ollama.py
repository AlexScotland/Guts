from .llm_provider import LLMProvider
from .config import OLLAMA_URL
from langchain_ollama import ChatOllama
 

class OllamaProvider(LLMProvider):

    def __init__(self, model="qwen3.6:27b"):
        self.llm = ChatOllama(
            model=model,
            base_url=OLLAMA_URL,
            temperature=0.2
        )

    def with_tools(self, tools):
        self.llm = self.llm.bind_tools(tools)

    def with_structured_output(self, schema):
        return self.llm.with_structured_output(schema)


    def invoke(self, messages):
        return self.llm.invoke(messages)