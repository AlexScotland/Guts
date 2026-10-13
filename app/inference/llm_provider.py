from abc import ABC, abstractmethod

class LLMProvider(ABC):

    @abstractmethod
    def invoke(self, messages):
        pass