"""The single goal extractor.

One class, one ``extract()``. There is no router and no factory: instead of
classifying the prompt into a category and then dispatching to a per-category
extractor (two LLM calls, N schemas, N registrations), this makes ONE
structured-output call against the single ``Goal`` schema and normalizes the
result. Everything a prompt asks for that has no dedicated field lands in the
open ``requirements`` list or the ``output_spec`` dict.
"""
from __future__ import annotations

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from .goal import Goal
from .examples import EXAMPLES


class GoalExtractor:
    def __init__(self, llm):
        # Bind the single Goal schema for one-shot structured extraction.
        self.structured_llm = llm.with_structured_output(Goal)

    def _build_messages(self, user_input: str, history: list | None = None) -> list:
        system = (
            "You are a prompt-analysis agent for an evaluation harness.\n"
            "Extract a Goal from the incoming prompt.\n\n"
            "Rules:\n"
            "1. 'intent': the high-level action the prompt asks for (one sentence).\n"
            "2. 'requirements': EVERY concrete requirement or deliverable the prompt "
            "asks for, one item each, independently verifiable. Do NOT merge several "
            "requirements into one item. This is the primary field.\n"
            "3. 'constraints': any rules, length limits, tone, style, format, or "
            "structural criteria. Empty list if none.\n"
            "4. 'suggested_sources': file paths or documents the prompt explicitly "
            "names. null if none.\n"
            "5. 'output_spec': an optional structured tail holding prompt-specific "
            "structure verbatim - e.g. multiple-choice answer_options, "
            "required_keywords, mandatory_sections, target_format. Copy such items "
            "exactly as given. Omit it (null) when the prompt has no such structure.\n\n"
            "The full conversation history is provided for context. Use it to "
            "resolve references, follow-ups, and corrections so the extracted Goal "
            "reflects the user's complete intent across the whole conversation, "
            "not just the latest message.\n\n"
            "Return a valid Goal object. Do not invent requirements the prompt did "
            "not state."
        )
        
        # 1. Start with the system message
        messages: list = [SystemMessage(content=system)]
        
        # 2. Add examples with correct message types
        for example in EXAMPLES:
            messages.append(HumanMessage(content=example["prompt"]))
            messages.append(AIMessage(content=str(example["goal"]))) # Changed to AIMessage
            
        # 3. Add history, ensuring no nested SystemMessages exist
        if history:
            for msg in history:
                if isinstance(msg, SystemMessage):
                    continue # Skip system messages in history to avoid Ollama errors
                messages.append(msg)
                
        # 4. Add the latest user input
        messages.append(HumanMessage(content=user_input))
        return messages


    def extract(self, user_input: str, history: list | None = None) -> Goal:
        """One structured call -> a normalized Goal.

        ``history`` is the full conversation so far (a list of BaseMessage).
        When provided it is included in the prompt so the extracted Goal
        reflects the whole conversation, not just the latest message.
        """
        raw = self.structured_llm.invoke(self._build_messages(user_input, history))
        return Goal.from_raw(raw)
