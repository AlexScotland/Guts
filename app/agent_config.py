from dataclasses import dataclass

@dataclass
class AgentConfig:
    agent_path: str = "AGENTS.md"
    max_iterations: int = 5