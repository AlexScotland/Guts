from langchain_core.messages import BaseMessage

class ConversationMemory():
    def __init__(self):
        self.messages: list[BaseMessage] = []

    def add(self, message):
        self.messages.append(message)

    def replace(self, messages: list[BaseMessage]):
        """Replace the entire conversation with a new set of messages.

        Used in stateless (HTTP) mode: each request supplies the conversation
        itself, so the server never carries turns over on its own.
        """
        self.messages = list(messages)

    def get(self):
        return self.messages

    def to_dict(self):
        return {
            "messages": [
                {
                    "type": message.type,
                    "content": message.content,
                    "additional_kwargs": message.additional_kwargs,
                    "response_metadata": message.response_metadata,
                }
                for message in self.messages
            ]
        }


class TrimmedMemory(ConversationMemory):
    """Conversation store with a hard context budget.

    Keeps the leading system message (if any) plus the most recent
    ``max_messages`` entries. A hot-swap target for the `fast` preset:
    bounds the prompt size regardless of how long the session gets.
    """

    def __init__(self, max_messages: int = 20):
        super().__init__()
        self.max_messages = max(1, int(max_messages))

    def add(self, message):
        super().add(message)
        messages = self.messages
        keep_first = 1 if messages and messages[0].type == "system" else 0
        overflow = len(messages) - keep_first - self.max_messages
        if overflow > 0:
            del messages[keep_first : keep_first + overflow]
